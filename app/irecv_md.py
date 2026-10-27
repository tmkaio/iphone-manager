"""pymobiledevice3's Recovery-mode (iBoot) USB transport, rebuilt on Apple's MobileDevice.dll.

On Windows the iPhone in Recovery is owned by Apple's driver (WinUSB plus Apple's UMDF filter),
so libusb sees it but every transfer fails and pymobiledevice3's IRecv waits forever for a device
it can open. MobileDevice.dll talks to that driver, so this class gives the restore code the same
IRecv interface on top of it: send a file, send a command (blind for the ones that reboot iBoot,
like libirecovery's bRequest 1), getenv, auto-boot and reboot.

Everything else in the update (TSS signing, the "Update" build identity that keeps user data, the
restore OS phase over usbmux) stays pymobiledevice3's own code.
"""

import array
import ctypes
import logging
import os
import sys
import tempfile
import time
from typing import Any, Optional

from usb.core import USBError

from pymobiledevice3.exceptions import IRecvNoDeviceConnectedError
from pymobiledevice3.irecv import IBOOT_FLAG_IMAGE4_AWARE, IRecv, Mode

from .i18n import _
from .modes import ModeWatcher, _err

logger = logging.getLogger(__name__)

# pymobiledevice3 waits forever by default; a device that doesn't show up in this long is not coming
MAX_WAIT = 300
COMMAND_TIMEOUT = 60
FILE_TIMEOUT = 600

# Chips before the A7 (CPID numbers aren't ordered by age: A7 is 0x8960, A17 Pro is 0x8130)
PRE_IMAGE4_CPIDS = {0x8920, 0x8922, 0x8930, 0x8940, 0x8942, 0x8945, 0x8947, 0x8950, 0x8955}

_watcher: Optional[ModeWatcher] = None


