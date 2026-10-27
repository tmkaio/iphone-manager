"""README screenshots, taken from the demo server (python -m app.demo) with headless Edge.

    python scripts/screenshots.py [--url http://127.0.0.1:8790/] [--out docs/screenshots]

Drives the page over the Chrome DevTools Protocol: no extra packages beyond `websockets`
(a dependency of the app's own stack).
"""

import argparse
import asyncio
import base64
import json
import shutil
import subprocess
import tempfile
import time
import urllib.request
from pathlib import Path

import websockets

ROOT = Path(__file__).resolve().parent.parent
EDGE = [r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe"]
PORT = 9339
W, H = 1440, 900

# name, language, JavaScript run before the capture, seconds to wait
SHOTS = [
    ("overview", "en", "", 1.5),
    ("photos", "en", "document.querySelector('[data-view=photos]').click()", 4),
    ("photos-size", "en", """document.querySelector('[data-view=photos]').click();
        await sleep(1500); const s = document.querySelector('#ph-sort'); s.value = 'size-desc';
        s.dispatchEvent(new Event('change')); const g = document.querySelector('#ph-group'); g.value = 'none';
        g.dispatchEvent(new Event('change'))""", 3),
    ("album", "en", """document.querySelector('[data-view=photos]').click(); await sleep(2000);
        [...document.querySelectorAll('#ph-side .side-item')].find(e => /City walks/.test(e.textContent))?.click()""", 3),
    ("lightbox", "en", """document.querySelector('[data-view=photos]').click(); await sleep(2000);
        document.querySelector('#ph-filter [data-f=image]').click(); await sleep(800);
        [...document.querySelectorAll('#ph-grid .tile')].filter(e => !e.classList.contains('cloud'))[3]?.click()""", 3),
    ("apps", "en", "document.querySelector('[data-view=apps]').click()", 3),
    ("files", "en", """document.querySelector('[data-view=files]').click(); await sleep(1500);
        document.querySelector('#fs-mode [data-m=flat]').click(); await sleep(800);
        const s = document.querySelector('#fs-sort'); s.value = 'size-desc'; s.dispatchEvent(new Event('change'))""", 3),
    ("screen", "en", "document.querySelector('[data-view=screen]').click()", 3),
    ("update", "en", "document.querySelector('[data-view=update]').click()", 3),
    ("overview-pt", "pt", "", 1.5),
    ("photos-pt", "pt", "document.querySelector('[data-view=photos]').click()", 4),
]


class Page:
    def __init__(self, ws):
        self.ws, self.n = ws, 0

    async def call(self, method: str, **params):
        self.n += 1
        await self.ws.send(json.dumps({"id": self.n, "method": method, "params": params}))
        while True:
            msg = json.loads(await self.ws.recv())
            if msg.get("id") == self.n:
                if "error" in msg:
                    raise RuntimeError(f"{method}: {msg['error']}")
                return msg.get("result", {})


async def shoot(url: str, out: Path) -> None:
    edge = next((p for p in EDGE if Path(p).exists()), shutil.which("msedge"))
    profile = tempfile.mkdtemp(prefix="ipm-shots-")
    proc = subprocess.Popen([edge, "--headless=new", f"--remote-debugging-port={PORT}", f"--user-data-dir={profile}",
                             f"--window-size={W},{H}", "--hide-scrollbars", "--force-device-scale-factor=1",
                             "about:blank"])
    try:
        for _attempt in range(50):
            try:
                targets = json.load(urllib.request.urlopen(f"http://127.0.0.1:{PORT}/json"))
                break
            except OSError:
                time.sleep(0.2)
        target = next(t for t in targets if t["type"] == "page")
        async with websockets.connect(target["webSocketDebuggerUrl"], max_size=None) as ws:
            page = Page(ws)
            await page.call("Emulation.setDeviceMetricsOverride", width=W, height=H, deviceScaleFactor=1, mobile=False)
            for name, lang, script, wait in SHOTS:
                await page.call("Page.navigate", url=url)
                await asyncio.sleep(0.8)
                await page.call("Runtime.evaluate", expression=f"localStorage.clear(); localStorage.setItem('lang', '{lang}')")
                await page.call("Page.navigate", url=url)
                await asyncio.sleep(2)
                if script:
                    await page.call("Runtime.evaluate", awaitPromise=True, expression=(
                        "(async () => { const sleep = (ms) => new Promise(r => setTimeout(r, ms)); "
                        + script + " })()"))
                await asyncio.sleep(wait)
                shot = await page.call("Page.captureScreenshot", format="png")
                (out / f"{name}.png").write_bytes(base64.b64decode(shot["data"]))
                print("saved", out / f"{name}.png")
    finally:
        proc.terminate()
        shutil.rmtree(profile, ignore_errors=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--url", default="http://127.0.0.1:8790/")
    parser.add_argument("--out", default=str(ROOT / "docs" / "screenshots"))
    args = parser.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    asyncio.run(shoot(args.url, out))
