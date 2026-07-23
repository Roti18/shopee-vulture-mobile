"""
Tiered Recovery — 5 level dari paling ringan ke paling drastis.

Level 1: Coba dump XML. Kalo gagal karena lag → langsung redirect URL.
         Kalo berhasil & detected checkout/payment/success → lanjut normal.
         Kalo berhasil & screen lain → redirect URL.
Level 2: ADB reconnect — disconnect + connect ulang (ADB beneran mati)
Level 3: Force stop app — am force-stop Shopee → buka ulang
Level 4: Restart ADB server — kill-server + start-server
Level 5: Panic — stop bot, notifikasi Telegram

Prinsip: LAG != ADB MATI. Jangan waste time retry 3× + reconnect kalo cuma lag.
"""
from __future__ import annotations

import asyncio

from bot.adb.client import ADBClient, SHOPEE_PACKAGE
from bot.adb.xml_cache import XMLCache
from bot.events.bus import EventBus
from bot.events import events as ev
from bot.models.bot_state import BotRuntimeState
from bot.models.enums import BotMode, RecoveryLevel, WorkflowState, ScreenType
from bot.parser.product_parser import ProductParser
from bot.parser.variant_parser import VariantParser
from bot.parser.checkout_parser import CheckoutParser
from bot.models.product import ProductConfig
from bot.utils.logger import get_logger

log = get_logger(__name__)

MAX_RECOVERY_LEVEL = RecoveryLevel.L5_PANIC