class MobileDeviceIRecv(IRecv):
    def __init__(self, ecid: Optional[int] = None, timeout: int = 0xFFFFFFFF, is_recovery: Optional[bool] = None):
        self._ref: Optional[int] = None
        super().__init__(ecid=ecid, timeout=timeout, is_recovery=is_recovery)

    # ---------- connection ----------

    def _reinit(self, ecid: Optional[int] = None, timeout: int = 0xFFFFFFFF, is_recovery: Optional[bool] = None):
        assert _watcher is not None
        states = ("recovery",) if is_recovery else ("dfu",) if is_recovery is False else ("recovery", "dfu")
        deadline = time.time() + min(timeout, MAX_WAIT)
        found = None
        while found is None:
            found = _watcher.find(ecid, states)
            if found is None:
                if time.time() > deadline:
                    raise IRecvNoDeviceConnectedError(_(
                        "The iPhone didn't show up in {mode} mode within {seconds} s",
                        mode="/".join(states), seconds=min(timeout, MAX_WAIT),
                    ))
                time.sleep(0.5)
        self._release()
        self._ref = found["_ref"]  # retained by find()
        self._device = self._ref  # IRecv.device must not be None
        try:
            self._mode = Mode(found["pid"])
        except ValueError:
            self._mode = Mode.RECOVERY_MODE_2 if found["state"] == "recovery" else Mode.DFU_MODE
        image4 = found["cpid"] not in PRE_IMAGE4_CPIDS
        self._device_info = {
            "ECID": f"{found['ecid']:016X}",
            "CPID": f"{found['cpid']:04X}",
            "BDID": f"{found['bdid']:02X}",
            "IBFL": f"{IBOOT_FLAG_IMAGE4_AWARE if image4 else 0:02X}",
        }
        if found.get("serial"):
            self._device_info["SRNM"] = found["serial"]
        # Only read by the DFU path and the TSS request, which this update makes over lockdown
        self.ap_nonce = None
        self.sep_nonce = None
        logger.info("Recovery via MobileDevice.dll: %s (%s)", self._device_info, self._mode.name)

    def _release(self) -> None:
        if self._ref:
            _watcher._cf.CFRelease(self._ref)
            self._ref = None

    def _recovery_device(self) -> int:
        rec = _watcher._md.AMRestorableDeviceGetRecoveryModeDevice(self._ref)
        if not rec:
            raise USBError(_("The device is no longer in Recovery mode"))
        return rec

    def _command(self, cmd: str, blind: bool) -> None:
        md = _watcher._md

        def run() -> int:
            rec = self._recovery_device()
            s = _watcher.cfstring(cmd)
            try:
                send = md.AMRecoveryModeDeviceSendBlindCommandToDevice if blind else md.AMRecoveryModeDeviceSendCommandToDevice
                return send(rec, s)
            finally:
                _watcher._cf.CFRelease(s)

        rc = _watcher.call(run, COMMAND_TIMEOUT)
        if rc:
            raise USBError(_("command '{cmd}' failed ({code})", cmd=cmd, code=_err(rc)))

    # ---------- IRecv API used by pymobiledevice3.restore ----------

    def send_command(self, cmd: str, timeout: int = 0, b_request: int = 0):
        # bRequest 1 ("go", "bootx", "reboot"): iBoot leaves without answering
        logger.debug("iBoot command: %s", cmd)
        self._command(cmd, blind=b_request == 1)

    def send_buffer(self, buf: bytes, desc: Optional[str] = None):
        md = _watcher._md
        fd, path = tempfile.mkstemp(prefix="ipsw-", suffix=".img4")
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(buf)

            def run() -> int:
                rec = self._recovery_device()
                s = _watcher.cfstring(path)
                try:
                    return md.AMRecoveryModeDeviceSendFileToDevice(rec, s)
                finally:
                    _watcher._cf.CFRelease(s)

            rc = _watcher.call(run, FILE_TIMEOUT)
            name = desc or _("file")
            if rc:
                raise USBError(_("sending {name} failed ({code})", name=name, code=_err(rc)))
            print(_("{name}: 100% ({size} bytes sent)", name=name, size=len(buf)), flush=True)
        finally:
            os.unlink(path)

    def getenv(self, name: str):
        md, cf = _watcher._md, _watcher._cf

        def run() -> Optional[bytearray]:
            try:
                rec = self._recovery_device()
            except USBError:
                return None
            s = _watcher.cfstring(name)
            out = ctypes.c_void_p()
            try:
                rc = md.AMRecoveryModeDeviceCopyEnvironmentVariableFromDevice(rec, s, out)
            finally:
                cf.CFRelease(s)
            if rc or not out.value:
                return None
            value = _watcher._string(out.value)  # releases it
            return bytearray(value.encode()) if value else None

        return _watcher.call(run, COMMAND_TIMEOUT)

    def set_autoboot(self, enable: bool):
        rc = _watcher.call(lambda: _watcher._md.AMRecoveryModeDeviceSetAutoBoot(self._recovery_device(), enable),
                           COMMAND_TIMEOUT)
        if rc:
            raise USBError(_("{call} failed ({code})", call="auto-boot", code=_err(rc)))

    def reboot(self):
        try:
            _watcher.call(lambda: _watcher._md.AMRecoveryModeDeviceReboot(self._recovery_device()), COMMAND_TIMEOUT)
        except USBError:
            pass

    def ctrl_transfer(self, bmRequestType: int, bRequest: int, timeout: int = 0, **kwargs: Any) -> "array[int]":
        # The only raw transfer the recovery path makes is the zero-length "upload finished" request
        # (0x21, 1) after a file; MobileDevice's SendFile already completes the transfer.
        logger.debug("ctrl_transfer(0x%x, %d) handled by MobileDevice", bmRequestType, bRequest)
        return array.array("B")

    def set_configuration(self, configuration: Optional[int] = None):
        pass

    def set_interface_altsetting(self, interface: Optional[int] = None, alternate_setting: Optional[int] = None):
        pass

    def __exit__(self, *exc: Any):
        self._release()
        self._device = None


def install() -> bool:
    """Swap pymobiledevice3's IRecv for the MobileDevice one. Returns False (keeps libusb) if the
    library can't be loaded."""
    global _watcher
    if sys.platform != "win32":
        return False
    _watcher = ModeWatcher()
    _watcher.start()
    _watcher.ready.wait(15)
    if _watcher.error or not _watcher.ready.is_set():
        print(_("MobileDevice.dll unavailable ({error}); using libusb", error=_(_watcher.error or "")), flush=True)
        _watcher = None
        return False

    import pymobiledevice3.cli.restore
    import pymobiledevice3.irecv
    import pymobiledevice3.restore.recovery

    for module in (pymobiledevice3.irecv, pymobiledevice3.restore.recovery, pymobiledevice3.cli.restore):
        module.IRecv = MobileDeviceIRecv
    print(_("Recovery mode: USB communication through MobileDevice.dll (Apple's driver)"), flush=True)
    return True
