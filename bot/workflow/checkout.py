"""State: CHECKOUT — verifikasi checkout page dan buat pesanan."""
from __future__ import annotations

import asyncio

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.actions import checkout_actions as cacts
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
        # Refresh dump
        self._cache.invalidate()
        await self._cache.get(self._adb)

        parser = CheckoutParser(self._cache)
        if not parser.is_checkout_page():
            log.error("CHECKOUT: bukan halaman checkout, recovery")
            return WorkflowState.RECOVERY

        # Log total pembayaran di bottom panel
        total = parser.get_total_payment()
        log.info("CHECKOUT: total pembayaran = %s", total)

        # Cari tombol Buat Pesanan
        el = parser.get_place_order_button()
        if el is None:
            log.error("CHECKOUT: tombol 'Buat Pesanan' tidak ditemukan")
            return WorkflowState.RECOVERY

        # ── Tap Loop 0.5s ──────────────────────────────────────────────────
        # Tap setiap 0.5 detik sampai berhasil.
        # User tinggal /stop kalo mau berhenti.
        while True:
            log.info(
                "Tap 'Buat Pesanan' via [%s] at (%d, %d)",
                el.resolved_via, el.tap_x, el.tap_y
            )
            await self._adb.tap(el.tap_x, el.tap_y)
            await asyncio.sleep(0.5)

            self._cache.invalidate()
            tree = await self._cache.get(self._adb)
            if tree is None:
                continue

            screen = CheckoutParser(self._cache).detect_screen()
            log.info("CHECKOUT: screen = %s", screen.value)

            if screen in (ScreenType.PAYMENT_PAGE, ScreenType.ORDER_SUCCESS):
                log.info("CHECKOUT: berhasil -> %s", screen.value)
                return WorkflowState.VERIFY_PAYMENT
