"""iCloud Photos access (icloud.com's web service, through pyicloud) for items whose original is not
on the iPhone: view, download and delete them.

The user signs in from the app (Apple ID, password, then the two-factor code); the password only
goes to Apple and is not stored. pyicloud keeps the trusted session's cookies in COOKIE_DIR, so
later the app reconnects with just the Apple ID. Doesn't work with Advanced Data Protection on
(icloud.com can't read the library then).

Items are matched by the iCloud asset id the iPhone's Photos database keeps (ZASSET.ZCLOUDASSETGUID,
the CloudKit CPLAsset record name). Deleting marks the asset deleted, which moves it to Recently
Deleted on every device, like deleting on icloud.com.
"""

import json
import logging
import threading
import time
from collections import OrderedDict
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any, Iterator, Optional

import requests

from .i18n import _
from .photos import CACHE_DIR

log = logging.getLogger(__name__)

COOKIE_DIR = CACHE_DIR.parent / "icloud"
ACCOUNT_FILE = COOKIE_DIR / "account.json"  # just the Apple ID, to reconnect
URL_TTL = 20 * 60  # signed download URLs expire: refresh the asset after this
BLOCK = 4 * 1024 * 1024  # read_range fetches the original in blocks of this size,
READ_AHEAD = 6  # this many ahead of the one being read (a 4K iPhone video is ~12 MB/s)
CACHE_BLOCKS = 96  # and keeps this many (~384 MB)


class ICloudError(Exception):
    pass


