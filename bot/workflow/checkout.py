"""State: CHECKOUT — cari teks "Beli Sekarang" di XML, tap itu."""
from __future__ import annotations

import asyncio

from bot.adb.client import ADBClient
from bot.adb.dumper import center_of_bounds
from bot.adb.xml_cache import XMLCache
from bot.models.enums import WorkflowState
from bot.utils.logger import get_logger

log = get_logger(__name__)

TARGET_TEXT = "Buat Pesanan"


class CheckoutHandler:
    def __init__(
        self, adb: ADBClient, cache: XMLCache, product=None,
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._product = product

    async def execute(self) -> WorkflowState:
        tree = await self._cache.get(self._adb, force=True)
        if tree is None:
            log.warning("CHECKOUT: dump timeout — loop lagi")
            return WorkflowState.OPEN_PRODUCT

        # Cari node dengan teks "Beli Sekarang"
        for node in self._cache.all_nodes():
            text = node.get("text", "") or node.get("content-desc", "")
            if TARGET_TEXT in text:
                bounds = node.get("bounds", "")
                center = center_of_bounds(bounds)
                if center:
                    cx, cy = center
                    log.info("CHECKOUT: tap '%s' at (%d, %d) — bounds: %s", text, cx, cy, bounds)
                    await self._adb.tap(cx, cy)
                    await asyncio.sleep(2)
                    return WorkflowState.VERIFY_PAYMENT

        log.warning("CHECKOUT: teks '%s' gak ditemukan — loop lagi", TARGET_TEXT)
        return WorkflowState.OPEN_PRODUCT
