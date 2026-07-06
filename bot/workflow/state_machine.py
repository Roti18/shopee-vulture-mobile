"""
State Machine Engine — dispatch ke handler per state.

Tidak ada nested if statement.
Setiap state adalah class terpisah dengan method execute() → WorkflowState.
"""
from __future__ import annotations

import asyncio
import time
from typing import Protocol, TYPE_CHECKING

if TYPE_CHECKING:
    from bot.scheduler.loop_scheduler import LoopScheduler

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import BotMode, WorkflowState
from bot.models.product import ProductConfig
from bot.utils.logger import get_logger

log = get_logger(__name__)


class StateHandler(Protocol):
    async def execute(self) -> WorkflowState: ...


class StateMachine:
    """
    Engine state machine.
    Memanggil handler yang sesuai untuk setiap state,
    menerima WorkflowState berikutnya sebagai return value.
    """

    def __init__(
        self,
        adb: ADBClient,
        cache: XMLCache,
        bus: EventBus,
        runtime: BotRuntimeState,
        product: ProductConfig,
        loop_scheduler: "LoopScheduler" = None,
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._bus = bus
        self._runtime = runtime
        self._product = product
        self._loop_sched = loop_scheduler
        self._handlers: dict[WorkflowState, StateHandler] = {}

    def register(self, state: WorkflowState, handler: StateHandler) -> None:
        self._handlers[state] = handler

    async def run(self) -> None:
        """
        Loop utama state machine.
        Berhenti jika mode = STOPPED.
        """
        log.info("StateMachine: mulai dari state %s", self._runtime.workflow_state.value)

        while True:
            # Handle stopped, idle, paused, blackout
            while self._runtime.mode in (BotMode.STOPPED, BotMode.IDLE, BotMode.PAUSED, BotMode.BLACKOUT):
                await asyncio.sleep(1)

            state = self._runtime.workflow_state
            handler = self._handlers.get(state)

            if handler is None:
                log.error("Tidak ada handler untuk state %s", state.value)
                self._runtime.workflow_state = WorkflowState.RECOVERY
                continue

            t0 = time.monotonic()
            try:
                prev_state = state
                next_state = await handler.execute()
                duration_ms = (time.monotonic() - t0) * 1000

                self._runtime.stats.record_loop(duration_ms)
                self._runtime.metrics.avg_workflow_loop_duration_ms = (
                    self._runtime.stats.avg_loop_duration_ms
                )

                if next_state != prev_state:
                    await self._bus.emit(
                        ev.StateChangedEvent(previous=prev_state, current=next_state)
                    )

                # Jika transisi kembali ke awal siklus monitoring (OPEN_PRODUCT)
                # atau RECOVERY → BUY_VOUCHER / OPEN_PRODUCT, terapkan interval
                is_monitoring_cycle = (
                    prev_state == WorkflowState.CREATE_ORDER
                    and next_state == WorkflowState.OPEN_PRODUCT
                )
                is_restock_cycle = (
                    prev_state == WorkflowState.CHECK_VARIANT
                    and next_state == WorkflowState.BUY_VOUCHER
                )
                is_recovery_cycle = (
                    prev_state == WorkflowState.RECOVERY
                    and next_state in (WorkflowState.OPEN_PRODUCT, WorkflowState.BUY_VOUCHER,
                                       WorkflowState.CHECK_VARIANT, WorkflowState.CHECKOUT,
                                       WorkflowState.CREATE_ORDER)
                )

                if is_monitoring_cycle or is_restock_cycle or is_recovery_cycle:
                    if self._loop_sched:
                        await self._loop_sched.wait()

                # Minimum delay antar state transisi untuk mencegah tight loop
                min_gap = 0.5 - ((time.monotonic() - t0) * 1000) / 1000
                if min_gap > 0:
                    await asyncio.sleep(min_gap)

                self._runtime.workflow_state = next_state

            except Exception as exc:
                log.exception("StateMachine: exception di state %s: %s", state.value, exc)
                self._runtime.stats.record_failure()
                await self._bus.emit(
                    ev.OrderFailedEvent(reason=str(exc), state=state)
                )
                self._runtime.workflow_state = WorkflowState.RECOVERY
