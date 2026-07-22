"""
XMLCache — satu dump XML dibagikan ke semua parser dalam satu loop.

v2: Adaptive backoff kalo dump gagal + TTL beneran dipake.
- invalidate() gak perlu dipanggil tiap polling — TTL yang atur.
- Kalo dump gagal, backoff naik (0.5s → 1s → 2s → 4s max).
- Kalo sukses, backoff langsung reset ke 0.
"""
import time
from dataclasses import dataclass, field
import xml.etree.ElementTree as ET

from bot.adb import dumper
from bot.utils.logger import get_logger

log = get_logger(__name__)


@dataclass
class XMLCache:
    ttl_seconds: float = 0.8                     # cache valid selama N detik (diturunkan biar deteksi UI lebih cepet)
    max_backoff: float = 4.0                     # max tunggu antar dump (lag protection)

    _tree: ET.ElementTree | None = field(default=None, repr=False)
    _cached_at: float = field(default=0.0, repr=False)
    _last_dump_duration_ms: float = field(default=0.0, repr=False)
    _backoff: float = field(default=0.0, repr=False)

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    async def get(self, adb: "ADBClient", force: bool = False) -> ET.ElementTree | None:
        """
        Kembalikan cached tree jika masih valid (ttl_seconds).
        Jika expired atau force=True, lakukan dump baru.

        Args:
            force: True kalo abis tap (butuh fresh dump, skip cache).
        """
        if not force and self._is_fresh():
            return self._tree

        # Adaptive backoff: tunggu makin lama kalo dump terus gagal
        if self._backoff > 0:
            log.debug("XMLCache: backoff %.1fs sebelum dump", self._backoff)
            await asyncio_sleep(self._backoff)

        t0 = time.monotonic()
        tree = await dumper.dump_xml(adb)
        self._last_dump_duration_ms = (time.monotonic() - t0) * 1000

        if tree is not None:
            self._tree = tree
            self._cached_at = time.monotonic()
            self._backoff = 0.0                     # reset backoff
            log.debug("XMLCache: dump sukses (%.0f ms)", self._last_dump_duration_ms)
        else:
            self._backoff = min(
                (self._backoff + 0.5) * 2 if self._backoff > 0 else 0.5,
                self.max_backoff,
            )
            log.warning(
                "XMLCache: dump gagal, backoff → %.1fs", self._backoff
            )
            # Kalo ada cached tree sebelumnya, jangan hapus — return aja yang lama
            # (biar parser bisa tetap kerja dengan snapshot terakhir)
            if self._tree is not None:
                log.debug("XMLCache: pake cached tree yang lama")
                return self._tree
            self._tree = None
            self._cached_at = 0.0

        return self._tree

    def invalidate(self) -> None:
        """Paksa refresh pada pemanggilan get() berikutnya."""
        self._cached_at = 0.0

    def reset_backoff(self) -> None:
        """Reset adaptive backoff ke 0 (dipanggil setelah sukses tap)."""
        self._backoff = 0.0

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #

    @property
    def last_dump_duration_ms(self) -> float:
        return self._last_dump_duration_ms

    @property
    def backoff(self) -> float:
        return self._backoff

    # ------------------------------------------------------------------ #
    # Element search
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


def asyncio_sleep(delay: float) -> None:
    """Bungkus asyncio.sleep biar import gak circular."""
    import asyncio
    return asyncio.sleep(delay)