class TieredRecovery:
    def __init__(
        self,
        adb: ADBClient,
        cache: XMLCache,
        bus: EventBus,
        runtime: BotRuntimeState,
        product: ProductConfig,
    ) -> None:
        self._adb = adb
        self._cache = cache
        self._bus = bus
        self._runtime = runtime
        self._product = product

    async def recover(self) -> WorkflowState:
        level = self._runtime.recovery_level
        log.warning("Recovery dimulai: Level %d", level.value)

        await self._bus.emit(
            ev.RecoveryStartedEvent(
                level=level,
                reason=f"State: {self._runtime.workflow_state.value}",
            )
        )

        result = await self._dispatch(level)

        if result != WorkflowState.RECOVERY:
            # Recovery berhasil — reset level
            self._runtime.recovery_level = RecoveryLevel.L1_SOFT_RETRY
            await self._bus.emit(ev.RecoverySuccessEvent(level=level))
        else:
            # Naikkan level untuk percobaan berikutnya
            next_level_val = min(level.value + 1, MAX_RECOVERY_LEVEL.value)
            self._runtime.recovery_level = RecoveryLevel(next_level_val)

        return result

    async def _dispatch(self, level: RecoveryLevel) -> WorkflowState:
        match level:
            case RecoveryLevel.L1_SOFT_RETRY:
                return await self._l1_soft_retry()
            case RecoveryLevel.L2_ADB_RECONNECT:
                return await self._l2_adb_reconnect()
            case RecoveryLevel.L3_FORCE_STOP_APP:
                return await self._l3_force_stop_app()
            case RecoveryLevel.L4_RESTART_ADB_SERVER:
                return await self._l4_restart_adb_server()
            case RecoveryLevel.L5_PANIC:
                return await self._l5_panic()
            case _:
                return await self._l5_panic()

    # ------------------------------------------------------------------ #
    # Level 1 — Soft retry
    # ------------------------------------------------------------------ #

    async def _l1_soft_retry(self) -> WorkflowState:
        """
        Coba dump XML. Kalo gagal (lag) → langsung redirect URL.
        Kalo berhasil & detected checkout/payment/success → lanjut.
        Kalo berhasil & screen lain (product page, variant popup, unknown) → redirect URL.

        Prinsip: LAG != ADB MATI. Gausa retry 3× + reconnect cuma karena dump timeout.
        """
        log.info("Recovery L1: dump XML ulang")
        tree = await self._cache.get(self._adb, force=True)
        if tree is None:
            log.warning("Recovery L1: dump gagal (lag) → langsung redirect URL")
            return await self._open_product_url()

        screen = self._detect_current_screen()
        log.info("Recovery L1: screen terdeteksi = %s", screen.value)

        # Screen checkout/payment/success → lanjut normal
        if screen == ScreenType.CHECKOUT_PAGE:
            log.info("Recovery L1: Terdeteksi halaman checkout.")
            return WorkflowState.CHECKOUT
        if screen == ScreenType.PAYMENT_PAGE:
            log.info("Recovery L1: Terdeteksi halaman payment.")
            return WorkflowState.CREATE_ORDER
        if screen == ScreenType.ORDER_SUCCESS:
            log.info("Recovery L1: Terdeteksi order sukses.")
            return WorkflowState.CREATE_ORDER

        # Screen lain (termasuk product page & variant popup) → redirect URL
        # Alasannya: kalo sampe masuk recovery, state machine udah gak sinkron.
        # Mending redirect URL biar muter dari awal yang bener.
        return await self._open_product_url()

    # ------------------------------------------------------------------ #
    # Level 2 — ADB reconnect
    # ------------------------------------------------------------------ #

    async def _l2_adb_reconnect(self) -> WorkflowState:
        log.info("Recovery L2: ADB reconnect")
        ok = await self._adb.reconnect()
        if not ok:
            log.warning("Recovery L2: reconnect gagal → naik L3")
            return WorkflowState.RECOVERY
        await asyncio.sleep(2)
        tree = await self._cache.get(self._adb, force=True)
        if tree is None:
            log.warning("Recovery L2: dump gagal setelah reconnect — redirect URL")
            return await self._open_product_url()
        screen = self._detect_current_screen()
        log.info("Recovery L2: setelah reconnect, screen terdeteksi = %s", screen.value)
        # Screen checkout/payment/success → lanjut normal
        if screen == ScreenType.CHECKOUT_PAGE:
            return WorkflowState.CHECKOUT
        if screen == ScreenType.PAYMENT_PAGE:
            return WorkflowState.CREATE_ORDER
        if screen == ScreenType.ORDER_SUCCESS:
            return WorkflowState.CREATE_ORDER
        # Screen lain → redirect URL (state machine gak sinkron)
        return await self._open_product_url()

    # ------------------------------------------------------------------ #
    # Level 3 — Force stop app
    # ------------------------------------------------------------------ #

    async def _l3_force_stop_app(self) -> WorkflowState:
        log.info("Recovery L3: force-stop Shopee")
        await self._adb.force_stop(SHOPEE_PACKAGE)
        await asyncio.sleep(3)
        return await self._open_product_url()

    # ------------------------------------------------------------------ #
    # Level 4 — Restart ADB server
    # ------------------------------------------------------------------ #

    async def _l4_restart_adb_server(self) -> WorkflowState:
        log.info("Recovery L4: restart ADB server")
        await self._adb.kill_server()
        await self._adb.start_server()
        await asyncio.sleep(3)

        # Reconnect device
        if self._adb.wifi_host:
            await self._adb.connect_wifi()
        ok = await self._adb.is_connected()
        if not ok:
            log.error("Recovery L4: device tidak terhubung setelah restart server")
            return WorkflowState.RECOVERY

        return await self._open_product_url()

    # ------------------------------------------------------------------ #
    # Level 5 — Panic
    # ------------------------------------------------------------------ #

    async def _l5_panic(self) -> WorkflowState:
        log.critical("Recovery L5: PANIC — bot dihentikan")
        await self._bus.emit(
            ev.PanicEvent(reason="Recovery L5: semua level gagal, intervensi manual diperlukan")
        )
        self._runtime.mode = BotMode.STOPPED
        return WorkflowState.IDLE

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #

    async def _open_product_url(self) -> WorkflowState:
        if not self._product.url:
            log.error("Recovery: URL produk kosong")
            return WorkflowState.RECOVERY
        ok = await self._adb.open_url(self._product.url)
        if ok:
            await asyncio.sleep(3)
            return WorkflowState.OPEN_PRODUCT
        return WorkflowState.RECOVERY

    def _detect_current_screen(self) -> ScreenType:
        """Identifikasi screen saat ini menggunakan semua parser."""
        if ProductParser(self._cache).is_product_page():
            return ScreenType.PRODUCT_PAGE
        if VariantParser(self._cache).is_variant_popup_open():
            return ScreenType.VARIANT_POPUP
        checkout = CheckoutParser(self._cache)
        return checkout.detect_screen()
