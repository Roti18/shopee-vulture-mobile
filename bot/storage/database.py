"""
Database — SQLite connection, schema init, async context.

File DB disimpan di data/bot.db — path ini di-mount sebagai Docker volume
sehingga data tetap ada walaupun container restart/crash.
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite

from bot.utils.logger import get_logger

log = get_logger(__name__)

DB_PATH = Path(__file__).parent.parent.parent / "data" / "bot.db"

# ─── Schema ──────────────────────────────────────────────────────────────────

_SCHEMA = """
-- Key-value store untuk semua pengaturan runtime
CREATE TABLE IF NOT EXISTS settings (
    key        TEXT PRIMARY KEY,
    value      TEXT NOT NULL,
    updated_at TEXT DEFAULT (datetime('now', 'localtime'))
);

-- Konfigurasi produk yang dimonitor (satu baris aktif: id=1)
CREATE TABLE IF NOT EXISTS product (
    id               INTEGER PRIMARY KEY DEFAULT 1,
    url              TEXT    DEFAULT '',
    name             TEXT    DEFAULT '',
    variant          TEXT    DEFAULT '',
    payment_method   TEXT    DEFAULT 'SeaBank Virtual Account',
    minimum_stock    INTEGER DEFAULT 1,
    purchase_quantity INTEGER DEFAULT 1,
    stock_mode       TEXT    DEFAULT 'any',
    restock_limit    INTEGER DEFAULT 3,
    updated_at       TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- Runtime state yang di-persist (satu baris: id=1)
-- Memungkinkan bot melanjutkan dari state terakhir setelah restart
CREATE TABLE IF NOT EXISTS bot_runtime (
    id                     INTEGER PRIMARY KEY DEFAULT 1,
    mode                   TEXT    DEFAULT 'idle',
    workflow_state         TEXT    DEFAULT 'idle',
    purchase_count_session INTEGER DEFAULT 0,
    cooldown_until         TEXT,
    recovery_level         INTEGER DEFAULT 1,
    updated_at             TEXT    DEFAULT (datetime('now', 'localtime'))
);

-- Statistik harian (aggregated per tanggal)
CREATE TABLE IF NOT EXISTS statistics (
    date          TEXT PRIMARY KEY,
    loop_count    INTEGER DEFAULT 0,
    success_count INTEGER DEFAULT 0,
    failure_count INTEGER DEFAULT 0,
    total_loop_ms REAL    DEFAULT 0.0,
    updated_at    TEXT    DEFAULT (datetime('now', 'localtime'))
);
"""


class Database:
    """
    Singleton-style database connection.
    Panggil await db.connect() sebelum digunakan.
    Panggil await db.close() saat shutdown.
    """

    def __init__(self, path: Path = DB_PATH) -> None:
        self._path = path
        self._conn: aiosqlite.Connection | None = None

    async def connect(self) -> None:
        """Buka koneksi dan inisialisasi schema."""
        self._path.parent.mkdir(parents=True, exist_ok=True)
        log.info("Database: connect %s", self._path)
        self._conn = await aiosqlite.connect(self._path)
        self._conn.row_factory = aiosqlite.Row
        # WAL mode: lebih cepat untuk concurrent read/write
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.executescript(_SCHEMA)
        await self._conn.commit()
        # Pastikan baris default ada
        await self._ensure_defaults()
        log.info("Database: siap")

    async def close(self) -> None:
        if self._conn:
            await self._conn.close()
            self._conn = None
            log.info("Database: koneksi ditutup")

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("Database belum terhubung — panggil await db.connect() dulu")
        return self._conn

    async def _ensure_defaults(self) -> None:
        """Pastikan baris default ada di tabel singleton."""
        await self._conn.execute(
            "INSERT OR IGNORE INTO product (id) VALUES (1)"
        )
        await self._conn.execute(
            "INSERT OR IGNORE INTO bot_runtime (id) VALUES (1)"
        )
        await self._conn.commit()
