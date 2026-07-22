"""
State: OPEN_PRODUCT — buka URL produk, polling cepat tombol voucher, tap, tunggu popup.

Optimasi kecepatan v3:
  - Skip L1 (wait_for_product_page) — polling cepat BUY_NOW_BUTTON (0.3s interval)
  - Gabung dengan BUY_VOUCHER jadi 1 state — hemat 1 transisi state machine
  - Begitu popup varian muncul → langsung CHECK_VARIANT
"""
from __future__ import annotations

import asyncio
import time

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.actions import product_actions as acts
from bot.actions import variant_actions as vacts
from bot.models.enums import WorkflowState
from bot.models.product import ProductConfig
from bot.parser.product_parser import ProductParser
from bot.utils.logger import get_logger

log = get_logger(__name__)


class OpenProductHandler:
    def __init__(
        self,
        adb: ADBClient,
        cache: XMLCache,
        product: ProductConfig,
        save_product_fn=None,
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._product = product
        self._save_product = save_product_fn

    async def execute(self) -> WorkflowState:
        log.info("OPEN_PRODUCT: %s", self._product.url)

        if not self._product.url:
            log.error("OPEN_PRODUCT: URL produk belum diset")
            return WorkflowState.RECOVERY

        ok = await self._adb.open_url(self._product.url)
        if not ok:
            log.error("OPEN_PRODUCT: gagal open URL")
            return WorkflowState.RECOVERY

        # Polling cepet sampai tombol "Beli Dengan Voucher" muncul
        # (bukan L1 — cuma cari 1 elemen, gak nunggu halaman penuh)
        arrived = await self._wait_for_buy_now(max_wait=8.0)
        if not arrived:
            log.warning("OPEN_PRODUCT: tombol Beli Dengan Voucher tidak muncul dalam 8s")
            return WorkflowState.RECOVERY

        # Fetch nama produk dari halaman untuk /status (cuma sekali tiap siklus)
        await self._fetch_product_name()

        # Tap "Beli Dengan Voucher"
        ok = await acts.tap_buy_now(self._adb, self._cache)
        if not ok:
            log.warning("OPEN_PRODUCT: tap_buy_now gagal, retry 1x")
            self._cache.invalidate()
            ok = await acts.tap_buy_now(self._adb, self._cache)
            if not ok:
                log.error("OPEN_PRODUCT: tap_buy_now gagal 2x")
                return WorkflowState.RECOVERY

        # Tunggu popup varian muncul
        appeared = await vacts.wait_for_variant_popup(
            self._adb, self._cache, max_wait=8.0
        )
        if not appeared:
            log.warning("OPEN_PRODUCT: popup varian tidak muncul")
            return WorkflowState.RECOVERY

        return WorkflowState.CHECK_VARIANT

    async def _wait_for_buy_now(self, max_wait: float = 8.0) -> bool:
        """Polling 0.3s interval sampe tombol Beli Dengan Voucher muncul di XML."""
        t0 = time.monotonic()
        # Kasih waktu awal biar app mulai load
        await asyncio.sleep(0.3)

        # First dump FORCE — URL baru dibuka, cache pasti stale.
        tree = await self._cache.get(self._adb, force=True)
        if tree is not None:
            if ProductParser(self._cache).get_buy_now_button() is not None:
                log.info("Tombol Beli Dengan Voucher terdeteksi (%.1fs)", time.monotonic() - t0)
                return True

        while (time.monotonic() - t0) < max_wait:
            tree = await self._cache.get(self._adb)
            if tree is not None:
                if ProductParser(self._cache).get_buy_now_button() is not None:
                    log.info("Tombol Beli Dengan Voucher terdeteksi (%.1fs)", time.monotonic() - t0)
                    return True
            await asyncio.sleep(0.3)

        return False

    async def _fetch_product_name(self) -> None:
        """Parse nama produk dari halaman simpan ke config biar /status muncul."""
        try:
            parser = ProductParser(self._cache)
            name = parser.get_product_name()
            if name and name != self._product.name:
                self._product.name = name
                if self._save_product:
                    await self._save_product("product.name", name)
                    log.info("Produk terdeteksi: %s", name)
        except Exception as exc:
            log.debug("Gagal fetch nama produk: %s", exc)
