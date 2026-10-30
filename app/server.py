"""HTTP API + static UI."""

import asyncio
import logging
import mimetypes
import os
import re
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Optional
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import iterate_in_threadpool
from pydantic import BaseModel

from .device import DeviceManager
from .firmware import FirmwareManager, inspect_ipsw, signed_firmwares
from .albums import AlbumLibrary
from .backups import BackupError, Backups
from .files import FileBrowser
from .icloud import ICloudAccounts, ICloudError
from .photos import PhotoLibrary, _to_jpeg as to_jpeg
from .screen import ScreenError, ScreenMirror

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
STATIC = Path(__file__).parent / "static"
log = logging.getLogger(__name__)

from . import mobiledevice
from .modes import ModeError, ModeWatcher, model_name
from .modes_usb import ModeWatcher as UsbModeWatcher
from . import i18n, preflight, ptp, system, video, watchdog
from .i18n import _

devices = DeviceManager()
photos = PhotoLibrary(devices)
albums = AlbumLibrary(devices)
files = FileBrowser(devices)
screen = ScreenMirror(devices)
icloud = ICloudAccounts()
firmware = FirmwareManager()
# Windows: Apple's MobileDevice.dll (its driver owns Recovery/DFU devices); elsewhere libusb
modes = ModeWatcher() if system.IS_WINDOWS else UsbModeWatcher()


def _update_running() -> Optional[str]:
    job = firmware.update_state()
    return _("An update is in progress") if job and job["status"] == "running" else None


backups = Backups(devices, busy=_update_running)


def _resume_modes() -> None:
    try:
        modes.resume()
    except ModeError:
        pass


firmware.on_update_finished = _resume_modes


@asynccontextmanager
async def lifespan(_app: FastAPI):
    watchdog.start(asyncio.get_running_loop())
    mobiledevice.ensure_running()
    modes.start()
    yield
    await screen.close()
    modes.stop()
    await devices.close_all()
    mobiledevice.stop()


app = FastAPI(title="iPhone Manager", lifespan=lifespan)


@app.middleware("http")
async def _no_cache_ui(request: Request, call_next):
    i18n.current.set(request.cookies.get("lang", "en"))  # the language of this request's messages
    response = await call_next(request)
    # the UI is served locally and changes with the app: always revalidate it
    if request.url.path == "/" or request.url.path.startswith("/static/"):
        response.headers["Cache-Control"] = "no-cache"
    return response


@app.exception_handler(KeyError)
async def _not_found(_req, exc):
    return JSONResponse({"detail": _("Device not found: {error}", error=exc)}, status_code=404)


# ---------------- devices ----------------


@app.get("/api/devices")
async def list_devices(wifi: bool = False):
    job = firmware.update_state()
    if job and job["status"] == "running":
        return []  # don't open lockdown sessions while the device is being updated
    service_error = mobiledevice.ensure_running()
    if service_error:
        raise HTTPException(503, service_error)
    result = await devices.list_devices(scan_wifi=wifi)
    if not result and devices.usbmux_error:
        raise HTTPException(503, devices.usbmux_error)
    for d in result:
        d["model_name"] = await asyncio.to_thread(model_name, d.get("product_type"))
        d["apps_total"] = files.apps_total(d["udid"])
    return result


class WifiBody(BaseModel):
    enabled: bool


@app.post("/api/devices/{udid}/wifi")
async def set_wifi(udid: str, body: WifiBody):
    await devices.set_wifi(udid, body.enabled)
    return {"ok": True}


# ---------------- Recovery / DFU (MobileDevice.dll on Windows, libusb elsewhere) ----------------


@app.get("/api/modes")
async def list_modes():
    """Every device MobileDevice sees, in any boot state (normal, recovery, dfu, restore)."""
    job = firmware.update_state()
    if job and job["status"] == "running":
        return {"error": None, "devices": []}  # the update owns the device, like /api/devices
    error = _(modes.error) if modes.error else None  # stored in English by the MobileDevice thread
    return {"error": error, "devices": await asyncio.to_thread(modes.devices)}


def _ensure_no_update() -> None:
    job = firmware.update_state()
    if job and job["status"] == "running":
        raise HTTPException(409, _("An update is in progress"))
    active = backups.listing_job()
    if active and active["status"] == "running":
        raise HTTPException(409, _("Wait for the backup or restore in progress to finish."))


