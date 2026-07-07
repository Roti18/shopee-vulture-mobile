"""
State: CHECK_VARIANT — satu-satunya tempat cek stok yang valid.

Flow per mode:
  MONITOR: scan "Stok: N", threshold, kalo ada -> alert -> close -> loop
  EXECUTE: find_variant_with_stock (1 scan, pake TTL cache) -> tap -> submit -> checkout

Semua failure return BUY_VOUCHER (bukan RECOVERY) biar loop natural.
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
        # ── 1. Dump (TTL — kalo abis wait_for_variant_popup, masih fresh) ─
        await self._cache.get(self._adb, force=False)

        parser = VariantParser(self._cache)

        if not parser.is_variant_popup_open():
            log.warning("CHECK_VARIANT: popup tak terdeteksi, recovery")
            return WorkflowState.RECOVERY

        # Submit button reference (sebelum UI diubah)
        submit_el = parser.get_submit_button()
        submit_x, submit_y, resolved_via = (
            (540, 2236, "default_fallback") if submit_el is None
            else (submit_el.tap_x, submit_el.tap_y, submit_el.resolved_via)
        )

        mode = self._runtime.mode if self._runtime else BotMode.RUNNING

        if mode == BotMode.MONITOR:
            return await self._handle_monitor(parser)

        return await self._handle_execute(parser, submit_x, submit_y, resolved_via)

    # ═══════════════════════════════════════════════════════════════════ #
    # MONITOR — full stock scan + threshold + alert
    # ═══════════════════════════════════════════════════════════════════ #

    async def _handle_monitor(self, parser: VariantParser) -> WorkflowState:
        all_stocks = parser.get_all_stock_counts()
        if not all_stocks:
            return WorkflowState.BUY_VOUCHER  # tanpa varian, skip

        variant_info = parser.find_variant_with_stock(
            target_variant=self._product.variant,
            minimum_stock=self._product.minimum_stock,
            stock_mode=self._product.stock_mode,
        )
        if variant_info is None:
            log.info("Stok threshold: mode=%s, min=%d, ditemukan=%s",
                     self._product.stock_mode, self._product.minimum_stock, all_stocks)
            await self._bus.emit(ev.StockEmptyEvent(
                variant=self._product.variant, stock_count=max(all_stocks),
                threshold=self._product.minimum_stock,
            ))
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        log.info("MONITOR: stok %d buat '%s'", variant_info.stock_count, self._product.variant)
        await self._bus.emit(ev.VariantStockDetectedEvent(
            product_name=self._product.name, variant=self._product.variant or variant_info.variant_text,
            stock_count=variant_info.stock_count,
        ))
        await vacts.close_variant_popup(self._adb, self._cache)
        return WorkflowState.BUY_VOUCHER

    # ═══════════════════════════════════════════════════════════════════ #
    # EXECUTE — 1 scan find_variant_with_stock, tanpa get_all_stock_counts
    # ═══════════════════════════════════════════════════════════════════ #

    async def _handle_execute(self, parser, sx, sy, sv) -> WorkflowState:
        # 1 scan — cari varian + threshold langsung
        variant_info = parser.find_variant_with_stock(
            target_variant=self._product.variant,
            minimum_stock=self._product.minimum_stock,
            stock_mode=self._product.stock_mode,
        )

        if not variant_info:
            log.info("EXECUTE: varian gak memenuhi threshold, close popup loop")
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── Tap variant ─────────────────────────────────────────────
        el = variant_info.resolved_element
        log.info("Tap variant [%s] (%d, %d)", el.resolved_via, el.tap_x, el.tap_y)
        tapped = await self._adb.tap(el.tap_x, el.tap_y)
        if not tapped:
            log.error("EXECUTE: gagal tap variant")
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        await asyncio.sleep(0.5)

        # Force dump abis tap — UI pasti berubah
        await self._cache.get(self._adb, force=True)
        parser = VariantParser(self._cache)
        submit_el = parser.get_submit_button()
        if submit_el is not None:
            sx, sy, sv = submit_el.tap_x, submit_el.tap_y, submit_el.resolved_via

        # ── Cek submit dulu ─────────────────────────────────────────
        submit_text = parser.get_submit_button_text()
        if "Beli Sekarang" not in submit_text:
            log.info("EXECUTE: submit = '%s' -> close loop", submit_text)
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── Qty ─────────────────────────────────────────────────────
        if self._product.purchase_quantity > 1:
            plus_el = parser.get_plus_button()
            ok = await vacts.set_purchase_quantity(
                self._adb, self._cache, self._product.purchase_quantity,
                plus_button_el=plus_el,
            )
            if not ok:
                log.error("EXECUTE: gagal set qty %d", self._product.purchase_quantity)
                await vacts.close_variant_popup(self._adb, self._cache)
                return WorkflowState.BUY_VOUCHER

        # ── Tap submit ─────────────────────────────────────────────
        log.info("Tap submit [%s] (%d, %d)", sv, sx, sy)
        ok = await self._adb.tap(sx, sy)
        if not ok:
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── Tunggu checkout ─────────────────────────────────────────
        arrived = await cacts.wait_for_checkout_page(
            self._adb, self._cache, max_wait=15.0
        )
        if not arrived:
            log.warning("EXECUTE: checkout page timeout")
            await self._adb.press_back()
            await asyncio.sleep(0.5)
            await self._adb.press_back()
            return WorkflowState.BUY_VOUCHER

        return WorkflowState.CHECKOUT
