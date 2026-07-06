"""
DailyReport — kirim laporan harian setiap tengah malam.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from typing import Callable

from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.utils.logger import get_logger

log = get_logger(__name__)


class DailyReport:
    def __init__(
        self,
        bus: EventBus,
        runtime: BotRuntimeState,
        get_stats_fn: Callable | None = None,
    ) -> None:
        self._bus = bus
        self._runtime = runtime
        self._get_stats = get_stats_fn
        self._running = False

    async def start(self) -> None:
        self._running = True
        log.info("DailyReport: mulai")
        while self._running:
            await self._wait_until_midnight()
            if not self._running:
                break
            await self._send()

    def stop(self) -> None:
        self._running = False

    async def _wait_until_midnight(self) -> None:
        now = datetime.now()
        midnight = (now + timedelta(days=1)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        wait_seconds = (midnight - now).total_seconds()
        log.debug("DailyReport: menunggu %.0f detik sampai tengah malam", wait_seconds)
        await asyncio.sleep(wait_seconds)

    async def _send(self) -> None:
        date = datetime.now().strftime("%Y-%m-%d")
        if self._get_stats:
            try:
                daily = await self._get_stats(date)
                await self._bus.emit(
                    ev.DailyReportEvent(
                        loops=daily.loop_count,
                        success=daily.success_count,
                        failure=daily.failure_count,
                        avg_loop_ms=daily.avg_loop_ms,
                        date=date,
                    )
                )
                return
            except Exception as exc:
                log.warning("DailyReport: gagal baca dari DB (%s), pakai in-memory fallback", exc)

        # Fallback: in-memory stats
        stats = self._runtime.stats
        await self._bus.emit(
            ev.DailyReportEvent(
                loops=stats.loop_count,
                success=stats.success_count,
                failure=stats.failure_count,
                avg_loop_ms=stats.avg_loop_duration_ms,
                date=date,
            )
        )
