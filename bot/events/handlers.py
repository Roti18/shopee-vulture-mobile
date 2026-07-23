"""
Event handlers — subscriber untuk EventBus.

Handler mendaftarkan diri ke bus dan merespons event secara terpisah
dari workflow, sehingga workflow tetap bersih.
"""
from __future__ import annotations

from bot.events.bus import EventBus
from bot.events import events as ev
from bot.utils.logger import get_logger

log = get_logger(__name__)


def register_log_handlers(bus: EventBus) -> None:
    """Daftarkan handler logging untuk semua event penting."""

    async def on_state_changed(event: ev.StateChangedEvent) -> None:
        log.info("State: %s → %s", event.previous.value, event.current.value)

    async def on_order_success(event: ev.OrderSuccessEvent) -> None:
        log.info(
            "ORDER SUCCESS: %s | %s | %s",
            event.product_name, event.variant, event.price,
        )

    async def on_order_failed(event: ev.OrderFailedEvent) -> None:
        log.warning("ORDER FAILED: %s (state: %s)", event.reason, event.state.value)

    async def on_recovery(event: ev.RecoveryStartedEvent) -> None:
        level = getattr(event.level, "value", "-")
        log.warning("RECOVERY L%s: %s", level, event.reason)

    async def on_panic(event: ev.PanicEvent) -> None:
        log.critical("PANIC: %s", event.reason)

    async def on_watchdog(event: ev.WatchdogAlertEvent) -> None:
        log.error("WATCHDOG: frozen %.0fs di state %s", event.frozen_seconds, event.last_state.value)

    bus.subscribe(ev.StateChangedEvent, on_state_changed)
    bus.subscribe(ev.OrderSuccessEvent, on_order_success)
    bus.subscribe(ev.OrderFailedEvent, on_order_failed)
    bus.subscribe(ev.RecoveryStartedEvent, on_recovery)
    bus.subscribe(ev.PanicEvent, on_panic)
    bus.subscribe(ev.WatchdogAlertEvent, on_watchdog)


def register_stats_handlers(bus: EventBus, runtime_state) -> None:
    """Update BotStats berdasarkan event."""

    async def on_success(event: ev.OrderSuccessEvent) -> None:
        runtime_state.stats.record_success()

    async def on_failed(event: ev.OrderFailedEvent) -> None:
        runtime_state.stats.record_failure()

    async def on_state_changed(event: ev.StateChangedEvent) -> None:
        runtime_state.update_state(event.current)

    bus.subscribe(ev.OrderSuccessEvent, on_success)
    bus.subscribe(ev.OrderFailedEvent, on_failed)
    bus.subscribe(ev.StateChangedEvent, on_state_changed)
