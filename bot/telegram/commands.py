"""
Telegram command handlers — semua /command yang didukung bot.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from telegram import Update
from telegram.ext import ContextTypes

from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import BotMode, WorkflowState
from bot.models.product import ProductConfig
from bot.utils import system_info
from bot.utils.logger import get_logger

log = get_logger(__name__)


def _fmt_duration(seconds: float) -> str:
    h = int(seconds // 3600)
    m = int((seconds % 3600) // 60)
    s = int(seconds % 60)
    return f"{h}j {m}m {s}d"


class CommandHandlers:
    def __init__(
        self,
        bus: EventBus,
        runtime: BotRuntimeState,
        product: ProductConfig,
        get_config_fn,   # callable → dict (dari AppConfig)
        set_config_fn,   # async callable (key, value) → None
    ) -> None:
        self._bus = bus
        self._runtime = runtime
        self._product = product
        self._get_config = get_config_fn
        self._set_config = set_config_fn

    # ── Lifecycle ────────────────────────────────────────────────────────

    async def cmd_start(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if self._runtime.mode == BotMode.RUNNING:
            await update.message.reply_text("⚠️ Bot sudah berjalan. Kalau mau restart, /stop dulu.")
            return
        if not self._product.url:
            await update.message.reply_text(
                "❌ <b>URL produk belum diset</b>\n\n"
                "Gunakan perintah:\n"
                "/setproduct &lt;url&gt;\n\n"
                "Contoh: /setproduct https://shopee.co.id/...",
                parse_mode="HTML",
            )
            return
        self._runtime.mode = BotMode.RUNNING
        self._runtime.workflow_state = WorkflowState.OPEN_PRODUCT
        self._runtime.metrics.last_state_change = datetime.now()
        await self._bus.emit(ev.BotStartedEvent())
        await update.message.reply_text("🤖 <b>Bot dimulai</b> — monitoring + checkout aktif.", parse_mode="HTML")

    async def cmd_stop(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._runtime.mode = BotMode.STOPPED
        await self._bus.emit(ev.BotStoppedEvent(reason="Command /stop"))
        await update.message.reply_text("🛑 <b>Bot dihentikan</b>. Semua proses berhenti.", parse_mode="HTML")

    async def cmd_pause(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._runtime.mode = BotMode.PAUSED
        await self._bus.emit(ev.BotPausedEvent())
        await update.message.reply_text("⏸ <b>Bot di-pause</b>. /resume untuk melanjutkan.", parse_mode="HTML")

    async def cmd_resume(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._runtime.mode = BotMode.RUNNING
        self._runtime.metrics.last_state_change = datetime.now()
        await self._bus.emit(ev.BotResumedEvent())
        await update.message.reply_text("▶️ <b>Bot dilanjutkan</b>. Monitoring aktif kembali.", parse_mode="HTML")

    async def cmd_panic(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        self._runtime.mode = BotMode.STOPPED
        await self._bus.emit(ev.PanicEvent(reason="Command /panic dari user"))
        await update.message.reply_text(
            "🚨 <b>PANIC — Bot Dihentikan Darurat</b>\n\n"
            "Semua proses dihentikan.\n"
            "Periksa bot secara manual sebelum start lagi.",
            parse_mode="HTML",
        )

    async def cmd_monitor(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        """Monitor Mode: cek stok doang, notif Telegram kalo ada, gak checkout."""
        if self._runtime.mode == BotMode.MONITOR:
            self._runtime.mode = BotMode.STOPPED
            await self._bus.emit(ev.BotStoppedEvent(reason="Command /monitor — dimatikan"))
            await update.message.reply_text("📡 <b>Monitor mode dimatikan</b>.", parse_mode="HTML")
            return

        if not self._product.url:
            await update.message.reply_text(
                "❌ <b>URL produk belum diset</b>\n\n"
                "Gunakan /setproduct &lt;url&gt; dulu.",
                parse_mode="HTML",
            )
            return

        self._runtime.mode = BotMode.MONITOR
        self._runtime.workflow_state = WorkflowState.OPEN_PRODUCT
        self._runtime.metrics.last_state_change = datetime.now()
        await self._bus.emit(ev.BotStartedEvent())
        await update.message.reply_text(
            "📡 <b>Monitor Mode Aktif</b>\n\n"
            "Bot akan memantau stok setiap interval.\n"
            "Kalau stok tersedia, kamu dapat notifikasi.\n"
            "Tidak ada proses checkout otomatis.\n\n"
            "Gunakan /monitor lagi untuk mematikan mode ini.",
            parse_mode="HTML",
        )

    # ── Status ───────────────────────────────────────────────────────────

    async def cmd_status(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        s = self._runtime.stats
        m = self._runtime.metrics
        cpu = system_info.get_cpu_percent()
        ram_used, ram_total = system_info.get_ram_mb()

        cooldown_str = "—"
        if self._runtime.cooldown_until:
            remaining = (self._runtime.cooldown_until - datetime.now()).total_seconds()
            cooldown_str = _fmt_duration(max(0, remaining))

        msg = (
            f"📊 <b>Status Bot</b>\n\n"
            f"🔄 Mode: {'📡 MONITOR' if self._runtime.mode == BotMode.MONITOR else self._runtime.mode.value}\n"
            f"📍 State: {self._runtime.workflow_state.value}\n"
            f"⏱ Runtime: {_fmt_duration(s.uptime_seconds)}\n\n"
            f"📦 Produk: {self._product.name or '—'}\n"
            f"🏷️ Varian: {self._product.variant or '—'}\n"
            f"🎯 Min stok: {self._product.minimum_stock}\n"
            f"🔢 Qty beli: {self._product.purchase_quantity}\n"
            f"⚙️ Stock mode: {self._product.stock_mode}\n\n"
            f"🔢 Loop: {s.loop_count}\n"
            f"✅ Sukses: {s.success_count}\n"
            f"❌ Gagal: {s.failure_count}\n"
            f"🛒 Pembelian sesi ini: {s.purchase_count_session}/{self._product.restock_limit}\n\n"
            f"💤 Cooldown tersisa: {cooldown_str}\n\n"
            f"📱 ADB latency: {m.adb_latency_ms:.0f} ms\n"
            f"🖥 CPU: {cpu:.1f}% | RAM: {ram_used:.0f}/{ram_total:.0f} MB"
        )
        await update.message.reply_text(msg, parse_mode="HTML")

    async def cmd_help(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            "📋 <b>Daftar Perintah Bot</b>\n\n"
            "━━━ <b>Kontrol</b> ━━━\n"
            "/start — Mulai bot (monitoring + checkout otomatis)\n"
            "/stop — Hentikan bot sepenuhnya\n"
            "/pause — Jeda sementara\n"
            "/resume — Lanjutkan setelah jeda\n"
            "/monitor — Mode pantau: notif stok aja, gak checkout\n"
            "/status — Tampilkan status lengkap bot\n"
            "/help — Tampilkan bantuan ini\n\n"
            "━━━ <b>Produk</b> ━━━\n"
            "/setproduct &lt;url&gt; — Set URL produk Shopee\n"
            "/product — Lihat detail produk aktif\n"
            "/reloadproduct — Muat ulang produk dari database\n"
            "/setvariant &lt;nama&gt; — Set varian target\n"
            "/setpayment &lt;metode&gt; — Set metode bayar\n\n"
            "━━━ <b>Pengaturan Stok</b> ━━━\n"
            "/target &lt;n&gt; — Minimal stok buat checkout (contoh: 2)\n"
            "/qty &lt;n&gt; — Jumlah barang yang dibeli\n"
            "/stockmode &lt;any|minimum&gt; — Mode cek stok\n"
            "/restocklimit &lt;n&gt; — Max pembelian per sesi (contoh: 3)\n\n"
            "━━━ <b>Pengaturan Waktu</b> ━━━\n"
            "/interval &lt;detik&gt; — Interval tiap siklus cek (contoh: 5)\n"
            "/cooldown &lt;2h|30m|0&gt; — Cooldown setelah sukses\n"
            "/sleepafter &lt;on|off&gt; — Matikan layar HP setelah sukses\n"
            "/blackout &lt;02:00-07:00&gt; — Window istirahat bot\n"
            "/blackout off — Nonaktifkan blackout\n"
            "/blackout status — Cek status blackout\n\n"
            "━━━ <b>Darurat</b> ━━━\n"
            "/panic — Hentikan bot darurat",
            parse_mode="HTML",
        )

    # ── Product ──────────────────────────────────────────────────────────

    async def cmd_setproduct(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        args = ctx.args
        if not args:
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n/setproduct &lt;url_shopee&gt;\n\n"
                "Contoh:\n/setproduct https://id.shp.ee/xxxxx",
                parse_mode="HTML",
            )
            return
        url = args[0]
        self._product.url = url
        await self._set_config("product.url", url)
        await update.message.reply_text(
            f"✅ <b>URL produk disimpan</b>\n\n{url}\n\n"
            f"Gunakan /start atau /monitor untuk mulai.",
            parse_mode="HTML",
        )

    async def cmd_product(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await update.message.reply_text(
            f"📦 <b>Produk Aktif</b>\n\n"
            f"Nama    : {self._product.name or '—'}\n"
            f"URL     : {self._product.url or '—'}\n"
            f"Varian  : {self._product.variant or '— (semua varian)'}\n"
            f"Payment : {self._product.payment_method}",
            parse_mode="HTML",
        )

    async def cmd_reloadproduct(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        await self._set_config("__reload__", True)
        await update.message.reply_text("🔄 <b>Produk di-reload</b> dari database.", parse_mode="HTML")

    async def cmd_setvariant(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n/setvariant &lt;nama_varian&gt;\n\n"
                "Contoh:\n/setvariant matcha latte 50ml",
                parse_mode="HTML",
            )
            return
        variant = " ".join(ctx.args)
        self._product.variant = variant
        await self._set_config("product.variant", variant)
        await update.message.reply_text(f"✅ <b>Varian target:</b> {variant}", parse_mode="HTML")

    async def cmd_setpayment(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n/setpayment &lt;metode_bayar&gt;\n\n"
                "Contoh:\n/setpayment SeaBank Virtual Account",
                parse_mode="HTML",
            )
            return
        method = " ".join(ctx.args)
        self._product.payment_method = method
        await self._set_config("product.payment_method", method)
        await update.message.reply_text(f"✅ <b>Metode pembayaran:</b> {method}", parse_mode="HTML")

    # ── Settings ─────────────────────────────────────────────────────────

    async def cmd_target(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args or not ctx.args[0].isdigit():
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n/target &lt;angka&gt;\n\n"
                "Contoh: /target 2  (minimal stok 2 baru checkout)",
                parse_mode="HTML",
            )
            return
        n = int(ctx.args[0])
        self._product.minimum_stock = n
        await self._set_config("product.minimum_stock", n)
        await update.message.reply_text(f"🎯 <b>Minimal stok:</b> {n}", parse_mode="HTML")

    async def cmd_qty(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args or not ctx.args[0].isdigit():
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n/qty &lt;angka&gt;\n\n"
                "Contoh: /qty 2  (beli 2 item setiap checkout)",
                parse_mode="HTML",
            )
            return
        n = int(ctx.args[0])
        self._product.purchase_quantity = n
        await self._set_config("product.purchase_quantity", n)
        await update.message.reply_text(f"🔢 <b>Jumlah beli:</b> {n} item", parse_mode="HTML")

    async def cmd_stockmode(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args or ctx.args[0].lower() not in ("any", "minimum"):
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n/stockmode &lt;any|minimum&gt;\n\n"
                "any     — Stok &gt; 0 langsung checkout\n"
                "minimum — Stok harus &gt;= /target",
                parse_mode="HTML",
            )
            return
        mode = ctx.args[0].lower()
        self._product.stock_mode = mode
        await self._set_config("product.stock_mode", mode)
        label = "Stok > 0 langsung checkout" if mode == "any" else f"Stok >= minimal target"
        await update.message.reply_text(f"⚙️ <b>Stock mode:</b> {mode} ({label})", parse_mode="HTML")

    async def cmd_restocklimit(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args or not ctx.args[0].isdigit():
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n/restocklimit &lt;angka&gt;\n\n"
                "Contoh: /restocklimit 3  (max 3x sukses, lalu cooldown)",
                parse_mode="HTML",
            )
            return
        n = int(ctx.args[0])
        self._product.restock_limit = n
        await self._set_config("product.restock_limit", n)
        await update.message.reply_text(f"🛒 <b>Batas pembelian/sesi:</b> {n}x", parse_mode="HTML")

    async def cmd_interval(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args or not ctx.args[0].replace(".", "").isdigit():
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n/interval &lt;detik&gt;\n\n"
                "Contoh:\n/interval 3  (cek setiap 3 detik)\n/interval 10 (cek setiap 10 detik)",
                parse_mode="HTML",
            )
            return
        interval = float(ctx.args[0])
        await self._set_config("bot.check_interval_seconds", interval)
        await update.message.reply_text(f"⏱ <b>Interval cek stok:</b> {interval} detik", parse_mode="HTML")

    async def cmd_cooldown(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n/cooldown &lt;waktu&gt;\n\n"
                "Contoh:\n/cooldown 2h   — cooldown 2 jam\n"
                "/cooldown 30m  — cooldown 30 menit\n"
                "/cooldown 0    — tanpa cooldown\n"
                "/cooldown 1.5  — cooldown 1.5 jam",
                parse_mode="HTML",
            )
            return

        arg = ctx.args[0].lower()
        try:
            if arg.endswith("h"):
                hours = float(arg[:-1])
            elif arg.endswith("m"):
                hours = float(arg[:-1]) / 60.0
            else:
                hours = float(arg)

            if hours < 0:
                raise ValueError("Cooldown tidak boleh negatif")

            await self._set_config("bot.cooldown_hours", hours)

            if hours == 0:
                await update.message.reply_text("💤 <b>Cooldown:</b> Tidak ada (langsung lanjut)", parse_mode="HTML")
            elif hours < 1:
                minutes = int(hours * 60)
                await update.message.reply_text(f"💤 <b>Cooldown:</b> {minutes} menit", parse_mode="HTML")
            else:
                await update.message.reply_text(f"💤 <b>Cooldown:</b> {hours:.1f} jam", parse_mode="HTML")

        except ValueError:
            await update.message.reply_text(
                "❌ <b>Format salah.</b>\nGunakan angka positif, contoh: 2h, 45m, 1.5, atau 0",
                parse_mode="HTML",
            )

    # ── Blackout ─────────────────────────────────────────────────────────

    async def cmd_blackout(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args:
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n"
                "/blackout 02:00-07:00  — set blackout\n"
                "/blackout off          — nonaktifkan\n"
                "/blackout status       — cek status",
                parse_mode="HTML",
            )
            return
        arg = ctx.args[0].lower()
        if arg == "off":
            await self._set_config("bot.blackout", None)
            await update.message.reply_text("🌙 <b>Blackout:</b> Dinonaktifkan", parse_mode="HTML")
        elif arg == "status":
            current = self._get_config().get("bot", {}).get("blackout") or "off"
            await update.message.reply_text(f"🌙 <b>Blackout:</b> {current}", parse_mode="HTML")
        else:
            await self._set_config("bot.blackout", arg)
            await update.message.reply_text(f"🌙 <b>Blackout:</b> {arg}", parse_mode="HTML")

    async def cmd_sleepafter(self, update: Update, ctx: ContextTypes.DEFAULT_TYPE) -> None:
        if not ctx.args or ctx.args[0].lower() not in ("on", "off"):
            await update.message.reply_text(
                "❌ <b>Gunakan format:</b>\n"
                "/sleepafter on   — matikan layar HP setelah sukses\n"
                "/sleepafter off  — biarkan HP tetap menyala",
                parse_mode="HTML",
            )
            return
        val = ctx.args[0].lower() == "on"
        await self._set_config("bot.sleep_after_success", val)
        status = "Nyala, bot pause setelah sukses" if val else "Mati, bot lanjut langsung"
        await update.message.reply_text(f"💤 <b>Sleep after success:</b> {status}", parse_mode="HTML")
