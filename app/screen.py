"""See the iPhone's screen on the PC (iOS 17+).

The frames come from Instruments' (DVT) screenshot channel, reached through pymobiledevice3's
in-process userspace tunnel (no admin rights, no extra driver). Measured on iOS 27: ~0.75 s per frame
over one reused channel; CoreDevice's screencaptureservice sends a bigger PNG (2.7 s) and its
connection stops answering after one request. These services only exist while Apple's Developer
Disk Image is mounted: `prepare` downloads it (~17 MB, from the pymobiledevice3 DeveloperDiskImage
repository) and installs it, personalized by Apple for this iPhone, in a separate process (inside
the server the transfer stalled). It stays mounted until the iPhone restarts. Developer Mode must
be on.
"""

import asyncio
import contextlib
import io
import json
import logging
import os
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any, Optional

from PIL import Image

from pymobiledevice3.exceptions import InvalidServiceError
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.services.dvt.instruments.dvt_provider import DvtProvider
from pymobiledevice3.services.dvt.instruments.screenshot import Screenshot

from .tasks import spawn
from .device import DeviceManager
from .i18n import _

log = logging.getLogger(__name__)

# present only while the developer disk image is mounted
SERVICE = "com.apple.instruments.dtservicehub"
PREPARE_TIMEOUT = 300

# Live mirroring process (mirror.py). The page loads the MJPEG straight from it and posts touch/keys
# there; it only accepts POSTs from the app's own origins.
MIRROR_PORT = 8770
MIRROR_URL = f"http://127.0.0.1:{MIRROR_PORT}"
MIRROR_ORIGINS = "http://127.0.0.1:8765,http://localhost:8765"
MIRROR_START_TIMEOUT = 60
MIRROR_IDLE_STOP = 90
IDLE_CLOSE = 45  # s without frames before the tunnel is closed
MAX_HEIGHT = 1400  # px of the JPEG sent to the browser
JPEG_QUALITY = 72


class ScreenError(Exception):
    pass


