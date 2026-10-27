"""The whole Photos library — every item, media types, albums and folders — read from the iPhone's
Photos database (/PhotoData/Photos.sqlite).

The database is big (1.6 GB on a 24k-item library: iCloud change history, scene classification,
OCR text…), but this only needs a few tables (~60 MB, ~7 s). sqlite_remote reads just their pages
in place over AFC, so nothing is copied and nothing on the iPhone changes.

Each item: [pk, path, unix date, flags, original size in bytes, iCloud asset id (CloudKit CPLAsset
record name, used by icloud.py)].

With iCloud "Optimize iPhone Storage" most originals are not on the iPhone, but every item has a
thumbnail under /PhotoData/Thumbnails/V2/<item path>/, so the app can show the whole library like
the Photos app and mark which items can be downloaded by cable.
"""

import logging
import time
from typing import Any

from .device import DeviceManager
from .sqlite_remote import CorruptRead, RemoteSqlite

log = logging.getLogger(__name__)

DB_PATH = "/PhotoData/Photos.sqlite"
ATTEMPTS = 3  # the database is live: a page can change mid-read
CACHE_SECONDS = 120
CORE_DATA_EPOCH = 978307200  # 2001-01-01 in Unix time

# ZGENERICALBUM.ZKIND
USER_ALBUM = 2
FOLDER = 4000
ROOT_FOLDER = 3999

# item flags (bit mask sent to the browser)
FAV, HIDDEN, TRASHED, LIVE, SCREENSHOT, PANO, SLOMO, TIMELAPSE, SCREENREC, PORTRAIT, CINEMATIC, BURST, ANIMATED, \
    SELFIE, VIDEO = (1 << i for i in range(15))
# ZASSET.ZKINDSUBTYPE
SUBTYPES = {1: PANO, 2: LIVE, 10: SCREENSHOT, 101: SLOMO, 102: TIMELAPSE, 103: SCREENREC}


class AlbumLibrary:
    def __init__(self, devices: DeviceManager) -> None:
        self.devices = devices
        self._cache: dict[str, tuple[float, dict[str, Any]]] = {}

    async def library(self, udid: str, refresh: bool = False) -> dict[str, Any]:
        cached = self._cache.get(udid)
        if cached and not refresh and time.time() - cached[0] < CACHE_SECONDS:
            return cached[1]
        for attempt in range(ATTEMPTS):
            afc = await self.devices.new_afc(udid)
            try:
                t = time.time()
                async with RemoteSqlite(afc, DB_PATH) as db:
                    result = await _read_library(db)
                    log.info("photo library: %d items, %d albums, %.1f MB read in %.1f s", len(result["items"]),
                             len(result["albums"]), db.pages_read * db.page_size / 1e6, time.time() - t)
                self._cache[udid] = (time.time(), result)
                return result
            except CorruptRead as e:
                log.info("photo library: inconsistent read (%s), retrying", e)
                if attempt == ATTEMPTS - 1:
                    raise
            finally:
                try:
                    await afc.aclose()
                except Exception:
                    pass
        raise RuntimeError("unreachable")

    def forget(self, udid: str) -> None:
        self._cache.pop(udid, None)


async def _read_library(db: RemoteSqlite) -> dict[str, Any]:
    if db._schema is None:
        db._schema = await db.schema()

    # front camera = selfie (the Selfies smart album is computed the same way by Photos)
    selfies = set()
    if "ZEXTENDEDATTRIBUTES" in db._schema:
        for asset, lens in await db.table("ZEXTENDEDATTRIBUTES", ["ZASSET", "ZLENSMODEL"]):
            if lens and "front" in lens.lower():
                selfies.add(asset)

    # original file size, also for items whose original is only in iCloud
    sizes: dict[int, int] = {}
    if "ZADDITIONALASSETATTRIBUTES" in db._schema:
        for asset, size in await db.table("ZADDITIONALASSETATTRIBUTES", ["ZASSET", "ZORIGINALFILESIZE"]):
            if asset and size:
                sizes[asset] = size

    items = []
    for (pk, directory, filename, kind, subtype, date, trashed, hidden, favorite, depth, burst, playback,
         screenshot, cloud_guid) in await db.table("ZASSET", [
            "Z_PK", "ZDIRECTORY", "ZFILENAME", "ZKIND", "ZKINDSUBTYPE", "ZDATECREATED", "ZTRASHEDSTATE", "ZHIDDEN",
            "ZFAVORITE", "ZDEPTHTYPE", "ZAVALANCHEUUID", "ZPLAYBACKSTYLE", "ZISDETECTEDSCREENSHOT", "ZCLOUDASSETGUID"]):
        if not directory or not filename:
            continue
        video = kind == 1
        flags = SUBTYPES.get(subtype or 0, 0)
        flags |= (VIDEO if video else 0) | (FAV if favorite else 0) | (HIDDEN if hidden else 0)
        flags |= (TRASHED if trashed else 0) | (BURST if burst else 0) | (ANIMATED if playback == 2 else 0)
        flags |= (SCREENSHOT if screenshot else 0) | (SELFIE if pk in selfies else 0)
        if depth:
            flags |= CINEMATIC if video else PORTRAIT
        items.append([pk, f"/{directory}/{filename}", round((date or 0) + CORE_DATA_EPOCH), flags, sizes.get(pk, 0),
                      cloud_guid])

    # albums (and their folders) <-> items
    join = album_col = asset_col = None
    for name in sorted(db._schema):
        if not (name.startswith("Z_") and name.endswith("ASSETS")):
            continue
        cols = await db.columns(name)
        albums_c = [c for c in cols if c.endswith("ALBUMS")]
        assets_c = [c for c in cols if c.endswith("ASSETS") and not c.startswith("Z_FOK")]
        if albums_c and assets_c:
            join, album_col, asset_col = name, albums_c[0], assets_c[0]
            break
    members: dict[int, list[int]] = {}
    if join:
        for album_pk, asset_pk in await db.table(join, [album_col, asset_col]):
            members.setdefault(album_pk, []).append(asset_pk)

    rows = await db.table("ZGENERICALBUM", ["Z_PK", "ZKIND", "ZTRASHEDSTATE", "ZTITLE", "ZPARENTFOLDER"])
    roots = {pk for pk, kind, *_rest in rows if kind == ROOT_FOLDER}
    folders, albums = [], []
    for pk, kind, trashed, title, parent in rows:
        if trashed or not title:
            continue
        parent = None if parent in roots else parent
        if kind == FOLDER:
            folders.append({"id": pk, "title": title, "parent": parent})
        elif kind == USER_ALBUM:
            albums.append({"id": pk, "title": title, "parent": parent, "items": members.get(pk, [])})
    albums.sort(key=lambda a: a["title"].lower())
    folders.sort(key=lambda f: f["title"].lower())
    return {"items": items, "albums": albums, "folders": folders}
