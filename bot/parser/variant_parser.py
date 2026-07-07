"""
VariantParser — identifikasi popup varian dan cek stok Level 2.

Perubahan dari v1:
  - find_variant_with_stock() sekarang mengembalikan VariantInfo
    yang menyertakan stock_count (diperlukan untuk Telegram alert).
  - Menerima minimum_stock dan stock_mode dari ProductConfig.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from bot.adb.xml_cache import XMLCache
from bot.models.enums import StockStatus
from bot.parser.base_parser import BaseParser, ResolvedElement
from bot.ui import variant_selectors as sel
from bot.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class VariantInfo:
    """Hasil pencarian varian yang memenuhi threshold stok."""
    resolved_element: ResolvedElement      # element yang bisa di-tap
    stock_count: int                       # jumlah stok yang terbaca
    variant_text: str = ""                 # teks stok asli ("Stok: 17")


class VariantParser(BaseParser):
    def __init__(self, cache: XMLCache) -> None:
        super().__init__(cache)

    # ------------------------------------------------------------------ #
    # Screen detection
    # ------------------------------------------------------------------ #

    def is_variant_popup_open(self) -> bool:
        """Popup varian terdeteksi jika VARIANT_CONTAINER ada."""
        return self.has_element(sel.VARIANT_CONTAINER)

    # ------------------------------------------------------------------ #
    # Stock check Level 2
    # ------------------------------------------------------------------ #

    def check_stock(self) -> StockStatus:
        """
        Cek apakah ada varian dengan stok > 0.
        Untuk threshold check yang lebih spesifik, gunakan find_variant_with_stock().
        """
        info = self.find_variant_with_stock()
        if info is None:
            # Periksa apakah ada overlay "Habis" yang berarti semua habis
            if self.has_element(sel.STOCK_OUT_OVERLAY):
                return StockStatus.OUT_OF_STOCK
            return StockStatus.UNKNOWN
        return StockStatus.IN_STOCK

    # ------------------------------------------------------------------ #
    # Variant discovery
    # ------------------------------------------------------------------ #

    def find_variant_with_stock(
        self,
        target_variant: str = "",
        minimum_stock: int = 1,
        stock_mode: str = "any",
    ) -> VariantInfo | None:
        """
        Cari varian yang memenuhi threshold stok.

        Algoritma:
        1. Cari semua node dengan text pattern "Stok: N"
        2. Filter berdasarkan stock_mode + minimum_stock
        3. Jika target_variant diisi, prioritaskan varian tersebut
        4. Return VariantInfo dari kandidat pertama yang memenuhi syarat
        """
        nodes = self._cache.all_nodes()
        pattern = re.compile(sel.STOCK_TEXT_REGEX)

        # Kumpulkan semua stock text nodes beserta jumlah stoknya
        stock_nodes: list[tuple[Element, int, str]] = []
        for node in nodes:
            text = node.get("text", "") or node.get("content-desc", "")
            match = pattern.search(text)
            if match:
                count = int(match.group(1))
                stock_nodes.append((node, count, text))

        if not stock_nodes:
            log.debug("find_variant_with_stock: tidak ada text 'Stok: N' ditemukan")
            return None

        # Filter berdasarkan threshold
        qualified: list[tuple[Element, int, str]] = []
        for node, count, text in stock_nodes:
            if self._meets_threshold(count, minimum_stock, stock_mode):
                qualified.append((node, count, text))
                log.debug("Kandidat varian: %s (threshold: %s/%d)", text, stock_mode, minimum_stock)

        if not qualified:
            log.info(
                "Semua varian tidak memenuhi threshold: mode=%s, minimum=%d",
                stock_mode, minimum_stock,
            )
            return None

        # Prioritaskan target_variant jika diisi
        if target_variant:
            for node, count, text in qualified:
                parent_text = self._get_nearby_text(node, nodes)
                if target_variant.lower() in parent_text.lower():
                    log.info("Target variant '%s' ditemukan, stok: %d", target_variant, count)
                    el = self._find_tappable_element(node, nodes)
                    return VariantInfo(resolved_element=el, stock_count=count, variant_text=text)

        # Fallback: ambil kandidat pertama
        first_node, first_count, first_text = qualified[0]
        log.info("Menggunakan varian pertama: %s", first_text)
        el = self._find_tappable_element(first_node, nodes)
        return VariantInfo(resolved_element=el, stock_count=first_count, variant_text=first_text)

    def get_all_stock_counts(self) -> list[int]:
        """Return semua nilai stok yang ditemukan di popup (untuk debug)."""
        pattern = re.compile(sel.STOCK_TEXT_REGEX)
        counts = []
        for node in self._cache.all_nodes():
            text = node.get("text", "") or node.get("content-desc", "")
            match = pattern.search(text)
            if match:
                counts.append(int(match.group(1)))
        return counts

    # ------------------------------------------------------------------ #
    # Element resolvers
    # ------------------------------------------------------------------ #

    def get_close_button(self) -> ResolvedElement | None:
        el = self.resolve(sel.CLOSE_POPUP_BUTTON)
        if el is None:
            log.error("CLOSE_POPUP_BUTTON tidak ditemukan")
        return el

    def get_submit_button(self) -> ResolvedElement | None:
        return self.resolve(sel.SUBMIT_BUTTON)

    def get_submit_button_text(self) -> str:
        el = self.resolve(sel.SUBMIT_BUTTON)
        if el and el.element is not None:
            # Cek teks pada tombol utama
            text = el.element.get("text", "")
            if text:
                return text
            # Jika tombol utama adalah ViewGroup (tanpa teks langsung), cari teks di anak/keturunannya
            for child in el.element.iter("node"):
                child_text = child.get("text", "")
                if child_text:
                    return child_text
        return ""

    def is_submit_ready(self) -> bool:
        return sel.SUBMIT_TEXT_READY in self.get_submit_button_text()

    def get_plus_button(self) -> ResolvedElement | None:
        return self.resolve(sel.BUTTON_PLUS)

    def get_minus_button(self) -> ResolvedElement | None:
        return self.resolve(sel.BUTTON_MINUS)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    @staticmethod
    def _meets_threshold(stock_count: int, minimum_stock: int, stock_mode: str) -> bool:
        if stock_count <= 0:
            return False
        if stock_mode == "any":
            return True
        # "minimum"
        return stock_count >= minimum_stock

    def _find_tappable_element(
        self, stock_node: Element, all_nodes: list[Element]
    ) -> ResolvedElement:
        """
        Temukan elemen yang bisa di-tap berdasarkan proximity ke stock_node.

        Karena resource_id varian bersifat dynamic (imageItemIcon_id-<hash>),
        kita pakai proximity bounds untuk menemukan ImageView terdekat.
        """
        stock_bounds = self._parse_bounds_tuple(stock_node.get("bounds", ""))

        # Cari ImageView di dalam sectionTierVariation yang bounds-nya overlap
        root = self._cache.root()
        container = self._by_resource_id(
            list(root.iter("node")) if root is not None else all_nodes,
            sel.VARIANT_CONTAINER.resource_id,
        )
        search_scope = list(container.iter("node")) if container is not None else all_nodes

        for node in search_scope:
            if node.get("class", "") == sel.VARIANT_ITEM_CLASS and node.get("clickable", "false") == "true":
                cb = self._parse_bounds_tuple(node.get("bounds", ""))
                if cb and self._bounds_near(stock_bounds, cb, tolerance_x=100, tolerance_y=250):
                    return self._make_result(node, "class_hierarchy+bounds_proximity")

        # Fallback: return stock node itu sendiri (masih bisa di-tap)
        return self._make_result(stock_node, "text_regex_fallback")

    def _get_nearby_text(self, node: Element, all_nodes: list[Element]) -> str:
        """Kumpulkan teks dari node-node di sekitar elemen ini."""
        bounds = self._parse_bounds_tuple(node.get("bounds", ""))
        if bounds is None:
            return ""
        texts = []
        for n in all_nodes:
            nb = self._parse_bounds_tuple(n.get("bounds", ""))
            if nb and self._bounds_near(bounds, nb, tolerance_x=150, tolerance_y=300):
                t = n.get("text", "") or n.get("content-desc", "")
                if t:
                    texts.append(t)
        return " ".join(texts)

    @staticmethod
    def _parse_bounds_tuple(bounds_str: str) -> tuple[int, int, int, int] | None:
        try:
            parts = bounds_str.replace("][", ",").strip("[]").split(",")
            return int(parts[0]), int(parts[1]), int(parts[2]), int(parts[3])
        except Exception:
            return None

    @staticmethod
    def _bounds_near(
        a: tuple[int, int, int, int] | None,
        b: tuple[int, int, int, int] | None,
        tolerance_x: int = 200,
        tolerance_y: int = 200,
    ) -> bool:
        if a is None or b is None:
            return False
        ax = (a[0] + a[2]) // 2
        ay = (a[1] + a[3]) // 2
        bx = (b[0] + b[2]) // 2
        by = (b[1] + b[3]) // 2
        return abs(ax - bx) < tolerance_x and abs(ay - by) < tolerance_y
