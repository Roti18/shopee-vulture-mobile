"""
Variant Actions — interaksi UI di popup pemilihan varian.

Perubahan dari v1:
  - select_variant() dihapus (digantikan oleh check_variant.py yang langsung
    memanggil adb.tap() menggunakan ResolvedElement dari VariantInfo).
  - Ditambah set_purchase_quantity() untuk mengatur jumlah beli.
"""
from __future__ import annotations

import asyncio
import time

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.parser.variant_parser import VariantParser
from bot.utils.logger import get_logger

log = get_logger(__name__)



async def close_variant_popup(adb: ADBClient, cache: XMLCache) -> bool:
    """Tutup popup varian via tombol X."""
    parser = VariantParser(cache)
    el = parser.get_close_button()
    if el is None:
        log.warning("close_variant_popup: tombol close tidak ditemukan, press_back")
        return await adb.press_back()
    log.info("close_variant_popup via [%s] at (%d, %d)", el.resolved_via, el.tap_x, el.tap_y)
    return await adb.tap(el.tap_x, el.tap_y)


async def set_purchase_quantity(
    adb: ADBClient, cache: XMLCache, quantity: int,
    plus_button_el = None,
) -> bool:
    """
    Atur jumlah beli di popup varian.
    Default quantity di UI adalah 1.
    Tap tombol + sebanyak (quantity - 1) untuk menaikkan.

    Args:
        plus_button_el: pre-resolved plus button dari caller (hemat dump)
    """
    if quantity <= 1:
        return True

    if plus_button_el is None:
        cache.invalidate()
        await cache.get(adb)
        parser = VariantParser(cache)
        plus_el = parser.get_plus_button()
    else:
        plus_el = plus_button_el
    plus_el = parser.get_plus_button()
    if plus_el is None:
        log.error("set_purchase_quantity: BUTTON_PLUS tidak ditemukan")
        return False

    log.info(
        "set_purchase_quantity: tap + %d kali via [%s] at (%d, %d)",
        quantity - 1, plus_el.resolved_via, plus_el.tap_x, plus_el.tap_y,
    )
    for i in range(quantity - 1):
        await adb.tap(plus_el.tap_x, plus_el.tap_y)
        await asyncio.sleep(0.2)   # beri jeda agar UI update

    return True


async def tap_submit_buy_now(adb: ADBClient, cache: XMLCache) -> bool:
    """
    Tap tombol 'Beli Sekarang' di popup varian.
    Hanya tap jika teks submit = 'Beli Sekarang' (bukan 'Habis').
    """
    parser = VariantParser(cache)
    if not parser.is_submit_ready():
        log.warning(
            "tap_submit_buy_now: submit button tidak ready (%s)",
            parser.get_submit_button_text(),
        )
        return False
    el = parser.get_submit_button()
    if el is None:
        log.error("tap_submit_buy_now: submit button tidak ditemukan")
        return False
    log.info(
        "tap_submit_buy_now via [%s] at (%d, %d)", el.resolved_via, el.tap_x, el.tap_y
    )
    return await adb.tap(el.tap_x, el.tap_y)


async def wait_for_variant_popup(
    adb: ADBClient, cache: XMLCache, max_wait: float = 8.0, poll: float = 0.3
) -> bool:
    """Tunggu sampai popup varian muncul."""
    t0 = time.monotonic()

    await asyncio.sleep(0.1)

    while (time.monotonic() - t0) < max_wait:
        cache.invalidate()
        tree = await cache.get(adb)
        if tree is None:
            await asyncio.sleep(poll)
            continue

        if VariantParser(cache).is_variant_popup_open():
            log.info("Popup varian muncul (%.1fs)", time.monotonic() - t0)
            return True
        await asyncio.sleep(poll)
    log.warning("wait_for_variant_popup: timeout %.1fs", max_wait)
    return False
