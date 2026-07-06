"""
Checkout Actions — interaksi UI di halaman checkout, payment, sukses order.
"""
from __future__ import annotations

import asyncio
import time

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.models.enums import ScreenType
from bot.parser.checkout_parser import CheckoutParser
from bot.utils.logger import get_logger

log = get_logger(__name__)


async def scroll_to_payment_method(adb: ADBClient) -> None:
    """Scroll ke bawah untuk memastikan payment method section visible."""
    await adb.swipe(540, 1800, 540, 600, 500)
    await asyncio.sleep(0.5)


async def confirm_order(adb: ADBClient, cache: XMLCache) -> bool:
    """
    Tap tombol 'Buat Pesanan'.
    Verifikasi halaman checkout terlebih dahulu.
    """
    parser = CheckoutParser(cache)
    if not parser.is_checkout_page():
        log.error("confirm_order: bukan halaman checkout")
        return False

    el = parser.get_place_order_button()
    if el is None:
        log.error("confirm_order: PLACE_ORDER_BUTTON tidak ditemukan")
        return False

    log.info("confirm_order via [%s] at (%d, %d)", el.resolved_via, el.tap_x, el.tap_y)
    return await adb.tap(el.tap_x, el.tap_y)


async def wait_for_checkout_page(
    adb: ADBClient, cache: XMLCache, max_wait: float = 15.0, poll: float = 0.3
) -> bool:
    """Tunggu sampai halaman checkout muncul."""
    t0 = time.monotonic()

    await asyncio.sleep(0.1)

    while (time.monotonic() - t0) < max_wait:
        cache.invalidate()
        tree = await cache.get(adb)
        if tree is None:
            await asyncio.sleep(poll)
            continue

        if CheckoutParser(cache).is_checkout_page():
            log.info("Halaman checkout terdeteksi (%.1fs)", time.monotonic() - t0)
            return True
        await asyncio.sleep(poll)
    log.warning("wait_for_checkout_page: timeout %.1fs", max_wait)
    return False


async def wait_for_order_result(
    adb: ADBClient, cache: XMLCache, max_wait: float = 20.0, poll: float = 0.3
) -> ScreenType:
    """
    Tunggu hasil setelah tap 'Buat Pesanan'.
    Return ScreenType: ORDER_SUCCESS, PAYMENT_PAGE, atau UNKNOWN.
    """
    t0 = time.monotonic()
    while (time.monotonic() - t0) < max_wait:
        cache.invalidate()
        await cache.get(adb)
        parser = CheckoutParser(cache)
        screen = parser.detect_screen()
        if screen in (ScreenType.ORDER_SUCCESS, ScreenType.PAYMENT_PAGE):
            log.info("Order result screen: %s (%.1fs)", screen.value, time.monotonic() - t0)
            return screen
        await asyncio.sleep(poll)
    log.warning("wait_for_order_result: timeout %.1fs", max_wait)
    return ScreenType.UNKNOWN
