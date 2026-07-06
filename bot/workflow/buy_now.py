"""State: BUY_NOW — tap 'Beli Sekarang' di popup varian."""
from __future__ import annotations

import asyncio

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.actions import variant_actions as vacts
from bot.actions import checkout_actions as cacts
from bot.models.enums import WorkflowState
from bot.utils.logger import get_logger

log = get_logger(__name__)


class BuyNowHandler:
    def __init__(self, adb: ADBClient, cache: XMLCache) -> None:
        self._adb = adb
        self._cache = cache

    async def execute(self) -> WorkflowState:
        # Tunggu halaman checkout (tindakan tap sudah dilakukan di check_variant secara instan)
        arrived = await cacts.wait_for_checkout_page(
            self._adb, self._cache, max_wait=15.0
        )
        if not arrived:
            log.warning("BUY_NOW: halaman checkout tidak muncul")
            return WorkflowState.RECOVERY

        return WorkflowState.CHECKOUT
