"""
BlackoutScheduler — kelola window blackout (tidak ada aktivitas bot).
Selama blackout: screen off, semua loop dihentikan.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, time

from bot.adb.client import ADBClient
from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import BotMode
from bot.utils.logger import get_logger

log = get_logger(__name__)


def _parse_time(t_str: str) -> time:
    """Parse 'HH:MM' ke time object."""
    h, m = t_str.split(":")
    return time(int(h), int(m))


def _in_window(start: time, end: time, now: time) -> bool:
    """True jika now berada di dalam window [start, end)."""
    if start <= end:
        return start <= now < end
    # Overnight: misal 22:00 - 06:00
    return now >= start or now < end


class BlackoutScheduler:
    def __init__(
        self,
        adb: ADBClient,
        bus: EventBus,
        runtime: BotRuntimeState,
        get_blackout_fn,        # callable → str | None, misal "02:00-07:00"
    ) -> None:
        self._adb = adb
        self._bus = bus
        self._runtime = runtime
        self._get_blackout = get_blackout_fn
        self._running = False
        self._in_blackout = False
        self._pre_blackout_mode: BotMode = BotMode.RUNNING

    async def start(self) -> None:
        self._running = True
        log.info("BlackoutScheduler: mulai")
        while self._running:
            await asyncio.sleep(30)     # cek setiap 30 detik
            await self._check()

    def stop(self) -> None:
        self._running = False

    async def _check(self) -> None:
        window_str = self._get_blackout()
        if not window_str or window_str.strip().lower() == "off":
            if self._in_blackout:
                await self._exit_blackout()
            return

        try:
            start_str, end_str = window_str.split("-")
            start = _parse_time(start_str.strip())
            end = _parse_time(end_str.strip())
        except Exception:
            return

        now = datetime.now().time()
        should_blackout = _in_window(start, end, now)

        if should_blackout and not self._in_blackout:
            await self._enter_blackout(window_str)
        elif not should_blackout and self._in_blackout:
            await self._exit_blackout()

    async def _enter_blackout(self, window: str) -> None:
        log.info("Blackout mulai: %s", window)
        self._in_blackout = True
        self._runtime.blackout_active = True
        self._pre_blackout_mode = self._runtime.mode
        self._runtime.mode = BotMode.BLACKOUT
        await self._adb.screen_off()
        await self._bus.emit(ev.BlackoutStartEvent(window=window))

    async def _exit_blackout(self) -> None:
        log.info("Blackout selesai — reconnect ADB dan resume")
        self._in_blackout = False
        self._runtime.blackout_active = False
        self._runtime.mode = self._pre_blackout_mode
        await self._adb.reconnect()
        await self._adb.screen_on()
        await self._bus.emit(ev.BlackoutEndEvent())
