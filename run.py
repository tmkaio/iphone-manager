"""Start the local server and open the UI in an app window.

Usage: run.py [--no-window] [--port N] [--detach]

  --no-window  only start the server (don't open the app window)
  --port N     listen on another port (default 8765)
  --detach     start the server in the background, open the window and return (macOS app,
               Linux AppImage: closing the window doesn't stop the server, opening the app again
               only shows the window)
"""

import argparse
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import webbrowser
from pathlib import Path

ROOT = Path(__file__).resolve().parent
# the installed build runs on an embeddable Python, which doesn't add the script dir to sys.path
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
os.chdir(ROOT)

from app.system import DATA_DIR, IS_MAC, IS_WINDOWS  # noqa: E402

HOST, DEFAULT_PORT = "127.0.0.1", 8765
LOG_DIR = DATA_DIR / "logs"

# browsers that open a page as an app window (no tabs or address bar)
MAC_APP_BROWSERS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
    "/Applications/Brave Browser.app/Contents/MacOS/Brave Browser",
    "/Applications/Chromium.app/Contents/MacOS/Chromium",
]
LINUX_APP_BROWSERS = ["google-chrome", "google-chrome-stable", "chromium", "chromium-browser",
                      "microsoft-edge", "microsoft-edge-stable", "brave-browser"]


def redirect_output(always: bool = False) -> None:
    """Under pythonw.exe there is no console: sys.stdout/sys.stderr are None, which breaks uvicorn's
    logging and faulthandler. Send both to a log file instead (also for the detached server, whose
    output nobody sees)."""
    if not always and sys.stdout is not None and sys.stderr is not None:
        return
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    path = LOG_DIR / "app.log"
    try:
        if path.exists() and path.stat().st_size > 5 * 1024 * 1024:
            path.replace(path.with_suffix(".old.log"))
    except OSError:
        pass
    fh = open(path, "a", encoding="utf-8", buffering=1, errors="replace")
    fh.write(f"\n===== {time.strftime('%Y-%m-%d %H:%M:%S')} start =====\n")
    if always or sys.stdout is None:
        sys.stdout = fh
    if always or sys.stderr is None:
        sys.stderr = fh


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def _app_browsers() -> list:
    if IS_WINDOWS:
        return [
            shutil.which("msedge"),
            r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
            r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
        ]
    if IS_MAC:
        return MAC_APP_BROWSERS
    return [shutil.which(name) for name in LINUX_APP_BROWSERS]


def open_window(url: str, delay: float = 1.2) -> None:
    time.sleep(delay)
    for browser in _app_browsers():
        if browser and Path(browser).exists():
            subprocess.Popen([browser, f"--app={url}", "--window-size=1280,860"],
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, start_new_session=not IS_WINDOWS)
            return
    if IS_MAC:
        subprocess.Popen(["open", url])  # the default browser
    else:
        webbrowser.open(url)


def start_detached(port: int) -> bool:
    """Start the server as its own process (it outlives this one) and wait until it answers."""
    subprocess.Popen([sys.executable, str(ROOT / "run.py"), "--no-window", "--port", str(port), "--log-to-file"],
                     cwd=str(ROOT), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                     stderr=subprocess.DEVNULL, start_new_session=True)
    for _ in range(120):
        if port_in_use(port):
            return True
        time.sleep(0.25)
    return False


def main() -> None:
    parser = argparse.ArgumentParser(description="iPhone Manager")
    parser.add_argument("--no-window", action="store_true", help="don't open the app window")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    parser.add_argument("--detach", action="store_true", help="run the server in the background and return")
    parser.add_argument("--log-to-file", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    url = f"http://{HOST}:{args.port}/"

    redirect_output(always=args.log_to_file)

    if port_in_use(args.port):
        # already running (e.g. the shortcut was clicked twice): just show the window
        if not args.no_window:
            open_window(url, delay=0)
        return

    if args.detach:
        if start_detached(args.port) and not args.no_window:
            open_window(url, delay=0)
        return

    import uvicorn

    if not args.no_window:
        threading.Thread(target=open_window, args=(url,), daemon=True).start()
    uvicorn.run("app.server:app", host=HOST, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
