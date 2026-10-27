"""Recovery / DFU on macOS and Linux, straight over USB (libusb), as pymobiledevice3 does it.

There Apple's driver doesn't hold the device in Recovery or DFU, so libusb can open it. A thread
lists Apple's USB devices every second and reads the iBoot string descriptor of those in Recovery
or DFU ("CPID:8130 ... BDID:0C ECID:001A... SRNM:[...]"). Booted iPhones aren't listed here: the
app reaches them through usbmuxd, and enters Recovery over lockdown.

- Recovery -> Normal: IRecv set_autoboot(True) + reboot (what MobileDevice does on Windows).
- DFU only comes and goes with the hardware buttons: this module reports when it shows up.
"""

import concurrent.futures
import logging
import re
import threading
import time
from typing import Any, Callable, Optional

from . import i18n, system
from .i18n import _
from .modes import ModeError, product_type

log = logging.getLogger(__name__)

APPLE_VENDOR_ID = 0x05AC
RECOVERY_PIDS = {0x1280, 0x1281, 0x1282, 0x1283}
DFU_PIDS = {0x1222, 0x1227}  # WTF, DFU
POLL_SECONDS = 1.0


def _fields(descriptor: str) -> dict[str, str]:
    return dict(re.findall(r"(\w+):\[?([^\] ]+)\]?", descriptor))


class ModeWatcher:
    def __init__(self) -> None:
        self.error: Optional[str] = None
        self._devices: dict[int, dict[str, Any]] = {}
        self._lock = threading.Lock()
        self._busy = threading.Lock()  # one USB command at a time, and none while polling
        self._paused = threading.Event()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.ready = threading.Event()
        self._pool = concurrent.futures.ThreadPoolExecutor(1, thread_name_prefix="usb-modes")

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="usb-modes", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def pause(self) -> "concurrent.futures.Future":
        """Stop polling, so the IPSW update has the device to itself."""

        def run() -> None:
            self._paused.set()
            with self._lock:
                self._devices = {}

        return self._call(run)

    def resume(self) -> "concurrent.futures.Future":
        return self._call(self._paused.clear)

    def _run(self) -> None:
        try:
            system.use_bundled_libusb()
            import usb.core

            usb.core.find(idVendor=APPLE_VENDOR_ID)  # fails here if libusb can't be loaded
            self.error = None
        except Exception as e:
            log.exception("libusb unavailable")
            self.error = _("libusb isn't available: {error}", error=e)
            return
        finally:
            self.ready.set()
        while not self._stop.wait(POLL_SECONDS):
            if self._paused.is_set():
                continue
            with self._busy:
                try:
                    self._poll(usb.core)
                except Exception:
                    log.exception("USB poll failed")

    def _poll(self, core: Any) -> None:
        seen: dict[int, dict[str, Any]] = {}
        for dev in core.find(find_all=True, idVendor=APPLE_VENDOR_ID):
            pid = dev.idProduct
            if pid not in RECOVERY_PIDS and pid not in DFU_PIDS:
                continue
            key = (dev.bus, dev.address)
            known = next((d for d in self._devices.values() if d["_usb"] == key), None)
            if known:
                seen[known["ecid"]] = known
                continue
            try:
                import usb.util

                fields = _fields(usb.util.get_string(dev, dev.iSerialNumber) or "")
                usb.util.dispose_resources(dev)
            except Exception as e:  # on Linux: no permission to open it (udev rule missing)
                log.warning("can't read USB device %04X: %s", pid, e)
                continue
            if "ECID" not in fields:
                continue
            ecid = int(fields["ECID"], 16)
            seen[ecid] = {
                "ecid": ecid,
                "state": "dfu" if pid in DFU_PIDS else "recovery",
                "raw_state": None,
                "cpid": int(fields.get("CPID", "0"), 16),
                "bdid": int(fields.get("BDID", "0"), 16),
                "pid": pid,
                "serial": fields.get("SRNM"),
                "udid": None,
                "since": time.time(),
                "_usb": key,
            }
            log.info("device %X: %s", ecid, seen[ecid]["state"])
        with self._lock:
            self._devices = seen

    def _call(self, fn: Callable[[], Any]) -> "concurrent.futures.Future":
        if self.error:
            raise ModeError(self.error)
        return self._pool.submit(i18n.run_in_lang(fn))

    # ---------- queries ----------

    def devices(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(d) for d in self._devices.values()]
        for d in items:
            del d["_usb"]
            d["ecid"] = str(d["ecid"])
            d["product_type"] = product_type(d["cpid"], d["bdid"])
        return items

    # ---------- commands ----------

    def enter_recovery(self, ecid: int) -> "concurrent.futures.Future":
        # booted iPhones aren't watched here: the server falls back to lockdown's EnterRecovery
        raise KeyError(f"ECID {ecid:X}")

    def exit_recovery(self, ecid: int) -> "concurrent.futures.Future":
        with self._lock:
            d = self._devices.get(ecid)
        if d is None:
            raise ModeError(_("The device is no longer in Recovery mode"))
        if d["state"] != "recovery":
            raise ModeError(_("The device isn't in the expected mode (it is in {state})", state=d["state"]))

        def run() -> None:
            from pymobiledevice3.irecv import IRecv

            with self._busy:
                try:
                    irecv = IRecv(ecid=ecid, timeout=10, is_recovery=True)
                    # auto-boot=true makes iBoot boot iOS instead of stopping in Recovery again
                    irecv.set_autoboot(True)
                    irecv.reboot()
                except Exception as e:
                    raise ModeError(_("Couldn't leave Recovery: {error}", error=e)) from e

        return self._call(run)
