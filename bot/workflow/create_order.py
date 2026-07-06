"""State: CREATE_ORDER — screenshot, Telegram notif, update stats."""
from __future__ import annotations

import asyncio
import time

from bot.adb.client import ADBClient
from bot.adb import screencap
from bot.adb.xml_cache import XMLCache
from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import WorkflowState, ScreenType
from bot.models.product import ProductConfig
from bot.parser.checkout_parser import CheckoutParser
from bot.utils.logger import get_logger

log = get_logger(__name__)


class CreateOrderHandler:
    def __init__(
        self,
        adb: ADBClient,
        cache: XMLCache,
        bus: EventBus,
        runtime: BotRuntimeState,
        product: ProductConfig,
        get_sleep_after_success_fn,
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._bus = bus
        self._runtime = runtime
        self._product = product
        self._get_sleep_after_success = get_sleep_after_success_fn

    async def execute(self) -> WorkflowState:
        log.info("CREATE_ORDER: mengambil screenshot dan notifikasi")

        # Tunggu sampai layar berubah dari checkout_page
        t0 = time.monotonic()
        max_wait = 10.0
        parser = None

        while (time.monotonic() - t0) < max_wait:
            tree = await self._cache.get(self._adb)
            if tree is None:
                await asyncio.sleep(0.3)
                continue

            parser = CheckoutParser(self._cache)
            screen = parser.detect_screen()
            log.info("CreateOrder: mendeteksi screen = %s", screen.value)

            if screen in (ScreenType.PAYMENT_PAGE, ScreenType.ORDER_SUCCESS):
                log.info("CreateOrder: layar %s terdeteksi, siap ambil screenshot", screen.value)
                break

            if screen != ScreenType.CHECKOUT_PAGE:
                # Layar loading atau unknown, tunggu sebentar agar render selesai
                log.info("CreateOrder: layar bukan checkout (%s), tunggu transisi...", screen.value)
                await asyncio.sleep(0.8)
                await self._cache.get(self._adb)
                parser = CheckoutParser(self._cache)
                break

            await asyncio.sleep(0.2)

        # Screenshot diambil setelah dipastikan sudah beralih dari checkout_page
        screenshot_path = await screencap.capture(self._adb)

        # Info dari parser (sudah terisi dari dump terakhir di loop)
        if parser is None:
            parser = CheckoutParser(self._cache)
        subtitle = parser.get_order_success_subtitle()

        # Emit event — Telegram + Logger + Stats dihandle subscriber
        await self._bus.emit(
            ev.OrderSuccessEvent(
                product_name=self._product.name,
                variant=self._product.variant,
                price=subtitle or "—",
                screenshot_path=screenshot_path,
            )
        )

        # Cek sleep setelah checkout sukses
        if self._get_sleep_after_success():
            log.info("CREATE_ORDER: sleep_after_success aktif → mematikan layar HP dan pause bot")
            # Kirim keyevent 26 (Power/Lock) untuk mematikan layar HP
            await self._adb.key(26)
            
            # Ubah mode bot ke PAUSED agar monitoring berhenti
            from bot.models.enums import BotMode
            self._runtime.mode = BotMode.PAUSED
            return WorkflowState.IDLE

        # Cek restock limit
        purchase_count = self._runtime.stats.purchase_count_session
        restock_limit = self._product.restock_limit
        log.info(
            "CREATE_ORDER: pembelian sesi ini %d/%d", purchase_count, restock_limit
        )

        if purchase_count >= restock_limit:
            log.info("CREATE_ORDER: restock limit tercapai → COOLDOWN")
            return WorkflowState.COOLDOWN

        return WorkflowState.OPEN_PRODUCT
