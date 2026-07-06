"""
Repositories — CRUD pattern untuk setiap tabel di SQLite.

Setiap repository menerima Database instance sebagai dependency injection.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from bot.models.product import ProductConfig
from bot.storage.database import Database
from bot.utils.logger import get_logger

log = get_logger(__name__)


# ─── Settings Repository ──────────────────────────────────────────────────────

class SettingsRepository:
    """Key-value store untuk runtime settings (interval, cooldown, blackout, dll)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self, key: str, default: str = "") -> str:
        async with self._db.conn.execute(
            "SELECT value FROM settings WHERE key = ?", (key,)
        ) as cur:
            row = await cur.fetchone()
            return row["value"] if row else default

    async def set(self, key: str, value: Any) -> None:
        await self._db.conn.execute(
            """INSERT INTO settings (key, value, updated_at)
               VALUES (?, ?, datetime('now','localtime'))
               ON CONFLICT(key) DO UPDATE SET
                 value = excluded.value,
                 updated_at = excluded.updated_at""",
            (key, str(value)),
        )
        await self._db.conn.commit()

    async def get_float(self, key: str, default: float) -> float:
        v = await self.get(key)
        try:
            return float(v) if v else default
        except ValueError:
            return default

    async def get_int(self, key: str, default: int) -> int:
        v = await self.get(key)
        try:
            return int(v) if v else default
        except ValueError:
            return default

    async def get_bool(self, key: str, default: bool = False) -> bool:
        v = await self.get(key)
        if not v:
            return default
        return v.lower() in ("true", "1", "yes")

    async def get_nullable(self, key: str) -> str | None:
        v = await self.get(key, "__NONE__")
        return None if v == "__NONE__" else v

    async def delete(self, key: str) -> None:
        await self._db.conn.execute("DELETE FROM settings WHERE key = ?", (key,))
        await self._db.conn.commit()

    async def get_all(self) -> dict[str, str]:
        async with self._db.conn.execute("SELECT key, value FROM settings") as cur:
            rows = await cur.fetchall()
            return {row["key"]: row["value"] for row in rows}


# ─── Product Repository ───────────────────────────────────────────────────────

class ProductRepository:
    """Persist konfigurasi produk ke tabel `product` (satu baris, id=1)."""

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self) -> ProductConfig:
        async with self._db.conn.execute("SELECT * FROM product WHERE id = 1") as cur:
            row = await cur.fetchone()
        if row is None:
            return ProductConfig()
        return ProductConfig(
            url=row["url"],
            name=row["name"],
            variant=row["variant"],
            payment_method=row["payment_method"],
            minimum_stock=row["minimum_stock"],
            purchase_quantity=row["purchase_quantity"],
            stock_mode=row["stock_mode"],
            restock_limit=row["restock_limit"],
        )

    async def save(self, product: ProductConfig) -> None:
        await self._db.conn.execute(
            """UPDATE product SET
                url = ?, name = ?, variant = ?, payment_method = ?,
                minimum_stock = ?, purchase_quantity = ?,
                stock_mode = ?, restock_limit = ?,
                updated_at = datetime('now','localtime')
               WHERE id = 1""",
            (
                product.url, product.name, product.variant, product.payment_method,
                product.minimum_stock, product.purchase_quantity,
                product.stock_mode, product.restock_limit,
            ),
        )
        await self._db.conn.commit()
        log.debug("ProductRepository.save: %s", product.name)


# ─── Runtime Repository ───────────────────────────────────────────────────────

@dataclass
class RuntimeSnapshot:
    mode: str = "idle"
    workflow_state: str = "idle"
    purchase_count_session: int = 0
    cooldown_until: str | None = None
    recovery_level: int = 1


class RuntimeRepository:
    """
    Persist bot runtime state ke SQLite.
    Memungkinkan bot melanjutkan dari state terakhir setelah restart/crash.
    """

    def __init__(self, db: Database) -> None:
        self._db = db

    async def get(self) -> RuntimeSnapshot:
        async with self._db.conn.execute("SELECT * FROM bot_runtime WHERE id = 1") as cur:
            row = await cur.fetchone()
        if row is None:
            return RuntimeSnapshot()
        return RuntimeSnapshot(
            mode=row["mode"],
            workflow_state=row["workflow_state"],
            purchase_count_session=row["purchase_count_session"],
            cooldown_until=row["cooldown_until"],
            recovery_level=row["recovery_level"],
        )

    async def save(self, snapshot: RuntimeSnapshot) -> None:
        await self._db.conn.execute(
            """UPDATE bot_runtime SET
                mode = ?, workflow_state = ?,
                purchase_count_session = ?, cooldown_until = ?,
                recovery_level = ?,
                updated_at = datetime('now','localtime')
               WHERE id = 1""",
            (
                snapshot.mode,
                snapshot.workflow_state,
                snapshot.purchase_count_session,
                snapshot.cooldown_until,
                snapshot.recovery_level,
            ),
        )
        await self._db.conn.commit()

    async def reset_session(self) -> None:
        await self._db.conn.execute(
            "UPDATE bot_runtime SET purchase_count_session = 0 WHERE id = 1"
        )
        await self._db.conn.commit()


# ─── Statistics Repository ────────────────────────────────────────────────────

@dataclass
class DailyStats:
    date: str
    loop_count: int = 0
    success_count: int = 0
    failure_count: int = 0
    total_loop_ms: float = 0.0

    @property
    def avg_loop_ms(self) -> float:
        if self.loop_count == 0:
            return 0.0
        return self.total_loop_ms / self.loop_count


class StatisticsRepository:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def today(self) -> DailyStats:
        today = datetime.now().strftime("%Y-%m-%d")
        async with self._db.conn.execute(
            "SELECT * FROM statistics WHERE date = ?", (today,)
        ) as cur:
            row = await cur.fetchone()
        if row is None:
            return DailyStats(date=today)
        return DailyStats(
            date=row["date"],
            loop_count=row["loop_count"],
            success_count=row["success_count"],
            failure_count=row["failure_count"],
            total_loop_ms=row["total_loop_ms"],
        )

    async def increment(
        self,
        loop_count: int = 0,
        success: int = 0,
        failure: int = 0,
        loop_ms: float = 0.0,
    ) -> None:
        today = datetime.now().strftime("%Y-%m-%d")
        await self._db.conn.execute(
            """INSERT INTO statistics (date, loop_count, success_count, failure_count, total_loop_ms)
               VALUES (?, ?, ?, ?, ?)
               ON CONFLICT(date) DO UPDATE SET
                 loop_count    = loop_count + excluded.loop_count,
                 success_count = success_count + excluded.success_count,
                 failure_count = failure_count + excluded.failure_count,
                 total_loop_ms = total_loop_ms + excluded.total_loop_ms,
                 updated_at    = datetime('now','localtime')""",
            (today, loop_count, success, failure, loop_ms),
        )
        await self._db.conn.commit()

    async def history(self, days: int = 7) -> list[DailyStats]:
        async with self._db.conn.execute(
            "SELECT * FROM statistics ORDER BY date DESC LIMIT ?", (days,)
        ) as cur:
            rows = await cur.fetchall()
        return [
            DailyStats(
                date=row["date"],
                loop_count=row["loop_count"],
                success_count=row["success_count"],
                failure_count=row["failure_count"],
                total_loop_ms=row["total_loop_ms"],
            )
            for row in rows
        ]
