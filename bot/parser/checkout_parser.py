"""
CheckoutParser — identifikasi halaman checkout, payment, dan order success.
"""
from __future__ import annotations

from bot.adb.xml_cache import XMLCache
from bot.models.enums import ScreenType
from bot.parser.base_parser import BaseParser, ResolvedElement
from bot.ui import checkout_selectors as sel
from bot.utils.logger import get_logger

log = get_logger(__name__)


class CheckoutParser(BaseParser):
    def __init__(self, cache: XMLCache) -> None:
        super().__init__(cache)

    # ------------------------------------------------------------------ #
    # Screen detection
    # ------------------------------------------------------------------ #

    def detect_screen(self) -> ScreenType:
        """
        Identifikasi screen saat ini berdasarkan elemen yang ada.
        Urutan: order_success > payment_page > checkout_page
        """
        if self.is_order_success():
            return ScreenType.ORDER_SUCCESS
        if self.is_payment_page():
            return ScreenType.PAYMENT_PAGE
        if self.is_checkout_page():
            return ScreenType.CHECKOUT_PAGE
        return ScreenType.UNKNOWN

    def is_checkout_page(self) -> bool:
        el = self.resolve(sel.CHECKOUT_TITLE)
        if el and el.element is not None:
            return el.element.get("text", "") == "Checkout"
        return False

    def is_payment_page(self) -> bool:
        return self.has_element(sel.PAYMENT_PAGE_TITLE)

    def is_order_success(self) -> bool:
        el = self.resolve(sel.ORDER_SUCCESS_TITLE)
        if el is None:
            return False
        if el.element is not None:
            text = el.element.get("text", "")
            return any(t in text for t in sel.SUCCESS_TEXTS)
        # Level 7 (hardcoded) tidak relevan di sini
        return False

    # ------------------------------------------------------------------ #
    # Element resolvers
    # ------------------------------------------------------------------ #

    def get_place_order_button(self) -> ResolvedElement | None:
        el = self.resolve(sel.PLACE_ORDER_BUTTON)
        if el is not None:
            return el

        # Fallback: cari teks "Buat Pesanan" di seluruh node
        log.warning("get_place_order_button: tombol utama tidak ditemukan, menggunakan fallback pencarian teks")
        for node in self._cache.all_nodes():
            text = node.get("text", "")
            if "buat pesanan" in text.lower():
                bounds = node.get("bounds", "")
                if bounds:
                    from bot.parser.base_parser import parse_bounds
                    x, y = parse_bounds(bounds)
                    return ResolvedElement(
                        element=node,
                        tap_x=x,
                        tap_y=y,
                        resolved_via="text_fallback"
                    )
        return None

    def get_back_button(self) -> ResolvedElement | None:
        return self.resolve(sel.BACK_BUTTON)

    def get_order_total(self) -> str:
        el = self.resolve(sel.ORDER_TOTAL_LABEL)
        if el and el.element is not None:
            return el.element.get("text", "")
        return ""

    def get_total_payment(self) -> str:
        el = self.resolve(sel.TOTAL_PAYMENT_LABEL)
        if el and el.element is not None:
            return el.element.get("text", "")
        return ""

    def get_order_success_subtitle(self) -> str:
        el = self.resolve(sel.ORDER_SUCCESS_SUBTITLE)
        if el and el.element is not None:
            return el.element.get("text", "")
        return ""
