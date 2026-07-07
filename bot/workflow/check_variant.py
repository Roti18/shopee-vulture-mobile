"""
State: CHECK_VARIANT — satu-satunya tempat cek stok yang valid.

Flow per mode:
  MONITOR: stok >= threshold → emit alert Telegram → close popup → loop
  EXECUTE: stok >= threshold → tap variant → tap submit → CHECKOUT

Behaviour:
  1. Refresh XML dump dari popup varian
  2. Parse semua "Stok: N"
  3. Cek threshold (stock_mode + minimum_stock)
  4. Tidak memenuhi → tutup popup → BUY_VOUCHER (loop)
  5. MONITOR + stok >= threshold: emit alert, close popup, loop
  6. EXECUTE + stok >= threshold: tap variant → refresh cache → set qty
     → validasi submit "Beli Sekarang" → tap submit → tunggu checkout page
  7. Setiap failure di EXECUTE → tutup popup → BUY_VOUCHER (gak RECOVERY)

Stock check di halaman produk (Level 1) sudah DIHAPUS.
Popup varian adalah SATU-SATUNYA sumber informasi stok yang valid.
"""
from __future__ import annotations

import asyncio

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.actions import variant_actions as vacts
from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import WorkflowState, BotMode
from bot.models.product import ProductConfig
from bot.parser.variant_parser import VariantParser
from bot.actions import checkout_actions as cacts
from bot.utils.logger import get_logger

log = get_logger(__name__)


