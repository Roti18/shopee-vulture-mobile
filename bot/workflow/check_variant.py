"""
State: CHECK_VARIANT — satu-satunya tempat cek stok yang valid.

Flow lengkap:
  1. Refresh XML dump dari popup varian
  2. Parse semua "Stok: N"
  3. Cek threshold (stock_mode + minimum_stock)
  4. Jika tidak memenuhi → tutup popup → OPEN_PRODUCT
  5. Jika memenuhi:
     a. Emit VariantStockDetectedEvent (Telegram alert dikirim di sini)
     b. Tap variant
     c. Set purchase_quantity jika > 1
     d. Verifikasi submit button = "Beli Sekarang"
     e. → BUY_NOW

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
        self._cache.invalidate()
        await self._cache.get(self._adb)

        parser = VariantParser(self._cache)

        if not parser.is_variant_popup_open():
            log.warning("CHECK_VARIANT: popup tidak terdeteksi, recovery")
            return WorkflowState.RECOVERY

        # ── 2. Cari varian yang memenuhi threshold ───────────────────────
        variant_info = parser.find_variant_with_stock(
            target_variant=self._product.variant,
            minimum_stock=self._product.minimum_stock,
            stock_mode=self._product.stock_mode,
        )

        if variant_info is None:
            # Stok tidak ada atau tidak memenuhi minimum_stock
            all_stocks = parser.get_all_stock_counts()
            log.info(
                "CHECK_VARIANT: stok tidak memenuhi threshold "
                "(mode=%s, min=%d, ditemukan=%s)",
                self._product.stock_mode,
                self._product.minimum_stock,
                all_stocks,
            )
            await self._bus.emit(
                ev.StockEmptyEvent(
                    variant=self._product.variant,
                    stock_count=max(all_stocks) if all_stocks else 0,
                    threshold=self._product.minimum_stock,
                )
            )
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── 3. Stok terdeteksi → emit alert SEBELUM checkout ────────────
        log.info(
            "CHECK_VARIANT: stok ditemukan! count=%d variant='%s'",
            variant_info.stock_count,
            self._product.variant,
        )
        await self._bus.emit(
            ev.VariantStockDetectedEvent(
                product_name=self._product.name,
                variant=self._product.variant or variant_info.variant_text,
                stock_count=variant_info.stock_count,
            )
        )

        # ── MONITOR MODE: stok terdeteksi, notif, close popup, loop ──────
        if self._runtime and self._runtime.mode == BotMode.MONITOR:
            log.info("MONITOR MODE: stok ada, notifikasi terkirim. Tutup popup dan loop.")
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # Dapatkan koordinat submit button dari dump pertama sebelum kita memodifikasi UI
        submit_el = parser.get_submit_button()
        if submit_el is None:
            # Fallback koordinat jika tidak ter-resolve (sangat jarang)
            submit_x, submit_y = 540, 2236
            resolved_via = "default_fallback"
        else:
            submit_x, submit_y = submit_el.tap_x, submit_el.tap_y
            resolved_via = submit_el.resolved_via

        # ── 4. Tap variant ───────────────────────────────────────────────
        el = variant_info.resolved_element
        log.info("Tap variant via [%s] at (%d, %d)", el.resolved_via, el.tap_x, el.tap_y)
        tapped = await self._adb.tap(el.tap_x, el.tap_y)
        if not tapped:
            log.error("CHECK_VARIANT: gagal tap variant")
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.RECOVERY

        # Jeda agar UI Android mendeteksi tap varian
        await asyncio.sleep(0.3)

        # ── 5. Set purchase quantity jika > 1 ────────────────────────────
        if self._product.purchase_quantity > 1:
            # Dump ulang karena layout varian berubah abis tap variant
            # set_purchase_quantity handle dump + resolve + tap
            ok = await vacts.set_purchase_quantity(
                self._adb, self._cache, self._product.purchase_quantity,
            )
            if not ok:
                log.error("CHECK_VARIANT: gagal set qty %d", self._product.purchase_quantity)
                await vacts.close_variant_popup(self._adb, self._cache)
                return WorkflowState.RECOVERY

        # ── 6. Tap submit button (Beli Sekarang) ────────────────────────────
        log.info("Tap submit button (Beli Sekarang) via [%s] at (%d, %d)", resolved_via, submit_x, submit_y)
        ok = await self._adb.tap(submit_x, submit_y)
        if not ok:
            log.error("CHECK_VARIANT: gagal tap submit button")
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.RECOVERY

        # Langsung tunggu checkout page (skip BUY_NOW state — hemat 1 transisi)
        arrived = await cacts.wait_for_checkout_page(
            self._adb, self._cache, max_wait=15.0
        )
        if not arrived:
            log.warning("CHECK_VARIANT: halaman checkout tidak muncul setelah tap submit")
            return WorkflowState.RECOVERY

        return WorkflowState.CHECKOUT
