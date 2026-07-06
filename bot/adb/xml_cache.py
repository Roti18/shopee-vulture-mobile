"""
XMLCache — satu dump XML dibagikan ke semua parser dalam satu loop.
Menghindari double-dump yang tidak perlu.
"""
import time
from dataclasses import dataclass, field
import xml.etree.ElementTree as ET

from bot.adb.client import ADBClient
from bot.adb import dumper
from bot.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class XMLCache:
    ttl_seconds: float = 2.0          # cache valid selama N detik

    _tree: ET.ElementTree | None = field(default=None, repr=False)
    _cached_at: float = field(default=0.0, repr=False)
    _last_dump_duration_ms: float = field(default=0.0, repr=False)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def get(self, adb: ADBClient) -> ET.ElementTree | None:
        """
        Kembalikan cached tree jika masih valid.
        Jika expired atau belum ada, lakukan dump baru.
        """
        if self._is_fresh():
            return self._tree

        t0 = time.monotonic()
        tree = await dumper.dump_xml(adb)
        self._last_dump_duration_ms = (time.monotonic() - t0) * 1000

        if tree is not None:
            self._tree = tree
            self._cached_at = time.monotonic()
        else:
            log.warning("XMLCache: dump gagal, cache dikosongkan")
            self._tree = None
            self._cached_at = 0.0

        return self._tree

    def invalidate(self) -> None:
        """Paksa refresh pada pemanggilan get() berikutnya."""
        self._cached_at = 0.0

    @property
    def last_dump_duration_ms(self) -> float:
        return self._last_dump_duration_ms

    # ------------------------------------------------------------------ #
    # Element search — menggunakan base_parser resolver
    # ------------------------------------------------------------------ #

    def root(self) -> ET.Element | None:
        if self._tree is None:
            return None
        return self._tree.getroot()

    def all_nodes(self) -> list[ET.Element]:
        root = self.root()
        if root is None:
            return []
        return list(root.iter("node"))

    # ------------------------------------------------------------------ #
    # Internal
    # ------------------------------------------------------------------ #

    def _is_fresh(self) -> bool:
        return (
            self._tree is not None
            and (time.monotonic() - self._cached_at) < self.ttl_seconds
        )
