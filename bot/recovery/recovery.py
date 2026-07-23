"""
Recovery — 1 fungsi: redirect URL. Gausa level-levelan.
Kalo ada masalah, buka URL lagi. Simple.
"""
from __future__ import annotations

import asyncio

from bot.adb.client import ADBClient
from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import WorkflowState
from bot.models.product import ProductConfig
from bot.utils.logger import get_logger

log = get_logger(__name__)


class Recovery:
    def __init__(
        self,
        adb: ADBClient,
        bus: EventBus,
        runtime: BotRuntimeState,
        product: ProductConfig,
    ) -> None:
        self._adb = adb
        self._bus = bus
        self._runtime = runtime
        self._product = product

    async def recover(self) -> WorkflowState:
        log.warning("Recovery: redirect URL")
        await self._bus.emit(
            ev.RecoveryStartedEvent(
                level=None,
                reason=f"State: {self._runtime.workflow_state.value}",
            )
        )

        if not self._product.url:
            log.error("Recovery: URL produk kosong")
            return WorkflowState.IDLE

        ok = await self._adb.open_url(self._product.url)
        if ok:
            await asyncio.sleep(3)
            await self._bus.emit(ev.RecoverySuccessEvent(level=None))
            return WorkflowState.OPEN_PRODUCT

        log.error("Recovery: open URL gagal — tunggu 10s, coba lagi")
        await asyncio.sleep(10)
        ok = await self._adb.open_url(self._product.url)
        if ok:
            await asyncio.sleep(3)
            await self._bus.emit(ev.RecoverySuccessEvent(level=None))
            return WorkflowState.OPEN_PRODUCT

        log.critical("Recovery: open URL gagal 2× — bot dihentikan")
        await self._bus.emit(
            ev.PanicEvent(reason="Recovery: open URL gagal 2×, intervensi manual diperlukan")
        )
        return WorkflowState.IDLE
