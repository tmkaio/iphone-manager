"""Start Apple's Mobile Device service (usbmuxd for Windows) the same way iMazing does.

iMazing launches AppleMobileDeviceProcess.exe from its MobileDevice folder, with no arguments,
as the current user, and stops it when it exits. That process talks to Apple's USB driver and
serves the standard usbmuxd protocol on 127.0.0.1:27015, which pymobiledevice3 uses.

If Apple Devices/iTunes is installed, its Windows service already provides the port and we
don't start anything.

macOS has usbmuxd built in, and on Linux it is the `usbmuxd` package: there is nothing to start,
only the socket to check (/var/run/usbmuxd).
"""

import logging
import os
import socket
import subprocess
import time
from pathlib import Path
from typing import Optional

from .i18n import _
from .system import IS_MAC, IS_WINDOWS

log = logging.getLogger(__name__)

USBMUXD_ADDRESS = ("127.0.0.1", 27015)
USBMUXD_SOCKET = "/var/run/usbmuxd"
PROCESS_NAME = "AppleMobileDeviceProcess.exe"

CANDIDATES = [
    Path(os.environ.get("ProgramData", r"C:\ProgramData")) / "DigiDNA" / "iMazing" / "MobileDevice" / "Current",
    Path(os.environ.get("CommonProgramFiles", r"C:\Program Files\Common Files")) / "Apple" / "Mobile Device Support",
]

_proc: Optional[subprocess.Popen] = None
_service_seen_at: Optional[float] = None
_first_seen: dict[str, float] = {}

# The service only picks up an iPhone that is (re)connected while it is running. When the app
# starts with the phone already plugged in, a USB restart of the device does the same as
# unplugging and plugging it back.
GRACE_SECONDS = 8
ENUM_USB = r"SYSTEM\CurrentControlSet\Enum\USB"


def is_running() -> bool:
    try:
        if not IS_WINDOWS:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
                s.settimeout(0.5)
                s.connect(USBMUXD_SOCKET)
                return True
        with socket.create_connection(USBMUXD_ADDRESS, timeout=0.5):
            return True
    except OSError:
        return False


def find_executable() -> Optional[Path]:
    for folder in CANDIDATES:
        exe = folder / PROCESS_NAME
        if exe.is_file():
            return exe
    return None


def ensure_running() -> Optional[str]:
    """Make sure usbmuxd is available. Returns an error message for the UI, or None."""
    global _proc, _service_seen_at
    if is_running():
        if _service_seen_at is None:
            _service_seen_at = time.time()
        return None
    _service_seen_at = None
    if IS_MAC:
        return _("The Apple Mobile Device service (usbmuxd) isn't answering. Restart the Mac.")
    if not IS_WINDOWS:
        # udev starts usbmuxd when an iPhone is plugged in, and it exits when none is left
        if any(Path(d, "usbmuxd").is_file() for d in ("/usr/sbin", "/usr/bin", "/usr/local/sbin", "/sbin")):
            return None
        return _(
            "usbmuxd isn't running. Install it (for example: sudo apt install usbmuxd), "
            "then plug the iPhone in again."
        )
    if _proc is not None and _proc.poll() is None:
        return None  # still starting
    exe = find_executable()
    if exe is None:
        return _(
            "Apple Mobile Device service not found. Install iMazing or the Apple Devices app "
            "(Microsoft Store)."
        )
    log.info("starting %s", exe)
    _proc = subprocess.Popen(
        [str(exe)], cwd=str(exe.parent), creationflags=subprocess.CREATE_NO_WINDOW,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
    )
    return None


def stop() -> None:
    """Stop the service only if this app started it."""
    global _proc
    if _proc is not None and _proc.poll() is None:
        _proc.terminate()
    _proc = None
