"""
UI XML dumper: uiautomator dump → shell cat → parse.

v4: shell cat instead of adb pull — lebih reliable di Wi-Fi ADB.
    adb pull fork child process yg inherit pipe FD, sering hang 30s+
    di koneksi Wi-Fi yang kurang stabil. shell cat pake stdout pipe biasa,
    timeout 10s udah cukup buat file XML < 2MB.
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

    Sebelum dump, kill dulu instance uiautomator yg mungkin stuck
    dari dump sebelumnya — biar gak numpuk dan timeout semua.
    """
    # Skip import di top-level biar gak circular
    from bot.adb.client import ADBClient

    t0 = time.monotonic()

    # Kill uiautomator yg macet dari dump sebelumnya — ini penyebab
    # utama timeout beruntun: instance pertama hang, instance baru
    # antri di belakangnya dan ikut timeout.
    await adb._run(["shell", "pkill", "-f", "uiautomator"], timeout=3)

    # Dump di device — shell command
    rc, out, err = await adb._run(
        ["shell", "uiautomator", "dump", DEVICE_DUMP_PATH], timeout=10
    )
    if rc != 0:
        log.error("uiautomator dump gagal: %s", err)
        return None

    # Baca file dump via shell cat — lebih reliable daripada adb pull.
    # adb pull fork child process yg inherit pipe FD, bisa hang di Wi-Fi ADB.
    # shell cat output lewat stdout pipe, timeout 10s cukup buat file < 2MB.
    rc, xml_str, err = await adb._run(
        ["shell", "cat", DEVICE_DUMP_PATH], timeout=10
    )
    if rc != 0:
        log.error("adb cat xml dump gagal: %s", err)
        return None
    if not xml_str:
        log.error("adb cat xml dump: output kosong")
        return None

    # Parse
    try:
        tree = ET.ElementTree(ET.fromstring(xml_str.encode("utf-8")))
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
