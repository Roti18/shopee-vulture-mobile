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


async def spam_confirm_order(
    adb: ADBClient,
    cache: XMLCache,
    max_taps: int = 12,
    tap_interval: float = 0.18,
) -> ScreenType:
    """
    Tap tombol 'Buat Pesanan' berulang cepat sampai layar berubah.

    Return:
      - ORDER_SUCCESS / PAYMENT_PAGE kalau layar sudah pindah
      - CHECKOUT_PAGE kalau masih di checkout setelah spam
      - UNKNOWN kalau tombol tidak bisa di-resolve / tap gagal total
    """
    parser = CheckoutParser(cache)
    if not parser.is_checkout_page():
        log.warning("spam_confirm_order: bukan halaman checkout")
        return ScreenType.UNKNOWN

    last_screen = ScreenType.CHECKOUT_PAGE

    for attempt in range(1, max_taps + 1):
        force_dump = attempt == 1 or attempt % 3 == 0
        tree = await cache.get(adb, force=force_dump)
        if tree is not None:
            parser = CheckoutParser(cache)
            last_screen = parser.detect_screen()
            if last_screen in (ScreenType.ORDER_SUCCESS, ScreenType.PAYMENT_PAGE):
                log.info(
                    "spam_confirm_order: layar %s terdeteksi setelah %d tap",
                    last_screen.value,
                    attempt - 1,
                )
                return last_screen

        el = parser.get_place_order_button()
        if el is None:
            log.warning("spam_confirm_order: tombol 'Buat Pesanan' tidak ditemukan")
            return ScreenType.UNKNOWN

        log.info(
            "spam_confirm_order #%d via [%s] at (%d, %d)",
            attempt,
            el.resolved_via,
            el.tap_x,
            el.tap_y,
        )
        ok = await adb.tap(el.tap_x, el.tap_y)
        if not ok:
            log.warning("spam_confirm_order: tap gagal pada attempt %d", attempt)
            continue

        await asyncio.sleep(tap_interval)

    tree = await cache.get(adb, force=True)
    if tree is not None:
        parser = CheckoutParser(cache)
        last_screen = parser.detect_screen()

    return last_screen


async def wait_for_checkout_page(
    adb: ADBClient, cache: XMLCache, max_wait: float = 15.0, poll: float = 0.3
) -> bool:
    """Tunggu sampai halaman checkout muncul."""
    t0 = time.monotonic()

    await asyncio.sleep(0.1)

    # First dump FORCE — caller baru aja tap submit.
    tree = await cache.get(adb, force=True)
    if tree is not None and CheckoutParser(cache).is_checkout_page():
        return True

    while (time.monotonic() - t0) < max_wait:
        tree = await cache.get(adb)
        if tree is not None and CheckoutParser(cache).is_checkout_page():
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
        tree = await cache.get(adb)
        if tree is None:
            await asyncio.sleep(poll)
            continue
        parser = CheckoutParser(cache)
        screen = parser.detect_screen()
        if screen in (ScreenType.ORDER_SUCCESS, ScreenType.PAYMENT_PAGE):
            log.info("Order result screen: %s (%.1fs)", screen.value, time.monotonic() - t0)
            return screen
        await asyncio.sleep(poll)
    log.warning("wait_for_order_result: timeout %.1fs", max_wait)
    return ScreenType.UNKNOWN
