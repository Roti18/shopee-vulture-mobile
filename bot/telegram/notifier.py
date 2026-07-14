"""
Telegram Notifier — subscribes ke EventBus dan kirim pesan ke Telegram.

Format pesan:
  VariantStockDetectedEvent → 🟢 Restock Detected (SEBELUM checkout)
  OrderSuccessEvent         → ✅ Order Success
  CheckoutFailedEvent       → ⚠️ Checkout Failed
"""
from __future__ import annotations

from datetime import timedelta
from pathlib import Path

from telegram import Bot
from telegram.error import TelegramError

from bot.events.bus import EventBus
from bot.events import events as ev
from bot.utils.logger import get_logger
from bot.utils import system_info

log = get_logger(__name__)


def _fmt_duration(seconds: float) -> str:
    td = timedelta(seconds=int(seconds))
    d = td.days
    h, rem = divmod(td.seconds, 3600)
    m, s = divmod(rem, 60)
    parts = []
    if d:
        parts.append(f"{d}h")
    if h:
        parts.append(f"{h}j")
    if m:
        parts.append(f"{m}m")
    parts.append(f"{s}d")
    return " ".join(parts)


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self._bot = Bot(token=token)
        self._chat_id = chat_id

    async def send(self, text: str) -> None:
        try:
            await self._bot.send_message(
                chat_id=self._chat_id, text=text, parse_mode="HTML"
            )
        except TelegramError as exc:
            log.error("TelegramNotifier.send error: %s", exc)

    async def send_photo(self, path: Path, caption: str = "") -> None:
        try:
            with open(path, "rb") as f:
                await self._bot.send_photo(
                    chat_id=self._chat_id, photo=f, caption=caption, parse_mode="HTML"
                )
        except TelegramError as exc:
            log.error("TelegramNotifier.send_photo error: %s", exc)

    # ------------------------------------------------------------------ #
    # Register ke EventBus
    # ------------------------------------------------------------------ #

    def register(self, bus: EventBus) -> None:
        bus.subscribe(ev.VariantStockDetectedEvent, self._on_variant_stock_detected)
        bus.subscribe(ev.OrderSuccessEvent, self._on_order_success)
        bus.subscribe(ev.CheckoutFailedEvent, self._on_checkout_failed)
        bus.subscribe(ev.StockEmptyEvent, self._on_stock_empty)
        bus.subscribe(ev.HeartbeatEvent, self._on_heartbeat)
        bus.subscribe(ev.DailyReportEvent, self._on_daily_report)
        bus.subscribe(ev.WatchdogAlertEvent, self._on_watchdog_alert)
        bus.subscribe(ev.RecoveryStartedEvent, self._on_recovery_started)
        bus.subscribe(ev.PanicEvent, self._on_panic)
        bus.subscribe(ev.CooldownStartEvent, self._on_cooldown_start)
        bus.subscribe(ev.CooldownEndEvent, self._on_cooldown_end)
        bus.subscribe(ev.BlackoutStartEvent, self._on_blackout_start)
        bus.subscribe(ev.BlackoutEndEvent, self._on_blackout_end)
        bus.subscribe(ev.BotStartedEvent, self._on_bot_started)
        bus.subscribe(ev.BotStoppedEvent, self._on_bot_stopped)

    # ------------------------------------------------------------------ #
    # Stock & Order Handlers
    # ------------------------------------------------------------------ #

    async def _on_variant_stock_detected(self, event: ev.VariantStockDetectedEvent) -> None:
        """
        🟢 Restock Detected — ada stok.
        Kalo is_checkout=False (MONITOR mode), langsung lanjut monitoring
        tanpa ngomong "mulai proses checkout".
        """
        if event.is_checkout:
            msg = (
                "🟢 <b>Restock Detected</b>\n\n"
                f"Product : {event.product_name}\n"
                f"Variant : {event.variant}\n"
                f"Stock   : {event.stock_count}\n\n"
                "⚡ Bot akan mulai proses checkout..."
            )
        else:
            msg = (
                "🟢 <b>Stok Terpantau</b>\n\n"
                f"Product : {event.product_name}\n"
                f"Variant : {event.variant}\n"
                f"Stock   : {event.stock_count}\n\n"
                "📡 Mode MONITOR — stok masih dipantau..."
            )
        await self.send(msg)

    async def _on_order_success(self, event: ev.OrderSuccessEvent) -> None:
        """
        ✅ Order Success — dikirim setelah order berhasil dibuat.
        """
        msg = (
            "✅ <b>Order Success</b>\n\n"
            f"Product : {event.product_name}\n"
            f"Variant : {event.variant}\n"
            f"Info    : {event.price}\n"
            f"Waktu   : {event.timestamp.strftime('%H:%M:%S')}"
        )
        await self.send(msg)
        if event.screenshot_path and event.screenshot_path.exists():
            await self.send_photo(
                event.screenshot_path,
                caption="📸 Screenshot konfirmasi order",
            )

    async def _on_checkout_failed(self, event: ev.CheckoutFailedEvent) -> None:
        """
        ⚠️ Checkout Failed — stok terdeteksi tetapi order gagal dibuat.
        """
        await self.send(
            "⚠️ <b>Checkout Failed</b>\n\n"
            f"Product : {event.product_name}\n"
            f"Variant : {event.variant}\n\n"
            "Stock terdeteksi tetapi order gagal dibuat.\n\n"
            f"Reason:\n{event.reason}"
        )

    async def _on_stock_empty(self, event: ev.StockEmptyEvent) -> None:
        """Silent log — tidak kirim Telegram setiap kali stok habis (terlalu banyak notif)."""
        log.debug(
            "Stok habis: variant=%s count=%d threshold=%d",
            event.variant, event.stock_count, event.threshold,
        )

    # ------------------------------------------------------------------ #
    # Scheduler Handlers
    # ------------------------------------------------------------------ #

    async def _on_heartbeat(self, event: ev.HeartbeatEvent) -> None:
        adb_status = "✅ Connected" if event.adb_connected else "❌ Disconnected"
        await self.send(
            "💓 <b>Heartbeat</b>\n\n"
            f"⏱ Runtime  : {_fmt_duration(event.runtime_seconds)}\n"
            f"📱 ADB      : {adb_status}\n"
            f"🔄 State    : {event.current_state}\n"
            f"🔢 Loop     : {event.loop_count} | ✅ Sukses: {event.success_count}\n"
            f"🖥 CPU      : {event.cpu_percent:.1f}% | "
            f"RAM: {event.ram_used_mb:.0f}/{event.ram_total_mb:.0f} MB"
        )

    async def _on_daily_report(self, event: ev.DailyReportEvent) -> None:
        await self.send(
            f"📊 <b>Laporan Harian — {event.date}</b>\n\n"
            f"🔢 Loop    : {event.loops}\n"
            f"✅ Sukses  : {event.success}\n"
            f"❌ Gagal   : {event.failure}\n"
            f"⏱ Avg loop : {event.avg_loop_ms:.0f} ms"
        )

    async def _on_watchdog_alert(self, event: ev.WatchdogAlertEvent) -> None:
        await self.send(
            "⚠️ <b>WATCHDOG ALERT</b>\n\n"
            f"Bot frozen {event.frozen_seconds:.0f} detik\n"
            f"State terakhir: {event.last_state.value}\n"
            "Memulai recovery otomatis..."
        )

    async def _on_recovery_started(self, event: ev.RecoveryStartedEvent) -> None:
        await self.send(
            f"🔧 <b>Recovery L{event.level.value}</b>\n"
            f"Alasan: {event.reason}"
        )

    async def _on_panic(self, event: ev.PanicEvent) -> None:
        await self.send(
            "🚨 <b>PANIC — Bot Dihentikan!</b>\n\n"
            f"{event.reason}\n\n"
            "⚠️ Intervensi manual diperlukan!"
        )

    async def _on_cooldown_start(self, event: ev.CooldownStartEvent) -> None:
        await self.send(
            f"💤 <b>Cooldown Dimulai</b>\n"
            f"Pembelian sesi ini : {event.purchase_count}\n"
            f"Durasi cooldown    : {event.hours:.1f} jam"
        )

    async def _on_cooldown_end(self, event: ev.CooldownEndEvent) -> None:
        await self.send("✅ <b>Cooldown selesai</b> — Monitoring dilanjutkan")

    async def _on_blackout_start(self, event: ev.BlackoutStartEvent) -> None:
        await self.send(f"🌙 <b>Blackout Mode</b> — Window: {event.window}")

    async def _on_blackout_end(self, event: ev.BlackoutEndEvent) -> None:
        await self.send("☀️ <b>Blackout selesai</b> — Bot aktif kembali")

    async def _on_bot_started(self, event: ev.BotStartedEvent) -> None:
        await self.send("🤖 <b>Bot dimulai</b> — Monitoring aktif")

    async def _on_bot_stopped(self, event: ev.BotStoppedEvent) -> None:
        await self.send(f"🛑 <b>Bot dihentikan</b>\nAlasan: {event.reason}")
