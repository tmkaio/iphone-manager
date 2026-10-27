"""Demo mode: the real UI on made-up data, with no iPhone, for screenshots and trying the app out.

    python -m app.demo --fetch    # once: download free stock photos (Lorem Picsum / Unsplash license)
    python -m app.demo            # http://127.0.0.1:8790/

Serves app/static unchanged and answers the API the UI uses with a fictional iPhone: photos (stock
pictures from picsum.photos, some "only in iCloud"), albums, apps with drawn icons, files, a
drawn home screen for the mirror and a list of iOS versions. Nothing touches a real device and
every action that would change something just pretends to succeed.
"""

import argparse
import hashlib
import io
import random
import sys
import time
import urllib.request
from pathlib import Path
from typing import Any

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from PIL import Image, ImageDraw, ImageFilter, ImageFont

ROOT = Path(__file__).resolve().parent.parent
STATIC = Path(__file__).resolve().parent / "static"
ASSETS = ROOT / "build" / "demo-assets"
PORT = 8790
UDID = "00000000-DEMO0000DEMO0000"

# picsum.photos image ids (photos from Unsplash, free to use)
PICSUM_IDS = [10, 11, 13, 14, 15, 16, 17, 18, 19, 20, 22, 24, 25, 27, 28, 29, 33, 35, 36, 37, 39, 40, 42, 43, 44,
              46, 47, 48, 49, 50, 53, 54, 55, 56, 57, 58, 59, 61, 62, 63, 64, 65, 66, 67, 68, 69, 70, 71, 72, 73,
              74, 75, 76, 77, 78, 79, 80, 82, 83, 84, 85, 87, 88, 89, 90, 91, 92, 93, 94, 95, 96, 98, 99, 100,
              101, 102, 103, 104, 106, 107, 108, 109, 110, 111, 112, 113, 114, 115, 116, 117, 118, 119, 120, 121,
              122, 123, 124, 125, 126, 127, 128, 129, 130, 131, 132, 133, 134, 135, 136, 137, 139, 140, 141, 142]

F = dict(FAV=1, HIDDEN=2, TRASHED=4, LIVE=8, SCREENSHOT=16, PANO=32, SLOMO=64, TIMELAPSE=128, SCREENREC=256,
         PORTRAIT=512, CINEMATIC=1024, BURST=2048, ANIMATED=4096, SELFIE=8192, VIDEO=16384)


def fetch_assets() -> None:
    ASSETS.mkdir(parents=True, exist_ok=True)
    for n, pid in enumerate(PICSUM_IDS):
        target = ASSETS / f"{pid}.jpg"
        if target.exists():
            continue
        w, h = (900, 1200) if n % 3 == 0 else (1200, 900)
        url = f"https://picsum.photos/id/{pid}/{w}/{h}"
        try:
            with urllib.request.urlopen(url, timeout=30) as r:
                target.write_bytes(r.read())
            print("ok", url)
        except Exception as e:
            print("skip", url, e)


def images() -> list[Path]:
    found = sorted(ASSETS.glob("*.jpg"))
    if not found:
        sys.exit("No demo photos yet: run `python -m app.demo --fetch` first.")
    return found


# ---------------- made-up data ----------------

rng = random.Random(7)
NOW = time.time()


