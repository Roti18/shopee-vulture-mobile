"""
UI XML dumper: uiautomator dump via stdout pipeline — skip adb pull + file I/O.

v2: Satu adb shell pipeline:
    uiautomator dump /sdcard/ui_dump.xml && cat /sdcard/ui_dump.xml
→ stdout langsung di-parse, gak perlu pull ke lokal.
"""
import asyncio
import time
import xml.etree.ElementTree as ET

from bot.utils.logger import get_logger

log = get_logger(__name__)

DEVICE_DUMP_PATH = "/sdcard/ui_dump.xml"


async def dump_xml(adb: "ADBClient") -> ET.ElementTree | None:
    """
    Dump UI XML via stdout pipeline — 1 shell call, no file I/O lokal.

    Returns ElementTree atau None jika gagal.
    """
    # Skip import di top-level biar gak circular
    from bot.adb.client import ADBClient

    t0 = time.monotonic()

    rc, out, err = await adb._run([
        "shell", f"uiautomator dump {DEVICE_DUMP_PATH} >/dev/null 2>&1 && cat {DEVICE_DUMP_PATH}",
    ], timeout=15)

    if rc != 0 or not out.strip().startswith("<?xml"):
        log.error("XML dump gagal (rc=%d): %s", rc, err or "stdout bukan XML")
        return None

    try:
        # Hapus stderr yang mungkin nyempil (biasanya "Events injected: ...")
        xml_start = out.index("<?xml")
        clean_xml = out[xml_start:]
        tree = ET.ElementTree(ET.fromstring(clean_xml))
        duration_ms = (time.monotonic() - t0) * 1000
        log.debug("XML dump selesai (%.0f ms)", duration_ms)
        return tree
    except (ET.ParseError, ValueError) as exc:
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
