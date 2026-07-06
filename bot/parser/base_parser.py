"""
BaseParser — engine resolusi selector 7 level.

Priority chain (dari paling robust ke paling fragile):
  1. resource_id          → atribut resource-id di XML
  2. text                 → atribut text (exact atau contains)
  3. content_desc         → atribut content-desc
  4. class_hierarchy      → class + parent resource-id
  5. text_regex           → regex pada text atau content-desc
  6. bounds               → match string bounds persis
  7. hardcoded_coordinate → (x, y) sebagai fallback mutlak terakhir

Koordinat diambil dari center of bounds elemen yang ditemukan,
bukan dari koordinat yang di-hardcode di selector.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from xml.etree.ElementTree import Element

from bot.adb.dumper import center_of_bounds
from bot.adb.xml_cache import XMLCache
from bot.ui.base_selectors import UISelector
from bot.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class ResolvedElement:
    """Hasil resolusi selector — element XML + koordinat tap yang sudah dihitung."""
    element: Element | None
    tap_x: int
    tap_y: int
    resolved_via: str           # level yang berhasil (untuk logging/debug)

    def found(self) -> bool:
        return self.element is not None or (self.tap_x > 0 and self.tap_y > 0)

    def center(self) -> tuple[int, int]:
        return (self.tap_x, self.tap_y)


class BaseParser:
    """
    Mixin/base class untuk semua parser.
    Berikan XMLCache saat inisialisasi, lalu panggil resolve() dengan UISelector.
    """

    def __init__(self, cache: XMLCache) -> None:
        self._cache = cache

    # ------------------------------------------------------------------ #
    # Public resolve entry point
    # ------------------------------------------------------------------ #

    def resolve(self, selector: UISelector) -> ResolvedElement | None:
        """
        Coba setiap level selector secara berurutan.
        Return ResolvedElement pertama yang ditemukan, atau None.
        """
        nodes = self._cache.all_nodes()
        if not nodes and not selector.hardcoded_coordinate:
            log.warning("resolve: XML cache kosong, tidak ada node")
            return None

        # Level 1: resource_id
        if selector.resource_id:
            el = self._by_resource_id(nodes, selector.resource_id)
            if el is not None:
                return self._make_result(el, "resource_id")

        # Level 2: text
        if selector.text:
            el = self._by_text(nodes, selector.text, selector.text_exact)
            if el is not None:
                return self._make_result(el, "text")

        # Level 3: content_desc
        if selector.content_desc:
            el = self._by_content_desc(nodes, selector.content_desc)
            if el is not None:
                return self._make_result(el, "content_desc")

        # Level 4: class_hierarchy
        if any(selector.class_hierarchy):
            el = self._by_class_hierarchy(nodes, *selector.class_hierarchy)
            if el is not None:
                return self._make_result(el, "class_hierarchy")

        # Level 5: text_regex
        if selector.text_regex:
            el = self._by_text_regex(nodes, selector.text_regex)
            if el is not None:
                return self._make_result(el, "text_regex")

        # Level 6: bounds string
        if selector.bounds:
            el = self._by_bounds(nodes, selector.bounds)
            if el is not None:
                return self._make_result(el, "bounds")

        # Level 7: hardcoded coordinate (last resort)
        if selector.hardcoded_coordinate:
            x, y = selector.hardcoded_coordinate
            log.warning(
                "resolve: menggunakan hardcoded_coordinate (%d, %d) untuk [%s]",
                x, y, selector.description or "unknown",
            )
            return ResolvedElement(
                element=None, tap_x=x, tap_y=y, resolved_via="hardcoded_coordinate"
            )

        log.debug(
            "resolve: TIDAK ditemukan untuk selector [%s] — levels: %s",
            selector.description,
            selector.priority_summary(),
        )
        return None

    def resolve_all(self, selector: UISelector) -> list[ResolvedElement]:
        """
        Kembalikan SEMUA element yang cocok dengan selector ini.
        Berguna untuk menemukan daftar varian.
        """
        nodes = self._cache.all_nodes()
        results: list[ResolvedElement] = []

        if selector.resource_id:
            for el in self._all_by_resource_id(nodes, selector.resource_id):
                results.append(self._make_result(el, "resource_id"))

        if not results and selector.text:
            for el in self._all_by_text(nodes, selector.text, selector.text_exact):
                results.append(self._make_result(el, "text"))

        if not results and selector.text_regex:
            for el in self._all_by_text_regex(nodes, selector.text_regex):
                results.append(self._make_result(el, "text_regex"))

        return results

    def has_element(self, selector: UISelector) -> bool:
        return self.resolve(selector) is not None

    # ------------------------------------------------------------------ #
    # Level 1 — resource_id
    # ------------------------------------------------------------------ #

    @staticmethod
    def _by_resource_id(nodes: list[Element], resource_id: str) -> Element | None:
        for node in nodes:
            rid = node.get("resource-id", "")
            # exact match ATAU ends-with (karena ada prefix package)
            if rid == resource_id or rid.endswith("/" + resource_id) or rid.endswith(":" + resource_id):
                return node
        return None

    @staticmethod
    def _all_by_resource_id(nodes: list[Element], resource_id: str) -> list[Element]:
        return [
            n for n in nodes
            if (
                n.get("resource-id", "") == resource_id
                or n.get("resource-id", "").endswith("/" + resource_id)
            )
        ]

    # ------------------------------------------------------------------ #
    # Level 2 — text
    # ------------------------------------------------------------------ #

    @staticmethod
    def _by_text(nodes: list[Element], text: str, exact: bool) -> Element | None:
        for node in nodes:
            t = node.get("text", "")
            if exact and t == text:
                return node
            if not exact and text.lower() in t.lower():
                return node
        return None

    @staticmethod
    def _all_by_text(nodes: list[Element], text: str, exact: bool) -> list[Element]:
        result = []
        for node in nodes:
            t = node.get("text", "")
            if (exact and t == text) or (not exact and text.lower() in t.lower()):
                result.append(node)
        return result

    # ------------------------------------------------------------------ #
    # Level 3 — content-desc
    # ------------------------------------------------------------------ #

    @staticmethod
    def _by_content_desc(nodes: list[Element], desc: str) -> Element | None:
        for node in nodes:
            if node.get("content-desc", "").strip() == desc:
                return node
        return None

    # ------------------------------------------------------------------ #
    # Level 4 — class + parent resource_id
    # ------------------------------------------------------------------ #

    def _by_class_hierarchy(
        self, nodes: list[Element], class_name: str, parent_resource_id: str
    ) -> Element | None:
        """
        Cari node dengan class tertentu yang merupakan descendant dari
        node dengan parent_resource_id tertentu.
        """
        root = self._cache.root()
        if root is None:
            return None

        # Temukan parent
        parent = self._by_resource_id(list(root.iter("node")), parent_resource_id)
        search_scope = list(parent.iter("node")) if parent is not None else nodes

        for node in search_scope:
            if class_name and node.get("class", "") == class_name:
                return node
        return None

    # ------------------------------------------------------------------ #
    # Level 5 — text regex
    # ------------------------------------------------------------------ #

    @staticmethod
    def _by_text_regex(nodes: list[Element], pattern: str) -> Element | None:
        compiled = re.compile(pattern)
        for node in nodes:
            text = node.get("text", "") or node.get("content-desc", "")
            if compiled.search(text):
                return node
        return None

    @staticmethod
    def _all_by_text_regex(nodes: list[Element], pattern: str) -> list[Element]:
        compiled = re.compile(pattern)
        return [
            n for n in nodes
            if compiled.search(n.get("text", "") or n.get("content-desc", ""))
        ]

    # ------------------------------------------------------------------ #
    # Level 6 — bounds string
    # ------------------------------------------------------------------ #

    @staticmethod
    def _by_bounds(nodes: list[Element], bounds: str) -> Element | None:
        for node in nodes:
            if node.get("bounds", "") == bounds:
                return node
        return None

    # ------------------------------------------------------------------ #
    # Helper: buat ResolvedElement dari Element XML
    # ------------------------------------------------------------------ #

    @staticmethod
    def _make_result(element: Element, via: str) -> ResolvedElement:
        bounds_str = element.get("bounds", "")
        center = center_of_bounds(bounds_str)
        if center is None:
            log.warning("_make_result: bounds tidak bisa diparsing: %r", bounds_str)
            return ResolvedElement(element=element, tap_x=0, tap_y=0, resolved_via=via)
        return ResolvedElement(
            element=element, tap_x=center[0], tap_y=center[1], resolved_via=via
        )
