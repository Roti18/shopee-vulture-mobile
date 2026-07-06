"""
main.py — Entry point bot Shopee ADB Automation.

Menginisialisasi semua komponen dan menjalankan secara concurrent:
  - State Machine (workflow utama)
  - Telegram Bot (polling)
  - Watchdog, Heartbeat, Blackout Scheduler, Daily Report
  - Health Checker (Docker healthcheck support)

Perubahan dari v1:
  - CHECK_STOCK state dihapus dari state machine
  - SQLite sebagai persistent storage
  - HealthChecker untuk Docker healthcheck
  - Graceful shutdown: simpan state ke DB sebelum exit
  - Config di-load async (karena SQLite async)
"""
from __future__ import annotations

import asyncio
import signal

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.config import load_config
from bot.events.bus import EventBus
from bot.events.handlers import register_log_handlers, register_stats_handlers
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import BotMode, RecoveryLevel, WorkflowState
from bot.recovery.recovery import TieredRecovery
from bot.scheduler.blackout_scheduler import BlackoutScheduler
from bot.scheduler.daily_report import DailyReport
from bot.scheduler.heartbeat import Heartbeat
from bot.scheduler.loop_scheduler import LoopScheduler
from bot.scheduler.watchdog import Watchdog
from bot.storage.repositories import RuntimeSnapshot
from bot.telegram.bot import build_application
from bot.telegram.commands import CommandHandlers
from bot.telegram.notifier import TelegramNotifier
from bot.utils.health import HealthChecker
from bot.utils.logger import get_logger
from bot.workflow.state_machine import StateMachine
from bot.workflow.open_product import OpenProductHandler
from bot.workflow.buy_voucher import BuyVoucherHandler
from bot.workflow.check_variant import CheckVariantHandler
from bot.workflow.buy_now import BuyNowHandler
from bot.workflow.checkout import CheckoutHandler
from bot.workflow.verify_payment import VerifyPaymentHandler
from bot.workflow.create_order import CreateOrderHandler
from bot.workflow.cooldown import CooldownHandler

log = get_logger(__name__)


