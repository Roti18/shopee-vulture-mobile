"""
LoopScheduler — mengatur interval antar loop monitoring.
Dapat diubah via Telegram command /interval tanpa restart.
"""
from __future__ import annotations

import asyncio

from bot.models.bot_state import BotRuntimeState
from bot.models.enums import BotMode
from bot.utils.logger import get_logger

log = get_logger(__name__)


class LoopScheduler:
    def __init__(self, runtime: BotRuntimeState, get_interval_fn) -> None:
        self._runtime = runtime
        self._get_interval = get_interval_fn   # callable → float (detik)

    async def wait(self) -> None:
        """Tunggu interval sebelum loop berikutnya. Respek terhadap mode PAUSED/BLACKOUT."""
        interval = self._get_interval()
        elapsed = 0.0
        tick = 0.5

        while elapsed < interval:
            if self._runtime.mode == BotMode.STOPPED:
                return
            # Selama pause/blackout, berhenti hitung interval
            if self._runtime.mode in (BotMode.PAUSED, BotMode.BLACKOUT):
                await asyncio.sleep(tick)
                continue
            await asyncio.sleep(tick)
            elapsed += tick
