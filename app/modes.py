"""Recovery / DFU through Apple's MobileDevice.dll (the same library iMazing and Apple Devices use).

MobileDevice reports every Apple device it can see, in any boot state, as an
AMRestorableDevice: booted iOS (through usbmuxd), Recovery (iBoot) and DFU (talking to Apple's
USB driver directly). Its notifications and commands run on a CoreFoundation run loop, so the
library lives on one dedicated thread and every call into it is queued onto that thread.

- Normal  -> Recovery: AMDeviceEnterRecovery (lockdown "EnterRecovery").
- Recovery -> Normal:  AMRecoveryModeDeviceSetAutoBoot(true) + AMRecoveryModeDeviceReboot.
- DFU can only be entered with the hardware buttons, and left with a force restart: the UI guides
  the user and this module reports when the device shows up (or leaves) in DFU.
"""

import concurrent.futures
import ctypes
import logging
import os
import queue
import threading
import time
from ctypes import CFUNCTYPE, c_bool, c_char_p, c_double, c_int, c_uint32, c_uint64, c_void_p
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from . import i18n, mobiledevice
from .i18n import _

log = logging.getLogger(__name__)

# AMRestorableDeviceGetState (seen: 2 in Recovery, 4 booted). DFU and Recovery are also told apart by
# which mode-device object the restorable device carries, see _state().
STATES = {2: "recovery", 3: "restore", 4: "normal"}
EVENT_CONNECTED, EVENT_DISCONNECTED = 0, 1
UTF8 = 0x08000100

_Callback = CFUNCTYPE(None, c_void_p, c_int, c_void_p)


class ModeError(Exception):
    pass


def _err(code: int) -> str:
    return f"0x{code & 0xFFFFFFFF:08X}"


