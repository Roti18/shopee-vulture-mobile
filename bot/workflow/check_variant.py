"""
State: CHECK_VARIANT — cek stok di popup varian.

Struktur popup Shopee untuk produk multi-varian:
  sectionTierVariation
    cartPanelTierVariation
      buttonOption_selected / buttonOption_unselected → text varian

EXECUTE:
  1. Cari buttonOption (selected/unselected) yg text-nya cocok target
  2. Tap kalo belum selected
  3. Cek submit = "Beli Sekarang" → qty → tap submit → checkout

MONITOR:
  scan "Stok: N" + threshold + alert Telegram
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
        await self._cache.get(self._adb, force=False)

        parser = VariantParser(self._cache)

        if not parser.is_variant_popup_open():
            log.warning("CHECK_VARIANT: popup tak terdeteksi, recovery")
            return WorkflowState.RECOVERY

        mode = self._runtime.mode if self._runtime else BotMode.RUNNING

        if mode == BotMode.MONITOR:
            return await self._handle_monitor(parser)

        return await self._handle_execute(parser)

    # ═══════════════════════════════════════════════════════════════════ #
    # MONITOR
    # ═══════════════════════════════════════════════════════════════════ #

    async def _handle_monitor(self, parser: VariantParser) -> WorkflowState:
        all_stocks = parser.get_all_stock_counts()
        if not all_stocks:
            # Popup gak ada stock info — mungkin salah screen, reopen aja
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        variant_info = parser.find_variant_with_stock(
            target_variant=self._product.variant,
            minimum_stock=self._product.minimum_stock,
            stock_mode=self._product.stock_mode,
        )
        if variant_info is None:
            await self._bus.emit(ev.StockEmptyEvent(
                variant=self._product.variant,
                stock_count=max(all_stocks),
                threshold=self._product.minimum_stock,
            ))
            # Popup masih terbuka — tinggal tunggu interval, gak perlu close + reopen.
            return WorkflowState.MONITOR_POPUP

        await self._bus.emit(ev.VariantStockDetectedEvent(
            product_name=self._product.name,
            variant=self._product.variant or variant_info.variant_text,
            stock_count=variant_info.stock_count,
            is_checkout=False,
        ))
        # Stock terdeteksi, popup gak usah ditutup — nanti di-scan ulang.
        return WorkflowState.MONITOR_POPUP

    # ═══════════════════════════════════════════════════════════════════ #
    # EXECUTE — tap variant option, cek submit, checkout
    # ═══════════════════════════════════════════════════════════════════ #

    async def _handle_execute(self, parser: VariantParser) -> WorkflowState:
        nodes = self._cache.all_nodes()

        # ── Cari & tap varian target ─────────────────────────────────
        await self._tap_option_by_text(nodes)

        # ── Refresh cache setelah tap ────────────────────────────────
        await self._cache.get(self._adb, force=True)
        parser = VariantParser(self._cache)

        # ── Cek submit text ──────────────────────────────────────────
        submit_text = parser.get_submit_button_text()
        if "Beli Sekarang" not in submit_text:
            log.info("EXECUTE: submit = '%s' -> close", submit_text)
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── Qty ──────────────────────────────────────────────────────
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

        # ── Tap submit ───────────────────────────────────────────────
        submit_el = parser.get_submit_button()
        if submit_el is None:
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        log.info("Tap submit [%s] (%d, %d)", submit_el.resolved_via, submit_el.tap_x, submit_el.tap_y)
        ok = await self._adb.tap(submit_el.tap_x, submit_el.tap_y)
        if not ok:
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── Tunggu checkout ──────────────────────────────────────────
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

    # ═══════════════════════════════════════════════════════════════════ #
    # Tap variant option by text match
    # ═══════════════════════════════════════════════════════════════════ #

    async def _tap_option_by_text(self, nodes) -> bool:
        """
        Cari node buttonOption_selected / buttonOption_unselected
        yg text child-nya mengandung target_variant.

        Resource ID: buttonOption_selected / buttonOption_unselected
        Di dalamnya ada ImageView + TextView dgn text "Penthouse 50ml" dll.
        """
        target = self._product.variant
        if not target:
            log.info("EXECUTE: ga ada target varian, pake varian pertama")
            return False

        target_lower = target.lower()

        for node in nodes:
            rid = node.get("resource-id", "")
            if "buttonOption" not in rid:
                continue

            # Cari text di node + child nodes
            text = node.get("text", "") or node.get("content-desc", "")
            if text:
                full_text = text
            else:
                # Kumpulin text dari semua child node
                parts = []
                for child in node.iter("node"):
                    t = child.get("text", "") or child.get("content-desc", "")
                    if t:
                        parts.append(t)
                full_text = " ".join(parts)

            if target_lower in full_text.lower():
                # Skip kalo udah selected
                if "selected" in rid:
                    log.info("EXECUTE: varian '%s' udah dipilih", full_text)
                    return True

                # Tap yang unselected
                bounds = node.get("bounds", "")
                cx, cy = _center(bounds)
                if cx > 0:
                    log.info("Tap varian '%s' [%s] (%d, %d)", full_text, rid, cx, cy)
                    tapped = await self._adb.tap(cx, cy)
                    if tapped:
                        await asyncio.sleep(0.2)
                        return True
                    log.error("EXECUTE: gagal tap option '%s'", full_text)
                    return False

        log.info("EXECUTE: varian '%s' gak ditemukan di option buttons", target)
        return False


def _center(bounds_str: str) -> tuple[int, int]:
    try:
        parts = bounds_str.replace("][", ",").strip("[]").split(",")
        x1, y1, x2, y2 = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        return ((x1 + x2) // 2, (y1 + y2) // 2)
    except Exception:
        return (0, 0)
