"""
State: MONITOR_POPUP — scan ulang popup varian tanpa nutup/buka ulang.

Optimasi: Di MONITOR mode, CHECK_VARIANT dulu nutup popup trus balik ke
BUY_VOUCHER buat buka lagi. Ini buang waktu ~2-3 detik tiap cycle.
MONITOR_POPUP skip itu — popup udah kebuka, tinggal scan ulang stok.
"""
from __future__ import annotations

from datetime import datetime

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.actions import variant_actions as vacts
from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import WorkflowState
from bot.models.product import ProductConfig
from bot.parser.variant_parser import VariantParser
from bot.utils.logger import get_logger

log = get_logger(__name__)


class MonitorPopupHandler:
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
        # Refresh cache — popup udah kebuka dari siklus sebelumnya
        tree = await self._cache.get(self._adb, force=True)
        if tree is None:
            log.warning("MONITOR_POPUP: dump gagal, reopen popup")
            return WorkflowState.BUY_VOUCHER

        parser = VariantParser(self._cache)

        if not parser.is_variant_popup_open():
            log.warning("MONITOR_POPUP: popup udah ketutup, buka ulang")
            return WorkflowState.BUY_VOUCHER

        all_stocks = parser.get_all_stock_counts()
        if not all_stocks:
            log.warning("MONITOR_POPUP: gak ada info stok, reopen")
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
        else:
            await self._bus.emit(ev.VariantStockDetectedEvent(
                product_name=self._product.name,
                variant=self._product.variant or variant_info.variant_text,
                stock_count=variant_info.stock_count,
                is_checkout=False,
            ))

        # Reset frozen timer biar watchdog gak trigger recovery
        if self._runtime:
            self._runtime.metrics.last_state_change = datetime.now()

        return WorkflowState.MONITOR_POPUP