async def main() -> None:
    log.info("=" * 60)
    log.info("Shopee ADB Automation Bot — starting")
    log.info("=" * 60)

    # ── Config & DB ──────────────────────────────────────────────────────
    cfg = await load_config()   # async: connect DB, load settings

    # ── Load persisted runtime state ─────────────────────────────────────
    snapshot = await cfg.db.conn.execute("SELECT * FROM bot_runtime WHERE id = 1")
    snap_row = await snapshot.fetchone()
    saved = RuntimeSnapshot()
    if snap_row:
        saved = RuntimeSnapshot(
            mode=snap_row["mode"],
            workflow_state=snap_row["workflow_state"],
            purchase_count_session=snap_row["purchase_count_session"],
            cooldown_until=snap_row["cooldown_until"],
            recovery_level=snap_row["recovery_level"],
        )

    # ── Core objects ─────────────────────────────────────────────────────
    adb = ADBClient(
        device_serial=cfg.adb_device_serial,
        wifi_host=cfg.adb_wifi_host,
    )
    cache = XMLCache()
    bus = EventBus()

    # Bot selalu mulai dalam mode IDLE — user harus kirim /start via Telegram
    runtime = BotRuntimeState(
        mode=BotMode.IDLE,
        workflow_state=WorkflowState.IDLE,
    )
    # Restore dari persistent DB
    runtime.stats.purchase_count_session = saved.purchase_count_session
    runtime.recovery_level = RecoveryLevel.L1_SOFT_RETRY
    if saved.cooldown_until:
        try:
            from datetime import datetime as _dt
            runtime.cooldown_until = _dt.fromisoformat(saved.cooldown_until)
        except Exception:
            runtime.cooldown_until = None

    # ── Event handlers ────────────────────────────────────────────────────
    register_log_handlers(bus)
    register_stats_handlers(bus, runtime)

    notifier = TelegramNotifier(
        token=cfg.telegram_token,
        chat_id=cfg.telegram_chat_id,
    )
    notifier.register(bus)

    # ── Periodic DB sync handler ──────────────────────────────────────────
    async def sync_runtime_to_db() -> None:
        """Simpan runtime state ke DB setiap loop."""
        cooldown_str = runtime.cooldown_until.isoformat() if runtime.cooldown_until else None
        await cfg.db.conn.execute(
            """UPDATE bot_runtime SET
                mode = ?, workflow_state = ?,
                purchase_count_session = ?,
                cooldown_until = ?,
                recovery_level = ?,
                updated_at = datetime('now','localtime')
               WHERE id = 1""",
            (
                runtime.mode.value,
                runtime.workflow_state.value,
                runtime.stats.purchase_count_session,
                cooldown_str,
                runtime.recovery_level.value,
            ),
        )
        await cfg.db.conn.commit()

    # Register stats → DB sync
    from bot.events import events as ev
    async def on_state_changed_db(event: ev.StateChangedEvent) -> None:
        await sync_runtime_to_db()

    async def on_order_success_db(event: ev.OrderSuccessEvent) -> None:
        await cfg.settings.set("bot.last_success", event.timestamp.isoformat())
        await cfg.db.conn.execute(
            """INSERT INTO statistics (date, loop_count, success_count, failure_count, total_loop_ms)
               VALUES (date('now','localtime'), 0, 1, 0, 0)
               ON CONFLICT(date) DO UPDATE SET
                 success_count = success_count + 1,
                 updated_at = datetime('now','localtime')"""
        )
        await cfg.db.conn.commit()

    bus.subscribe(ev.StateChangedEvent, on_state_changed_db)
    bus.subscribe(ev.OrderSuccessEvent, on_order_success_db)

    # ── Recovery ──────────────────────────────────────────────────────────
    recovery = TieredRecovery(
        adb=adb, cache=cache, bus=bus, runtime=runtime, product=cfg.product
    )

    # ── State Machine ─────────────────────────────────────────────────────
    loop_sched = LoopScheduler(runtime, cfg.get_interval)
    sm = StateMachine(
        adb=adb,
        cache=cache,
        bus=bus,
        runtime=runtime,
        product=cfg.product,
        loop_scheduler=loop_sched,
    )

    # Daftarkan semua state handler
    # CHECK_STOCK dihapus — stok hanya valid dari popup varian
    sm.register(WorkflowState.OPEN_PRODUCT,
                OpenProductHandler(adb, cache, cfg.product, cfg.set_config))
    sm.register(WorkflowState.BUY_VOUCHER,
                BuyVoucherHandler(adb, cache))
    sm.register(WorkflowState.CHECK_VARIANT,
                CheckVariantHandler(adb, cache, bus, cfg.product, runtime))
    sm.register(WorkflowState.BUY_NOW,
                BuyNowHandler(adb, cache))
    sm.register(WorkflowState.CHECKOUT,
                CheckoutHandler(adb, cache, cfg.product))
    sm.register(WorkflowState.VERIFY_PAYMENT,
                VerifyPaymentHandler(adb, cache))
    sm.register(WorkflowState.CREATE_ORDER,
                CreateOrderHandler(adb, cache, bus, runtime, cfg.product, cfg.get_sleep_after_success))
    sm.register(WorkflowState.COOLDOWN,
                CooldownHandler(bus, runtime, cfg.get_cooldown_hours))
    sm.register(WorkflowState.RECOVERY,
                _RecoveryAdapter(recovery))

    # ── Schedulers ────────────────────────────────────────────────────────
    watchdog = Watchdog(adb, cache, bus, runtime)
    heartbeat = Heartbeat(adb, bus, runtime)
    blackout = BlackoutScheduler(adb, bus, runtime, cfg.get_blackout)
    daily = DailyReport(bus, runtime, get_stats_fn=cfg.get_daily_stats)
    health = HealthChecker(runtime)

    # ── Telegram ──────────────────────────────────────────────────────────
    cmd_handlers = CommandHandlers(
        bus=bus,
        runtime=runtime,
        product=cfg.product,
        get_config_fn=cfg.get_config_dict,
        set_config_fn=cfg.set_config,
    )
    tg_app = build_application(cfg.telegram_token, cmd_handlers)

    # ── Graceful shutdown ─────────────────────────────────────────────────
    stop_event = asyncio.Event()

    def _handle_signal():
        log.info("Signal diterima — graceful shutdown...")
        stop_event.set()

    loop = asyncio.get_event_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal)
        except NotImplementedError:
            pass  # Windows tidak support add_signal_handler

    log.info("Semua komponen siap. Bot IDLE — kirim /start via Telegram.")

    async with tg_app:
        await tg_app.initialize()
        await tg_app.start()
        await tg_app.updater.start_polling(drop_pending_updates=True)

        try:
            await asyncio.gather(
                sm.run(),
                watchdog.start(),
                heartbeat.start(),
                blackout.start(),
                daily.start(),
                health.start(),
                stop_event.wait(),
            )
        finally:
            log.info("Shutdown: menyimpan state ke DB...")
            runtime.mode = BotMode.STOPPED
            await sync_runtime_to_db()

            # Stop schedulers
            watchdog.stop()
            heartbeat.stop()
            blackout.stop()
            daily.stop()
            health.stop()

            # Stop Telegram
            await tg_app.updater.stop()
            await tg_app.stop()
            await tg_app.shutdown()

            # Close DB
            await cfg.db.close()

            log.info("Shutdown selesai.")


class _RecoveryAdapter:
    """Adapter agar TieredRecovery bisa dipanggil sebagai StateHandler."""
    def __init__(self, recovery: TieredRecovery) -> None:
        self._recovery = recovery

    async def execute(self) -> WorkflowState:
        return await self._recovery.recover()


if __name__ == "__main__":
    asyncio.run(main())
