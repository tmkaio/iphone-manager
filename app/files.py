"""Installed apps and a file browser over AFC.

Roots:
- "media": the iPhone's media partition (/var/mobile/Media: DCIM, Downloads, Books, PhotoData...).
- "app:<bundle id>": an app's sandbox via house_arrest. iOS only vends it for apps with file sharing
  on (UIFileSharingEnabled), and then only their Documents folder; development builds get the whole
  container. The Files app's "On My iPhone" storage isn't reachable from a computer at all.
"""

import asyncio
import logging
import posixpath
import time
import uuid
from typing import Any, AsyncIterator, Optional

from pymobiledevice3.services.afc import AfcService
from pymobiledevice3.services.house_arrest import HouseArrestService
from pymobiledevice3.services.installation_proxy import InstallationProxyService
from pymobiledevice3.services.springboard import SpringBoardServicesService

from .tasks import spawn
from .device import DeviceManager
from .i18n import _
from .photos import CHUNK, _ts

log = logging.getLogger(__name__)

APP_ATTRIBUTES = ["CFBundleIdentifier", "CFBundleDisplayName", "CFBundleName", "CFBundleShortVersionString",
                  "CFBundleVersion", "UIFileSharingEnabled"]

# Deleting these behind the Photos app's back leaves its library with broken items
PROTECTED_MEDIA = {"DCIM", "PhotoData"}


