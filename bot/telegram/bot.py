"""
Telegram Bot — setup polling dan registrasi command handlers.
"""
from __future__ import annotations

from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

from bot.telegram.commands import CommandHandlers
from bot.utils.logger import get_logger

log = get_logger(__name__)


def build_application(token: str, handlers: CommandHandlers) -> Application:
    """Buat Application Telegram dan daftarkan semua command."""
    app = Application.builder().token(token).build()

    def check_auth(handler_fn):
        async def auth_wrapper(update: Update, context: ContextTypes.DEFAULT_TYPE):
            if not update.effective_chat:
                return
            config = handlers._get_config()
            allowed_chat_id = config.get("telegram_chat_id")

            if allowed_chat_id and str(update.effective_chat.id) != str(allowed_chat_id):
                log.warning(
                    "Akses ditolak untuk chat_id=%s pada command /%s",
                    update.effective_chat.id,
                    update.message.text.split()[0] if update.message and update.message.text else "unknown"
                )
                return
            return await handler_fn(update, context)
        return auth_wrapper

    cmd_map = {
        "start": handlers.cmd_start,
        "stop": handlers.cmd_stop,
        "pause": handlers.cmd_pause,
        "resume": handlers.cmd_resume,
        "status": handlers.cmd_status,
        "help": handlers.cmd_help,
        "panic": handlers.cmd_panic,
        "monitor": handlers.cmd_monitor,
        # Product
        "setproduct": handlers.cmd_setproduct,
        "product": handlers.cmd_product,
        "reloadproduct": handlers.cmd_reloadproduct,
        "setvariant": handlers.cmd_setvariant,
        "setpayment": handlers.cmd_setpayment,
        # Settings
        "target": handlers.cmd_target,
        "qty": handlers.cmd_qty,
        "stockmode": handlers.cmd_stockmode,
        "restocklimit": handlers.cmd_restocklimit,
        "interval": handlers.cmd_interval,
        "cooldown": handlers.cmd_cooldown,
        "blackout": handlers.cmd_blackout,
        "sleepafter": handlers.cmd_sleepafter,
    }

    for cmd, handler in cmd_map.items():
        app.add_handler(CommandHandler(cmd, check_auth(handler)))

    log.info("Telegram bot: %d commands registered", len(cmd_map))
    return app
