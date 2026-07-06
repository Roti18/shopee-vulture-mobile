"""
Heartbeat — kirim status bot ke Telegram setiap 30 menit.
"""
from __future__ import annotations

import asyncio

from bot.adb.client import ADBClient
from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import BotMode
from bot.utils import system_info
from bot.utils.logger import get_logger

log = get_logger(__name__)

HEARTBEAT_INTERVAL_SECONDS = 1800   # 30 menit


class Heartbeat:
    def __init__(
        self,
        adb: ADBClient,
        bus: EventBus,
        runtime: BotRuntimeState,
    ) -> None:
        self._adb = adb
        self._bus = bus
        self._runtime = runtime
        self._running = False

    async def start(self) -> None:
        self._running = True
        log.info("Heartbeat: mulai (interval %ds)", HEARTBEAT_INTERVAL_SECONDS)
        while self._running:
            await asyncio.sleep(HEARTBEAT_INTERVAL_SECONDS)
            if self._runtime.mode == BotMode.STOPPED:
                break
            await self._send()

    def stop(self) -> None:
        self._running = False

    async def _send(self) -> None:
        cpu = system_info.get_cpu_percent()
        ram_used, ram_total = system_info.get_ram_mb()
        adb_ok = await self._adb.is_connected()

        await self._bus.emit(
            ev.HeartbeatEvent(
                runtime_seconds=self._runtime.stats.uptime_seconds,
                cpu_percent=cpu,
                ram_used_mb=ram_used,
                ram_total_mb=ram_total,
                adb_connected=adb_ok,
                current_state=self._runtime.workflow_state.value,
                loop_count=self._runtime.stats.loop_count,
                success_count=self._runtime.stats.success_count,
            )
        )