@app.post("/api/devices/{udid}/recovery/enter")
async def enter_recovery(udid: str):
    _ensure_no_update()
    entry = await devices.get(udid)
    if entry.connection != "USB":
        raise HTTPException(400, _("Connect the iPhone with the USB cable"))
    ecid = entry.info.get("UniqueChipID")
    await devices.close_all()  # the device is about to reboot
    try:
        await asyncio.wrap_future(modes.enter_recovery(int(ecid)))
    except (KeyError, ModeError, TypeError) as e:
        # MobileDevice did not see it (yet): ask lockdown directly
        log.warning("MobileDevice enter recovery failed (%s), using lockdown", e)
        try:
            entry = await devices.get(udid)
            await entry.lockdown.enter_recovery()
        except Exception as e2:
            raise HTTPException(502, _("Couldn't enter Recovery: {error}", error=e2))
    return {"ok": True}


@app.post("/api/modes/{ecid}/recovery/exit")
async def exit_recovery(ecid: str):
    _ensure_no_update()
    try:
        await asyncio.wrap_future(modes.exit_recovery(int(ecid)))
    except ModeError as e:
        raise HTTPException(400, str(e))
    return {"ok": True}


# ---------------- photos ----------------


@app.post("/api/devices/{udid}/photos/scan")
async def scan(udid: str, force: bool = False):
    await devices.get(udid)
    st = photos.start_scan(udid, force=force)
    return {k: v for k, v in st.items() if k != "items"}


@app.get("/api/devices/{udid}/photos")
async def list_photos(udid: str):
    st = photos.scan_state(udid)
    return {"status": st["status"], "done": st["done"], "error": st.get("error"), "items": st.get("items", [])}


@app.get("/api/devices/{udid}/library")
async def photo_library(udid: str, refresh: bool = False):
    await devices.get(udid)
    try:
        return await albums.library(udid, refresh=refresh)
    except Exception as e:
        log.exception("photo library failed")
        raise HTTPException(502, _("Couldn't read the photo library: {error}", error=f"{type(e).__name__}: {e}"))


@app.get("/api/devices/{udid}/thumb")
async def thumb(udid: str, path: str, full: bool = False):
    # full: the iPhone's own thumbnail as is (the best view of an item that is only in iCloud)
    data = await (photos.device_thumbnail(udid, path) if full else photos.thumbnail(udid, path))
    if data is None:
        raise HTTPException(404)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/devices/{udid}/preview")
async def preview(udid: str, path: str):
    data = await photos.preview_jpeg(udid, path)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/devices/{udid}/video/info")
async def video_info(udid: str, path: str, request: Request):
    if not video.available():
        return {"direct": True, "duration": 0, "codec": None, "error": _("ffmpeg not found")}
    try:
        return await video.probe(str(request.base_url).rstrip("/"), udid, path)
    except Exception as e:
        raise HTTPException(502, _("Couldn't read the video: {error}", error=f"{type(e).__name__}: {e}"))


@app.get("/api/devices/{udid}/video")
async def video_preview(udid: str, path: str, request: Request, t: float = 0):
    """The video converted on the fly to H.264, which the browser can show (see video.py)."""
    if not video.available():
        raise HTTPException(501, _("ffmpeg not found in PATH"))
    base = str(request.base_url).rstrip("/")
    return StreamingResponse(video.stream(base, udid, path, max(0.0, t), gone=request.is_disconnected),
                             media_type="video/mp4", headers={"Cache-Control": "no-store"})


