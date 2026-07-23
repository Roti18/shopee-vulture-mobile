"""State: CHECKOUT — tap 'Buat Pesanan' terus sampe berhasil.

Submit di CHECK_VARIANT udah bekerja — kita PASTI udah di checkout page.
Gausa verify/cek apa-apa, gausa dump, langsung tap loop aja.
uiautomator sering timeout di checkout page (WebView berat) — jangan di-treat
sebagai kegagalan. Tinggal tap terus.
"""
from __future__ import annotations

import asyncio

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.models.enums import WorkflowState, ScreenType
from bot.models.product import ProductConfig
from bot.parser.checkout_parser import CheckoutParser
from bot.utils.logger import get_logger

log = get_logger(__name__)

# Hardcoded fallback: tombol "Buat Pesanan" selalu di bottom-center.
# Dipake kalo parser gagal resolve (dump timeout di checkout page).
_FALLBACK_TAP_X = 540
_FALLBACK_TAP_Y = 2180


class CheckoutHandler:
    def __init__(
        self, adb: ADBClient, cache: XMLCache, product: ProductConfig
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._product = product

    async def execute(self) -> WorkflowState:
        # Coba resolve tombol "Buat Pesanan" sekali — kalo dump gagal
        # pake hardcoded fallback. Gausa verify checkout page, submit
        # di CHECK_VARIANT udah pasti bekerja.
        await self._cache.get(self._adb, force=True)
        el = CheckoutParser(self._cache).get_place_order_button()

        if el is not None:
            tap_x, tap_y = el.tap_x, el.tap_y
            via = el.resolved_via
        else:
            tap_x, tap_y = _FALLBACK_TAP_X, _FALLBACK_TAP_Y
            via = "hardcoded_fallback"
            log.warning("CHECKOUT: tombol ga resolve, pake hardcoded (%d, %d)", tap_x, tap_y)

        # ── Tap Loop — tap terus sampe screen berubah ──────────────────
        while True:
            log.info("CHECKOUT: tap via [%s] at (%d, %d)", via, tap_x, tap_y)
            await self._adb.tap(tap_x, tap_y)
            await asyncio.sleep(0.8)

            # Polling cache — kalo gagal (None) ya lanjut tap aja
            tree = await self._cache.get(self._adb)
            if tree is None:
                continue

            screen = CheckoutParser(self._cache).detect_screen()
            log.info("CHECKOUT: screen = %s", screen.value)

            if screen in (ScreenType.PAYMENT_PAGE, ScreenType.ORDER_SUCCESS):
                log.info("CHECKOUT: berhasil -> %s", screen.value)
                return WorkflowState.VERIFY_PAYMENT