class ModeWatcher:
    def __init__(self) -> None:
        self.error: Optional[str] = None
        self._devices: dict[int, dict[str, Any]] = {}  # ecid -> info (+ "_ref": retained AMRestorableDeviceRef)
        self._lock = threading.Lock()
        self._calls: "queue.Queue[tuple[Callable[[], Any], concurrent.futures.Future]]" = queue.Queue()
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self.ready = threading.Event()  # set once the library is loaded (or failed to load)
        self._client: Optional[int] = None  # notification registration
        self._callback = _Callback(self._on_event)  # keep a reference: ctypes would free it

    # ---------- lifecycle ----------

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="MobileDevice", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()

    def _load(self) -> None:
        exe = mobiledevice.find_executable()
        if exe is None:
            raise ModeError(_("MobileDevice.dll not found (install iMazing or the Apple Devices app)"))
        folder = str(exe.parent)
        os.add_dll_directory(folder)
        os.environ["PATH"] = folder + os.pathsep + os.environ.get("PATH", "")
        cf = ctypes.CDLL(os.path.join(folder, "CoreFoundation.dll"))
        md = ctypes.CDLL(os.path.join(folder, "MobileDevice.dll"))

        cf.CFRunLoopRunInMode.argtypes = [c_void_p, c_double, c_bool]
        cf.CFStringGetCString.argtypes = [c_void_p, c_char_p, c_int, c_uint32]
        cf.CFStringGetCString.restype = c_bool
        cf.CFRetain.argtypes = [c_void_p]
        cf.CFRetain.restype = c_void_p
        cf.CFRelease.argtypes = [c_void_p]
        cf.CFStringCreateWithCString.argtypes = [c_void_p, c_char_p, c_uint32]
        cf.CFStringCreateWithCString.restype = c_void_p

        def fn(name: str, restype: Any, *argtypes: Any) -> None:
            f = getattr(md, name)
            f.restype = restype
            f.argtypes = list(argtypes)

        fn("AMRestorableDeviceRegisterForNotifications", c_int, _Callback, c_void_p, c_void_p)
        fn("AMRestorableDeviceGetState", c_int, c_void_p)
        fn("AMRestorableDeviceGetECID", c_uint64, c_void_p)
        fn("AMRestorableDeviceGetChipID", c_uint32, c_void_p)
        fn("AMRestorableDeviceGetBoardID", c_uint32, c_void_p)
        fn("AMRestorableDeviceCopySerialNumber", c_void_p, c_void_p)
        fn("AMRestorableDeviceCopyAMDevice", c_void_p, c_void_p)
        fn("AMRestorableDeviceGetRecoveryModeDevice", c_void_p, c_void_p)
        fn("AMRestorableDeviceGetDFUModeDevice", c_void_p, c_void_p)
        fn("AMDeviceCopyDeviceIdentifier", c_void_p, c_void_p)
        for name in ("AMDeviceConnect", "AMDeviceDisconnect", "AMDeviceValidatePairing",
                     "AMDeviceStartSession", "AMDeviceStopSession", "AMDeviceEnterRecovery"):
            fn(name, c_int, c_void_p)
        fn("AMRecoveryModeDeviceSetAutoBoot", c_int, c_void_p, c_bool)
        fn("AMRecoveryModeDeviceReboot", c_int, c_void_p)
        # iBoot transport used by the IPSW update (see irecv_md.py)
        fn("AMRestorableDeviceUnregisterForNotifications", c_bool, c_int)
        fn("AMRestorableDeviceGetProductID", ctypes.c_uint16, c_void_p)
        fn("AMRecoveryModeDeviceSendFileToDevice", c_int, c_void_p, c_void_p)
        fn("AMRecoveryModeDeviceSendCommandToDevice", c_int, c_void_p, c_void_p)
        fn("AMRecoveryModeDeviceSendBlindCommandToDevice", c_int, c_void_p, c_void_p)
        fn("AMRecoveryModeDeviceCopyEnvironmentVariableFromDevice", c_int, c_void_p, c_void_p, ctypes.POINTER(c_void_p))

        self._cf, self._md = cf, md
        self._mode = c_void_p.in_dll(cf, "kCFRunLoopDefaultMode")

    def _run(self) -> None:
        try:
            self._load()
            self._register()
            self.error = None
        except Exception as e:
            log.exception("MobileDevice unavailable")
            self.error = str(e)
            return
        finally:
            self.ready.set()
        while not self._stop.is_set():
            self._cf.CFRunLoopRunInMode(self._mode, 0.2, False)
            while True:
                try:
                    call, fut = self._calls.get_nowait()
                except queue.Empty:
                    break
                if fut.set_running_or_notify_cancel():
                    try:
                        fut.set_result(call())
                    except BaseException as e:
                        fut.set_exception(e)

    def _register(self) -> None:
        err = c_void_p()
        client = self._md.AMRestorableDeviceRegisterForNotifications(self._callback, None, ctypes.byref(err))
        if client == 0:
            raise ModeError(_("MobileDevice: couldn't register for device notifications"))
        self._client = client

    def pause(self) -> "concurrent.futures.Future":
        """Stop watching and drop every device handle, so another process (the IPSW update) has the
        device to itself."""

        def run() -> None:
            if self._client is not None:
                self._md.AMRestorableDeviceUnregisterForNotifications(self._client)
                self._client = None
            with self._lock:
                old, self._devices = self._devices, {}
            for d in old.values():
                self._cf.CFRelease(d["_ref"])

        return self._call(run)

    def resume(self) -> "concurrent.futures.Future":
        def run() -> None:
            if self._client is None:
                self._register()

        return self._call(run)

    def call(self, fn: Callable[[], Any], timeout: Optional[float] = None) -> Any:
        """Run fn on the MobileDevice thread and wait for its result."""
        return self._call(fn).result(timeout)

    def find(self, ecid: Optional[int], states: tuple[str, ...]) -> Optional[dict[str, Any]]:
        """Info of the device with this ECID (or any device) in one of these states. Its "_ref" is
        retained for the caller (CFRelease it), so a disconnect can't free it underneath."""
        with self._lock:  # _on_event pops under this lock before releasing
            for d in self._devices.values():
                if (ecid is None or d["ecid"] == ecid) and d["state"] in states:
                    self._cf.CFRetain(d["_ref"])
                    return dict(d)
        return None

    def cfstring(self, text: str) -> int:
        """New CFStringRef (caller releases it)."""
        return self._cf.CFStringCreateWithCString(None, text.encode(), UTF8)

    def _on_event(self, ref: int, event: int, _ctx: int) -> None:
        try:
            md = self._md
            ecid = md.AMRestorableDeviceGetECID(ref)
            if event == EVENT_DISCONNECTED:
                with self._lock:
                    old = self._devices.pop(ecid, None)
                if old:
                    self._cf.CFRelease(old["_ref"])
                return
            if event != EVENT_CONNECTED:
                return
            raw_state = md.AMRestorableDeviceGetState(ref)
            info = {
                "ecid": ecid,
                "state": self._state(ref, raw_state),
                "raw_state": raw_state,
                "cpid": md.AMRestorableDeviceGetChipID(ref),
                "bdid": md.AMRestorableDeviceGetBoardID(ref),
                "pid": md.AMRestorableDeviceGetProductID(ref),
                "serial": self._string(md.AMRestorableDeviceCopySerialNumber(ref)),
                "udid": None,
                "since": time.time(),
                "_ref": self._cf.CFRetain(ref),
            }
            if info["state"] == "normal":
                amdev = md.AMRestorableDeviceCopyAMDevice(ref)
                if amdev:
                    info["udid"] = self._string(md.AMDeviceCopyDeviceIdentifier(amdev))
                    self._cf.CFRelease(amdev)
            with self._lock:
                old = self._devices.get(ecid)
                self._devices[ecid] = info
            if old:
                self._cf.CFRelease(old["_ref"])
            log.info("device %X: %s (%d)", ecid, info["state"], raw_state)
        except Exception:
            log.exception("MobileDevice notification failed")

    def _state(self, ref: int, raw_state: int) -> str:
        md = self._md
        if md.AMRestorableDeviceGetDFUModeDevice(ref):
            return "dfu"
        if md.AMRestorableDeviceGetRecoveryModeDevice(ref):
            return "recovery"
        if raw_state in STATES:
            return STATES[raw_state]
        amdev = md.AMRestorableDeviceCopyAMDevice(ref)
        if amdev:
            self._cf.CFRelease(amdev)
            return "normal"
        return "unknown"

    def _string(self, ref: Optional[int]) -> Optional[str]:
        """CFStringRef (from a Copy function) -> str, releasing it."""
        if not ref:
            return None
        try:
            buf = ctypes.create_string_buffer(1024)
            return buf.value.decode() if self._cf.CFStringGetCString(ref, buf, len(buf), UTF8) else None
        finally:
            self._cf.CFRelease(ref)

    def _call(self, fn: Callable[[], Any]) -> "concurrent.futures.Future":
        if self.error or not self._thread or not self._thread.is_alive():
            # self.error was set on the MobileDevice thread, in English: translate it here
            raise ModeError(_(self.error) if self.error else _("MobileDevice is not running"))
        fut: concurrent.futures.Future = concurrent.futures.Future()
        # fn runs on the MobileDevice thread: in the caller's language, for its error messages
        self._calls.put((i18n.run_in_lang(fn), fut))
        return fut

    # ---------- queries ----------

    def devices(self) -> list[dict[str, Any]]:
        with self._lock:
            items = [dict(d) for d in self._devices.values()]
        for d in items:
            del d["_ref"]
            d["ecid"] = str(d["ecid"])  # 64-bit: keep it exact in JSON
            d["product_type"] = product_type(d["cpid"], d["bdid"])
        return items

    def _ref(self, ecid: int, *states: str) -> int:
        with self._lock:
            d = self._devices.get(ecid)
        if d is None:
            raise KeyError(f"ECID {ecid:X}")
        if states and d["state"] not in states:
            raise ModeError(_("The device isn't in the expected mode (it is in {state})", state=d["state"]))
        return d["_ref"]

    # ---------- commands ----------

    def enter_recovery(self, ecid: int) -> "concurrent.futures.Future":
        ref = self._ref(ecid, "normal")

        def run() -> None:
            md = self._md
            amdev = md.AMRestorableDeviceCopyAMDevice(ref)
            if not amdev:
                raise ModeError(_("The device can't be reached over USB"))
            try:
                rc = md.AMDeviceConnect(amdev)
                if rc:
                    raise ModeError(_("{call} failed ({code})", call="AMDeviceConnect", code=_err(rc)))
                try:
                    if md.AMDeviceValidatePairing(amdev) == 0 and md.AMDeviceStartSession(amdev) == 0:
                        rc = md.AMDeviceEnterRecovery(amdev)
                        md.AMDeviceStopSession(amdev)
                    else:
                        rc = md.AMDeviceEnterRecovery(amdev)
                finally:
                    md.AMDeviceDisconnect(amdev)
                if rc:
                    raise ModeError(_("{call} failed ({code})", call="AMDeviceEnterRecovery", code=_err(rc)))
            finally:
                self._cf.CFRelease(amdev)

        return self._call(run)

    def exit_recovery(self, ecid: int) -> "concurrent.futures.Future":
        ref = self._ref(ecid, "recovery")

        def run() -> None:
            md = self._md
            rec = md.AMRestorableDeviceGetRecoveryModeDevice(ref)
            if not rec:
                raise ModeError(_("The device is no longer in Recovery mode"))
            # auto-boot=true makes iBoot boot iOS instead of stopping in Recovery again
            rc = md.AMRecoveryModeDeviceSetAutoBoot(rec, True)
            if rc:
                raise ModeError(_("{call} failed ({code})", call="AMRecoveryModeDeviceSetAutoBoot", code=_err(rc)))
            rc = md.AMRecoveryModeDeviceReboot(rec)
            if rc:
                raise ModeError(_("{call} failed ({code})", call="AMRecoveryModeDeviceReboot", code=_err(rc)))

        return self._call(run)


