"""Start the local server and open the UI in an app window.

Usage: run.py [--no-window] [--port N]

  --no-window  only start the server (don't open the Edge app window)
  --port N     listen on another port (default 8765)
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

HOST, DEFAULT_PORT = "127.0.0.1", 8765
LOG_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "iPhoneManager" / "logs"


def redirect_output_if_windowless() -> None:
    """Under pythonw.exe there is no console: sys.stdout/sys.stderr are None, which breaks uvicorn's
    logging and faulthandler. Send both to a log file instead."""
    if sys.stdout is not None and sys.stderr is not None:
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
    if sys.stdout is None:
        sys.stdout = fh
    if sys.stderr is None:
        sys.stderr = fh


def port_in_use(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.settimeout(0.5)
        return s.connect_ex((HOST, port)) == 0


def open_window(url: str, delay: float = 1.2) -> None:
    time.sleep(delay)
    edge_paths = [
        shutil.which("msedge"),
        r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for edge in edge_paths:
        if edge and Path(edge).exists():
            subprocess.Popen([edge, f"--app={url}", "--window-size=1280,860"])
            return
    webbrowser.open(url)


def main() -> None:
    parser = argparse.ArgumentParser(description="iPhone Manager")
    parser.add_argument("--no-window", action="store_true", help="don't open the app window")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help=f"port (default {DEFAULT_PORT})")
    args = parser.parse_args()
    url = f"http://{HOST}:{args.port}/"

    redirect_output_if_windowless()

    if port_in_use(args.port):
        # already running (e.g. the shortcut was clicked twice): just show the window
        if not args.no_window:
            open_window(url, delay=0)
        return

    import uvicorn

    if not args.no_window:
        threading.Thread(target=open_window, args=(url,), daemon=True).start()
    uvicorn.run("app.server:app", host=HOST, port=args.port, log_level="info")


if __name__ == "__main__":
    main()
