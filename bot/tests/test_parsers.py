"""
Tests untuk parser layer menggunakan XML fixtures.
Mencakup:
  - ProductParser: stock check, element resolution, selector priority
  - VariantParser: stock check, VariantInfo, threshold logic, submit button
  - CheckoutParser: screen detection, element resolution
"""
from __future__ import annotations

import sys
from pathlib import Path
from xml.etree import ElementTree

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from bot.adb.xml_cache import XMLCache
from bot.models.enums import StockStatus
from bot.parser.product_parser import ProductParser
from bot.parser.variant_parser import VariantParser, VariantInfo
from bot.parser.checkout_parser import CheckoutParser

FIXTURES = Path(__file__).parent / "fixtures"


def _load_cache(xml_file: str) -> XMLCache:
    """Load XML fixture ke XMLCache."""
    tree = ElementTree.parse(FIXTURES / xml_file)
    cache = XMLCache()
    cache._tree = tree
    cache._cached_at = 9e18   # tidak akan expired
    return cache


# ── Product Parser ───────────────────────────────────────────────────────────

class TestProductParser:
    def test_is_product_page_when_buy_now_exists(self):
        cache = _load_cache("product_page_in_stock.xml")
        assert ProductParser(cache).is_product_page() is True

    def test_buy_now_button_resolved_via_resource_id(self):
        cache = _load_cache("product_page_in_stock.xml")
        el = ProductParser(cache).get_buy_now_button()
        assert el is not None
        assert el.resolved_via == "resource_id"
        # center of [540,2224][1080,2265]
        assert el.tap_x == 810
        assert el.tap_y == 2244

    def test_product_name_parsed(self):
        cache = _load_cache("product_page_out_of_stock.xml")
        name = ProductParser(cache).get_product_name()
        assert "Mykonos" in name


# ── Variant Parser ───────────────────────────────────────────────────────────

class TestVariantParser:
    def test_popup_detected(self):
        cache = _load_cache("variant_popup_mixed.xml")
        assert VariantParser(cache).is_variant_popup_open() is True

    def test_check_stock_in_stock_from_mixed(self):
        cache = _load_cache("variant_popup_mixed.xml")
        assert VariantParser(cache).check_stock() == StockStatus.IN_STOCK

    def test_find_variant_returns_variant_info(self):
        """find_variant_with_stock harus return VariantInfo dengan stock_count."""
        cache = _load_cache("variant_popup_mixed.xml")
        info = VariantParser(cache).find_variant_with_stock()
        assert info is not None
        assert isinstance(info, VariantInfo)
        assert info.stock_count > 0
        assert info.resolved_element is not None

    def test_variant_info_has_stock_count(self):
        cache = _load_cache("variant_popup_mixed.xml")
        info = VariantParser(cache).find_variant_with_stock()
        assert info.stock_count == 17

    def test_stock_mode_any_returns_variant(self):
        """stock_mode='any' → stock > 0 cukup."""
        cache = _load_cache("variant_popup_mixed.xml")
        info = VariantParser(cache).find_variant_with_stock(
            minimum_stock=1, stock_mode="any"
        )
        assert info is not None

    def test_stock_mode_minimum_below_threshold(self):
        """stock_mode='minimum', minimum_stock=100 → tidak ada yang memenuhi."""
        cache = _load_cache("variant_popup_mixed.xml")
        info = VariantParser(cache).find_variant_with_stock(
            minimum_stock=100, stock_mode="minimum"
        )
        assert info is None    # stok 17 < 100

    def test_stock_mode_minimum_above_threshold(self):
        """stock_mode='minimum', minimum_stock=10 → stok 17 memenuhi."""
        cache = _load_cache("variant_popup_mixed.xml")
        info = VariantParser(cache).find_variant_with_stock(
            minimum_stock=10, stock_mode="minimum"
        )
        assert info is not None
        assert info.stock_count >= 10

    def test_get_all_stock_counts(self):
        cache = _load_cache("variant_popup_mixed.xml")
        counts = VariantParser(cache).get_all_stock_counts()
        assert 17 in counts
        assert 0 in counts

    def test_submit_button_ready(self):
        cache = _load_cache("variant_popup_mixed.xml")
        assert VariantParser(cache).is_submit_ready() is True

    def test_submit_button_text(self):
        cache = _load_cache("variant_popup_mixed.xml")
        assert "Beli Sekarang" in VariantParser(cache).get_submit_button_text()

    def test_close_button_resolved(self):
        cache = _load_cache("variant_popup_mixed.xml")
        el = VariantParser(cache).get_close_button()
        assert el is not None
        assert el.resolved_via == "resource_id"


# ── Checkout Parser ──────────────────────────────────────────────────────────

class TestCheckoutParser:
    def test_is_checkout_page(self):
        cache = _load_cache("checkout_page.xml")
        assert CheckoutParser(cache).is_checkout_page() is True

    def test_place_order_button_resolved(self):
        cache = _load_cache("checkout_page.xml")
        el = CheckoutParser(cache).get_place_order_button()
        assert el is not None
        assert el.resolved_via == "text"
        # center of [728,2216][1058,2265]
        assert el.tap_x == 893

    def test_total_payment(self):
        cache = _load_cache("checkout_page.xml")
        total = CheckoutParser(cache).get_total_payment()
        assert "330" in total


# ── ProductConfig threshold logic ────────────────────────────────────────────

class TestProductConfigThreshold:
    def test_stock_meets_threshold_any_positive(self):
        from bot.models.product import ProductConfig
        p = ProductConfig(stock_mode="any", minimum_stock=1)
        assert p.stock_meets_threshold(1) is True
        assert p.stock_meets_threshold(100) is True

    def test_stock_meets_threshold_any_zero(self):
        from bot.models.product import ProductConfig
        p = ProductConfig(stock_mode="any", minimum_stock=1)
        assert p.stock_meets_threshold(0) is False

    def test_stock_meets_threshold_minimum_above(self):
        from bot.models.product import ProductConfig
        p = ProductConfig(stock_mode="minimum", minimum_stock=10)
        assert p.stock_meets_threshold(10) is True
        assert p.stock_meets_threshold(15) is True

    def test_stock_meets_threshold_minimum_below(self):
        from bot.models.product import ProductConfig
        p = ProductConfig(stock_mode="minimum", minimum_stock=10)
        assert p.stock_meets_threshold(5) is False
        assert p.stock_meets_threshold(9) is False


# ── Selector Priority ─────────────────────────────────────────────────────────

class TestSelectorPriority:
    def test_resource_id_wins_first(self):
        cache = _load_cache("product_page_in_stock.xml")
        el = ProductParser(cache).get_buy_now_button()
        assert el.resolved_via == "resource_id"

    def test_checkout_title_via_resource_id(self):
        cache = _load_cache("checkout_page.xml")
        parser = CheckoutParser(cache)
        from bot.ui.checkout_selectors import CHECKOUT_TITLE
        el = parser.resolve(CHECKOUT_TITLE)
        assert el is not None
        assert el.resolved_via == "text"
