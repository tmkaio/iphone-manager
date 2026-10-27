"""Photos & videos over AFC: scan /DCIM, thumbnails, streaming and bulk download."""

import asyncio
import io
import logging
import os
import posixpath
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, AsyncIterator, Optional

from PIL import Image, ImageOps
from pillow_heif import register_heif_opener

from . import system
from .tasks import spawn
from .device import DeviceManager

register_heif_opener()
log = logging.getLogger(__name__)

IMAGE_EXT = {".jpg", ".jpeg", ".heic", ".heif", ".png", ".gif", ".dng", ".tif", ".tiff", ".webp"}
VIDEO_EXT = {".mov", ".mp4", ".m4v", ".3gp", ".avi"}
SIDECAR_EXT = {".aae", ".xmp"}
CHUNK = 1024 * 1024
THUMB_SIZE = 320

CACHE_DIR = system.DATA_DIR / "cache"


def media_kind(name: str) -> Optional[str]:
    ext = posixpath.splitext(name)[1].lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in VIDEO_EXT:
        return "video"
    return None


def _ts(value: Any) -> float:
    if isinstance(value, datetime):
        return value.timestamp()
    try:
        return float(value) / 1e9
    except (TypeError, ValueError):
        return 0.0


class PhotoLibrary:
    def __init__(self, devices: DeviceManager) -> None:
        self.devices = devices
        self.scans: dict[str, dict[str, Any]] = {}  # udid -> {status, done, items, error}
        self.jobs: dict[str, dict[str, Any]] = {}
        self._thumb_sem = asyncio.Semaphore(4)

    # ---------- scanning ----------

    def scan_state(self, udid: str) -> dict[str, Any]:
        return self.scans.get(udid) or {"status": "idle", "done": 0, "items": []}

    def start_scan(self, udid: str, force: bool = False) -> dict[str, Any]:
        state = self.scans.get(udid)
        if state and state["status"] == "scanning":
            return state
        if state and state["status"] == "done" and not force:
            return state
        state = {"status": "scanning", "done": 0, "items": [], "error": None, "started": time.time()}
        self.scans[udid] = state
        spawn(self._scan(udid, state))
        return state

    async def _scan(self, udid: str, state: dict[str, Any]) -> None:
        afc = None
        try:
            afc = await self.devices.new_afc(udid)
            items: list[dict[str, Any]] = []
            folders = sorted(f for f in await afc.listdir("/DCIM") if f not in (".", ".."))
            for folder in folders:
                folder_path = f"/DCIM/{folder}"
                try:
                    names = await afc.listdir(folder_path)
                except Exception:
                    continue
                for name in sorted(names):
                    kind = media_kind(name)
                    if kind is None:
                        continue
                    path = f"{folder_path}/{name}"
                    try:
                        st = await afc.stat(path)
                    except Exception:
                        continue
                    items.append(
                        {
                            "path": path,
                            "name": name,
                            "folder": folder,
                            "kind": kind,
                            "size": int(st.get("st_size", 0)),
                            "mtime": _ts(st.get("st_mtime")),
                            "btime": _ts(st.get("st_birthtime")),
                        }
                    )
                    state["done"] = len(items)
            items = _pair_live_photos(items)
            items.sort(key=lambda i: i["btime"] or i["mtime"], reverse=True)
            state["items"] = items
            state["status"] = "done"
        except Exception as e:
            log.exception("scan failed")
            state["status"] = "error"
            state["error"] = f"{type(e).__name__}: {e}"
        finally:
            if afc:
                try:
                    await afc.aclose()
                except Exception:
                    pass

    # ---------- reading files ----------

    async def read_all(self, udid: str, path: str) -> bytes:
        afc = await self.devices.afc(udid)
        try:
            return await afc.get_file_contents(path)
        except (ConnectionError, OSError, asyncio.IncompleteReadError):
            await self.devices.reset_afc(udid)
            afc = await self.devices.afc(udid)
            return await afc.get_file_contents(path)

    async def stream(self, udid: str, path: str, start: int = 0, end: Optional[int] = None) -> AsyncIterator[bytes]:
        """Yield bytes [start, end] (inclusive) using a dedicated AFC connection."""
        afc = await self.devices.new_afc(udid)
        handle = None
        try:
            handle = await afc.fopen(path, "r")
            if start:
                await afc.fseek(handle, start)
            remaining = None if end is None else end - start + 1
            while remaining is None or remaining > 0:
                n = CHUNK if remaining is None else min(CHUNK, remaining)
                data = await afc.fread(handle, n)
                if not data:
                    break
                if remaining is not None:
                    remaining -= len(data)
                yield data
        finally:
            try:
                if handle is not None:
                    await afc.fclose(handle)
            finally:
                await afc.aclose()

    # ---------- thumbnails ----------

    def _thumb_cache(self, udid: str, path: str) -> Path:
        safe = path.strip("/").replace("/", "_")
        return CACHE_DIR / udid / "thumbs" / f"{safe}.jpg"

    async def thumbnail(self, udid: str, path: str) -> Optional[bytes]:
        cache = self._thumb_cache(udid, path)
        if cache.exists():
            return cache.read_bytes()
        async with self._thumb_sem:
            data = await self._make_thumbnail(udid, path)
        if data:
            cache.parent.mkdir(parents=True, exist_ok=True)
            cache.write_bytes(data)
        return data

    async def device_thumbnail(self, udid: str, path: str) -> Optional[bytes]:
        """The JPEG iOS keeps for every library item (also videos, and items whose original is only
        in iCloud) under /PhotoData/Thumbnails/V2/<item path>/."""
        thumb_dir = f"/PhotoData/Thumbnails/V2{path}"
        try:
            afc = await self.devices.afc(udid)
            names = [n for n in await afc.listdir(thumb_dir) if n.lower().endswith(".jpg")]
            if names:
                # Largest variant gives the best quality; they're all small
                best = max(names, key=lambda n: int(n.split(".")[0]) if n.split(".")[0].isdigit() else 0)
                return await afc.get_file_contents(f"{thumb_dir}/{best}")
        except Exception:
            pass
        return None

    async def _make_thumbnail(self, udid: str, path: str) -> Optional[bytes]:
        raw = await self.device_thumbnail(udid, path)
        if raw:
            return await asyncio.to_thread(_resize, raw)
        if media_kind(path) != "image":
            return None
        try:
            raw = await self.read_all(udid, path)
            return await asyncio.to_thread(_resize, raw)
        except Exception as e:
            log.debug("thumbnail failed for %s: %s", path, e)
            return None

    async def preview_jpeg(self, udid: str, path: str) -> bytes:
        """Full-size image converted to JPEG (for HEIC/DNG that browsers can't show)."""
        raw = await self.read_all(udid, path)
        return await asyncio.to_thread(_to_jpeg, raw, None)

    # ---------- downloads ----------

    def start_download(self, udid: str, paths: list[str], dest: str, convert_heic: bool, by_month: bool,
                       include_live: bool = True) -> dict:
        job_id = uuid.uuid4().hex[:8]
        index = {i["path"]: i for i in self.scan_state(udid).get("items", [])}
        files = []
        for p in paths:
            item = index.get(p, {"path": p, "name": posixpath.basename(p), "size": 0, "mtime": 0, "btime": 0})
            files.append(item)
            if include_live and item.get("live"):
                # the motion part keeps the photo's date so both land in the same month folder
                files.append({**item, "path": item["live"], "name": posixpath.basename(item["live"]),
                              "size": item["live_size"]})
        job = {
            "id": job_id,
            "udid": udid,
            "dest": dest,
            "status": "running",
            "total": len(files),
            "done": 0,
            "failed": [],
            "bytes_total": sum(f["size"] for f in files),
            "bytes_done": 0,
            "current": None,
            "current_size": 0,
            "current_done": 0,
            "speed": 0,
            "cancel": False,
            "started": time.time(),
            "_speed_t": time.time(),
            "_speed_b": 0,
        }
        self.jobs[job_id] = job
        spawn(self._download(job, files, Path(dest), convert_heic, by_month))
        return job

    async def _download(self, job: dict, files: list[dict], dest: Path, convert_heic: bool, by_month: bool) -> None:
        afc = None
        try:
            afc = await self.devices.new_afc(job["udid"])
            for f in files:
                if job["cancel"]:
                    job["status"] = "cancelled"
                    return
                job["current"] = f["name"]
                job["current_size"] = f.get("size", 0)
                job["current_done"] = 0
                when = f.get("btime") or f.get("mtime") or 0
                target_dir = dest / datetime.fromtimestamp(when).strftime("%Y-%m") if by_month and when else dest
                target_dir.mkdir(parents=True, exist_ok=True)
                name = f["name"]
                is_heic = name.lower().endswith((".heic", ".heif"))
                if convert_heic and is_heic:
                    name = posixpath.splitext(name)[0] + ".jpg"
                target = _unique(target_dir / name, f.get("size", 0) if not (convert_heic and is_heic) else None)
                if target is None:  # identical file already there
                    job["done"] += 1
                    job["bytes_done"] += f.get("size", 0)
                    continue
                try:
                    if convert_heic and is_heic:
                        raw = await afc.get_file_contents(f["path"])
                        _progress(job, len(raw))
                        data = await asyncio.to_thread(_to_jpeg, raw, 92)
                        target.write_bytes(data)
                    else:
                        await self._copy(afc, f["path"], target, job)
                    if when:
                        os.utime(target, (when, f.get("mtime") or when))
                except DownloadCancelled:
                    job["status"] = "cancelled"
                    return
                except Exception as e:
                    log.warning("download failed %s: %s", f["path"], e)
                    job["failed"].append({"path": f["path"], "error": str(e)})
                    try:
                        await afc.aclose()
                    except Exception:
                        pass
                    afc = await self.devices.new_afc(job["udid"])
                job["done"] += 1
            job["status"] = "done"
        except Exception as e:
            job["status"] = "error"
            job["error"] = f"{type(e).__name__}: {e}"
        finally:
            job["current"] = None
            job["speed"] = 0
            job["finished"] = time.time()
            if afc:
                try:
                    await afc.aclose()
                except Exception:
                    pass

    async def _copy(self, afc, src: str, target: Path, job: dict) -> None:
        tmp = target.with_suffix(target.suffix + ".part")
        handle = await afc.fopen(src, "r")
        try:
            with open(tmp, "wb") as out:
                while True:
                    if job["cancel"]:  # checked per chunk so big videos stop right away
                        raise DownloadCancelled()
                    data = await afc.fread(handle, CHUNK)
                    if not data:
                        break
                    out.write(data)
                    _progress(job, len(data))
        except DownloadCancelled:
            tmp.unlink(missing_ok=True)
            raise
        finally:
            await afc.fclose(handle)
        tmp.replace(target)


