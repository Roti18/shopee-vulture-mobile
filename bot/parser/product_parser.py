"""
ProductParser — identifikasi halaman produk dan cek stok Level 1.
"""
from __future__ import annotations

from bot.adb.xml_cache import XMLCache
from bot.models.enums import StockStatus, ScreenType
from bot.parser.base_parser import BaseParser, ResolvedElement
from bot.ui import product_selectors as sel
from bot.utils.logger import get_logger

log = get_logger(__name__)


class ProductParser(BaseParser):
    def __init__(self, cache: XMLCache) -> None:
        super().__init__(cache)

    # ------------------------------------------------------------------ #
    # Screen detection
    # ------------------------------------------------------------------ #

    def is_product_page(self) -> bool:
        """
        Halaman produk terdeteksi jika tombol BUY_NOW ada.
        Digunakan oleh recovery untuk identifikasi screen.
        """
        return self.has_element(sel.BUY_NOW_BUTTON)

    # ------------------------------------------------------------------ #
    # Stock check Level 1
    # ------------------------------------------------------------------ #

    def check_stock(self) -> StockStatus:
        """
        Level 1 stock check di halaman produk.

        Logic:
        - Cari elemen dengan text 'Habis' (STOCK_OUT_LABEL)
        - Jika ditemukan → OUT_OF_STOCK
        - Jika tidak → IN_STOCK (lanjut ke BUY_VOUCHER)
        """
        habis_el = self.resolve(sel.STOCK_OUT_LABEL)
        if habis_el is not None:
            log.info("Stok HABIS (Level 1 — product page)")
            return StockStatus.OUT_OF_STOCK

        # Cek fallback: ada link "Habis? Temukan Produk Lainnya"
        if self.has_element(sel.FIND_OTHER_PRODUCTS):
            log.info("Stok HABIS (Level 1 — fallback: find_other_products)")
            return StockStatus.OUT_OF_STOCK

        log.info("Stok tersedia (Level 1 — lanjut ke BUY_VOUCHER)")
        return StockStatus.IN_STOCK

    # ------------------------------------------------------------------ #
    # Element resolvers (dipakai oleh action layer)
    # ------------------------------------------------------------------ #

    def get_buy_now_button(self) -> ResolvedElement | None:
        el = self.resolve(sel.BUY_NOW_BUTTON)
        if el is None:
            log.error("BUY_NOW_BUTTON tidak ditemukan di halaman produk")
        return el

    def get_back_button(self) -> ResolvedElement | None:
        return self.resolve(sel.BACK_BUTTON)

    # ------------------------------------------------------------------ #
    # Product info
    # ------------------------------------------------------------------ #

    def get_product_name(self) -> str:
        el = self.resolve(sel.PRODUCT_NAME_LABEL)
        if el and el.element is not None:
            return el.element.get("text", "").lstrip("0")
        return ""
