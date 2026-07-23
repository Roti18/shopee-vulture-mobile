"""State: CHECKOUT — spam tombol 'Buat Pesanan' lalu lanjut verifikasi order."""
from __future__ import annotations

from bot.adb import screencap
from bot.adb.client import ADBClient
from bot.adb.xml_cache import XMLCache
from bot.actions import checkout_actions as cacts
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import WorkflowState, ScreenType
from bot.utils.logger import get_logger

log = get_logger(__name__)


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
        await cacts.wait_for_checkout_page(self._adb, self._cache, max_wait=6.0)

        screen = await cacts.spam_confirm_order(
            self._adb,
            self._cache,
            max_taps=12,
            tap_interval=0.12,
        )

        if screen == ScreenType.UNKNOWN:
            screenshot_path = await screencap.capture(self._adb)
            log.error(
                "CHECKOUT: tombol 'Buat Pesanan' gagal diproses, screenshot=%s",
                screenshot_path,
            )
            return WorkflowState.RECOVERY

        return WorkflowState.VERIFY_PAYMENT
