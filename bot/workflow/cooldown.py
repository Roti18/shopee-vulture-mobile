"""State: COOLDOWN — tunggu sesuai cooldown_hours, lalu resume."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import WorkflowState
from bot.utils.logger import get_logger

log = get_logger(__name__)


class CooldownHandler:
    def __init__(
        self,
        bus: EventBus,
        runtime: BotRuntimeState,
        get_cooldown_hours_fn,   # callable → float (dari config runtime)
    ) -> None:
        self._bus = bus
        self._runtime = runtime
        self._get_hours = get_cooldown_hours_fn

    async def execute(self) -> WorkflowState:
        hours = self._get_hours()
        until = datetime.now() + timedelta(hours=hours)
        self._runtime.cooldown_until = until

        log.info("COOLDOWN: %.1f jam sampai %s", hours, until.strftime("%H:%M"))
        await self._bus.emit(
            ev.CooldownStartEvent(
                hours=hours,
                purchase_count=self._runtime.stats.purchase_count_session,
            )
        )

        # Tunggu dengan polling setiap 1 detik agar responsif terhadap stop/pause
        total_seconds = hours * 3600
        elapsed = 0.0
        while elapsed < total_seconds:
            from bot.models.enums import BotMode
            if self._runtime.mode == BotMode.STOPPED:
                log.info("COOLDOWN: bot dihentikan, exit cooldown")
                return WorkflowState.IDLE
            if self._runtime.mode == BotMode.PAUSED:
                await asyncio.sleep(1.0)
                continue
            await asyncio.sleep(1.0)
            elapsed += 1.0

        self._runtime.stats.reset_session()
        self._runtime.cooldown_until = None
        await self._bus.emit(ev.CooldownEndEvent())
        log.info("COOLDOWN: selesai, resume monitoring")

        return WorkflowState.OPEN_PRODUCT
