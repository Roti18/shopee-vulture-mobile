"""
Health check — tulis timestamp ke /tmp/bot_health setiap N detik.
Docker HEALTHCHECK membaca file ini untuk memverifikasi bot masih hidup.
"""
from __future__ import annotations

import asyncio
import time
from pathlib import Path

from bot.models.bot_state import BotRuntimeState
from bot.models.enums import BotMode
from bot.utils.logger import get_logger

log = get_logger(__name__)

HEALTH_FILE = Path("/tmp/bot_health")
WRITE_INTERVAL_SECONDS = 300   # tulis setiap 5 menit


class HealthChecker:
    def __init__(self, runtime: BotRuntimeState) -> None:
        self._runtime = runtime
        self._running = False

    async def start(self) -> None:
        self._running = True
        log.info("HealthChecker: mulai (interval %ds)", WRITE_INTERVAL_SECONDS)
        while self._running:
            self._write()
            await asyncio.sleep(WRITE_INTERVAL_SECONDS)

    def stop(self) -> None:
        self._running = False

    def _write(self) -> None:
        try:
            HEALTH_FILE.parent.mkdir(parents=True, exist_ok=True)
            HEALTH_FILE.write_text(
                f"{time.time():.0f} | {self._runtime.mode.value} | "
                f"{self._runtime.workflow_state.value}"
            )
        except Exception as exc:
            log.warning("HealthChecker: gagal menulis %s — %s", HEALTH_FILE, exc)
