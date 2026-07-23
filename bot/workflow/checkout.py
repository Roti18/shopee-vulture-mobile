"""State: CHECKOUT — cari teks 'Buat Pesanan' di XML, tap itu.
Kalo dump timeout, pake koordinat submit button dari check_variant."""
from __future__ import annotations

import asyncio

from bot.adb.client import ADBClient
from bot.adb.dumper import center_of_bounds
from bot.adb.xml_cache import XMLCache
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import WorkflowState
from bot.utils.logger import get_logger

log = get_logger(__name__)

TARGET_TEXT = "Buat Pesanan"


class CheckoutHandler:
    def __init__(
        self, adb: ADBClient, cache: XMLCache, product=None,
        runtime: BotRuntimeState = None,
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._product = product
        self._runtime = runtime

    async def execute(self) -> WorkflowState:
        # Coba cari "Buat Pesanan" di XML
        tree = await self._cache.get(self._adb, force=True)
        if tree is not None:
            for node in self._cache.all_nodes():
                text = node.get("text", "") or node.get("content-desc", "")
                if TARGET_TEXT in text:
                    bounds = node.get("bounds", "")
                    center = center_of_bounds(bounds)
                    if center:
                        cx, cy = center
                        log.info("CHECKOUT: tap '%s' at (%d, %d)", text, cx, cy)
                        await self._adb.tap(cx, cy)
                        await asyncio.sleep(2)
                        return WorkflowState.VERIFY_PAYMENT

            log.warning("CHECKOUT: teks '%s' gak ditemukan di XML", TARGET_TEXT)
        else:
            log.warning("CHECKOUT: dump timeout")

        # Fallback: pake koordinat submit button dari check_variant
        if self._runtime and self._runtime.last_submit_x > 0:
            sx, sy = self._runtime.last_submit_x, self._runtime.last_submit_y
            log.info("CHECKOUT: tap fallback koordinat submit (%d, %d)", sx, sy)
            await self._adb.tap(sx, sy)
            await asyncio.sleep(2)
            return WorkflowState.VERIFY_PAYMENT

        log.warning("CHECKOUT: gak ada fallback — loop lagi")
        return WorkflowState.OPEN_PRODUCT
