"""
Watchdog — deteksi state machine frozen dan trigger recovery.
Juga mengumpulkan metrics: ADB latency, dump duration, loop duration.
"""
from __future__ import annotations

import asyncio
from datetime import datetime

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import BotMode, WorkflowState
from bot.utils.logger import get_logger

log = get_logger(__name__)

CHECK_INTERVAL_SECONDS = 60


class Watchdog:
    def __init__(
        self,
        adb: ADBClient,
        cache: XMLCache,
        bus: EventBus,
        runtime: BotRuntimeState,
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._bus = bus
        self._runtime = runtime
        self._running = False

    async def start(self) -> None:
        self._running = True
        log.info("Watchdog: mulai (check interval %ds)", CHECK_INTERVAL_SECONDS)
        while self._running:
            await asyncio.sleep(CHECK_INTERVAL_SECONDS)
            if self._runtime.mode != BotMode.RUNNING:
                continue
            await self._check()

    def stop(self) -> None:
        self._running = False

    async def _check(self) -> None:
        metrics = self._runtime.metrics
        elapsed = (datetime.now() - metrics.last_state_change).total_seconds()

        # Update ADB latency
        await self._adb.is_connected()
        metrics.adb_latency_ms = self._adb.last_latency_ms

        # Update XML dump duration
        metrics.xml_dump_duration_ms = self._cache.last_dump_duration_ms

        log.debug(
            "Watchdog: frozen=%.0fs ADB=%.0fms dump=%.0fms loop=%.0fms",
            elapsed,
            metrics.adb_latency_ms,
            metrics.xml_dump_duration_ms,
            metrics.avg_workflow_loop_duration_ms,
        )

        if elapsed > metrics.frozen_threshold_seconds:
            log.error(
                "Watchdog: state machine FROZEN %.0f detik di %s — trigger recovery",
                elapsed,
                self._runtime.workflow_state.value,
            )
            await self._bus.emit(
                ev.WatchdogAlertEvent(
                    frozen_seconds=elapsed,
                    last_state=self._runtime.workflow_state,
                )
            )
            # Force state ke RECOVERY jika tidak sedang di state RECOVERY
            # (hindari race dengan state machine yang sedang execute handler)
            if self._runtime.workflow_state != WorkflowState.RECOVERY:
                self._runtime.workflow_state = WorkflowState.RECOVERY
                self._runtime.metrics.last_state_change = datetime.now()