def _library() -> tuple[list[dict], list[list], list[dict], list[dict]]:
    scan, lib = [], []
    t = NOW - 3 * 3600
    for n in range(1, 361):
        t -= rng.uniform(0.2, 2.6) * 86400 * (0.4 if n < 60 else 1)
        video = rng.random() < 0.14
        flags = F["VIDEO"] if video else 0
        r = rng.random()
        if not video:
            if r < 0.18:
                flags |= F["LIVE"]
            elif r < 0.24:
                flags |= F["PORTRAIT"]
            elif r < 0.29:
                flags |= F["SCREENSHOT"]
            elif r < 0.31:
                flags |= F["PANO"]
            elif r < 0.36:
                flags |= F["SELFIE"]
        elif r < 0.15:
            flags |= F["SLOMO"]
        if rng.random() < 0.09:
            flags |= F["FAV"]
        if rng.random() < 0.015:
            flags |= F["HIDDEN"]
        name = f"IMG_{9000 - n:04d}.{'MOV' if video else ('PNG' if flags & F['SCREENSHOT'] else 'HEIC')}"
        path = f"/DCIM/{100 + (9000 - n) // 1000}APPLE/{name}"
        size = int(rng.uniform(40, 900) * 1e6) if video else int(rng.uniform(1.2, 5.5) * 1e6)
        cloud = n > 40 and rng.random() < 0.38
        guid = f"DEMO{n:04d}-0000-4000-8000-{n:012d}"
        lib.append([n, path, round(t), flags, size, guid])
        if not cloud:
            scan.append({"path": path, "name": name, "folder": path.rsplit("/", 1)[0],
                         "kind": "video" if video else "image", "size": size, "mtime": t, "btime": t,
                         **({"live": path[:-4] + "MOV", "live_size": int(size * 0.7)} if flags & F["LIVE"] else {})})
    pks = [i[0] for i in lib]
    folders = [{"id": 5000, "title": "Travel", "parent": None}]
    albums = [
        {"id": 5001, "title": "Mountains", "parent": 5000, "items": rng.sample(pks, 34)},
        {"id": 5002, "title": "Coastline", "parent": 5000, "items": rng.sample(pks, 27)},
        {"id": 5003, "title": "City walks", "parent": None, "items": rng.sample(pks, 41)},
        {"id": 5004, "title": "Coffee & food", "parent": None, "items": rng.sample(pks, 18)},
        {"id": 5005, "title": "Family", "parent": None, "items": rng.sample(pks, 52)},
        {"id": 5006, "title": "Wallpapers", "parent": None, "items": rng.sample(pks, 12)},
    ]
    return scan, lib, albums, folders


SCAN, LIB, ALBUMS, FOLDERS = _library()

APPS = [  # name, bundle, version, app MB, data MB, file sharing, colors
    ("Atlas Maps", "app.demo.atlas", "4.2", 210, 640, False, ("#34c759", "#0a84ff")),
    ("Beacon Mail", "app.demo.mail", "12.1", 95, 1830, False, ("#0a84ff", "#5e5ce6")),
    ("Canvas Notes", "app.demo.notes", "3.9", 48, 220, True, ("#ffcc00", "#ff9500")),
    ("Cinema Box", "app.demo.cinema", "8.0", 160, 4120, False, ("#ff375f", "#af52de")),
    ("Daylight Weather", "app.demo.weather", "2.6", 37, 18, False, ("#5ac8fa", "#0a84ff")),
    ("Field Recorder", "app.demo.recorder", "1.14", 22, 960, True, ("#ff453a", "#ff9f0a")),
    ("Fitloop", "app.demo.fitloop", "6.3", 120, 310, False, ("#30d158", "#00c7be")),
    ("Harbor Bank", "app.demo.bank", "21.4", 180, 95, False, ("#1c1c1e", "#3a3a3c")),
    ("Inkwell Reader", "app.demo.reader", "5.0", 64, 2380, True, ("#bf5af2", "#5e5ce6")),
    ("Kiln Photo Editor", "app.demo.kiln", "9.1", 310, 1210, True, ("#ff9f0a", "#ff375f")),
    ("Lingo Cards", "app.demo.lingo", "3.2", 75, 140, False, ("#64d2ff", "#30d158")),
    ("Metro Transit", "app.demo.metro", "2.0", 58, 33, False, ("#ff453a", "#bf5af2")),
    ("Moodboard", "app.demo.moodboard", "1.8", 41, 520, True, ("#ff375f", "#ffcc00")),
    ("Nightsky", "app.demo.nightsky", "4.4", 260, 75, False, ("#1e1e5a", "#5e5ce6")),
    ("Orbit Chat", "app.demo.chat", "26.3", 240, 6870, False, ("#30d158", "#34c759")),
    ("Pocket Budget", "app.demo.budget", "7.7", 33, 12, False, ("#00c7be", "#0a84ff")),
    ("Quartet Music", "app.demo.music", "11.0", 140, 3550, False, ("#ff2d55", "#ff375f")),
    ("Recipe Box", "app.demo.recipes", "2.3", 55, 410, False, ("#ff9500", "#ffcc00")),
    ("Sketchbook", "app.demo.sketch", "5.6", 190, 880, True, ("#5e5ce6", "#64d2ff")),
    ("Trailhead", "app.demo.trail", "3.0", 88, 290, False, ("#34c759", "#a2845e")),
    ("Voice Memo Pro", "app.demo.voice", "2.9", 19, 1540, True, ("#3a3a3c", "#ff453a")),
    ("Zen Timer", "app.demo.zen", "1.2", 14, 3, False, ("#8e8e93", "#5ac8fa")),
]


