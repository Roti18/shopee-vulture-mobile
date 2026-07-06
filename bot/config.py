"""
AppConfig — loader: .env (secrets) + SQLite (runtime settings).

Hierarki konfigurasi:
  .env          → TELEGRAM_TOKEN, TELEGRAM_CHAT_ID, ADB_*, secrets
  data/bot.db   → semua runtime settings (interval, cooldown, blackout, product, stats)
  data/config.json → seed awal untuk product jika DB belum berisi URL (opsional)

Setelah startup, semua perubahan via /command Telegram disimpan ke SQLite.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path

from dotenv import load_dotenv

from bot.models.product import ProductConfig
from bot.storage.database import Database
from bot.storage.repositories import ProductRepository, SettingsRepository
from bot.utils.logger import get_logger

log = get_logger(__name__)

DATA_DIR = Path(__file__).parent.parent / "data"
ENV_PATH = DATA_DIR / ".env"
CONFIG_SEED_PATH = DATA_DIR / "config.json"   # optional seed file


@dataclass
class AppConfig:
    # Secrets (dari .env — tidak pernah disimpan di DB)
    telegram_token: str
    telegram_chat_id: str
    adb_device_serial: str
    adb_wifi_host: str

    # Runtime objects (di-inject setelah DB connect)
    product: ProductConfig
    db: Database
    settings: SettingsRepository
    product_repo: ProductRepository

    # ── Getters (baca dari DB secara sync via cached value) ──────────────
    # Nilai-nilai ini di-cache in-memory dan di-refresh saat set_config() dipanggil

    _interval: float = 5.0
    _cooldown_hours: float = 6.0
    _blackout: str | None = None
    _sleep_after_success: bool = False

    def get_interval(self) -> float:
        return self._interval

    def get_cooldown_hours(self) -> float:
        return self._cooldown_hours

    def get_blackout(self) -> str | None:
        return self._blackout

    def get_sleep_after_success(self) -> bool:
        return self._sleep_after_success

    async def get_daily_stats(self, date: str):
        """Baca statistik harian dari DB (untuk DailyReport)."""
        from bot.storage.repositories import StatisticsRepository
        repo = StatisticsRepository(self.db)
        return await repo.today()

    def get_config_dict(self) -> dict:
        return {
            "telegram_token": self.telegram_token,
            "telegram_chat_id": self.telegram_chat_id,
            "bot": {
                "check_interval_seconds": self._interval,
                "cooldown_hours": self._cooldown_hours,
                "blackout": self._blackout,
                "sleep_after_success": self._sleep_after_success,
            }
        }

    # ── set_config ────────────────────────────────────────────────────────

    async def set_config_async(self, key: str, value) -> None:
        """
        Update setting di SQLite + update cache in-memory.
        Dipanggil dari Telegram command handlers.
        """
        if key == "__reload__":
            await self._reload_product_from_db()
            return

        await self.settings.set(key, value)

        # Update cache
        if key == "bot.check_interval_seconds":
            self._interval = float(value)
        elif key == "bot.cooldown_hours":
            self._cooldown_hours = float(value)
        elif key == "bot.blackout":
            self._blackout = str(value) if value else None
        elif key == "bot.sleep_after_success":
            self._sleep_after_success = bool(value)
        elif key.startswith("product."):
            field = key.split(".", 1)[1]
            setattr(self.product, field, value)
            await self.product_repo.save(self.product)

        log.debug("set_config: %s = %s", key, value)

    async def set_config(self, key: str, value) -> None:
        """Alias async — dipanggil dari Telegram command handlers."""
        await self.set_config_async(key, value)

    async def _reload_product_from_db(self) -> None:
        self.product = await self.product_repo.get()
        log.info("AppConfig: product di-reload dari DB")

    async def load_runtime_settings(self) -> None:
        """Load cached values dari DB ke memory (dipanggil setelah connect)."""
        self._interval = await self.settings.get_float(
            "bot.check_interval_seconds", 5.0
        )
        self._cooldown_hours = await self.settings.get_float(
            "bot.cooldown_hours", 6.0
        )
        self._blackout = await self.settings.get_nullable("bot.blackout")
        self._sleep_after_success = await self.settings.get_bool(
            "bot.sleep_after_success", False
        )
        log.info(
            "AppConfig: interval=%.1fs cooldown=%.1fh blackout=%s",
            self._interval, self._cooldown_hours, self._blackout,
        )


async def load_config() -> AppConfig:
    """
    Load dan return AppConfig.
    1. Baca secrets dari .env
    2. Connect ke SQLite
    3. Load runtime settings dari DB
    4. Seed product dari config.json jika DB masih kosong
    """
    # .env
    if ENV_PATH.exists():
        load_dotenv(ENV_PATH)
    else:
        load_dotenv()

    token = os.getenv("TELEGRAM_TOKEN", "")
    chat_id = os.getenv("TELEGRAM_CHAT_ID", "")
    serial = os.getenv("ADB_DEVICE_SERIAL", "")
    wifi = os.getenv("ADB_WIFI_HOST", "")

    if not token:
        log.warning("TELEGRAM_TOKEN tidak ditemukan di .env")

    # SQLite
    db = Database()
    await db.connect()

    settings_repo = SettingsRepository(db)
    product_repo = ProductRepository(db)

    # Load product dari DB
    product = await product_repo.get()

    # Seed dari config.json jika product belum ada URL
    if not product.url and CONFIG_SEED_PATH.exists():
        log.info("Seeding product dari config.json")
        with open(CONFIG_SEED_PATH, encoding="utf-8") as f:
            seed = json.load(f)
        product = ProductConfig.from_dict(seed.get("product", {}))
        await product_repo.save(product)

    config = AppConfig(
        telegram_token=token,
        telegram_chat_id=chat_id,
        adb_device_serial=serial,
        adb_wifi_host=wifi,
        product=product,
        db=db,
        settings=settings_repo,
        product_repo=product_repo,
    )

    await config.load_runtime_settings()

    log.info(
        "Config loaded: device=%r wifi=%r product=%r",
        serial, wifi, product.name or "(belum diset)",
    )
    return config
