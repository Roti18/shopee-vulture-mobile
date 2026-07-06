"""
Tiered Recovery — 5 level dari paling ringan ke paling drastis.

Level 1: Soft retry — dump XML ulang, identifikasi screen
Level 2: ADB reconnect — disconnect + connect ulang
Level 3: Force stop app — am force-stop Shopee → buka ulang
Level 4: Restart ADB server — kill-server + start-server
Level 5: Panic — stop bot, notifikasi Telegram
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
        self._l1_attempts = 0

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
            self._l1_attempts = 0
            await self._bus.emit(ev.RecoverySuccessEvent(level=level))
        else:
            # Jika di level L1, coba hingga 3 kali sebelum naik ke L2 (menghindari force-stop saat lag jaringan)
            if level == RecoveryLevel.L1_SOFT_RETRY:
                self._l1_attempts += 1
                log.info("Recovery L1 gagal: percobaan %d/3 sebelum eskalasi ke L2", self._l1_attempts)
                # Exponential backoff: 1s, 2s, diikuti sleep 1s setelah retry
                backoff = 2 ** (self._l1_attempts - 1)
                await asyncio.sleep(backoff)
                if self._l1_attempts < 3:
                    # Tetap di L1
                    return WorkflowState.RECOVERY

            # Reset counter L1 jika eskalasi level
            self._l1_attempts = 0
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
        Dump XML ulang → identifikasi screen saat ini.
        Jika product page → lanjut normal.
        Jika bukan → coba open URL.
        """
        log.info("Recovery L1: dump XML ulang")
        tree = await self._cache.get(self._adb, force=True)
            log.warning("Recovery L1: dump gagal → naik L2")
            return WorkflowState.RECOVERY

        screen = self._detect_current_screen()
        log.info("Recovery L1: screen terdeteksi = %s", screen.value)

        if screen == ScreenType.PRODUCT_PAGE:
            return WorkflowState.BUY_VOUCHER
        if screen == ScreenType.VARIANT_POPUP:
            log.info("Recovery L1: Terdeteksi popup varian terbuka. Lanjut ke CHECK_VARIANT.")
            return WorkflowState.CHECK_VARIANT
        if screen == ScreenType.CHECKOUT_PAGE:
            log.info("Recovery L1: Terdeteksi halaman checkout. Lanjut ke CHECKOUT.")
            return WorkflowState.CHECKOUT
        if screen == ScreenType.PAYMENT_PAGE:
            log.info("Recovery L1: Terdeteksi halaman payment. Lanjut ke CREATE_ORDER.")
            return WorkflowState.CREATE_ORDER
        if screen == ScreenType.ORDER_SUCCESS:
            log.info("Recovery L1: Terdeteksi order sukses. Lanjut ke CREATE_ORDER.")
            return WorkflowState.CREATE_ORDER

        # Buka ulang URL produk
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
        self._cache.invalidate()
        tree = await self._cache.get(self._adb, force=True)
        if tree is not None:
            screen = self._detect_current_screen()
            log.info("Recovery L2: setelah reconnect, screen terdeteksi = %s", screen.value)
            if screen == ScreenType.CHECKOUT_PAGE:
                return WorkflowState.CHECKOUT
            if screen == ScreenType.PAYMENT_PAGE:
                return WorkflowState.CREATE_ORDER
            if screen == ScreenType.ORDER_SUCCESS:
                return WorkflowState.CREATE_ORDER
            if screen == ScreenType.VARIANT_POPUP:
                return WorkflowState.CHECK_VARIANT
            if screen == ScreenType.PRODUCT_PAGE:
                return WorkflowState.BUY_VOUCHER

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
