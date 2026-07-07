"""
State: CHECK_VARIANT — satu-satunya tempat cek stok yang valid.

Flow per mode:
  MONITOR: scan "Stok: N", threshold, kalo ada -> alert Telegram -> close -> loop
  EXECUTE: langsung tap varian pertama yg clickable -> cek submit "Beli Sekarang" -> checkout

EXECUTE gak scan "Stok: N" sama sekali — irit 1 ADB dump + 1 full node scan.
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
        # ── 1. Dump fresh ───────────────────────────────────────────────
        await self._cache.get(self._adb, force=True)

        parser = VariantParser(self._cache)

        if not parser.is_variant_popup_open():
            log.warning("CHECK_VARIANT: popup tidak terdeteksi, recovery")
            return WorkflowState.RECOVERY

        # Resolve submit button buat nanti
        submit_el = parser.get_submit_button()
        if submit_el is None:
            submit_x, submit_y, resolved_via = 540, 2236, "default_fallback"
        else:
            submit_x, submit_y, resolved_via = submit_el.tap_x, submit_el.tap_y, submit_el.resolved_via

        is_monitor = self._runtime and self._runtime.mode == BotMode.MONITOR

        if is_monitor:
            # ── MONITOR: scan stok ────────────────────────────────────
            all_stocks = parser.get_all_stock_counts()

            if all_stocks:
                variant_info = parser.find_variant_with_stock(
                    target_variant=self._product.variant,
                    minimum_stock=self._product.minimum_stock,
                    stock_mode=self._product.stock_mode,
                )

                if variant_info is None:
                    log.info(
                        "Stok threshold: mode=%s, min=%d, ditemukan=%s",
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

                log.info("MONITOR: stok %d buat '%s'", variant_info.stock_count, self._product.variant)
                await self._bus.emit(
                    ev.VariantStockDetectedEvent(
                        product_name=self._product.name,
                        variant=self._product.variant or variant_info.variant_text,
                        stock_count=variant_info.stock_count,
                    )
                )
                await vacts.close_variant_popup(self._adb, self._cache)
                return WorkflowState.BUY_VOUCHER
        else:
            # ── EXECUTE: langsung cari & tap varian ───────────────────
            # Cari ImageView clickable di popup — itu varian-variantnya
            nodes = self._cache.all_nodes()
            root = self._cache.root()

            # Cari container varian
            container = self._find_container(root, nodes)
            scope = list(container.iter("node")) if container is not None else nodes

            # Urutin varian ImageView clickable dari atas ke bawah
            variants = [
                n for n in scope
                if n.get("class", "") == sel.VARIANT_ITEM_CLASS
                and n.get("clickable", "false") == "true"
            ]

            if not variants:
                log.info("EXECUTE: ga ada varian clickable, skip ke submit langsung")
            else:
                # Pilih varian: cari yg cocok target_variant dulu
                target = self._product.variant
                target_el = None

                if target:
                    # Cari variant yg bounds-nya deket text target
                    target_lower = target.lower()
                    for node in scope:
                        text = (node.get("text", "") or node.get("content-desc", "")).lower()
                        if target_lower in text:
                            tb = self._parse_bounds(node.get("bounds", ""))
                            if tb:
                                for v in variants:
                                    vb = self._parse_bounds(v.get("bounds", ""))
                                    if vb and self._bounds_near(tb, vb):
                                        target_el = v
                                        break
                            break

                if target_el is None and variants:
                    target_el = variants[0]  # fallback: varian pertama

                if target_el is not None:
                    cx, cy = self._center_of_bounds(target_el.get("bounds", ""))
                    log.info("Tap varian [%s] (%d, %d)", target_el.get("class", ""), cx, cy)
                    tapped = await self._adb.tap(cx, cy)
                    if not tapped:
                        log.error("EXECUTE: gagal tap varian")
                        await vacts.close_variant_popup(self._adb, self._cache)
                        return WorkflowState.BUY_VOUCHER
                    await asyncio.sleep(0.5)

                    # Force dump abis tap — UI berubah
                    await self._cache.get(self._adb, force=True)
                    parser = VariantParser(self._cache)
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
                log.error("EXECUTE: gagal set qty %d", self._product.purchase_quantity)
                await vacts.close_variant_popup(self._adb, self._cache)
                return WorkflowState.BUY_VOUCHER

        # ── 6. Validasi submit button ──────────────────────────────────
        submit_text = parser.get_submit_button_text()
        if "Beli Sekarang" not in submit_text:
            log.info("EXECUTE: submit = '%s' -> close popup loop", submit_text)
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── 7. Tap submit ──────────────────────────────────────────────
        log.info("Tap submit [%s] (%d, %d)", resolved_via, submit_x, submit_y)
        ok = await self._adb.tap(submit_x, submit_y)
        if not ok:
            log.error("EXECUTE: gagal tap submit")
            await vacts.close_variant_popup(self._adb, self._cache)
            return WorkflowState.BUY_VOUCHER

        # ── 8. Tunggu checkout page ────────────────────────────────────
        arrived = await cacts.wait_for_checkout_page(
            self._adb, self._cache, max_wait=15.0
        )
        if not arrived:
            log.warning("EXECUTE: checkout page timeout, press_back balik")
            await self._adb.press_back()
            await asyncio.sleep(0.5)
            await self._adb.press_back()
            return WorkflowState.BUY_VOUCHER

        return WorkflowState.CHECKOUT

    # ── Helpers ──────────────────────────────────────────────────────────

    def _find_container(self, root, nodes):
        if root is None:
            return None
        from bot.parser.base_parser import BaseParser
        return BaseParser._by_resource_id(
            list(root.iter("node")), sel.VARIANT_CONTAINER.resource_id
        )

    @staticmethod
    def _parse_bounds(bounds_str: str) -> tuple[int, int, int, int] | None:
        try:
            parts = bounds_str.replace("][", ",").strip("[]").split(",")
            return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        except Exception:
            return None

    @staticmethod
    def _center_of_bounds(bounds_str: str) -> tuple[int, int]:
        b = CheckVariantHandler._parse_bounds(bounds_str)
        if b is None:
            return (0, 0)
        return ((b[0] + b[2]) // 2, (b[1] + b[3]) // 2)

    @staticmethod
    def _bounds_near(a, b, tx=100, ty=250):
        ax = (a[0] + a[2]) // 2
        ay = (a[1] + a[3]) // 2
        bx = (b[0] + b[2]) // 2
        by = (b[1] + b[3]) // 2
        return abs(ax - bx) < tx and abs(ay - by) < ty
