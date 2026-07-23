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
from bot.models.enums import WorkflowState
from bot.utils.logger import get_logger

log = get_logger(__name__)

# Hardcoded: tombol "Buat Pesanan" di bottom.
# Submit aja di (540, 2236), "Buat Pesanan" lebih bawah.
_FALLBACK_TAP_X = 540
_FALLBACK_TAP_Y = 2260


class CheckoutHandler:
    def __init__(
        self, adb: ADBClient, cache: XMLCache, product: ProductConfig
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._product = product

    async def execute(self) -> WorkflowState:
        # LANGSUNG TAP LOOP. Gausa dump, gausa resolve, gausa verify.
        # Submit CHECK_VARIANT udah bekerja — user PASTI di checkout.
        tap_x, tap_y = _FALLBACK_TAP_X, _FALLBACK_TAP_Y
        via = "checkout_hardcoded"

        # ── Tap "Buat Pesanan" 30× (≈45 detik) ───────────────────────
        # 45 detik cukup buat Shopee proses order.
        # Kalo sukses ya sukses, user liat sendiri di HP.
        # Gausa VERIFY_PAYMENT/CREATE_ORDER — uiautomator selalu timeout
        # di WebView checkout, verify gagal + false positive dari stale cache.
        for i in range(30):
            log.info("CHECKOUT: tap (%d, %d) #%d", tap_x, tap_y, i + 1)
            await self._adb.tap(tap_x, tap_y)
            await asyncio.sleep(1.5)

        log.info("CHECKOUT: 30× tap selesai — loop lagi")
        return WorkflowState.OPEN_PRODUCT
