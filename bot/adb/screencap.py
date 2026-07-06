"""
Screenshot capture: ambil via ADB, simpan ke disk, kembalikan path.
"""
import asyncio
import time
from pathlib import Path

from bot.adb.client import ADBClient
from bot.utils.logger import get_logger

log = get_logger(__name__)

SCREENSHOT_DIR = Path(__file__).parent.parent.parent / "screenshots"
SCREENSHOT_DIR.mkdir(exist_ok=True)

DEVICE_TMP = "/sdcard/screen.png"


async def capture(adb: ADBClient) -> Path | None:
    """
    Ambil screenshot device → simpan ke screenshots/<timestamp>.png.
    Returns path file atau None jika gagal.
    """
    local_path = SCREENSHOT_DIR / f"shot_{int(time.time())}.png"
    log.info("Screenshot → %s", local_path)

    # Capture di device
    rc, _, _ = await adb._run(["shell", "screencap", "-p", DEVICE_TMP])
    if rc != 0:
        log.error("screencap gagal")
        return None

    # Pull ke lokal
    rc, _, _ = await adb._run(["pull", DEVICE_TMP, str(local_path)])
    if rc != 0:
        log.error("adb pull screenshot gagal")
        return None

    # Hapus tmp di device
    await adb._run(["shell", "rm", DEVICE_TMP])

    return local_path
