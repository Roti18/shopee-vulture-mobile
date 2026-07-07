"""
State: CHECK_VARIANT — satu-satunya tempat cek stok yang valid.

Flow per mode:
  MONITOR: stok >= threshold -> emit alert Telegram -> close popup -> loop
  EXECUTE: stok >= threshold -> tap variant -> tap submit -> CHECKOUT

Behaviour:
  1. Dump dari cache (TTL, gak force biar cepet)
  2. MONITOR: get_all_stock_counts + find_variant_with_stock
  3. EXECUTE: find_variant_with_stock direct (1 scan, tanpa get_all_stock_counts)
  4. Gak nemu varian -> close popup -> BUY_VOUCHER (short circuit, gak lanjut set qty)
  5. Setiap failure di EXECUTE -> close popup -> BUY_VOUCHER (gak RECOVERY)
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
        # ── 1. Dump dari cache (TTL, jangan force buang 2-3 dtk) ─────────
        await self._cache.get(self._adb, force=False)

        parser = VariantParser(self._cache)

        if not parser.is_variant_popup_open():
            log.warning("CHECK_VARIANT: popup tidak terdeteksi, recovery")
            return WorkflowState.RECOVERY

        # ── 2. Resolve submit button (dari dump, sebelum UI diubah) ────────
        submit_el = parser.get_submit_button()
        if submit_el is None:
            submit_x, submit_y, resolved_via = 540, 2236, "default_fallback"
        else:
            submit_x, submit_y, resolved_via = submit_el.tap_x, submit_el.tap_y, submit_el.resolved_via

        # ── 3. Cari varian ────────────────────────────────────────────────
        is_monitor = self._runtime and self._runtime.mode == BotMode.MONITOR

        if is_monitor:
            # MONITOR: 2 scan — get_all_stock_counts + find_variant_with_stock
            all_stocks = parser.get_all_stock_counts()

            if all_stocks:
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

                # Stok terdeteksi -> kirim alert
                log.info("MONITOR: stok %d buat '%s'", variant_info.stock_count, self._product.variant)
                await self._bus.emit(
                    ev.VariantStockDetectedEvent(
                        product_name=self._product.name,
                        variant=self._product.variant or variant_info.variant_text,
                        stock_count=variant_info.stock_count,
                    )
                )
                log.info("MONITOR: notif terkirim, tutup popup lanjut loop")
                await vacts.close_variant_popup(self._adb, self._cache)
                return WorkflowState.BUY_VOUCHER
            else:
                log.info("CHECK_VARIANT: tanpa varian, skip ke submit")
        else:
            # EXECUTE: 1 scan — find_variant_with_stock langsung
            variant_info = parser.find_variant_with_stock(
                target_variant=self._product.variant,
                minimum_stock=self._product.minimum_stock,
                stock_mode=self._product.stock_mode,
            )

            if not variant_info:
                log.info("CHECK_VARIANT: varian gak memenuhi threshold, close popup loop")
                await vacts.close_variant_popup(self._adb, self._cache)
                return WorkflowState.BUY_VOUCHER

            # ── 4. Tap variant ──────────────────────────────────────────
            el = variant_info.resolved_element
            log.info("Tap variant [%s] (%d, %d)", el.resolved_via, el.tap_x, el.tap_y)
            tapped = await self._adb.tap(el.tap_x, el.tap_y)
            if not tapped:
                log.error("CHECK_VARIANT: gagal tap variant")
                await vacts.close_variant_popup(self._adb, self._cache)
                return WorkflowState.BUY_VOUCHER
            await asyncio.sleep(0.5)

            # Refresh cache — UI berubah setelah tap variant
            await self._cache.get(self._adb, force=True)
            parser = VariantParser(self._cache)

            # Resolve ulang submit button
            submit_el = parser.get_submit_button()
            if submit_el is not None:
                submit_x, submit_y, resolved_via = submit_el.tap_x, submit_el.tap_y, submit_el.resolved_via

        # ── 5. Set purchase quantity ────────────────────────────────────
        if self._product.purchase_quantity > 1:
            plus_el = parser.get_plus_button()
            ok = await vacts.set_purchase_quantity(
                self._adb, self._cache, self._product.purchase_quantity,
                plus_button_el=plus_el,
            )
            if not ok:
                log.error("CHECK_VARIANT: gagal set qty %d", self._product.purchase_quantity)
                await vacts.close_variant_popup(self._adb, self._cache)
                return WorkflowState.BUY_VOUCHER

        # ── 6. Validasi submit button ──────────────────────────────────
        submit_text = parser.get_submit_button_text()
        if "Beli Sekarang" not in submit_text:
            log.warning(
                "CHECK_VARIANT: submit = '%s' -> close popup loop",
                submit_text,
            )
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── 7. Tap submit ──────────────────────────────────────────────
        log.info("Tap submit [%s] (%d, %d)", resolved_via, submit_x, submit_y)
        ok = await self._adb.tap(submit_x, submit_y)
        if not ok:
            log.error("CHECK_VARIANT: gagal tap submit")
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── 8. Tunggu checkout page ────────────────────────────────────
        arrived = await cacts.wait_for_checkout_page(
            self._adb, self._cache, max_wait=15.0
        )
        if not arrived:
            log.warning("CHECK_VARIANT: checkout page timeout, press_back balik")
            await self._adb.press_back()
            await asyncio.sleep(0.5)
            await self._adb.press_back()
            return WorkflowState.BUY_VOUCHER

        return WorkflowState.CHECKOUT
