"""
State: CHECK_VARIANT — satu-satunya tempat cek stok yang valid.

Flow per mode:
  MONITOR: scan "Stok: N", threshold, kalo ada -> alert Telegram -> close -> loop
  EXECUTE: cari text varian target di semua node, tap, cek submit, checkout

EXECUTE gak scan "Stok: N" sama sekali. Pake TTL cache biar gak force dump ulang.
"""
from __future__ import annotations

import asyncio
import re

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
from bot.ui import variant_selectors as sel
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
        # ── 1. Dump dari cache ──────────────────────────────────────────
        # Pake TTL — kalo abis wait_for_variant_popup, cache masih fresh
        await self._cache.get(self._adb, force=False)

        parser = VariantParser(self._cache)

        if not parser.is_variant_popup_open():
            log.warning("CHECK_VARIANT: popup tak terdeteksi, recovery")
            return WorkflowState.RECOVERY

        # Submit button reference
        submit_el = parser.get_submit_button()
        if submit_el is None:
            submit_x, submit_y, resolved_via = 540, 2236, "default_fallback"
        else:
            submit_x, submit_y, resolved_via = submit_el.tap_x, submit_el.tap_y, submit_el.resolved_via

        # ── Mode ──────────────────────────────────────────────────────
        is_monitor = self._runtime and self._runtime.mode == BotMode.MONITOR

        if is_monitor:
            return await self._handle_monitor(parser)

        return await self._handle_execute(parser, submit_x, submit_y, resolved_via)

    # ═══════════════════════════════════════════════════════════════════ #
    # MONITOR mode (lengkap: scan stok + threshold + alert)
    # ═══════════════════════════════════════════════════════════════════ #

    async def _handle_monitor(self, parser: VariantParser) -> WorkflowState:
        all_stocks = parser.get_all_stock_counts()
        if not all_stocks:
            log.info("MONITOR: tanpa varian, skip ke submit")
            return WorkflowState.BUY_VOUCHER

        variant_info = parser.find_variant_with_stock(
            target_variant=self._product.variant,
            minimum_stock=self._product.minimum_stock,
            stock_mode=self._product.stock_mode,
        )

        if variant_info is None:
            log.info("Stok threshold: mode=%s, min=%d, ditemukan=%s",
                     self._product.stock_mode, self._product.minimum_stock, all_stocks)
            await self._bus.emit(ev.StockEmptyEvent(
                variant=self._product.variant,
                stock_count=max(all_stocks),
                threshold=self._product.minimum_stock,
            ))
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        log.info("MONITOR: stok %d buat '%s'", variant_info.stock_count, self._product.variant)
        await self._bus.emit(ev.VariantStockDetectedEvent(
            product_name=self._product.name,
            variant=self._product.variant or variant_info.variant_text,
            stock_count=variant_info.stock_count,
        ))
        await vacts.close_variant_popup(self._adb, self._cache)
        return WorkflowState.BUY_VOUCHER

    # ═══════════════════════════════════════════════════════════════════ #
    # EXECUTE mode: cepat — cari text varian, tap, submit
    # ═══════════════════════════════════════════════════════════════════ #

    async def _handle_execute(self, parser, sx, sy, sv) -> WorkflowState:
        nodes = self._cache.all_nodes()

        # ── Cari & tap varian ─────────────────────────────────────────
        tapped = await self._tap_target_variant(nodes)

        if tapped:
            await asyncio.sleep(0.3)
            # Force dump abis tap — UI pasti berubah
            await self._cache.get(self._adb, force=True)
            parser = VariantParser(self._cache)
            submit_el = parser.get_submit_button()
            if submit_el is not None:
                sx, sy, sv = submit_el.tap_x, submit_el.tap_y, submit_el.resolved_via

        # ── Cek submit dulu sebelum buang waktu set qty ───────────────
        submit_text = parser.get_submit_button_text()

        if "Beli Sekarang" not in submit_text:
            log.info("EXECUTE: submit = '%s' -> close loop", submit_text)
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── Set purchase quantity ─────────────────────────────────────
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
        log.info("Tap submit [%s] (%d, %d)", sv, sx, sy)
        ok = await self._adb.tap(sx, sy)
        if not ok:
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── Tunggu checkout page ──────────────────────────────────────
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
    # Varian tap helper — cari text match, tap bounds
    # ═══════════════════════════════════════════════════════════════════ #

    async def _tap_target_variant(self, nodes) -> bool:
        target = self._product.variant

        # Cari semua node yg text-nya mengandung target_variant
        # Ini bakal nemu text "50ml", "15ml", dll
        for node in nodes:
            text = node.get("text", "") or node.get("content-desc", "")
            if target and target.lower() in text.lower():
                bounds = node.get("bounds", "")
                if not bounds:
                    continue
                cx, cy = _center(bounds)
                if cx == 0 and cy == 0:
                    continue
                log.info("Tap varian text '%s' di (%d, %d)", text, cx, cy)
                tapped = await self._adb.tap(cx, cy)
                if tapped:
                    return True

        # Fallback: cari ImageView manapun di container varian (clickable or not)
        root = self._cache.root()
        if root is not None:
            scope = list(root.iter("node"))
            container = _by_rid(scope, sel.VARIANT_CONTAINER.resource_id)
            cscope = list(container.iter("node")) if container is not None else scope

            # Cari ImageView (biasanya varian image)
            img = _first_imageview(cscope)
            if img:
                bounds = img.get("bounds", "")
                cx, cy = _center(bounds)
                log.info("Tap varian ImageView fallback (%d, %d)", cx, cy)
                return await self._adb.tap(cx, cy)

            # Last resort: cari node manapun yg clickable di container
            for n in cscope:
                if n.get("clickable", "false") == "true":
                    bounds = n.get("bounds", "")
                    cx, cy = _center(bounds)
                    if cx > 0:
                        log.info("Tap clickable fallback (%d, %d)", cx, cy)
                        return await self._adb.tap(cx, cy)

        log.info("EXECUTE: varian gak ditemukan, skip submit")
        return False


# ── Module-level helpers ──────────────────────────────────────────────

def _center(bounds_str: str) -> tuple[int, int]:
    try:
        parts = bounds_str.replace("][", ",").strip("[]").split(",")
        x1, y1, x2, y2 = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        return ((x1 + x2) // 2, (y1 + y2) // 2)
    except Exception:
        return (0, 0)


def _by_rid(nodes, rid: str):
    for n in nodes:
        nrid = n.get("resource-id", "")
        if nrid == rid or nrid.endswith("/" + rid) or nrid.endswith(":" + rid):
            return n
    return None


def _first_imageview(nodes):
    for n in nodes:
        if n.get("class", "") == "android.widget.ImageView":
            return n
    return None
