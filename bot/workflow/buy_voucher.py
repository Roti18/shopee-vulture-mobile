"""State: BUY_VOUCHER — tap 'Beli Dengan Voucher', tunggu popup varian."""
from __future__ import annotations

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.actions import product_actions as pacts
from bot.actions import variant_actions as vacts
from bot.models.enums import WorkflowState
from bot.utils.logger import get_logger

log = get_logger(__name__)


class BuyVoucherHandler:
    def __init__(self, adb: ADBClient, cache: XMLCache) -> None:
        self._adb = adb
        self._cache = cache

    async def execute(self) -> WorkflowState:
        # Tap tombol "Beli Dengan Voucher"
        ok = await pacts.tap_buy_now(self._adb, self._cache)
        if not ok:
            log.error("BUY_VOUCHER: tap_buy_now gagal")
            return WorkflowState.RECOVERY

        # Tunggu popup varian muncul
        appeared = await vacts.wait_for_variant_popup(
            self._adb, self._cache, max_wait=8.0
        )
        if not appeared:
            log.warning("BUY_VOUCHER: popup varian tidak muncul — redirect URL")
            return WorkflowState.OPEN_PRODUCT

        return WorkflowState.CHECK_VARIANT
