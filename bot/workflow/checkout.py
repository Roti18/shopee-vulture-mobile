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
        # LANGSUNG TAP LOOP. Gausa dump, gausa resolve, gausa apa-apa.
        # Kalo user udah tap submit di CHECK_VARIANT, dia PASTI di checkout.
        # Dump cuma bikin timeout 10s + redirect loop.
        tap_x, tap_y = _FALLBACK_TAP_X, _FALLBACK_TAP_Y
        via = "hardcoded_fallback"

        # ── Tap Loop — tap "Buat Pesanan" 8× (≈10 detik) ─────────────
        # Abis itu lanjut VERIFY_PAYMENT, gausa nunggu screen detect —
        # kalo sukses ya sukses, kalo gagal ketangkep di CREATE_ORDER.
        for i in range(8):
            log.info("CHECKOUT: tap [%s] at (%d, %d) #%d", via, tap_x, tap_y, i + 1)
            await self._adb.tap(tap_x, tap_y)
            await asyncio.sleep(1.2)

        log.info("CHECKOUT: 8× tap selesai — lanjut VERIFY_PAYMENT")
        return WorkflowState.VERIFY_PAYMENT