# ---------- device catalogue (ipsw.me): CPID/BDID -> ProductType, ProductType -> name ----------

_boards: Optional[dict[tuple[int, int], str]] = None
_names: dict[str, str] = {}
_boards_at = 0.0


def _load_catalogue() -> None:
    global _boards, _boards_at
    if _boards is not None or time.time() - _boards_at < 300:
        return
    _boards_at = time.time()
    try:
        r = requests.get("https://api.ipsw.me/v4/devices", timeout=10)
        r.raise_for_status()
        devices = r.json()
        _boards = {
            (b["cpid"], b["bdid"]): dev["identifier"]
            for dev in devices
            for b in dev.get("boards", [])
            if b.get("cpid") is not None and b.get("bdid") is not None
        }
        _names.update({dev["identifier"]: dev["name"] for dev in devices if dev.get("name")})
    except Exception as e:
        log.warning("ipsw.me device list unavailable: %s", e)


def product_type(cpid: int, bdid: int) -> Optional[str]:
    """Recovery/DFU only report the board."""
    _load_catalogue()
    return (_boards or {}).get((cpid, bdid))


def model_name(identifier: Optional[str]) -> Optional[str]:
    """"iPhone16,2" -> "iPhone 15 Pro Max"."""
    if not identifier:
        return None
    _load_catalogue()
    return _names.get(identifier)
