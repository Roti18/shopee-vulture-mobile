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

Progressive backoff OOS:
  - Produk habis (submit "Habis? Temukan Produk Lainnya"): increment counter,
    sleep progressive (3s → 6s → 12s → ...), max 120s.
  - Counter reset saat "Beli Sekarang" terdeteksi.
  - Jika habis >= max_consecutive_oos (15×): redirect ke OPEN_PRODUCT
    untuk reload URL (refresh page) — putus cycle.

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

# Progressive backoff untuk OOS loop prevention
OOS_BACKOFF_BASE = 3        # detik, base backoff
OOS_BACKOFF_MAX = 120       # detik, cap maksimum backoff


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
            return WorkflowState.RECOVERY

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
            await vacts.close_variant_popup(self._adb, self._cache)

            # Apply OOS backoff di MONITOR mode juga
            if self._runtime:
                self._runtime.consecutive_oos_count += 1
                oos_count = self._runtime.consecutive_oos_count
                if oos_count >= self._runtime.max_consecutive_oos:
                    self._runtime.consecutive_oos_count = 0
                    return WorkflowState.OPEN_PRODUCT
                backoff = min(
                    OOS_BACKOFF_BASE * (2 ** (oos_count - 1)),
                    OOS_BACKOFF_MAX,
                )
                await asyncio.sleep(backoff)
            return WorkflowState.BUY_VOUCHER

        # Reset OOS counter — stok terdeteksi di MONITOR
        if self._runtime and self._runtime.consecutive_oos_count > 0:
            self._runtime.consecutive_oos_count = 0

        await self._bus.emit(ev.VariantStockDetectedEvent(
            product_name=self._product.name,
            variant=self._product.variant or variant_info.variant_text,
            stock_count=variant_info.stock_count,
            is_checkout=False,
        ))
        await vacts.close_variant_popup(self._adb, self._cache)
        return WorkflowState.BUY_VOUCHER

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

            # ── Progressive backoff OOS ──────────────────────────────
            self._runtime.consecutive_oos_count += 1
            oos_count = self._runtime.consecutive_oos_count
            log.info(
                "OOS #%d/%d — apply progressive backoff",
                oos_count, self._runtime.max_consecutive_oos,
            )

            await vacts.close_variant_popup(self._adb, self._cache)

            if oos_count >= self._runtime.max_consecutive_oos:
                log.warning(
                    "OOS %d× berturut-turut — reload URL (refresh page)",
                    oos_count,
                )
                self._runtime.consecutive_oos_count = 0
                return WorkflowState.OPEN_PRODUCT

            # Exponential backoff: 3s → 6s → 12s → 24s → 48s → 96s → cap 120s
            backoff = min(
                OOS_BACKOFF_BASE * (2 ** (oos_count - 1)),
                OOS_BACKOFF_MAX,
            )
            log.info("OOS backoff: tidur %ds sebelum coba lagi", backoff)
            await asyncio.sleep(backoff)
            return WorkflowState.BUY_VOUCHER

        # ── Reset OOS counter — stok tersedia ────────────────────────
        if self._runtime.consecutive_oos_count > 0:
            log.info(
                "Submit 'Beli Sekarang' terdeteksi — reset OOS counter "
                "(sebelumnya %d×)",
                self._runtime.consecutive_oos_count,
            )
            self._runtime.consecutive_oos_count = 0

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