class FileBrowser:
    def __init__(self, devices: DeviceManager) -> None:
        self.devices = devices
        self.scans: dict[str, dict[str, Any]] = {}  # scan id -> {status, done, items, error}
        self._icons: dict[tuple[str, str], Optional[bytes]] = {}
        self._apps: dict[str, list[dict[str, Any]]] = {}
        self._sizes: dict[str, str] = {}
        self._docs_only: dict[tuple[str, str], bool] = {}  # (udid, root) -> only Documents vended

    # ---------- apps ----------

    async def apps(self, udid: str, refresh: bool = False) -> dict[str, Any]:
        """The list comes back at once; sizes take the iPhone ~1 min for a full phone, so they're
        computed in the background and merged in when ready ("sizes": "computing" | "ready" | "error")."""
        if udid not in self._apps or refresh:
            entry = await self.devices.get(udid)
            raw = await self._lookup(entry.lockdown, APP_ATTRIBUTES)
            apps = []
            for a in raw.values():
                bundle = a["CFBundleIdentifier"]
                apps.append({
                    "bundle": bundle,
                    "name": a.get("CFBundleDisplayName") or a.get("CFBundleName") or bundle,
                    "version": a.get("CFBundleShortVersionString") or a.get("CFBundleVersion"),
                    "app_size": None,
                    "data_size": None,
                    "file_sharing": bool(a.get("UIFileSharingEnabled")),
                })
            apps.sort(key=lambda x: x["name"].lower())
            self._apps[udid] = apps
            self._sizes[udid] = "computing"
            spawn(self._fill_sizes(udid, apps))
        return {"apps": self._apps[udid], "sizes": self._sizes.get(udid)}

    async def _fill_sizes(self, udid: str, apps: list[dict[str, Any]]) -> None:
        try:
            entry = await self.devices.get(udid)
            raw = await self._lookup(entry.lockdown, ["CFBundleIdentifier", "StaticDiskUsage", "DynamicDiskUsage"])
            sizes = {a["CFBundleIdentifier"]: a for a in raw.values()}
            for app in apps:
                s = sizes.get(app["bundle"], {})
                app["app_size"] = int(s.get("StaticDiskUsage") or 0)
                app["data_size"] = int(s.get("DynamicDiskUsage") or 0)
            if self._apps.get(udid) is apps:
                self._sizes[udid] = "ready"
        except Exception as e:
            log.warning("app sizes failed: %s", e)
            if self._apps.get(udid) is apps:
                self._sizes[udid] = "error"

    @staticmethod
    async def _lookup(lockdown: Any, attributes: list[str]) -> dict[str, dict[str, Any]]:
        proxy = InstallationProxyService(lockdown=lockdown)
        try:
            return await proxy.lookup({"ApplicationType": "User", "ReturnAttributes": attributes}) or {}
        finally:
            await _close(proxy)

    def apps_total(self, udid: str) -> Optional[int]:
        """Apps + their data, once the sizes are known."""
        if self._sizes.get(udid) != "ready":
            return None
        return sum((a["app_size"] or 0) + (a["data_size"] or 0) for a in self._apps.get(udid, []))

    async def uninstall(self, udid: str, bundle: str) -> None:
        """Remove an app and all of its data (what deleting it on the home screen does)."""
        entry = await self.devices.get(udid)
        proxy = InstallationProxyService(lockdown=entry.lockdown)
        try:
            await proxy.uninstall(bundle)
        finally:
            await _close(proxy)
        apps = self._apps.get(udid)
        if apps is not None:
            apps[:] = [a for a in apps if a["bundle"] != bundle]

    async def icon(self, udid: str, bundle: str) -> Optional[bytes]:
        key = (udid, bundle)
        if key not in self._icons:
            entry = await self.devices.get(udid)
            sb = SpringBoardServicesService(lockdown=entry.lockdown)
            try:
                self._icons[key] = await sb.get_icon_pngdata(bundle)
            except Exception as e:
                log.debug("icon %s: %s", bundle, e)
                self._icons[key] = None
            finally:
                await _close(sb)
        return self._icons[key]

    # ---------- connections ----------

    async def _open(self, udid: str, root: str) -> AfcService:
        """A dedicated AFC connection for this root (caller closes it)."""
        if root == "media":
            return await self.devices.new_afc(udid)
        if not root.startswith("app:"):
            raise ValueError(_("unknown root: {root}", root=root))
        bundle = root[4:]
        entry = await self.devices.get(udid)
        try:  # whole container: only development builds
            afc = await HouseArrestService.create(entry.lockdown, bundle, documents_only=False)
            self._docs_only[(udid, root)] = False
            return afc
        except Exception:
            pass
        try:
            afc = await HouseArrestService.create(entry.lockdown, bundle, documents_only=True)
            self._docs_only[(udid, root)] = True
            return afc
        except Exception as e:
            raise PermissionError(_(
                "This app doesn't share files with the computer (only apps with 'File Sharing' "
                "give access to their Documents). ({error})", error=type(e).__name__
            ))

    def _home(self, udid: str, root: str, path: str) -> str:
        """With only Documents vended, the container's root can't be listed: "/" means /Documents."""
        if path.strip("/") == "" and self._docs_only.get((udid, root)):
            return "/Documents"
        return path

    # ---------- listing ----------

    async def listdir(self, udid: str, root: str, path: str) -> list[dict[str, Any]]:
        afc = await self._open(udid, root)
        path = self._home(udid, root, path)
        try:
            result = []
            for name in await afc.listdir(path):
                if name in (".", "..", ""):
                    continue
                full = posixpath.join(path, name)
                try:
                    st = await afc.stat(full)
                except Exception:
                    continue
                result.append(_entry(full, name, st))
            return result
        finally:
            await _close(afc)

    def start_scan(self, udid: str, root: str, path: str) -> dict[str, Any]:
        """Every file under path, recursively (for "largest files anywhere")."""
        scan = {"id": uuid.uuid4().hex[:8], "status": "scanning", "done": 0, "dirs": 0, "items": [], "error": None,
                "root": root, "path": path, "started": time.time(), "cancel": False}
        self.scans[scan["id"]] = scan
        spawn(self._scan(udid, scan))
        return scan

    async def _scan(self, udid: str, scan: dict[str, Any]) -> None:
        afc = None
        try:
            afc = await self._open(udid, scan["root"])
            stack = [self._home(udid, scan["root"], scan["path"])]
            while stack and not scan["cancel"]:
                folder = stack.pop()
                try:
                    names = await afc.listdir(folder)
                except Exception:
                    continue  # unreadable folder (permissions): skip it
                scan["dirs"] += 1
                for name in names:
                    if name in (".", "..", ""):
                        continue
                    full = posixpath.join(folder, name)
                    try:
                        st = await afc.stat(full)
                    except Exception:
                        continue
                    if st.get("st_ifmt") == "S_IFDIR":
                        stack.append(full)
                    else:
                        scan["items"].append(_entry(full, name, st))
                        scan["done"] += 1
            scan["status"] = "cancelled" if scan["cancel"] else "done"
        except Exception as e:
            log.exception("file scan failed")
            scan["status"] = "error"
            scan["error"] = f"{type(e).__name__}: {e}"
        finally:
            if afc:
                await _close(afc)

    # ---------- reading / writing ----------

    async def stat(self, udid: str, root: str, path: str) -> dict[str, Any]:
        afc = await self._open(udid, root)
        try:
            return await afc.stat(path)
        finally:
            await _close(afc)

    async def stream(self, udid: str, root: str, path: str, start: int = 0,
                     end: Optional[int] = None) -> AsyncIterator[bytes]:
        afc = await self._open(udid, root)
        handle = None
        try:
            handle = await afc.fopen(path, "r")
            if start:
                await afc.fseek(handle, start)
            remaining = None if end is None else end - start + 1
            while remaining is None or remaining > 0:
                data = await afc.fread(handle, CHUNK if remaining is None else min(CHUNK, remaining))
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
                await _close(afc)

    async def upload(self, udid: str, root: str, folder: str, name: str, chunks: AsyncIterator[bytes]) -> str:
        name = posixpath.basename(name.replace("\\", "/"))
        if not name or name in (".", ".."):
            raise ValueError(_("invalid file name"))
        afc = await self._open(udid, root)
        target = posixpath.join(self._home(udid, root, folder), name)
        try:
            if await afc.exists(target):
                raise FileExistsError(_("A file named {name} already exists in this folder", name=name))
            handle = await afc.fopen(target, "w")
            try:
                async for chunk in chunks:
                    await afc.fwrite(handle, chunk)
            finally:
                await afc.fclose(handle)
            return target
        finally:
            await _close(afc)

    async def delete(self, udid: str, root: str, paths: list[str]) -> list[str]:
        """Remove files/folders; returns the ones that couldn't be removed."""
        if root == "media":
            protected = [p for p in paths if p.strip("/").split("/")[0] in PROTECTED_MEDIA or not p.strip("/")]
            if protected:
                raise PermissionError(_(
                    "Photo library files (DCIM/PhotoData) can't be deleted here: the Photos app "
                    "would be left with broken items. Delete them on the iPhone itself."
                ))
        afc = await self._open(udid, root)
        failed: list[str] = []
        try:
            for p in paths:
                failed += await afc.rm(p, force=True)
            return failed
        finally:
            await _close(afc)

    async def mkdir(self, udid: str, root: str, path: str) -> None:
        afc = await self._open(udid, root)
        try:
            await afc.makedirs(path)
        finally:
            await _close(afc)


def _entry(path: str, name: str, st: dict[str, Any]) -> dict[str, Any]:
    is_dir = st.get("st_ifmt") == "S_IFDIR"
    return {
        "path": path,
        "name": name,
        "dir": is_dir,
        "size": 0 if is_dir else int(st.get("st_size", 0)),
        "mtime": _ts(st.get("st_mtime")),
        "btime": _ts(st.get("st_birthtime")),
        "ext": "" if is_dir else posixpath.splitext(name)[1].lower().lstrip("."),
    }


async def _close(service: Any) -> None:
    try:
        await asyncio.wait_for(service.close(), 3)
    except BaseException as e:
        if isinstance(e, (KeyboardInterrupt, SystemExit)):
            raise
