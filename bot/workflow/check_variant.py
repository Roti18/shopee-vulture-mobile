"""
State: CHECK_VARIANT — cek stok di popup varian.

EXECUTE mode:
  1. Cek submit button text
  2. Kalo "Beli Sekarang" → tap varian ImageView pertama → submit
  3. Kalo "Habis" / selain itu → langsung close popup
  4. Gak scan "Stok: N" — hemat 1 ADB dump + 1 full node scan

MONITOR mode:
  1. Scan "Stok: N" + threshold
  2. Kalo memenuhi → alert Telegram
  3. Kalo tidak → close loop
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

# ── Module-level helpers ──────────────────────────────────────────────

def _center(bounds_str: str) -> tuple[int, int]:
    try:
        parts = bounds_str.replace("][", ",").strip("[]").split(",")
        x1, y1, x2, y2 = int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        return ((x1 + x2) // 2, (y1 + y2) // 2)
    except Exception:
        return (0, 0)


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
    # MONITOR — full stock scan + threshold + alert Telegram
    # ═══════════════════════════════════════════════════════════════════ #

    async def _handle_monitor(self, parser: VariantParser) -> WorkflowState:
        all_stocks = parser.get_all_stock_counts()
        if not all_stocks:
            return WorkflowState.BUY_VOUCHER

        variant_info = parser.find_variant_with_stock(
            target_variant=self._product.variant,
            minimum_stock=self._product.minimum_stock,
            stock_mode=self._product.stock_mode,
        )
        if variant_info is None:
            await self._bus.emit(ev.StockEmptyEvent(
                variant=self._product.variant, stock_count=max(all_stocks),
                threshold=self._product.minimum_stock,
            ))
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        await self._bus.emit(ev.VariantStockDetectedEvent(
            product_name=self._product.name, variant=self._product.variant or variant_info.variant_text,
            stock_count=variant_info.stock_count,
        ))
        await vacts.close_variant_popup(self._adb, self._cache)
        return WorkflowState.BUY_VOUCHER

    # ═══════════════════════════════════════════════════════════════════ #
    # EXECUTE — cepet: cek submit button dulu, baru tap varian
    # ═══════════════════════════════════════════════════════════════════ #

    async def _handle_execute(self, parser: VariantParser) -> WorkflowState:
        # ── Cek submit button text ────────────────────────────────────
        # Kalo "Habis" atau bukan "Beli Sekarang", langsung close — hemat
        submit_text = parser.get_submit_button_text()

        if "Beli Sekarang" not in submit_text:
            log.info("EXECUTE: submit = '%s' -> close loop", submit_text)
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── Cari & tap varian ImageView di sectionTierVariation ──────
        # Dari XML dump: ImageView clickable di dalem container varian
        root = self._cache.root()
        if root is not None:
            nodes = list(root.iter("node"))
            container = _by_rid(nodes, "sectionTierVariation")
            scope = list(container.iter("node")) if container is not None else nodes

            # Cari ImageView clickable di container
            for node in scope:
                if node.get("class", "") == "android.widget.ImageView" and node.get("clickable", "false") == "true":
                    cx, cy = _center(node.get("bounds", ""))
                    if cx > 0:
                        log.info("Tap varian ImageView (%d, %d)", cx, cy)
                        tapped = await self._adb.tap(cx, cy)
                        if tapped:
                            await asyncio.sleep(0.5)
                            break
                        else:
                            log.error("EXECUTE: gagal tap ImageView")

        # ── Refresh abis tap ─────────────────────────────────────────
        await self._cache.get(self._adb, force=True)
        parser = VariantParser(self._cache)

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

        # ── Submit ───────────────────────────────────────────────────
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


def _by_rid(nodes, rid: str):
    for n in nodes:
        nrid = n.get("resource-id", "")
        if nrid == rid or nrid.endswith("/" + rid) or nrid.endswith(":" + rid):
            return n
    return None
