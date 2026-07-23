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
import time

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.config import load_config
from bot.events.bus import EventBus
from bot.events.handlers import register_log_handlers, register_stats_handlers
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import BotMode, WorkflowState
from bot.recovery.recovery import Recovery
from bot.scheduler.blackout_scheduler import BlackoutScheduler
from bot.scheduler.daily_report import DailyReport
from bot.scheduler.heartbeat import Heartbeat
from bot.scheduler.loop_scheduler import LoopScheduler
from bot.scheduler.watchdog import Watchdog
from bot.storage.repositories import RuntimeSnapshot
from telegram.error import TelegramError

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


# ── Telegram Polling Guard ──────────────────────────────────────────────
class _PollingGuard:
    """
    Guard task: monitor koneksi polling Telegram dan restart otomatis.

    Masalah: kalo Tailscale/VPN reconnect, httpx connection pool jadi stale.
    internal retry python-telegram-bot pake backoff sampe 30s, dan `updater.running`
    tetap True walaupun get_updates terus gagal — jadi guard versi lama gak pernah trigger.

    Solusi:
      - error_callback ngumpulin error counter. Kalo dah > N error berturut-turut,
        guard langsung restart polling total (fresh connection pool).
      - restart polling pake bootstrap_retries=-1 (retry indefinite).
      - Setiap restart, shutdown total + ganti koneksi biar stale connections di-reset.
    """

    def __init__(self, tg_app) -> None:
        self._app = tg_app
        self._error_count = 0
        self._last_error_at = 0.0
        self._restarting = False

    # Dipanggil dari error_callback start_polling — inline, sync, jangan block.
    def on_polling_error(self, _exc: TelegramError) -> None:
        self._error_count += 1
        self._last_error_at = time.monotonic()

    def _reset_error_count(self) -> None:
        self._error_count = 0

    async def _restart_polling(self) -> bool:
        """Shutdown → re-init → start_polling. Return True kalo berhasil."""
        RECONNECT_DELAYS = [2, 5, 10, 20, 30, 30, 30]
        self._restarting = True

        # 1) Shutdown total — bersihin stale connections
        try:
            await self._app.updater.stop()
        except Exception:
            pass
        try:
            await self._app.stop()
        except Exception:
            pass
        try:
            await self._app.shutdown()
        except Exception:
            pass

        await asyncio.sleep(1)

        # 2) Re-init
        for attempt in range(7):
            try:
                await asyncio.wait_for(self._app.initialize(), timeout=10)
                await asyncio.wait_for(self._app.start(), timeout=10)
                await asyncio.wait_for(
                    self._app.updater.start_polling(
                        drop_pending_updates=True,
                        bootstrap_retries=-1,
                        error_callback=self.on_polling_error,
                    ),
                    timeout=20,
                )
                log.info("Telegram polling restored (attempt %d/7)", attempt + 1)
                self._restarting = False
                return True
            except Exception as exc:
                delay = RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)]
                log.warning(
                    "TG reconnect %d/7 gagal: %s — retry %ds",
                    attempt + 1, exc, delay,
                )
                await asyncio.sleep(delay)

        log.error("Telegram polling gagal total setelah 7x reconnect — bot jalan tanpa Telegram")
        self._restarting = False
        return False

    async def run(self) -> None:
        log.info("PollingGuard: mulai monitoring error polling")

        while True:
            try:
                await asyncio.sleep(10)
                now = time.monotonic()

                # Kasus 1: polling beneran berhenti (updater.running = False) → restart
                if not self._app.updater.running and not self._restarting:
                    log.warning("PollingGuard: updater berhenti — restart...")
                    ok = await self._restart_polling()
                    self._reset_error_count()
                    self._restarting = False
                    if not ok:
                        await asyncio.sleep(60)
                    continue

                # Kasus 2: error bertumpuk (Tailscale reconnect dll) → restart walau running masih True
                # Threshold: >=5 error dalam 60 detik terakhir = pool stale
                if (
                    self._error_count >= 5
                    and not self._restarting
                    and now - self._last_error_at < 60
                ):
                    log.warning(
                        "PollingGuard: %d error berturut-turut (pool mungkin stale) — restart...",
                        self._error_count,
                    )
                    ok = await self._restart_polling()
                    self._reset_error_count()
                    self._restarting = False
                    if not ok:
                        await asyncio.sleep(60)
                    continue

                # Reset error count kalo udah >60d dari error terakhir (polling sehat)
                if self._error_count > 0 and now - self._last_error_at > 60:
                    self._error_count = 0

            except asyncio.CancelledError:
                break
            except Exception as exc:
                log.error("PollingGuard error: %s", exc)
                await asyncio.sleep(15)


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

    # ── Inisialisasi koneksi ADB ─────────────────────────────────────────
    # Coba connect via Wi-Fi dulu (kalo ada), fallback ke USB.
    if adb.wifi_host:
        connected = await adb.connect_wifi()
        if connected:
            log.info("ADB: terhubung via Wi-Fi ke %s", adb.wifi_host)
        else:
            log.warning("ADB: koneksi Wi-Fi gagal, fallback ke USB...")
    else:
        log.info("ADB: tanpa Wi-Fi host, deteksi otomatis via USB...")

    # Verifikasi device beneran nyambung
    device_ok = await adb.is_connected()
    if device_ok:
        log.info("ADB: device terdeteksi — %s", adb.device_serial or "(default)")
    else:
        log.warning(
            "ADB: device TIDAK terdeteksi — bot tetap jalan, "
            "tapi /start bakal ditolak sampai device nyambung."
        )

    # Auto-start ke MONITOR mode — bot langsung jalan tanpa nunggu /start
    # Kalo ADB blom nyambung, state machine nunggu otomatis di loop utama
    runtime = BotRuntimeState(
        mode=BotMode.MONITOR,
        workflow_state=WorkflowState.OPEN_PRODUCT,
    )
    # Restore dari persistent DB
    runtime.stats.purchase_count_session = saved.purchase_count_session
    runtime.recovery_level = 0  # recovery disederhanakan, gausa level
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
                runtime.recovery_level,
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
    recovery = Recovery(
        adb=adb, bus=bus, runtime=runtime, product=cfg.product
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
                CheckoutHandler(adb, cache, cfg.product, runtime))
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
        adb=adb,
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

    # ── Inisialisasi Telegram dengan retry ──────────────────────────────────
    # Biar gak mati total kalo Telegram API unreachable (timeout/proxy/DNS).
    tg_ready = False
    tg_polling_guard = None
    for attempt in range(1, 4):
        try:
            await asyncio.wait_for(tg_app.initialize(), timeout=10)
            await asyncio.wait_for(tg_app.start(), timeout=10)
            # Buat guard dulu biar error_callback-nya ready
            tg_polling_guard = _PollingGuard(tg_app)
            await asyncio.wait_for(
                tg_app.updater.start_polling(
                    drop_pending_updates=True,
                    bootstrap_retries=-1,
                    error_callback=tg_polling_guard.on_polling_error,
                ),
                timeout=20,
            )
            tg_ready = True
            log.info("Telegram bot siap (percobaan ke-%d)", attempt)
            break
        except Exception as exc:
            log.warning(
                "Telegram init gagal (percobaan %d/3): %s",
                attempt, exc,
            )
            if attempt < 3:
                await asyncio.sleep(5)
            else:
                log.error(
                    "Telegram unreachable setelah 3 percobaan — bot jalan "
                    "tanpa Telegram. Kirim pesan manual ke admin."
                )

    # ── Jalankan state machine & schedulers ────────────────────────────────
    try:
        if tg_ready:
            tg_polling_guard_task = asyncio.create_task(tg_polling_guard.run())
            await asyncio.gather(
                sm.run(),
                watchdog.start(),
                heartbeat.start(),
                blackout.start(),
                daily.start(),
                health.start(),
                stop_event.wait(),
            )
        else:
            # Bot tetap jalan tanpa Telegram (mode CLI-only)
            log.info("Bot jalan tanpa Telegram — /start via console gak bisa.")
            await asyncio.gather(
                sm.run(),
                watchdog.start(),
                heartbeat.start(),
                blackout.start(),
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

            # Stop Telegram (kalo sempet init)
            if tg_ready:
                tg_polling_guard_task.cancel()
                try:
                    await asyncio.wait_for(tg_polling_guard_task, timeout=3)
                except (asyncio.CancelledError, asyncio.TimeoutError):
                    pass
                await tg_app.updater.stop()
                await tg_app.stop()
                await tg_app.shutdown()

            # Close DB
            await cfg.db.close()

            log.info("Shutdown selesai.")


class _RecoveryAdapter:
    """Adapter agar Recovery bisa dipanggil sebagai StateHandler."""
    def __init__(self, recovery: Recovery) -> None:
        self._recovery = recovery

    async def execute(self) -> WorkflowState:
        return await self._recovery.recover()


if __name__ == "__main__":
    asyncio.run(main())
