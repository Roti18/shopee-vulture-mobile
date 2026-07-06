"""State: VERIFY_PAYMENT — tunggu konfirmasi order setelah tap 'Buat Pesanan'."""
from __future__ import annotations

from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.actions import checkout_actions as cacts
from bot.models.enums import WorkflowState, ScreenType
from bot.utils.logger import get_logger

log = get_logger(__name__)


class VerifyPaymentHandler:
    def __init__(self, adb: ADBClient, cache: XMLCache) -> None:
        self._adb = adb
        self._cache = cache

    async def execute(self) -> WorkflowState:
        screen = await cacts.wait_for_order_result(
            self._adb, self._cache, max_wait=20.0
        )

        if screen == ScreenType.ORDER_SUCCESS:
            log.info("VERIFY_PAYMENT: order success")
            return WorkflowState.CREATE_ORDER

        if screen == ScreenType.PAYMENT_PAGE:
            log.info("VERIFY_PAYMENT: halaman pembayaran → order dibuat")
            return WorkflowState.CREATE_ORDER

        log.warning("VERIFY_PAYMENT: screen tidak dikenali (%s)", screen.value)
        return WorkflowState.RECOVERY
