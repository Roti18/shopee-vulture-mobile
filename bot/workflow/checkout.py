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

        # ── Tap Sekali ──────────────────────────────────────────────────────
        # 1 tap doang. Kalau gagal, biar state machine + interval + recovery yang handle.
        log.info(
            "Tap 'Buat Pesanan' via [%s] at (%d, %d)",
            el.resolved_via, el.tap_x, el.tap_y
        )
        await self._adb.tap(el.tap_x, el.tap_y)

        # Tunggu sebentar, cek apakah layar udah pindah
        await asyncio.sleep(0.5)
        self._cache.invalidate()
        tree = await self._cache.get(self._adb)
        if tree is None:
            log.error("CHECKOUT: XML dump gagal setelah tap")
            return WorkflowState.RECOVERY

        parser = CheckoutParser(self._cache)
        screen = parser.detect_screen()

        if screen in (ScreenType.PAYMENT_PAGE, ScreenType.ORDER_SUCCESS):
            return WorkflowState.VERIFY_PAYMENT

        if screen == ScreenType.UNKNOWN:
            await asyncio.sleep(0.5)
            self._cache.invalidate()
            await self._cache.get(self._adb)
            screen = parser.detect_screen()
            if screen in (ScreenType.PAYMENT_PAGE, ScreenType.ORDER_SUCCESS):
                return WorkflowState.VERIFY_PAYMENT

        log.error("CHECKOUT: gagal, screen=%s", screen.value)
        return WorkflowState.RECOVERY
