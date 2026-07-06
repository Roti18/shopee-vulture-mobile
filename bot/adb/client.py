"""
ADB command wrapper — semua interaksi dengan device dilakukan di sini.
Tidak ada logika bisnis, hanya low-level ADB commands.
"""
import asyncio
import time
from dataclasses import dataclass, field

from bot.utils.logger import get_logger

log = get_logger(__name__)

SHOPEE_PACKAGE = "id.co.shopee"


@dataclass
class ADBClient:
    device_serial: str = ""
    wifi_host: str = ""
    _last_latency_ms: float = field(default=0.0, repr=False)

    # ------------------------------------------------------------------ #
    # Internal helpers
    # ------------------------------------------------------------------ #

    def _base_cmd(self) -> list[str]:
        """Prefix adb dengan -s <serial> jika serial tersedia."""
        if self.device_serial:
            return ["adb", "-s", self.device_serial]
        return ["adb"]

    async def _run(self, args: list[str], timeout: int = 30) -> tuple[int, str, str]:
        """
        Jalankan perintah adb secara async.
        Returns (returncode, stdout, stderr).
        Mengukur latency untuk WatchdogMetrics.
        """
        cmd = self._base_cmd() + args
        t0 = time.monotonic()
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=timeout
            )
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            return (
                proc.returncode or 0,
                stdout.decode("utf-8", errors="replace").strip(),
                stderr.decode("utf-8", errors="replace").strip(),
            )
        except asyncio.TimeoutError:
            log.error("ADB command timed out: %s", " ".join(cmd))
            try:
                proc.kill()
                await proc.wait()
            except Exception:
                pass
            return (-1, "", "timeout")
        except Exception as exc:
            log.error("ADB command error: %s — %s", " ".join(cmd), exc)
            return (-1, "", str(exc))

    # ------------------------------------------------------------------ #
    # Connection
    # ------------------------------------------------------------------ #

    async def is_connected(self) -> bool:
        """True jika device terdeteksi dan statusnya 'device'."""
        rc, out, _ = await self._run(["get-state"])
        return rc == 0 and out.strip() == "device"

    async def connect_wifi(self) -> bool:
        if not self.wifi_host:
            return False
        rc, out, _ = await self._run(["connect", self.wifi_host])
        return rc == 0 and "connected" in out.lower()

    async def disconnect(self) -> None:
        await self._run(["disconnect"])

    async def reconnect(self) -> bool:
        """Coba reconnect: disconnect → connect lagi (USB atau Wi-Fi)."""
        log.info("ADB reconnect: mulai...")
        await self._run(["disconnect"])
        await asyncio.sleep(1)
        if self.wifi_host:
            ok = await self.connect_wifi()
        else:
            # USB: cukup re-probe
            rc, _, _ = await self._run(["wait-for-device"])
            ok = rc == 0
        log.info("ADB reconnect: %s", "OK" if ok else "GAGAL")
        return ok

    async def kill_server(self) -> None:
        await asyncio.create_subprocess_exec("adb", "kill-server")
        await asyncio.sleep(2)

    async def start_server(self) -> None:
        await asyncio.create_subprocess_exec("adb", "start-server")
        await asyncio.sleep(2)

    # ------------------------------------------------------------------ #
    # Input
    # ------------------------------------------------------------------ #

    async def tap(self, x: int, y: int) -> bool:
        log.debug("tap(%d, %d)", x, y)
        rc, _, _ = await self._run(["shell", "input", "tap", str(x), str(y)])
        return rc == 0

    async def swipe(
        self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300
    ) -> bool:
        log.debug("swipe(%d,%d → %d,%d) %dms", x1, y1, x2, y2, duration_ms)
        rc, _, _ = await self._run([
            "shell", "input", "swipe",
            str(x1), str(y1), str(x2), str(y2), str(duration_ms),
        ])
        return rc == 0

    async def key(self, keycode: int | str) -> bool:
        rc, _, _ = await self._run(["shell", "input", "keyevent", str(keycode)])
        return rc == 0

    async def press_back(self) -> bool:
        return await self.key(4)

    async def press_home(self) -> bool:
        return await self.key(3)

    # ------------------------------------------------------------------ #
    # Screen
    # ------------------------------------------------------------------ #

    async def screen_on(self) -> None:
        await self.key(82)      # KEYCODE_MENU — wake up

    async def screen_off(self) -> None:
        await self.key(26)      # KEYCODE_POWER

    async def unlock_screen(self) -> None:
        await self.screen_on()
        await asyncio.sleep(0.5)
        await self.swipe(540, 1800, 540, 900, 300)   # swipe up to unlock

    # ------------------------------------------------------------------ #
    # App
    # ------------------------------------------------------------------ #

    async def open_url(self, url: str) -> bool:
        """Buka URL via Android Intent — cara paling reliable untuk navigasi."""
        log.info("open_url: %s", url)
        rc, _, _ = await self._run([
            "shell", "am", "start",
            "-a", "android.intent.action.VIEW",
            "-d", url,
        ])
        return rc == 0

    async def force_stop(self, package: str = SHOPEE_PACKAGE) -> bool:
        log.info("force_stop: %s", package)
        rc, _, _ = await self._run(["shell", "am", "force-stop", package])
        return rc == 0

    # ------------------------------------------------------------------ #
    # Metrics
    # ------------------------------------------------------------------ #

    @property
    def last_latency_ms(self) -> float:
        return self._last_latency_ms
