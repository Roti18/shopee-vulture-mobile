"""
Mock ADBClient untuk unit testing — tidak mengirim command ke device nyata.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from xml.etree import ElementTree


@dataclass
class MockADBClient:
    """Drop-in replacement untuk ADBClient di unit tests."""
    device_serial: str = "mock-device"
    wifi_host: str = ""
    _connected: bool = True
    _last_latency_ms: float = 5.0

    # Recorded calls untuk assert
    tap_calls: list[tuple[int, int]] = field(default_factory=list)
    swipe_calls: list[tuple] = field(default_factory=list)
    key_calls: list[int | str] = field(default_factory=list)
    open_url_calls: list[str] = field(default_factory=list)
    force_stop_calls: list[str] = field(default_factory=list)

    # Fixture XML path untuk dump
    xml_fixture_path: Path | None = None

    async def tap(self, x: int, y: int) -> bool:
        self.tap_calls.append((x, y))
        return True

    async def swipe(self, x1, y1, x2, y2, duration_ms=300) -> bool:
        self.swipe_calls.append((x1, y1, x2, y2, duration_ms))
        return True

    async def key(self, keycode) -> bool:
        self.key_calls.append(keycode)
        return True

    async def press_back(self) -> bool:
        return await self.key(4)

    async def press_home(self) -> bool:
        return await self.key(3)

    async def screen_on(self) -> None:
        pass

    async def screen_off(self) -> None:
        pass

    async def unlock_screen(self) -> None:
        pass

    async def open_url(self, url: str) -> bool:
        self.open_url_calls.append(url)
        return True

    async def force_stop(self, package: str = "") -> bool:
        self.force_stop_calls.append(package)
        return True

    async def is_connected(self) -> bool:
        return self._connected

    async def reconnect(self) -> bool:
        return self._connected

    async def kill_server(self) -> None:
        pass

    async def start_server(self) -> None:
        pass

    async def connect_wifi(self) -> bool:
        return True

    async def disconnect(self) -> None:
        pass

    async def _run(self, args, timeout=30):
        """Minimal stub untuk dumper.dump_xml compatibility."""
        return (0, "UI dump: /sdcard/ui_dump.xml", "")

    @property
    def last_latency_ms(self) -> float:
        return self._last_latency_ms