@app.get("/api/devices/{udid}/file")
async def file(udid: str, path: str, request: Request):
    """Raw file with HTTP Range support so videos can seek."""
    afc = await devices.afc(udid)
    size = int((await afc.stat(path)).get("st_size", 0))
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    if path.lower().endswith(".mov"):
        ctype = "video/quicktime"
    headers = {"Accept-Ranges": "bytes", "Content-Disposition": f'inline; filename="{os.path.basename(path)}"'}
    rng = request.headers.get("range")
    if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
        end = int(m.group(2)) if m.group(1) and m.group(2) else size - 1
        end = min(end, size - 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(photos.stream(udid, path, start, end), 206, headers=headers, media_type=ctype)
    headers["Content-Length"] = str(size)
    return StreamingResponse(photos.stream(udid, path), headers=headers, media_type=ctype)


class DownloadBody(BaseModel):
    paths: list[str]
    dest: str
    convert_heic: bool = False
    by_month: bool = True
    include_live: bool = True


@app.post("/api/devices/{udid}/photos/download")
async def download(udid: str, body: DownloadBody):
    if not body.paths:
        raise HTTPException(400, _("Nothing selected"))
    Path(body.dest).mkdir(parents=True, exist_ok=True)
    job = photos.start_download(udid, body.paths, body.dest, body.convert_heic, body.by_month, body.include_live)
    return _public_job(job)


class PhotoDeleteBody(BaseModel):
    paths: list[str]
    confirm: bool = False


@app.post("/api/devices/{udid}/photos/delete")
async def delete_photos(udid: str, body: PhotoDeleteBody):
    if not body.confirm or not body.paths:
        raise HTTPException(400, _("Confirmation required"))
    entry = await devices.get(udid)
    if entry.connection != "USB":
        raise HTTPException(400, _("To delete photos, connect the iPhone with the USB cable"))
    index = {i["path"]: i for i in photos.scan_state(udid).get("items", [])}
    items = []
    for p in body.paths:
        it = index.get(p)
        if it is None:
            raise HTTPException(400, _("Unknown item: {path} (reload the photos)", path=p))
        items.append({"path": p, "name": it["name"], "time": it.get("btime") or it.get("mtime"),
                      "extra": ptp.companion_names(it)})
    try:
        result = await asyncio.to_thread(ptp.delete, items)
    except ptp.PtpError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        log.exception("photo delete failed")
        raise HTTPException(502, _("Delete failed: {error}", error=f"{type(e).__name__}: {e}"))
    gone = set(result["deleted"])
    state = photos.scans.get(udid)
    if state:
        state["items"] = [i for i in state["items"] if i["path"] not in gone]
    return result


@app.get("/api/jobs/{job_id}")
async def job_status(job_id: str):
    job = photos.jobs.get(job_id) or firmware.downloads.get(job_id) or backups.jobs.get(job_id)
    if not job:
        raise HTTPException(404)
    return _public_job(job)


@app.post("/api/jobs/{job_id}/cancel")
async def job_cancel(job_id: str):
    if job_id in backups.jobs:
        backups.cancel(job_id)
        return {"ok": True}
    job = photos.jobs.get(job_id) or firmware.downloads.get(job_id)
    if job:
        job["cancel"] = True
    return {"ok": True}


def _public_job(job: dict) -> dict:
    return {k: v for k, v in job.items() if k != "cancel" and not k.startswith("_")}


# ---------------- iCloud Photos (items not on the iPhone) ----------------
# Each iPhone has its own iCloud account (icloud.ICloudAccounts): every route is under the device.

GUID_SAMPLE = 12  # iCloud ids of the iPhone's photos looked up to tell whose library it is


async def _device_guids(udid: str) -> list[str]:
    """iCloud ids of some of the iPhone's photos, spread over its whole library."""
    try:
        items = (await albums.library(udid))["items"]
    except Exception as e:
        log.warning("iCloud: couldn't read the photo library to check the account (%s)", e)
        return []
    guids = [it[5] for it in sorted(items, key=lambda it: it[2]) if it[5]]
    step = max(1, len(guids) // GUID_SAMPLE)
    return guids[::step][:GUID_SAMPLE]


@app.get("/api/devices/{udid}/icloud/status")
async def icloud_status(udid: str):
    guids = await _device_guids(udid) if icloud.needs_check(udid) else []
    return await asyncio.to_thread(icloud.status, udid, guids)


class ICloudLogin(BaseModel):
    apple_id: str
    password: str


@app.post("/api/devices/{udid}/icloud/login")
async def icloud_login(udid: str, body: ICloudLogin):
    await devices.get(udid)
    guids = await _device_guids(udid)
    try:
        return await asyncio.to_thread(icloud.login, udid, body.apple_id.strip(), body.password, guids)
    except ICloudError as e:
        raise HTTPException(401, str(e))


class ICloudCode(BaseModel):
    code: str


@app.post("/api/devices/{udid}/icloud/2fa")
async def icloud_2fa(udid: str, body: ICloudCode):
    guids = await _device_guids(udid)
    try:
        return await asyncio.to_thread(icloud.verify, udid, body.code, guids)
    except ICloudError as e:
        raise HTTPException(401, str(e))


@app.post("/api/devices/{udid}/icloud/cancel")
async def icloud_cancel(udid: str):
    await asyncio.to_thread(icloud.cancel, udid)
    return {"ok": True}


@app.post("/api/devices/{udid}/icloud/logout")
async def icloud_logout(udid: str):
    await asyncio.to_thread(icloud.logout, udid)
    return {"ok": True}


async def _icloud(fn, *args):
    try:
        return await asyncio.to_thread(fn, *args)
    except ICloudError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        log.exception("iCloud request failed")
        raise HTTPException(502, f"iCloud: {type(e).__name__}: {e}")


async def _account(udid: str):
    return await _icloud(icloud.for_device, udid)


@app.get("/api/devices/{udid}/icloud/{guid}/info")
async def icloud_info(udid: str, guid: str, request: Request):
    acc = await _account(udid)
    info = await _icloud(acc.info, guid)
    info.pop("url", None)
    if info["video"] and await _icloud(acc.playable_url, guid):
        info["direct"] = True  # the frontend plays /playable as is
    elif info["video"] and video.available():
        try:
            info.update(await video.probe_url(_icloud_source(request, udid, guid)))
        except Exception as e:
            log.warning("iCloud video probe failed: %s", e)
    return info


@app.get("/api/devices/{udid}/icloud/{guid}/original")
async def icloud_original(udid: str, guid: str, download: bool = False):
    acc = await _account(udid)
    if not download:  # signed URL: the browser streams it with Range support (H.264 videos, images)
        return RedirectResponse(await _icloud(acc.original_url, guid))
    filename, size, chunks = await _icloud(acc.iter_original, guid)
    headers = {"Content-Disposition": f"attachment; filename*=UTF-8''{quote(filename)}"}
    if size:
        headers["Content-Length"] = str(size)
    return StreamingResponse(iterate_in_threadpool(chunks), headers=headers,
                             media_type=mimetypes.guess_type(filename)[0] or "application/octet-stream")


@app.get("/api/devices/{udid}/icloud/{guid}/preview")
async def icloud_preview(udid: str, guid: str):
    """The original image as JPEG (HEIC/DNG don't display in the browser)."""
    acc = await _account(udid)
    raw = await _icloud(acc.read_original, guid)
    data = await asyncio.to_thread(to_jpeg, raw, None)
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "max-age=3600"})


