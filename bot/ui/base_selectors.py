"""
UISelector: definisi selector dengan 7-level priority chain.

Priority (dari paling robust ke paling fragile):
  1. resource_id          — paling stabil, berubah hanya saat major refactor Shopee
  2. text                 — teks yang ditampilkan (exact match)
  3. content_desc         — accessibility label
  4. class_hierarchy      — (class, parent_resource_id) — stabil meski teks berubah
  5. text_regex           — regex pada text/content-desc (untuk pattern "Stok: N")
  6. bounds               — range koordinat sebagai string "[x1,y1][x2,y2]"
  7. hardcoded_coordinate — (x, y) absolut — LAST RESORT, mungkin rusak saat layout berubah

Setiap UISelector bisa memiliki satu atau lebih level terisi.
Resolver (di base_parser.py) akan mencoba dari level 1 hingga 7 secara berurutan.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class UISelector:
    # Level 1
    resource_id: str = ""

    # Level 2
    text: str = ""
    text_exact: bool = True                  # False = contains match

    # Level 3
    content_desc: str = ""

    # Level 4: (class_name, parent_resource_id)
    # contoh: ("android.widget.TextView", "sectionProductPrice")
    class_hierarchy: tuple[str, str] = field(default=("", ""))

    # Level 5: regex match pada atribut text atau content-desc
    text_regex: str = ""

    # Level 6: bounds string untuk narrow down element
    bounds: str = ""

    # Level 7: koordinat absolut hardcoded (LAST RESORT)
    hardcoded_coordinate: tuple[int, int] | None = None

    # Metadata tambahan untuk debug
    description: str = ""                    # nama human-readable

    def has_any(self) -> bool:
        """True jika setidaknya satu selector terisi."""
        return bool(
            self.resource_id
            or self.text
            or self.content_desc
            or any(self.class_hierarchy)
            or self.text_regex
            or self.bounds
            or self.hardcoded_coordinate
        )

    def priority_summary(self) -> list[str]:
        """Urutan level yang tersedia untuk logging."""
        levels = []
        if self.resource_id:
            levels.append(f"resource_id={self.resource_id!r}")
        if self.text:
            levels.append(f"text={self.text!r}")
        if self.content_desc:
            levels.append(f"content_desc={self.content_desc!r}")
        if any(self.class_hierarchy):
            levels.append(f"class_hierarchy={self.class_hierarchy}")
        if self.text_regex:
            levels.append(f"text_regex={self.text_regex!r}")
        if self.bounds:
            levels.append(f"bounds={self.bounds!r}")
        if self.hardcoded_coordinate:
            levels.append(f"hardcoded={self.hardcoded_coordinate}")
        return levels