def _apps() -> list[dict]:
    return [{"bundle": b, "name": n, "version": v, "app_size": int(a * 1e6), "data_size": int(d * 1e6),
             "file_sharing": fs} for n, b, v, a, d, fs, _c in APPS]


def _device() -> dict:
    total = 256_000_000_000
    return {
        "udid": UDID, "connection": "USB", "name": "Demo iPhone", "product_type": "iPhone16,2",
        "model_name": "iPhone 15 Pro Max", "model": "MU2X3", "hardware_model": "D84AP", "ios": "26.0",
        "build": "23A341", "serial": "DEMO0SERIAL0", "ecid": 1234567890123, "color": "1",
        "disk_total": total, "disk_free": 91_400_000_000, "system_size": 14_200_000_000, "battery": 86,
        "charging": True, "wifi_enabled": True, "phone": "+1 555 0100", "region": "LL/A", "carrier": "Demo Mobile",
        "activation": "Activated", "imei": "000000000000000", "imei2": "000000000000001",
        "wifi_mac": "00:00:5e:00:53:01", "bt_mac": "00:00:5e:00:53:02", "baseband": "3.00.01",
        "iboot": "iBoot-12345.0.1", "passcode": True, "timezone": "America/New_York",
        "apps_total": sum((a["app_size"] + a["data_size"]) for a in _apps()),
    }


FILES = {
    "/": [("DCIM", True, 0), ("Downloads", True, 0), ("Books", True, 0), ("Recordings", True, 0),
          ("PhotoData", True, 0), ("iTunes_Control", True, 0)],
    "/Downloads": [("Trip itinerary.pdf", False, 2_400_000), ("Lisbon map.png", False, 5_100_000),
                   ("Concert tickets.pdf", False, 380_000), ("Podcast episode 42.mp3", False, 61_000_000),
                   ("Presentation draft.key", False, 148_000_000), ("Notes backup.zip", False, 920_000_000)],
    "/Books": [("Field guide to birds.epub", False, 18_000_000), ("Cooking basics.pdf", False, 42_000_000)],
    "/Recordings": [("Interview 03.m4a", False, 75_000_000), ("Guitar idea.m4a", False, 6_400_000)],
    "/DCIM": [("100APPLE", True, 0), ("101APPLE", True, 0)],
    "/PhotoData": [("Photos.sqlite", False, 1_610_000_000), ("Thumbnails", True, 0)],
    "/iTunes_Control": [("Music", True, 0)],
}


def _fs_entry(folder: str, name: str, is_dir: bool, size: int) -> dict:
    path = f"{folder.rstrip('/')}/{name}"
    ext = "" if is_dir else name.rsplit(".", 1)[-1].lower()
    t = NOW - (int(hashlib.md5(path.encode()).hexdigest(), 16) % 200) * 86400
    return {"path": path, "name": name, "dir": is_dir, "size": size, "mtime": t, "btime": t, "ext": ext}


# ---------------- drawn images ----------------