class CheckVariantHandler:
    def __init__(
        self,
        adb: ADBClient,
        cache: XMLCache,
        bus: EventBus,
        product: ProductConfig,
        runtime: BotRuntimeState = None,
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._bus = bus
        self._product = product
        self._runtime = runtime

    async def execute(self) -> WorkflowState:
        # ── 1. Refresh dump ─────────────────────────────────────────────
        await self._cache.get(self._adb, force=True)

        parser = VariantParser(self._cache)

        if not parser.is_variant_popup_open():
            log.warning("CHECK_VARIANT: popup tidak terdeteksi, recovery")
            return WorkflowState.RECOVERY

        # ── 2. Resolve submit button (dari dump pertama, sebelum UI diubah) ──
        submit_el = parser.get_submit_button()
        if submit_el is None:
            submit_x, submit_y, resolved_via = 540, 2236, "default_fallback"
        else:
            submit_x, submit_y, resolved_via = submit_el.tap_x, submit_el.tap_y, submit_el.resolved_via

        # ── 3. Cari varian target ────────────────────────────────────────
        # MONITOR mode: cek stok + threshold dulu, baru cari varian
        # EXECUTE mode: langsung cari varian target — cepet, skips get_all_stock_counts
        is_monitor = self._runtime and self._runtime.mode == BotMode.MONITOR

        if is_monitor:
            # MONITOR: scan stok dulu buat log + threshold
            all_stocks = parser.get_all_stock_counts()

            if not all_stocks:
                # Produk tanpa varian — skip ke submit
                log.info("CHECK_VARIANT: produk tanpa varian, skip langsung ke submit")
            else:
                variant_info = parser.find_variant_with_stock(
                    target_variant=self._product.variant,
                    minimum_stock=self._product.minimum_stock,
                    stock_mode=self._product.stock_mode,
                )

                if variant_info is None:
                    log.info(
                        "CHECK_VARIANT: stok tidak memenuhi threshold "
                        "(mode=%s, min=%d, ditemukan=%s)",
                        self._product.stock_mode, self._product.minimum_stock, all_stocks,
                    )
                    await self._bus.emit(
                        ev.StockEmptyEvent(
                            variant=self._product.variant,
                            stock_count=max(all_stocks),
                            threshold=self._product.minimum_stock,
                        )
                    )
                    await vacts.close_variant_popup(self._adb, self._cache)
                    return WorkflowState.BUY_VOUCHER

                # Stok terdeteksi → kirim alert
                log.info(
                    "CHECK_VARIANT: stok ditemukan! count=%d variant='%s'",
                    variant_info.stock_count, self._product.variant,
                )
                await self._bus.emit(
                    ev.VariantStockDetectedEvent(
                        product_name=self._product.name,
                        variant=self._product.variant or variant_info.variant_text,
                        stock_count=variant_info.stock_count,
                    )
                )
                log.info("MONITOR MODE: stok ada, notifikasi terkirim. Tutup popup dan loop.")
                await vacts.close_variant_popup(self._adb, self._cache)
                return WorkflowState.BUY_VOUCHER
        else:
            # EXECUTE: find_variant_fast — 2x cepet, tanpa regex "Stok: N"
            variant_el = parser.find_variant_fast(
                target_variant=self._product.variant,
            )
            if variant_el:
                log.info("Tap variant via [%s] at (%d, %d)", variant_el.resolved_via, variant_el.tap_x, variant_el.tap_y)
                tapped = await self._adb.tap(variant_el.tap_x, variant_el.tap_y)
                if not tapped:
                    log.error("CHECK_VARIANT: gagal tap variant — lanjut monitoring")
                    await vacts.close_variant_popup(self._adb, self._cache)
                    return WorkflowState.BUY_VOUCHER
                await asyncio.sleep(0.5)

                # Refresh cache setelah tap variant — UI berubah
                await self._cache.get(self._adb, force=True)
                parser = VariantParser(self._cache)

                # Resolve ulang submit button dari dump fresh
                submit_el = parser.get_submit_button()
                if submit_el is not None:
                    submit_x, submit_y, resolved_via = submit_el.tap_x, submit_el.tap_y, submit_el.resolved_via
            else:
                # Produk tanpa varian atau varian gak ditemukan
                log.info("CHECK_VARIANT: varian tidak ditemukan, skip ke submit langsung")

        # ── 5. Set purchase quantity jika > 1 ────────────────────────────
        if self._product.purchase_quantity > 1:
            # plus_el di-resolve dari dump pertama (resource_id tetap = buttonPlus)
            plus_el = parser.get_plus_button()
            ok = await vacts.set_purchase_quantity(
                self._adb, self._cache, self._product.purchase_quantity,
                plus_button_el=plus_el,
            )
            if not ok:
                log.error("CHECK_VARIANT: gagal set qty %d — lanjut monitoring", self._product.purchase_quantity)
                await vacts.close_variant_popup(self._adb, self._cache)
                return WorkflowState.BUY_VOUCHER

        # ── 6. Validasi submit button — jangan tap kalo "Habis" ────────────
        submit_text = parser.get_submit_button_text()
        if "Beli Sekarang" not in submit_text:
            log.warning(
                "CHECK_VARIANT: submit button bukan 'Beli Sekarang': '%s' — "
                "stok mungkin habis, tutup popup dan lanjut monitoring",
                submit_text,
            )
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── 7. Tap submit button (Beli Sekarang) ────────────────────────────
        log.info("Tap submit button (Beli Sekarang) via [%s] at (%d, %d)", resolved_via, submit_x, submit_y)
        ok = await self._adb.tap(submit_x, submit_y)
        if not ok:
            log.error("CHECK_VARIANT: gagal tap submit — lanjut monitoring")
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # Langsung tunggu checkout page (skip BUY_NOW state — hemat 1 transisi)
        arrived = await cacts.wait_for_checkout_page(
            self._adb, self._cache, max_wait=15.0
        )
        if not arrived:
            log.warning(
                "CHECK_VARIANT: checkout page tidak muncul setelah tap submit — "
                "stok habis duluan, navigasi balik ke produk"
            )
            # Popup udah di-dismiss sama tap submit, jadi press_back aja
            # beberapa kali sampe balik ke halaman produk
            await self._adb.press_back()
            await asyncio.sleep(0.5)
            await self._adb.press_back()
            return WorkflowState.BUY_VOUCHER

        return WorkflowState.CHECKOUT
