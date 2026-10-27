"""What differs between Windows, macOS and Linux: where the app keeps its data, showing a folder,
the native folder/file pickers and the libusb library pyusb loads."""

import os
import shutil
import subprocess
import sys
from pathlib import Path

IS_WINDOWS = sys.platform == "win32"
IS_MAC = sys.platform == "darwin"


def _data_dir() -> Path:
    if IS_WINDOWS:
        return Path(os.environ.get("LOCALAPPDATA", Path.home())) / "iPhoneManager"
    if IS_MAC:
        return Path.home() / "Library" / "Application Support" / "iPhoneManager"
    return Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "iPhoneManager"


DATA_DIR = _data_dir()


def open_folder(path: str) -> None:
    if IS_WINDOWS:
        subprocess.Popen(["explorer", path])
    elif IS_MAC:
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def _run(args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(args, capture_output=True, text=True)
    return proc.returncode, proc.stdout.strip()


def _applescript_text(text: str) -> str:
    return '"' + text.replace("\\", "\\\\").replace('"', '\\"') + '"'


def pick(kind: str, title: str, filter_name: str = "") -> str:
    """Native folder (kind "folder") or .ipsw file picker. Returns "" when cancelled."""
    if IS_MAC:
        # Tk must run on the main thread on macOS; AppleScript's dialogs don't care
        if kind == "folder":
            script = f"POSIX path of (choose folder with prompt {_applescript_text(title)})"
        else:
            script = f"POSIX path of (choose file with prompt {_applescript_text(title)} of type {{\"ipsw\"}})"
        code, out = _run(["osascript", "-e", script])
        return out if code == 0 else ""
    if not IS_WINDOWS:
        if shutil.which("zenity"):
            args = ["zenity", "--file-selection", f"--title={title}"]
            args += ["--directory"] if kind == "folder" else [f"--file-filter={filter_name} | *.ipsw"]
            code, out = _run(args)
            return out if code == 0 else ""
        if shutil.which("kdialog"):
            args = ["kdialog", "--title", title]
            args += ["--getexistingdirectory", str(Path.home())] if kind == "folder" else \
                ["--getopenfilename", str(Path.home()), "*.ipsw"]
            code, out = _run(args)
            return out if code == 0 else ""
    import tkinter as tk
    from tkinter import filedialog

    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    try:
        if kind == "folder":
            return filedialog.askdirectory(title=title) or ""
        return filedialog.askopenfilename(title=title, filetypes=[(filter_name, "*.ipsw"), ("*", "*.*")]) or ""
    finally:
        root.destroy()


def use_bundled_libusb() -> None:
    """Make pyusb load the libusb that comes with the `libusb` package (macOS and Linux don't ship
    one where ctypes looks, and a signed macOS app ignores DYLD_* variables)."""
    if IS_WINDOWS:
        return
    try:
        from libusb._platform import DLL_PATH
        import usb.backend.libusb1 as libusb1
    except Exception:
        return
    if getattr(libusb1.get_backend, "_bundled", False) or not Path(DLL_PATH).is_file():
        return
    original = libusb1.get_backend

    def get_backend(find_library=None):
        return original(find_library=find_library or (lambda _name: str(DLL_PATH)))

    get_backend._bundled = True
    libusb1.get_backend = get_backend