def _font(size: int) -> ImageFont.FreeTypeFont:
    for name in ("segoeuib.ttf", "seguisb.ttf", "arialbd.ttf", "DejaVuSans-Bold.ttf"):
        try:
            return ImageFont.truetype(name, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _gradient(size: int, c1: str, c2: str) -> Image.Image:
    a, b = Image.new("RGB", (size, size), c1), Image.new("RGB", (size, size), c2)
    mask = Image.linear_gradient("L").resize((size, size)).rotate(45, expand=False)
    return Image.composite(b, a, mask)


def _icon(name: str, colors: tuple[str, str], size: int = 180) -> Image.Image:
    img = _gradient(size, *colors).convert("RGBA")
    d = ImageDraw.Draw(img)
    letter = "".join(w[0] for w in name.split()[:2])
    font = _font(int(size * 0.38))
    box = d.textbbox((0, 0), letter, font=font)
    d.text(((size - box[2] - box[0]) / 2, (size - box[3] - box[1]) / 2), letter, font=font, fill="white")
    mask = Image.new("L", (size, size), 0)
    ImageDraw.Draw(mask).rounded_rectangle((0, 0, size - 1, size - 1), radius=int(size * 0.225), fill=255)
    img.putalpha(mask)
    return img


def _png(img: Image.Image) -> bytes:
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return buf.getvalue()


def _jpeg(img: Image.Image, quality: int = 85) -> bytes:
    buf = io.BytesIO()
    img.convert("RGB").save(buf, "JPEG", quality=quality)
    return buf.getvalue()


_home_cache: dict[str, bytes] = {}


def _home_screen() -> bytes:
    """A home screen drawn from a stock photo and the demo apps' icons (1179×2556 scaled by half)."""
    if "home" in _home_cache:
        return _home_cache["home"]
    w, h = 590, 1278
    bg = Image.open(images()[7]).convert("RGB")
    scale = max(w / bg.width, h / bg.height)
    bg = bg.resize((int(bg.width * scale) + 1, int(bg.height * scale) + 1)).crop((0, 0, w, h))
    bg = bg.filter(ImageFilter.GaussianBlur(6))
    bg = Image.blend(bg, Image.new("RGB", bg.size, "#101828"), 0.35).convert("RGBA")  # white labels stay readable
    d = ImageDraw.Draw(bg)
    d.text((58, 22), "9:41", font=_font(30), fill="white")
    d.rounded_rectangle((w - 110, 30, w - 62, 50), radius=6, outline="white", width=3)
    d.rectangle((w - 104, 36, w - 74, 44), fill="white")
    icon, gap_x, top = 118, (w - 4 * 118) // 5, 120
    small = _font(19)
    for n, (name, _b, _v, _a, _d, _fs, colors) in enumerate(APPS[:20]):
        row, col = divmod(n, 4)
        x, y = gap_x + col * (icon + gap_x), top + row * (icon + 52)
        bg.paste(_icon(name, colors, icon), (x, y), _icon(name, colors, icon))
        label = name.split()[0]
        lw = d.textlength(label, font=small)
        d.text((x + (icon - lw) / 2, y + icon + 8), label, font=small, fill="white")
    dock = Image.new("RGBA", bg.size, (0, 0, 0, 0))
    ImageDraw.Draw(dock).rounded_rectangle((20, h - 180, w - 21, h - 31), radius=48, fill=(255, 255, 255, 60))
    bg = Image.alpha_composite(bg, dock)
    for n, (name, _b, _v, _a, _d, _fs, colors) in enumerate(APPS[14:18]):
        x = 20 + gap_x - 20 + n * (icon + gap_x)
        bg.paste(_icon(name, colors, icon), (x, h - 164), _icon(name, colors, icon))
    _home_cache["home"] = _jpeg(bg, 90)
    return _home_cache["home"]


_thumb_cache: dict[tuple[str, int], bytes] = {}


def _photo_for(path: str, size: int) -> bytes:
    key = (path, size)
    if key not in _thumb_cache:
        imgs = images()
        if path.endswith(".PNG"):  # screenshots: the drawn home screen
            img = Image.open(io.BytesIO(_home_screen()))
        else:
            img = Image.open(imgs[int(hashlib.md5(path.encode()).hexdigest(), 16) % len(imgs)])
        img.thumbnail((size, size))
        _thumb_cache[key] = _jpeg(img)
    return _thumb_cache[key]


# ---------------- the API ----------------

app = FastAPI(title="iPhone Manager (demo)")
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html", headers={"Cache-Control": "no-cache"})


@app.get("/api/devices")
async def devices():
    return [_device()]


@app.get("/api/modes")
async def modes():
    return {"error": None, "devices": []}


@app.get("/api/update")
async def update():
    return {"status": "idle"}


@app.get("/api/defaults")
async def defaults():
    return {"photos_dest": r"C:\Users\Demo\Pictures\iPhone"}


@app.post("/api/devices/{udid}/photos/scan")
async def photos_scan(udid: str):
    return {"status": "done", "done": len(SCAN)}


@app.get("/api/devices/{udid}/photos")
async def photos_list(udid: str):
    return {"status": "done", "done": len(SCAN), "error": None, "items": SCAN}


@app.get("/api/devices/{udid}/library")
async def library(udid: str):
    return {"items": LIB, "albums": ALBUMS, "folders": FOLDERS}


@app.get("/api/devices/{udid}/thumb")
async def thumb(udid: str, path: str, full: bool = False):
    return Response(_photo_for(path, 420), media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/devices/{udid}/preview")
async def preview(udid: str, path: str):
    return Response(_photo_for(path, 1400), media_type="image/jpeg")


@app.get("/api/devices/{udid}/file")
async def file(udid: str, path: str):
    return Response(_photo_for(path, 1400), media_type="image/jpeg")


@app.get("/api/icloud/status")
async def icloud_status():
    return {"state": "ok", "apple_id": "demo@example.com", "error": None}


@app.get("/api/icloud/{guid}/info")
async def icloud_info(guid: str):
    item = next((i for i in LIB if i[5] == guid), None)
    return {"filename": item[1].rsplit("/", 1)[1] if item else "IMG.HEIC", "size": item[4] if item else 0,
            "type": "image", "video": False}


@app.get("/api/icloud/{guid}/{kind}")
async def icloud_image(guid: str, kind: str):
    item = next((i for i in LIB if i[5] == guid), None)
    return Response(_photo_for(item[1] if item else guid, 1400), media_type="image/jpeg")


@app.get("/api/devices/{udid}/apps")
async def apps(udid: str):
    return {"apps": _apps(), "sizes": "ready"}


@app.get("/api/devices/{udid}/apps/{bundle}/icon")
async def app_icon(udid: str, bundle: str):
    a = next((a for a in APPS if a[1] == bundle), APPS[0])
    return Response(_png(_icon(a[0], a[6], 120)), media_type="image/png", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/devices/{udid}/fs/list")
async def fs_list(udid: str, root: str, path: str = "/"):
    if root != "media":
        path = "/Documents"
        return [_fs_entry(path, n, d, s) for n, d, s in
                [("Project notes.txt", False, 24_000), ("Exports", True, 0), ("Cover art.png", False, 3_800_000),
                 ("Session 12.wav", False, 212_000_000)]]
    return [_fs_entry(path, n, d, s) for n, d, s in FILES.get(path.rstrip("/") or "/", [])]


@app.post("/api/devices/{udid}/fs/scan")
async def fs_scan(udid: str, request: Request):
    return {"id": "demo", "status": "done", "done": 0, "dirs": len(FILES)}


@app.get("/api/fs/scans/{scan_id}")
async def fs_scan_state(scan_id: str, items: bool = False):
    found = [_fs_entry(folder, n, d, s) for folder, entries in FILES.items() for n, d, s in entries if not d]
    found += [{**_fs_entry(i["folder"], i["name"], False, i["size"])} for i in SCAN[:80]]
    result = {"id": scan_id, "status": "done", "done": len(found), "dirs": len(FILES), "error": None}
    if items:
        result["items"] = found
    return result


@app.get("/api/devices/{udid}/screen/status")
async def screen_status(udid: str):
    return {"developer_mode": True, "ready": True, "preparing": False, "ios": "26.0", "connection": "USB"}


@app.post("/api/devices/{udid}/mirror/start")
async def mirror_start(udid: str):
    return {"url": "/demo/mirror"}


@app.get("/demo/mirror/mjpeg")
@app.get("/demo/mirror/frame.jpg")
async def mirror_frame():
    return Response(_home_screen(), media_type="image/jpeg")


@app.get("/demo/mirror/info")
async def mirror_info():
    return {"streaming": True, "fps": 60}


@app.get("/api/devices/{udid}/firmwares")
async def firmwares(udid: str):
    def fw(version: str, build: str, released: str, current: bool = False) -> dict:
        name = f"iPhone16,2_{version}_{build}_Restore.ipsw"
        return {"version": version, "build": build, "url": f"https://updates.cdn-apple.com/demo/{name}",
                "size": 9_870_000_000, "sha1": None, "released": released, "can_update": True,
                "is_current": current, "downloaded": False, "local_path": rf"C:\Users\Demo\Downloads\{name}"}
    return {"product_type": "iPhone16,2", "current": "26.0",
            "firmwares": [fw("26.0.1", "23A355", "2026-10-14"), fw("26.0", "23A341", "2026-09-15", True)]}


@app.api_route("/{rest:path}", methods=["GET", "POST"])
async def anything_else(rest: str):
    """Every other action pretends to succeed (the demo never changes anything)."""
    return JSONResponse({"ok": True})


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fetch", action="store_true", help="download the stock photos and exit")
    parser.add_argument("--port", type=int, default=PORT)
    args = parser.parse_args()
    if args.fetch:
        fetch_assets()
    else:
        images()
        uvicorn.run(app, host="127.0.0.1", port=args.port)