class DownloadCancelled(Exception):
    pass


def _progress(job: dict, n: int) -> None:
    job["bytes_done"] += n
    job["current_done"] += n
    now = time.time()
    elapsed = now - job["_speed_t"]
    if elapsed >= 1:  # average over ~1 s windows so the number doesn't jitter
        job["speed"] = (job["bytes_done"] - job["_speed_b"]) / elapsed
        job["_speed_t"], job["_speed_b"] = now, job["bytes_done"]


def _pair_live_photos(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """A Live Photo is IMG_x.HEIC/JPG + IMG_x.MOV in the same folder: show it as one photo."""
    photos = {(i["folder"], posixpath.splitext(i["name"])[0]): i for i in items if i["kind"] == "image"}
    result = []
    for i in items:
        if i["kind"] == "video":
            photo = photos.get((i["folder"], posixpath.splitext(i["name"])[0]))
            if photo is not None:
                photo["live"] = i["path"]
                photo["live_size"] = i["size"]
                continue
        result.append(i)
    return result


def _unique(target: Path, size: Optional[int]) -> Optional[Path]:
    """Avoid overwriting; returns None if an identical-size file already exists (skip)."""
    if not target.exists():
        return target
    if size and target.stat().st_size == size:
        return None
    stem, suffix, n = target.stem, target.suffix, 1
    while True:
        candidate = target.with_name(f"{stem} ({n}){suffix}")
        if not candidate.exists():
            return candidate
        n += 1


def _resize(raw: bytes) -> bytes:
    img = Image.open(io.BytesIO(raw))
    img = ImageOps.exif_transpose(img)
    img.thumbnail((THUMB_SIZE, THUMB_SIZE))
    out = io.BytesIO()
    img.convert("RGB").save(out, "JPEG", quality=80)
    return out.getvalue()


def _to_jpeg(raw: bytes, quality: Optional[int]) -> bytes:
    img = ImageOps.exif_transpose(Image.open(io.BytesIO(raw)))
    # exif_transpose resets the orientation tag, so this keeps date/GPS without double-rotating
    exif = img.getexif().tobytes() if img.getexif() else None
    out = io.BytesIO()
    kwargs = {"quality": quality or 90}
    if exif:
        kwargs["exif"] = exif
    img.convert("RGB").save(out, "JPEG", **kwargs)
    return out.getvalue()
