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

    async def _run(
        self, args: list[str], timeout: int = 30, capture_output: bool = True
    ) -> tuple[int, str, str]:
        """
        Jalankan perintah adb secara async.
        Returns (returncode, stdout, stderr).
        Mengukur latency untuk WatchdogMetrics.

        capture_output:
          True  → pipe stdout/stderr (untuk command yg outputnya perlu dianalisis,
                   seperti get-state, shell, dll).
          False → redirect ke DEVNULL, cuma returncode aja (connect, disconnect, kill-server).
                   Mencegah hang karena ADB fork child process yg inherit pipe FD,
                   bikin proc.communicate() gak pernah dapet EOF.
        """
        cmd = self._base_cmd() + args
        t0 = time.monotonic()
        try:
            if capture_output:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
                out_text = stdout.decode("utf-8", errors="replace").strip()
                err_text = stderr.decode("utf-8", errors="replace").strip()
            else:
                proc = await asyncio.create_subprocess_exec(
                    *cmd,
                    stdout=asyncio.subprocess.DEVNULL,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                await asyncio.wait_for(proc.wait(), timeout=timeout)
                out_text = ""
                err_text = ""
            self._last_latency_ms = (time.monotonic() - t0) * 1000
            return (proc.returncode or 0, out_text, err_text)
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
        """True kalo ADB device beneran nyambung.
        Pake get-state dulu (cepet, 3s). Kalo return device artinya
        ADB daemon ngaku nyambung — itu yg penting buat decision.
        Shell echo timeout bukan berarti disconnected, bisa cuma lag."""
        rc, out, _ = await self._run(["get-state"], timeout=3)
        return rc == 0 and out.strip() == "device"

    async def connect_wifi(self) -> bool:
        if not self.wifi_host:
            return False
        # Disconnect dulu — bersihin state stale ADB server biar koneksi baru bersih.
        # ADB server nyimpen per-connection state; kalo langsung connect tanpa
        # disconnect, negosiasi bisa hang/gagal.
        await self._run(["disconnect"], capture_output=False, timeout=8)
        await asyncio.sleep(0.5)
        # Connect — pake DEVNULL biar gak hang. ADB `connect` fork child process
        # yg inherit pipe FD, bikin proc.communicate() gak pernah dapet EOF.
        # Timeout 8s: kalo WiFi ADB gak nyambung dalam 8s, mending cepet fail
        # daripada nunggu 30s.
        rc, _, _ = await self._run(["connect", self.wifi_host], capture_output=False, timeout=8)
        if rc != 0:
            return False
        await asyncio.sleep(1)
        # Verifikasi via get-state (butuh output — pake capture_output=True normal)
        return await self.is_connected()

    async def disconnect(self) -> None:
        await self._run(["disconnect"], capture_output=False, timeout=8)

    async def reconnect(self) -> bool:
        """Coba reconnect: disconnect → connect lagi (USB atau Wi-Fi)."""
        log.info("ADB reconnect: mulai...")
        await self._run(["disconnect"], capture_output=False, timeout=8)
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
        await self._run(["kill-server"], capture_output=False, timeout=8)
        await asyncio.sleep(2)

    async def start_server(self) -> None:
        await self._run(["start-server"], capture_output=False, timeout=15)
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

    async def ensure_screen_on(self) -> None:
        """
        Bangunin layar HP kalo lagi mati/sleep.

        KEYCODE_WAKEUP (224) = bangunin DOANG tanpa toggle (beda sama POWER 26 yg toggle).
        Ini AMAN dipanggil tiap siklus — kalo layar udah nyala, WAKEUP gak ngapa-ngapain.
        """
        log.info("Screen wake: kirim WAKEUP + swipe unlock")
        await self.key(224)     # KEYCODE_WAKEUP — bangunin layar (gak toggle)
        await asyncio.sleep(0.5)
        # Swipe dari bawah ke atas biar unlock kalo ada lockscreen
        await self.swipe(540, 2000, 540, 300, 600)

    async def screen_on(self) -> None:
        await self.key(26)      # KEYCODE_POWER

    async def screen_off(self) -> None:
        await self.key(26)      # KEYCODE_POWER

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