class ICloudPhotos:
    def __init__(self) -> None:
        self.api: Any = None
        self.state = "logged_out"  # logged_out | need_2fa | ok
        self.apple_id: Optional[str] = None
        self.error: Optional[str] = None
        self._lock = threading.RLock()
        self._assets: dict[str, tuple[float, Any]] = {}
        # block cache for read_range (video previews)
        self._blocks: "OrderedDict[tuple[str, int], Future]" = OrderedDict()
        self._blocks_lock = threading.Lock()
        self._pool = ThreadPoolExecutor(READ_AHEAD + 2, thread_name_prefix="icloud-read")
        self._cdn = requests.Session()  # the signed URLs need no cookies

    # ---------- account (all blocking: call from a thread) ----------

    def status(self) -> dict[str, Any]:
        return {"state": self.state, "apple_id": self.apple_id, "error": self.error}

    def resume(self) -> dict[str, Any]:
        """Reconnect with the saved trusted session, without a password."""
        with self._lock:
            # only from scratch: mid-login (waiting for the 2FA code) must not be reset
            if self.state != "logged_out" or not ACCOUNT_FILE.exists():
                return self.status()
            try:
                apple_id = json.loads(ACCOUNT_FILE.read_text())["apple_id"]
                self._start(apple_id, None)
            except Exception as e:
                log.info("iCloud: saved session not usable (%s)", e)
                self.state, self.api = "logged_out", None
            return self.status()

    def login(self, apple_id: str, password: str) -> dict[str, Any]:
        with self._lock:
            self.error = None
            try:
                self._start(apple_id, password)
            except Exception as e:
                self.state, self.api = "logged_out", None
                self.error = _friendly(e)
                raise ICloudError(self.error)
            return self.status()

    def _start(self, apple_id: str, password: Optional[str]) -> None:
        from pyicloud import PyiCloudService

        COOKIE_DIR.mkdir(parents=True, exist_ok=True)
        api = PyiCloudService(apple_id, password, cookie_directory=str(COOKIE_DIR))
        if api.requires_2fa or api.requires_2sa:
            if password is None:  # the saved session isn't trusted anymore: a full login is needed
                raise ICloudError(_("session expired"))
            self.state = "need_2fa"
        else:
            self.state = "ok"
        self.api, self.apple_id = api, apple_id
        ACCOUNT_FILE.write_text(json.dumps({"apple_id": apple_id}))

    def verify(self, code: str) -> dict[str, Any]:
        with self._lock:
            if self.api is None or self.state != "need_2fa":
                raise ICloudError(_("Sign in first"))
            if not self.api.validate_2fa_code(code.strip()):
                self.error = _("Incorrect code")
                raise ICloudError(self.error)
            if not self.api.is_trusted_session:
                self.api.trust_session()  # so later starts don't ask for the code (or the password)
            self.state, self.error = "ok", None
            return self.status()

    def logout(self) -> None:
        with self._lock:
            self.api, self.state, self.apple_id = None, "logged_out", None
            self._assets.clear()
            for f in COOKIE_DIR.glob("*"):
                try:
                    f.unlink()
                except OSError:
                    pass

    # ---------- assets ----------

    def asset(self, guid: str) -> Any:
        with self._lock:
            if self.state != "ok" or self.api is None:
                raise ICloudError(_("Connect iCloud first"))
            cached = self._assets.get(guid)
            if cached and time.time() - cached[0] < URL_TTL:
                return cached[1]
            asset = self._lookup(guid)
            self._assets[guid] = (time.time(), asset)
            return asset

    def _lookup(self, guid: str) -> Any:
        """CloudKit records/lookup by name (~0.3 s). pyicloud's album.get() runs a list query that
        misses these ids and then walks the whole library looking for it (minutes on 24k items)."""
        from pyicloud.common.cloudkit.models import CKZoneIDReq
        from pyicloud.services.photos_cloudkit.service import PHOTO_DESIRED_KEYS

        library = self.api.photos.all._library
        zone = CKZoneIDReq(**library.zone_id)

        def lookup(names: list[str]) -> list[Any]:
            return library._client.lookup(record_names=names, zone_id=zone, desired_keys=PHOTO_DESIRED_KEYS).records

        records = [r for r in lookup([guid]) if getattr(r, "recordType", None) == "CPLAsset"]
        if not records:
            raise ICloudError(_("This item wasn't found in iCloud"))
        ref = records[0].fields.get("masterRef")
        ref = getattr(ref, "root", ref)  # pydantic RootModel wrapper
        master = getattr(getattr(ref, "value", None), "recordName", None)
        if not master:
            raise ICloudError(_("iCloud didn't report this item's original"))
        asset = library._asset_from_uploaded_records(lookup([master, guid]))
        if asset is None:
            raise ICloudError(_("This item wasn't found in iCloud"))
        return asset

    def info(self, guid: str) -> dict[str, Any]:
        a = self.asset(guid)
        return {"filename": a.filename, "size": a.size, "type": a.item_type,
                "video": a.item_type == "movie", "url": a.download_url("original")}

    def original_url(self, guid: str) -> str:
        url = self.asset(guid).download_url("original")
        if not url:
            raise ICloudError(_("iCloud didn't offer this item's original"))
        return url

    def iter_original(self, guid: str) -> tuple[str, Optional[int], Iterator[bytes]]:
        """(filename, size, chunks) of the original, streamed from iCloud."""
        a = self.asset(guid)
        resp = self.api.session.get(self.original_url(guid), stream=True, timeout=60)
        resp.raise_for_status()
        size = int(resp.headers.get("Content-Length") or 0) or a.size
        return a.filename, size, resp.iter_content(1024 * 1024)

    def playable_url(self, guid: str) -> Optional[str]:
        """The 720p (or smaller) H.264 MP4 iCloud keeps of a video, which the browser plays as is:
        a 4K original can be ~100 Mbit/s, more than the connection to iCloud reliably gives."""
        versions = self.asset(guid).versions
        for key in ("medium", "thumb"):
            v = versions.get(key) or {}
            if v.get("url") and v.get("type") == "public.mpeg-4":
                return v["url"]
        return None

    def read_original(self, guid: str, limit: int = 200 * 1024 * 1024) -> bytes:
        _name, size, chunks = self.iter_original(guid)
        if size and size > limit:
            raise ICloudError(_("File too large to preview"))
        return b"".join(chunks)

    def read_range(self, guid: str, start: int, end: int) -> Iterator[bytes]:
        """Bytes start..end (inclusive) of the original, through a block cache with read-ahead.
        ffmpeg jumps back and forth between the audio and video samples of a .MOV; straight
        against iCloud every jump is a new request (~0.4 s), and a 4K video plays at 1/50 speed."""
        first, last = start // BLOCK, end // BLOCK
        for n in range(first, last + 1):
            data = self._block(guid, n)
            for m in range(n + 1, n + 1 + READ_AHEAD):
                self._block_future(guid, m)
            lo = start - n * BLOCK if n == first else 0
            hi = end - n * BLOCK + 1 if n == last else len(data)
            yield data[lo:hi]

    def _block(self, guid: str, n: int) -> bytes:
        return self._block_future(guid, n).result()

    def _block_future(self, guid: str, n: int) -> Future:
        with self._blocks_lock:
            key = (guid, n)
            fut = self._blocks.get(key)
            if fut is None or (fut.done() and fut.exception() is not None):
                url = self.original_url(guid)
                fut = self._pool.submit(self._fetch, url, n)
                self._blocks[key] = fut
                while len(self._blocks) > CACHE_BLOCKS:
                    self._blocks.popitem(last=False)
            self._blocks.move_to_end(key)
            return fut

    def _fetch(self, url: str, n: int) -> bytes:
        resp = self._cdn.get(url, headers={"Range": f"bytes={n * BLOCK}-{(n + 1) * BLOCK - 1}"}, timeout=60)
        resp.raise_for_status()
        return resp.content

    def delete(self, guid: str) -> None:
        a = self.asset(guid)
        if not a.delete():
            raise ICloudError(_("iCloud refused the deletion"))
        self._assets.pop(guid, None)


def _friendly(e: Exception) -> str:
    name = type(e).__name__
    if "FailedLogin" in name:
        return _("Incorrect Apple ID or password")
    if "AccountLocked" in name:
        return _("Account locked by Apple")
    if "ServiceNotActivated" in name or "ADP" in str(e) or "Advanced" in str(e):
        return _("iCloud doesn't allow web access (is Advanced Data Protection on?)")
    return f"{name}: {e}"