@app.get("/api/devices/{udid}/icloud/{guid}/playable")
async def icloud_playable(udid: str, guid: str):
    """A video the browser can play: iCloud's 720p MP4 when there is one, else the original."""
    acc = await _account(udid)
    return RedirectResponse(await _icloud(acc.playable_url, guid) or await _icloud(acc.original_url, guid))


@app.get("/api/devices/{udid}/icloud/{guid}/raw")
async def icloud_raw(udid: str, guid: str, request: Request):
    """The original with Range support, served from icloud's block cache: what ffmpeg reads."""
    acc = await _account(udid)
    size = (await _icloud(acc.asset, guid)).size
    headers = {"Accept-Ranges": "bytes"}
    start, end, status = 0, size - 1, 200
    rng = request.headers.get("range")
    if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
        end = min(int(m.group(2)) if m.group(1) and m.group(2) else size - 1, size - 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        status = 206
    headers["Content-Length"] = str(end - start + 1)
    return StreamingResponse(iterate_in_threadpool(acc.read_range(guid, start, end)), status,
                             headers=headers, media_type="application/octet-stream")


def _icloud_source(request: Request, udid: str, guid: str) -> str:
    return f"{str(request.base_url).rstrip('/')}/api/devices/{quote(udid)}/icloud/{quote(guid)}/raw"


@app.get("/api/devices/{udid}/icloud/{guid}/video")
async def icloud_video(udid: str, guid: str, request: Request, t: float = 0):
    if not video.available():
        raise HTTPException(501, _("ffmpeg not found in PATH"))
    acc = await _account(udid)
    await _icloud(acc.asset, guid)  # a clear error now rather than ffmpeg failing later
    url = _icloud_source(request, udid, guid)
    return StreamingResponse(video.stream_url(url, max(0.0, t), gone=request.is_disconnected),
                             media_type="video/mp4", headers={"Cache-Control": "no-store"})


class ICloudDelete(BaseModel):
    guids: list[str]
    confirm: bool = False


@app.post("/api/devices/{udid}/icloud/delete")
async def icloud_delete(udid: str, body: ICloudDelete):
    if not body.confirm or not body.guids:
        raise HTTPException(400, _("Confirmation required"))
    acc = await _account(udid)
    deleted, failed = [], []
    for guid in body.guids:
        try:
            await asyncio.to_thread(acc.delete, guid)
            deleted.append(guid)
        except Exception as e:
            failed.append({"guid": guid, "error": str(e)})
    return {"deleted": deleted, "failed": failed}


# ---------------- apps & files ----------------


@app.get("/api/devices/{udid}/apps")
async def list_apps(udid: str, refresh: bool = False):
    return await files.apps(udid, refresh=refresh)


class UninstallBody(BaseModel):
    confirm: bool = False


@app.post("/api/devices/{udid}/apps/{bundle}/uninstall")
async def uninstall_app(udid: str, bundle: str, body: UninstallBody):
    if not body.confirm:
        raise HTTPException(400, _("Confirmation required"))
    try:
        await files.uninstall(udid, bundle)
    except Exception as e:
        raise HTTPException(502, _("Couldn't remove the app: {error}", error=f"{type(e).__name__}: {e}"))
    return {"ok": True}


@app.get("/api/devices/{udid}/apps/{bundle}/icon")
async def app_icon(udid: str, bundle: str):
    data = await files.icon(udid, bundle)
    if not data:
        raise HTTPException(404)
    return Response(data, media_type="image/png", headers={"Cache-Control": "max-age=86400"})


@app.get("/api/devices/{udid}/fs/list")
async def fs_list(udid: str, root: str, path: str = "/"):
    return await files.listdir(udid, root, path)


class ScanBody(BaseModel):
    root: str
    path: str = "/"


@app.post("/api/devices/{udid}/fs/scan")
async def fs_scan(udid: str, body: ScanBody):
    await devices.get(udid)
    return _public_job(files.start_scan(udid, body.root, body.path))


@app.get("/api/fs/scans/{scan_id}")
async def fs_scan_state(scan_id: str, items: bool = False):
    scan = files.scans.get(scan_id)
    if not scan:
        raise HTTPException(404)
    public = _public_job(scan)
    if not items:
        public.pop("items")
    return public


@app.post("/api/fs/scans/{scan_id}/cancel")
async def fs_scan_cancel(scan_id: str):
    if scan := files.scans.get(scan_id):
        scan["cancel"] = True
    return {"ok": True}


@app.get("/api/devices/{udid}/fs/file")
async def fs_file(udid: str, root: str, path: str, request: Request, download: bool = False):
    size = int((await files.stat(udid, root, path)).get("st_size", 0))
    ctype = mimetypes.guess_type(path)[0] or "application/octet-stream"
    disposition = "attachment" if download else "inline"
    headers = {"Accept-Ranges": "bytes",
               "Content-Disposition": f"{disposition}; filename*=UTF-8''{quote(os.path.basename(path))}"}
    rng = request.headers.get("range")
    if rng and (m := re.match(r"bytes=(\d*)-(\d*)", rng)):
        start = int(m.group(1)) if m.group(1) else max(0, size - int(m.group(2)))
        end = min(int(m.group(2)) if m.group(1) and m.group(2) else size - 1, size - 1)
        headers["Content-Range"] = f"bytes {start}-{end}/{size}"
        headers["Content-Length"] = str(end - start + 1)
        return StreamingResponse(files.stream(udid, root, path, start, end), 206, headers=headers, media_type=ctype)
    headers["Content-Length"] = str(size)
    return StreamingResponse(files.stream(udid, root, path), headers=headers, media_type=ctype)


@app.post("/api/devices/{udid}/fs/upload")
async def fs_upload(udid: str, root: str, path: str, name: str, request: Request):
    """Raw request body = the file (the browser sends it with fetch, no multipart)."""
    target = await files.upload(udid, root, path, name, request.stream())
    return {"path": target}


class DeleteBody(BaseModel):
    root: str
    paths: list[str]


@app.post("/api/devices/{udid}/fs/delete")
async def fs_delete(udid: str, body: DeleteBody):
    failed = await files.delete(udid, body.root, body.paths)
    return {"failed": failed}


class MkdirBody(BaseModel):
    root: str
    path: str


@app.post("/api/devices/{udid}/fs/mkdir")
async def fs_mkdir(udid: str, body: MkdirBody):
    await files.mkdir(udid, body.root, body.path)
    return {"ok": True}


@app.exception_handler(PermissionError)
async def _forbidden(_req, exc):
    return JSONResponse({"detail": str(exc)}, status_code=403)


@app.exception_handler(FileExistsError)
async def _exists(_req, exc):
    return JSONResponse({"detail": str(exc)}, status_code=409)


# ---------------- screen ----------------


@app.get("/api/devices/{udid}/screen/status")
async def screen_status(udid: str):
    _ensure_no_update()
    return await screen.status(udid)


@app.post("/api/devices/{udid}/screen/prepare")
async def screen_prepare(udid: str):
    _ensure_no_update()
    try:
        await screen.prepare(udid)
    except Exception as e:
        log.exception("screen prepare failed")
        raise HTTPException(502, _("Couldn't mount the developer disk image: {error}", error=f"{type(e).__name__}: {e}"))
    return {"ok": True}


@app.post("/api/devices/{udid}/mirror/start")
async def mirror_start(udid: str):
    _ensure_no_update()
    try:
        return await screen.start_mirror(udid)
    except Exception as e:
        log.exception("mirror start failed")
        raise HTTPException(502, _("Couldn't start mirroring: {error}", error=f"{type(e).__name__}: {e}"))


@app.post("/api/mirror/stop")
async def mirror_stop():
    await screen.stop_mirror()
    return {"ok": True}


@app.get("/api/devices/{udid}/screen/frame")
async def screen_frame(udid: str):
    _ensure_no_update()
    try:
        data = await screen.frame(udid)
    except ScreenError as e:
        raise HTTPException(409, str(e))
    except Exception as e:
        raise HTTPException(502, f"{type(e).__name__}: {e}")
    return Response(data, media_type="image/jpeg", headers={"Cache-Control": "no-store"})


# ---------------- firmware ----------------


@app.get("/api/devices/{udid}/firmwares")
async def firmwares(udid: str):
    entry = await devices.get(udid)
    info = entry.info
    try:
        fws = await asyncio.to_thread(signed_firmwares, info["ProductType"], info["ProductVersion"])
    except Exception as e:
        raise HTTPException(502, _("Couldn't query ipsw.me: {error}", error=e))
    return {"product_type": info["ProductType"], "current": info["ProductVersion"], "firmwares": fws}


class InspectBody(BaseModel):
    path: str


@app.post("/api/devices/{udid}/ipsw/inspect")
async def ipsw_inspect(udid: str, body: InspectBody):
    entry = await devices.get(udid)
    try:
        return await asyncio.to_thread(
            inspect_ipsw, body.path, entry.info["ProductType"], entry.info.get("HardwareModel", "")
        )
    except Exception as e:
        raise HTTPException(400, _("Invalid IPSW: {error}", error=e))


class FwDownloadBody(BaseModel):
    url: str
    size: Optional[int] = None
    sha1: Optional[str] = None


@app.post("/api/ipsw/download")
async def ipsw_download(body: FwDownloadBody):
    if not body.url.startswith("https://updates.cdn-apple.com/") and "apple.com/" not in body.url:
        raise HTTPException(400, _("Only IPSW files from Apple's servers are accepted"))
    return _public_job(firmware.start_download(body.url, body.size, body.sha1))


class UpdateBody(BaseModel):
    ipsw_path: str
    confirm: bool = False


@app.post("/api/devices/{udid}/update/preflight")
async def update_preflight(udid: str, body: InspectBody):
    _ensure_no_update()
    return await preflight.run(await devices.get(udid), body.path)


@app.post("/api/devices/{udid}/update")
async def start_update(udid: str, body: UpdateBody):
    if not body.confirm:
        raise HTTPException(400, _("Confirmation required"))
    _ensure_no_update()
    entry = await devices.get(udid)
    # every check again, right before starting: nothing is trusted from an earlier screen
    result = await preflight.run(entry, body.ipsw_path)
    if not result["ok"]:
        failed = [c["label"] + (f": {c['detail']}" if c["detail"] else "") for c in result["checks"] if not c["ok"]]
        raise HTTPException(400, _("Update blocked — {reasons}", reasons="; ".join(failed)))
    # Release our connections: the device is about to reboot into Recovery
    await screen.close()
    await devices.close_all()
    # and our MobileDevice handles, so the update process has the device in Recovery to itself
    try:
        await asyncio.wrap_future(modes.pause())
    except ModeError:
        pass
    try:
        job = await firmware.start_update(body.ipsw_path, entry.info.get("UniqueChipID"))
    except Exception:
        _resume_modes()
        raise
    return {k: v for k, v in job.items() if k != "log" and not k.startswith("_")}


@app.get("/api/update")
async def update_status(since: int = 0):
    job = firmware.update_state()
    if not job:
        return {"status": "idle"}
    public = {k: v for k, v in job.items() if k != "log" and not k.startswith("_")}
    return {**public, "log": job["log"][since:], "log_len": len(job["log"])}


@app.post("/api/update/cancel")
async def update_cancel():
    await firmware.cancel_update()
    return {"ok": True}


# ---------------- backups ----------------


@app.exception_handler(BackupError)
async def _backup_error(_req, exc):
    return JSONResponse({"detail": str(exc)}, status_code=400)


@app.get("/api/backups")
async def backup_list():
    return await asyncio.to_thread(backups.listing)


@app.get("/api/devices/{udid}/backup/info")
async def backup_info(udid: str):
    return await backups.device_info(udid)


class BackupBody(BaseModel):
    items: list[str] = []
    password: str = ""


@app.post("/api/devices/{udid}/backup")
async def backup_start(udid: str, body: BackupBody):
    await devices.get(udid)
    return backups.start_backup(udid, body.items, body.password)


class RestoreBody(BaseModel):
    source: Optional[str] = None  # udid folder of a full backup
    snapshot: Optional[str] = None  # id of a partial backup
    password: str = ""
    confirm: bool = False


@app.post("/api/devices/{udid}/restore")
async def backup_restore(udid: str, body: RestoreBody):
    if not body.confirm:
        raise HTTPException(400, _("Confirmation required"))
    await devices.get(udid)
    if body.snapshot:
        return backups.start_restore_snapshot(udid, body.snapshot, body.password)
    return backups.start_restore_full(udid, body.source or udid, body.password)


class EncryptionBody(BaseModel):
    enable: bool
    password: str
    old_password: str = ""


@app.post("/api/devices/{udid}/backup/encryption")
async def backup_encryption(udid: str, body: EncryptionBody):
    _ensure_no_update()
    if not body.password:
        raise HTTPException(400, _("Enter the password."))
    try:
        await backups.set_encryption(udid, body.enable, body.password, body.old_password)
    except BackupError:
        raise
    except Exception as e:
        raise HTTPException(502, _("The iPhone refused: {error}", error=f"{type(e).__name__}: {e}"))
    return await backups.device_info(udid)


class BackupDeleteBody(BaseModel):
    path: str
    confirm: bool = False


@app.post("/api/backups/delete")
async def backup_delete(body: BackupDeleteBody):
    if not body.confirm:
        raise HTTPException(400, _("Confirmation required"))
    await asyncio.to_thread(backups.delete, body.path)
    return {"ok": True}


class FolderBody(BaseModel):
    path: str


@app.post("/api/backups/folder")
async def backup_folder(body: FolderBody):
    if not body.path:
        raise HTTPException(400)
    backups.set_root(body.path)
    return {"ok": True}


# ---------------- local helpers (this is a desktop app on localhost) ----------------


def _dialog(kind: str) -> str:
    if kind == "folder":
        path = system.pick("folder", _("Choose the destination folder"))
    else:
        path = system.pick("ipsw", _("Choose the IPSW file"), _("iOS firmware"))
    return str(Path(path)) if path else ""  # Tk gives "/" on Windows too


@app.post("/api/pick/{kind}")
async def pick(kind: str):
    if kind not in ("folder", "ipsw"):
        raise HTTPException(400)
    return {"path": await asyncio.to_thread(_dialog, kind)}


@app.get("/api/defaults")
async def defaults():
    return {"photos_dest": str(Path.home() / "Pictures" / "iPhone"), "sep": os.sep,
            "can_delete_photos": system.IS_WINDOWS}


class OpenBody(BaseModel):
    path: str


@app.post("/api/open-folder")
async def open_folder(body: OpenBody):
    if Path(body.path).is_dir():
        system.open_folder(body.path)
    return {"ok": True}


@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


app.mount("/static", StaticFiles(directory=STATIC), name="static")
