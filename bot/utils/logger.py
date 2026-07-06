"""
Structured logging: JSON ke stdout (untuk Docker log driver) + human-readable ke file.

Docker logs driver mengharapkan JSON lines di stdout agar mudah di-query
dengan tools seperti Loki, CloudWatch, atau docker logs --filter.
"""
import json
import logging
import sys
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path


LOG_DIR = Path(__file__).parent.parent.parent / "logs"
LOG_DIR.mkdir(exist_ok=True)


class JsonFormatter(logging.Formatter):
    """
    Format log sebagai JSON single-line untuk stdout.
    Mudah di-parse oleh log aggregator di Docker/VPS.
    """

    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "ts": datetime.utcnow().isoformat() + "Z",
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


class HumanFormatter(logging.Formatter):
    """Format human-readable untuk file log."""
    _FMT = "%(asctime)s | %(levelname)-8s | %(name)-30s | %(message)s"
    _DATE = "%Y-%m-%d %H:%M:%S"

    def __init__(self):
        super().__init__(self._FMT, self._DATE)


def get_logger(name: str) -> logging.Logger:
    """
    Kembalikan logger dengan:
    - StreamHandler (stdout) → JSON format (INFO+) — untuk Docker
    - RotatingFileHandler   → human-readable (DEBUG+, 10 MB × 5)
    """
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger

    logger.setLevel(logging.DEBUG)

    # ── Stdout (JSON) ───────────────────────────────────────
    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.INFO)
    sh.setFormatter(JsonFormatter())

    # ── File (human) — fallback ke stdout kalo gak bisa nulis ──────────
    log_path = LOG_DIR / "bot.log"
    try:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            log_path,
            maxBytes=10 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setLevel(logging.DEBUG)
        fh.setFormatter(HumanFormatter())
        logger.addHandler(fh)
    except (PermissionError, OSError) as exc:
        # Fallback: file log gak bisa ditulis (biasanya karena Docker volume owner mismatch)
        # Log ke stdout aja — Docker logs udah cukup
        import warnings
        warnings.warn(f"File logging disabled: {exc}")

    logger.addHandler(sh)
    logger.addHandler(fh)
    return logger
