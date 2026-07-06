"""
Product Actions — interaksi UI di halaman produk.

Workflow memanggil fungsi-fungsi ini, BUKAN adb.tap() secara langsung.
Setiap action: resolve element via parser → ambil center → tap.
"""
from __future__ import annotations

import asyncio
import time

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.parser.product_parser import ProductParser
from bot.utils.logger import get_logger

log = get_logger(__name__)


async def tap_buy_now(adb: ADBClient, cache: XMLCache) -> bool:
    """
    Tap tombol 'Beli Dengan Voucher'.
    Resolve via resource_id → text → fallback.
    """
    parser = ProductParser(cache)
    el = parser.get_buy_now_button()
    if el is None:
        log.error("tap_buy_now: elemen tidak ditemukan")
        return False
    log.info("tap_buy_now via [%s] at (%d, %d)", el.resolved_via, el.tap_x, el.tap_y)
    return await adb.tap(el.tap_x, el.tap_y)


async def tap_back(adb: ADBClient, cache: XMLCache) -> bool:
    """Tap tombol back di action bar."""
    parser = ProductParser(cache)
    el = parser.get_back_button()
    if el is None:
        log.warning("tap_back: elemen tidak ditemukan, pakai keyevent BACK")
        return await adb.press_back()
    return await adb.tap(el.tap_x, el.tap_y)


async def wait_for_product_page(
    adb: ADBClient, cache: XMLCache, max_wait: float = 10.0, poll: float = 0.3
) -> bool:
    """
    Tunggu sampai halaman produk terdeteksi.
    Return True jika berhasil dalam max_wait detik.
    """
    t0 = time.monotonic()

    # Tunggu singkat agar app mulai load
    await asyncio.sleep(0.3)

    while (time.monotonic() - t0) < max_wait:
        # Pake TTL cache — gak perlu dump tiap poll interval
        tree = await cache.get(adb)
        if tree is None:
            await asyncio.sleep(poll)
            continue

        parser = ProductParser(cache)
        if parser.is_product_page():
            log.info("Halaman produk terdeteksi (%.1fs)", time.monotonic() - t0)
            return True
        await asyncio.sleep(poll)
    log.warning("wait_for_product_page: timeout setelah %.1fs", max_wait)
    return False