class ScreenMirror:
    def __init__(self, devices: DeviceManager) -> None:
        self.devices = devices
        self._tunnel: Optional[UserspaceRsdTunnel] = None
        self._rsd: Any = None
        self._udid: Optional[str] = None
        self._dvt: Optional[DvtProvider] = None
        self._capture: Optional[Screenshot] = None
        self._lock = asyncio.Lock()
        self._last_use = 0.0
        self._idle_task: Optional[asyncio.Task] = None
        self.preparing = False
        self._mirror: Optional[asyncio.subprocess.Process] = None
        self._mirror_udid: Optional[str] = None
        self._mirror_lock = asyncio.Lock()
        self._mirror_watch: Optional[asyncio.Task] = None
        self._mirror_idle_since: Optional[float] = None

    # ---------- tunnel ----------

    async def _open(self, udid: str) -> Any:
        if self._rsd is not None and self._udid == udid:
            return self._rsd
        await self._close()
        # one userspace tunnel per process: it lives here for as long as the screen is being watched
        self._tunnel = UserspaceRsdTunnel(serial=udid)
        try:
            self._rsd = await asyncio.wait_for(self._tunnel.aopen(), 40)
        except Exception:
            self._tunnel = None
            raise
        self._udid = udid
        if self._idle_task is None or self._idle_task.done():
            self._idle_task = asyncio.create_task(self._close_when_idle())
        return self._rsd

    async def _close(self) -> None:
        capture, dvt, tunnel = self._capture, self._dvt, self._tunnel
        self._capture = self._dvt = self._tunnel = self._rsd = self._udid = None
        closers = (capture.__aexit__(None, None, None) if capture else None,
                   dvt.__aexit__(None, None, None) if dvt else None,
                   tunnel.aclose() if tunnel else None)
        for closer in closers:
            if closer is None:
                continue
            try:
                await asyncio.wait_for(closer, 5)
            except BaseException as e:
                if isinstance(e, (KeyboardInterrupt, SystemExit)):
                    raise

    async def _close_when_idle(self) -> None:
        while self._tunnel is not None:
            await asyncio.sleep(10)
            if time.time() - self._last_use > IDLE_CLOSE and not self.preparing:
                async with self._lock:
                    log.info("screen: idle, closing the tunnel")
                    await self._close()

    # ---------- status / setup ----------

    async def status(self, udid: str) -> dict[str, Any]:
        entry = await self.devices.get(udid)
        try:
            dev_mode = bool(await asyncio.wait_for(
                entry.lockdown.get_value(domain="com.apple.security.mac.amfi", key="DeveloperModeStatus"), 5))
        except Exception:
            dev_mode = None
        result: dict[str, Any] = {"developer_mode": dev_mode, "ready": False, "preparing": self.preparing,
                                  "ios": entry.info.get("ProductVersion"), "connection": entry.connection}
        if dev_mode and entry.connection == "USB":
            self._last_use = time.time()
            async with self._lock:
                try:
                    rsd = await self._open(udid)
                    rsd._get_service_entry(SERVICE)
                    result["ready"] = True
                except InvalidServiceError:
                    pass  # the developer disk image isn't mounted
                except Exception as e:
                    result["error"] = f"{type(e).__name__}: {e}"
        return result

    async def prepare(self, udid: str) -> None:
        """Download (if needed) and mount the Developer Disk Image, in a separate process."""
        self.preparing = True
        try:
            async with self._lock:
                await self._close()  # the mounting process opens its own tunnel
                proc = await asyncio.create_subprocess_exec(
                    sys.executable, "-m", "app.mount_ddi", udid,
                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                    cwd=str(Path(__file__).resolve().parent.parent),
                )
                try:
                    out, _err = await asyncio.wait_for(proc.communicate(), PREPARE_TIMEOUT)
                except asyncio.TimeoutError:
                    proc.kill()
                    raise ScreenError(_("Mounting took too long and was stopped"))
                text = out.decode("utf-8", "replace").strip()
                log.info("mount_ddi: %s", text[-2000:])
                if proc.returncode != 0:
                    raise ScreenError(text.splitlines()[-1] if text else _("code {code}", code=proc.returncode))
                # the new services only show up in a fresh service list
                rsd = await self._open(udid)
                rsd._get_service_entry(SERVICE)
        finally:
            self.preparing = False
            self._last_use = time.time()

    # ---------- frames ----------

    async def frame(self, udid: str) -> bytes:
        self._last_use = time.time()
        async with self._lock:
            try:
                if self._capture is None or self._udid != udid:
                    rsd = await self._open(udid)
                    self._dvt = DvtProvider(rsd)
                    await self._dvt.__aenter__()
                    self._capture = Screenshot(self._dvt)
                    await self._capture.__aenter__()
                png = await asyncio.wait_for(self._capture.get_screenshot(), 15)
            except InvalidServiceError:
                await self._close()
                raise ScreenError(_("Developer disk image not mounted"))
            except Exception:
                await self._close()  # start over on the next frame
                raise
        return await asyncio.to_thread(_to_jpeg, png)

    async def close(self) -> None:
        await self.stop_mirror()
        async with self._lock:
            await self._close()

    # ---------- live mirroring (video + touch), see mirror.py ----------

    async def start_mirror(self, udid: str) -> dict[str, Any]:
        async with self._mirror_lock:
            proc = self._mirror
            if proc is not None and proc.returncode is None and self._mirror_udid == udid:
                return {"url": MIRROR_URL}
            await self._stop_mirror()
            async with self._lock:
                await self._close()  # the screenshot tunnel isn't needed while the video runs
            proc = await asyncio.create_subprocess_exec(
                sys.executable, "-m", "app.mirror", udid, str(MIRROR_PORT), MIRROR_ORIGINS,
                stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT,
                cwd=str(Path(__file__).resolve().parent.parent), env={**os.environ, "PYTHONIOENCODING": "utf-8"},
            )
            lines: list[str] = []

            async def until_ready() -> None:
                assert proc.stdout is not None
                while True:
                    raw = await proc.stdout.readline()
                    if not raw:
                        raise ScreenError(_("mirroring stopped while starting: {output}", output=" | ".join(lines[-3:])))
                    line = raw.decode("utf-8", "replace").rstrip()
                    lines.append(line)
                    if "mirror ready" in line:
                        return

            try:
                await asyncio.wait_for(until_ready(), MIRROR_START_TIMEOUT)
            except BaseException:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
                raise
            self._mirror, self._mirror_udid = proc, udid
            self._mirror_idle_since = None
            spawn(self._drain_mirror(proc))
            if self._mirror_watch is None or self._mirror_watch.done():
                self._mirror_watch = asyncio.create_task(self._watch_mirror())
            return {"url": MIRROR_URL}

    async def stop_mirror(self) -> None:
        async with self._mirror_lock:
            await self._stop_mirror()

    async def _stop_mirror(self) -> None:
        proc, self._mirror, self._mirror_udid = self._mirror, None, None
        if proc is None or proc.returncode is not None:
            return
        # /quit lets it stop the stream and release the iPhone's screen-sharing session
        with contextlib.suppress(Exception):
            await asyncio.to_thread(_mirror_request, "/quit", b"", 5)
        try:
            await asyncio.wait_for(proc.wait(), 25)
        except asyncio.TimeoutError:
            with contextlib.suppress(ProcessLookupError):
                proc.kill()

    async def _drain_mirror(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout is not None
        while raw := await proc.stdout.readline():
            log.debug("mirror: %s", raw.decode("utf-8", "replace").rstrip())
        log.info("mirror process exited (%s)", await proc.wait())

    async def _watch_mirror(self) -> None:
        """Stop the mirror when nobody has watched it for a while: it keeps the iPhone's
        screen-sharing indicator on and the encoder busy."""
        while self._mirror is not None:
            await asyncio.sleep(15)
            try:
                info = json.loads(await asyncio.to_thread(_mirror_request, "/info", None, 5))
            except Exception:
                continue
            if info.get("clients"):
                self._mirror_idle_since = None
            elif self._mirror_idle_since is None:
                self._mirror_idle_since = time.time()
            elif time.time() - self._mirror_idle_since > MIRROR_IDLE_STOP:
                log.info("mirror: nobody watching, stopping")
                await self.stop_mirror()


def _mirror_request(path: str, body: Optional[bytes], timeout: float) -> bytes:
    req = urllib.request.Request(MIRROR_URL + path, data=body, method="POST" if body is not None else "GET")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _to_jpeg(png: bytes) -> bytes:
    img = Image.open(io.BytesIO(png))
    if img.height > MAX_HEIGHT:
        img = img.resize((round(img.width * MAX_HEIGHT / img.height), MAX_HEIGHT), Image.BILINEAR)
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=JPEG_QUALITY)
    return out.getvalue()
