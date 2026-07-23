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
        # Tunggu halaman checkout beneran muncul — polling aja, gausa force dump
        # yang gampang timeout. Kalo submit udah di-tap, ini pasti landing
        # (kecuali Shopee error, yg bakal ketangkep di tap loop bawah).
        arrived = await cacts.wait_for_checkout_page(
            self._adb, self._cache, max_wait=12.0
        )
        if not arrived:
            log.warning("CHECKOUT: halaman checkout tidak muncul dalam 12s")
            # Jangan langsung RECOVERY — kemungkinan submit gagal,
            # back ke halaman produk, coba dari BUY_VOUCHER lagi
            await self._adb.press_back()
            await asyncio.sleep(0.5)
            return WorkflowState.BUY_VOUCHER

        parser = CheckoutParser(self._cache)

        # Log total pembayaran di bottom panel
        total = parser.get_total_payment()
        log.info("CHECKOUT: total pembayaran = %s", total)

        # ── Tap Loop 0.8s — Tap "Buat Pesanan" terus sampai order terkirim ──
        # Gausa resolve element di loop — tombolnya anchored di bottom,
        # tap koordinat tetap tiap iterasi. Kalo screen berubah ke payment/success
        # yg disengaja atau tidak, lanjut verify.
        el = parser.get_place_order_button()
        if el is None:
            log.error("CHECKOUT: tombol 'Buat Pesanan' tidak ditemukan")
            return WorkflowState.RECOVERY

        while True:
            log.info(
                "Tap 'Buat Pesanan' via [%s] at (%d, %d)",
                el.resolved_via, el.tap_x, el.tap_y
            )
            await self._adb.tap(el.tap_x, el.tap_y)
            await asyncio.sleep(0.8)

            tree = await self._cache.get(self._adb)
            if tree is None:
                continue

            screen = CheckoutParser(self._cache).detect_screen()
            log.info("CHECKOUT: screen = %s", screen.value)

            if screen in (ScreenType.PAYMENT_PAGE, ScreenType.ORDER_SUCCESS):
                log.info("CHECKOUT: berhasil -> %s", screen.value)
                return WorkflowState.VERIFY_PAYMENT
