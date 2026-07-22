"""
UI XML dumper: uiautomator dump → adb pull → parse.

v3: Kembali ke adb pull. ADB punya protokol transfer binary yang lebih efisien
    daripada shell pipeline `cat` lewat stdout — terutama buat file XML yg ~1MB+.
    adb pull bisa 2-5x lebih cepat.
"""
import asyncio
import time
from pathlib import Path
import xml.etree.ElementTree as ET

from bot.utils.logger import get_logger

log = get_logger(__name__)

DEVICE_DUMP_PATH = "/sdcard/ui_dump.xml"
LOCAL_DUMP_PATH = Path(__file__).parent.parent.parent / "data" / "ui_dump.xml"


async def dump_xml(adb: "ADBClient") -> ET.ElementTree | None:
    """
    Jalankan uiautomator dump → pull → parse.
    Returns ElementTree atau None jika gagal.
    """
    # Skip import di top-level biar gak circular
    from bot.adb.client import ADBClient

    t0 = time.monotonic()

    # Dump di device — shell command, pake PIPE aman
    rc, out, err = await adb._run(
        ["shell", "uiautomator", "dump", DEVICE_DUMP_PATH], timeout=15
    )
    if rc != 0:
        log.error("uiautomator dump gagal: %s", err)
        return None

    # Pull ke lokal — pake DEVNULL biar gak hang.
    # adb pull fork child process buat transfer binary, inherit pipe FD
    # bikin proc.communicate() gak pernah EOF.
    rc, _, err = await adb._run(["pull", DEVICE_DUMP_PATH, str(LOCAL_DUMP_PATH)], capture_output=False)
    if rc != 0:
        log.error("adb pull xml dump gagal: %s", err)
        return None

    # Parse
    try:
        tree = ET.parse(LOCAL_DUMP_PATH)
        duration_ms = (time.monotonic() - t0) * 1000
        log.debug("XML dump selesai (%.0f ms)", duration_ms)
        return tree
    except ET.ParseError as exc:
        log.error("XML parse error: %s", exc)
        return None


def parse_bounds(bounds_str: str) -> tuple[int, int, int, int] | None:
    """
    Parse bounds string "[x1,y1][x2,y2]" → (x1, y1, x2, y2).
    """
    try:
        parts = bounds_str.replace("][", ",").strip("[]").split(",")
        return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
    except Exception:
        return None


def center_of_bounds(bounds_str: str) -> tuple[int, int] | None:
    """Return (cx, cy) dari bounds string."""
    b = parse_bounds(bounds_str)
    if b is None:
        return None
    x1, y1, x2, y2 = b
    return (x1 + x2) // 2, (y1 + y2) // 2
