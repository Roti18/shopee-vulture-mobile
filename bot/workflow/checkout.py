"""State: CHECKOUT — tap 'Buat Pesanan' berdasarkan XML text.

Alur:
  1. Coba dump — kalo timeout ya udah, skip, loop lagi.
     Gausa dipaksa, gausa press_back, gausa hardcoded.
  2. Kalo dump berhasil, cari tombol "Buat Pesanan" by TEXT → tap.
  3. Kalo sukses (screen berubah ke payment/success), lanjut VERIFY_PAYMENT.
  4. Kalo gak berubah dalam 15 detik, loop lagi.
"""
from __future__ import annotations

import asyncio
import time

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.models.enums import WorkflowState, ScreenType
from bot.models.product import ProductConfig
from bot.parser.checkout_parser import CheckoutParser
from bot.utils.logger import get_logger

log = get_logger(__name__)


class CheckoutHandler:
    def __init__(
        self, adb: ADBClient, cache: XMLCache, product: ProductConfig
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._product = product

    async def execute(self) -> WorkflowState:
        # Coba dump — kalo timeout ya udah, gausa dipaksa
        tree = await self._cache.get(self._adb, force=True)
        if tree is None:
            log.warning("CHECKOUT: dump timeout — skip, loop lagi")
            return WorkflowState.OPEN_PRODUCT

        parser = CheckoutParser(self._cache)
        if not parser.is_checkout_page():
            log.warning("CHECKOUT: bukan halaman checkout — loop lagi")
            return WorkflowState.OPEN_PRODUCT

        # Cari tombol "Buat Pesanan" by TEXT
        el = parser.get_place_order_button()
        if el is None:
            log.warning("CHECKOUT: tombol Buat Pesanan gak ditemukan — loop lagi")
            return WorkflowState.OPEN_PRODUCT

        log.info("CHECKOUT: tap 'Buat Pesanan' via [%s] at (%d, %d)", el.resolved_via, el.tap_x, el.tap_y)

        # Tap — kalo screen berubah, berarti sukses
        await self._adb.tap(el.tap_x, el.tap_y)

        # Tunggu hasil 3 detik — cek apakah berubah ke payment/success
        await asyncio.sleep(3)
        tree = await self._cache.get(self._adb, force=True)
        if tree is None:
            # Kalo dump timeout di verify, ya udah — loop aja
            log.warning("CHECKOUT: verify dump timeout — loop lagi")
            return WorkflowState.OPEN_PRODUCT

        screen = CheckoutParser(self._cache).detect_screen()
        log.info("CHECKOUT: setelah tap screen = %s", screen.value)

        if screen in (ScreenType.PAYMENT_PAGE, ScreenType.ORDER_SUCCESS):
            log.info("CHECKOUT: berhasil -> %s", screen.value)
            return WorkflowState.VERIFY_PAYMENT

        log.info("CHECKOUT: tap gak mengubah screen — loop lagi")
        return WorkflowState.OPEN_PRODUCT
