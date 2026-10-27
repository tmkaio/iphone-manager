"""iPhone backups in the iTunes/Finder format (mobilebackup2), and partial backups made from them.

Layout of the backup folder (Settings: <app data>/Backups by default, see system.DATA_DIR):

    <root>/<udid>/                         the device's full backup, updated incrementally like iTunes
    <root>/Snapshots/<id>/snapshot.json    a partial backup: one app's data, Messages, Contacts...
    <root>/Snapshots/<id>/<udid>/          ...stored as a pruned copy of the full backup (same format)

A full backup can be restored as is (after a recovery or an update gone wrong). iOS has no way to
restore a single app or Messages alone, so restoring a partial backup does what iMazing does: back up
the iPhone now, put the partial backup's files in place of the current ones in a merged copy (hard
links, no extra space), and restore that merged backup. Everything else comes back as it was a minute
before. Encrypted backups work the same way; the per-file keys of the partial backup are rewrapped
with the current backup's keybag when they differ.
"""

import asyncio
import json
import logging
import os
import plistlib
import shutil
import sqlite3
import time
import uuid
from contextlib import closing
from pathlib import Path
from typing import Any, Callable, Optional

from cryptography.hazmat.primitives.keywrap import InvalidUnwrap, aes_key_unwrap, aes_key_wrap
from pyiosbackup.keybag import Keybag
from pyiosbackup.manifest_plist import ManifestPlist
from pymobiledevice3.services.installation_proxy import InstallationProxyService
from pymobiledevice3.services.mobilebackup2 import (
    BACKUP_SELECTIONS, Mobilebackup2Service, NP_LOCAL_AUTH_DISMISSED, NP_LOCAL_AUTH_PRESENTED,
    NP_SYNC_CANCEL_REQUEST, BackupSelection,
)

from . import system
from .device import DeviceManager
from .i18n import _
from .tasks import spawn

log = logging.getLogger(__name__)

DATA_DIR = system.DATA_DIR
SETTINGS = DATA_DIR / "settings.json"
METADATA = ("Info.plist", "Manifest.plist", "Status.plist")
# <root>/.state/<udid>.json: {"complete": bool}. Status.plist can't tell: it's written before the backup
# starts. Backups made by other apps have none and count as complete.
STATE_DIR = ".state"

# Partial backups of Apple's own data: (domain, path prefix) pairs, matched on whole path segments
BUILTIN: dict[str, dict[str, Any]] = {
    "messages": {"name": "Messages", "rules": [("HomeDomain", "Library/SMS"), ("MediaDomain", "Library/SMS")]},
    "contacts": {"name": "Contacts", "rules": [("HomeDomain", "Library/AddressBook")]},
    "call_history": {"name": "Call history", "rules": [("HomeDomain", "Library/CallHistoryDB"),
                                                       ("HomeDomain", "Library/CallHistoryTransactions")]},
    "notes": {"name": "Notes", "rules": [("AppDomainGroup-group.com.apple.notes", ""),
                                         ("HomeDomain", "Library/Notes")]},
    "calendar": {"name": "Calendar", "rules": [("HomeDomain", "Library/Calendar")]},
    "safari": {"name": "Safari bookmarks", "rules": [(r.domain, r.relative_path)
                                                     for r in BACKUP_SELECTIONS[BackupSelection.BOOKMARKS]]},
}


class BackupError(Exception):
    pass


def _matches(rules: list[tuple[str, str]], domain: str, path: str) -> bool:
    for d, prefix in rules:
        if domain == d and (not prefix or path == prefix or path.startswith(prefix + "/")):
            return True
    return False


def _app_matcher(bundle: str, groups: list[str]) -> Callable[[str, str], bool]:
    group_domains = {f"AppDomainGroup-{g}" for g in groups}

    def match(domain: str, _path: str) -> bool:
        return (domain == f"AppDomain-{bundle}" or domain.startswith(f"AppDomainPlugin-{bundle}.")
                or domain in group_domains)
    return match


def _matcher(meta: dict[str, Any]) -> Callable[[str, str], bool]:
    if meta["kind"] == "app":
        return _app_matcher(meta["bundle"], meta.get("groups") or [])
    rules = BUILTIN[meta["kind"]]["rules"]
    return lambda domain, path: _matches(rules, domain, path)


def _dir_size(path: Path) -> int:
    total = 0
    for root, _dirs, names in os.walk(path):
        for n in names:
            try:
                total += os.stat(os.path.join(root, n)).st_size
            except OSError:
                pass
    return total


def _plist(path: Path) -> dict[str, Any]:
    try:
        return plistlib.loads(path.read_bytes())
    except Exception:
        return {}


def _link_or_copy(src: Path, dst: Path) -> None:
    dst.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)


# ---------------- Manifest.db (decrypted when the backup is encrypted) ----------------


class Manifest:
    """Manifest.db of a backup folder, opened as plain SQLite in a work folder and written back
    (re-encrypted with the backup's manifest key) by save()."""

    def __init__(self, device_dir: Path, work: Path, password: str = "") -> None:
        self.device_dir = device_dir
        self.plist = _plist(device_dir / "Manifest.plist")
        self.encrypted = bool(self.plist.get("IsEncrypted"))
        self.path = work / f"manifest-{uuid.uuid4().hex}.db"
        self.keybag: Optional[Keybag] = None
        self._key: Optional[bytes] = None
        raw = (device_dir / "Manifest.db").read_bytes()
        if self.encrypted:
            if not password:
                raise BackupError(_("This backup is encrypted: enter its password."))
            manifest = ManifestPlist(self.plist)
            try:
                self.keybag = Keybag.from_manifest(manifest, password)
                raw = self.keybag.decrypt(raw, manifest.manifest_key)
                cls = int.from_bytes(manifest.manifest_key[:4], "little")
                self._key = aes_key_unwrap(self.keybag.get_key(cls), manifest.manifest_key[4:])
            except (InvalidUnwrap, ValueError, KeyError):
                raise BackupError(_("Wrong backup password."))
        self.path.write_bytes(raw)

    def connect(self) -> sqlite3.Connection:
        return sqlite3.connect(self.path)

    def save(self, device_dir: Path) -> None:
        with closing(self.connect()) as c:
            c.execute("VACUUM")
        data = self.path.read_bytes()
        if self.encrypted:
            from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
            enc = Cipher(algorithms.AES(self._key), modes.CBC(b"\x00" * 16)).encryptor()
            data = enc.update(data) + enc.finalize()
        (device_dir / "Manifest.db").write_bytes(data)

    def close(self) -> None:
        self.path.unlink(missing_ok=True)


def _rewrap(blob: bytes, old: Keybag, new: Keybag) -> bytes:
    """Re-wrap the per-file key inside an MBFile archive (Manifest.db Files.file) for another keybag.
    The file's content stays encrypted with the same key."""
    archive = plistlib.loads(blob)
    objects = archive["$objects"]
    top = objects[archive["$top"]["root"].data]
    ref = top.get("EncryptionKey")
    if ref is None:
        return blob
    obj = objects[ref.data]
    data = obj["NS.data"] if isinstance(obj, dict) else obj
    cls = int.from_bytes(data[:4], "little")
    rewrapped = data[:4] + aes_key_wrap(new.get_key(cls), aes_key_unwrap(old.get_key(cls), data[4:]))
    if isinstance(obj, dict):
        obj["NS.data"] = rewrapped
    else:
        objects[ref.data] = rewrapped
    return plistlib.dumps(archive, fmt=plistlib.FMT_BINARY)


# ---------------- the service ----------------


class _Mobilebackup2(Mobilebackup2Service):
    """Reports the iPhone's own prompts (passcode, cancel) to the job."""

    on_event: Callable[[str], None] = staticmethod(lambda _name: None)

    def _log_backup_notification(self, event: dict[str, Any]) -> None:
        name = event.get("Name")
        if name in (NP_LOCAL_AUTH_PRESENTED, NP_LOCAL_AUTH_DISMISSED, NP_SYNC_CANCEL_REQUEST):
            self.on_event(name)
        super()._log_backup_notification(event)


class Backups:
    def __init__(self, devices: DeviceManager, busy: Callable[[], Optional[str]]) -> None:
        self.devices = devices
        self.busy = busy  # another operation owning the device (an iOS update), or None
        self.jobs: dict[str, dict[str, Any]] = {}
        self._active: Optional[dict[str, Any]] = None
        self._sizes: dict[str, tuple[float, int]] = {}  # full backup dir -> (Status.plist mtime, size)

    # ---------- settings ----------

    @property
    def root(self) -> Path:
        try:
            custom = json.loads(SETTINGS.read_text("utf-8")).get("backup_dir")
        except Exception:
            custom = None
        return Path(custom) if custom else DATA_DIR / "Backups"

    def set_root(self, path: str) -> None:
        p = Path(path)
        p.mkdir(parents=True, exist_ok=True)
        try:
            settings = json.loads(SETTINGS.read_text("utf-8"))
        except Exception:
            settings = {}
        settings["backup_dir"] = str(p)
        SETTINGS.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS.write_text(json.dumps(settings, indent=2), "utf-8")

    # ---------- listing ----------

    def _full_info(self, device_dir: Path) -> Optional[dict[str, Any]]:
        info = _plist(device_dir / "Info.plist")
        status = _plist(device_dir / "Status.plist")
        manifest = _plist(device_dir / "Manifest.plist")
        if not info or not (device_dir / "Manifest.db").exists():
            return None
        status_file = device_dir / "Status.plist"
        mtime = status_file.stat().st_mtime if status_file.exists() else 0
        cached = self._sizes.get(str(device_dir))
        if not cached or cached[0] != mtime:
            cached = (mtime, _dir_size(device_dir))
            self._sizes[str(device_dir)] = cached
        date = status.get("Date")
        return {
            "udid": device_dir.name,
            "name": info.get("Device Name") or info.get("Display Name"),
            "product_type": info.get("Product Type"),
            "ios": info.get("Product Version"),
            "date": date.timestamp() if date else mtime,
            "encrypted": bool(manifest.get("IsEncrypted")),
            "complete": self._complete(device_dir),
            "size": cached[1],
            "path": str(device_dir),
        }

    def _complete(self, device_dir: Path) -> bool:
        try:
            return bool(json.loads((device_dir.parent / STATE_DIR / f"{device_dir.name}.json").read_text("utf-8"))
                        .get("complete"))
        except FileNotFoundError:
            return True
        except Exception:
            return False

    def _mark(self, device_dir: Path, complete: bool) -> None:
        state = device_dir.parent / STATE_DIR
        state.mkdir(parents=True, exist_ok=True)
        (state / f"{device_dir.name}.json").write_text(json.dumps({"complete": complete}), "utf-8")

    def listing(self) -> dict[str, Any]:
        root = self.root
        full, snaps = [], []
        if root.is_dir():
            for d in root.iterdir():
                if d.is_dir() and d.name not in ("Snapshots", ".restore", STATE_DIR):
                    info = self._full_info(d)
                    if info:
                        full.append(info)
            snap_root = root / "Snapshots"
            if snap_root.is_dir():
                for d in snap_root.iterdir():
                    try:
                        meta = json.loads((d / "snapshot.json").read_text("utf-8"))
                    except Exception:
                        continue
                    meta["path"] = str(d)
                    snaps.append(meta)
        full.sort(key=lambda b: -b["date"])
        snaps.sort(key=lambda s: -s["date"])
        try:
            free = shutil.disk_usage(root if root.exists() else root.anchor).free
        except OSError:
            free = None
        return {"root": str(root), "free": free, "full": full, "snapshots": snaps,
                "job": self._public(self._active) if self._active else None,
                "kinds": {k: v["name"] for k, v in BUILTIN.items()}}

    def listing_job(self) -> Optional[dict[str, Any]]:
        return self._active

    async def device_info(self, udid: str) -> dict[str, Any]:
        entry = await self.devices.get(udid)
        try:
            will_encrypt = bool(await entry.lockdown.get_value("com.apple.mobile.backup", "WillEncrypt"))
        except Exception:
            will_encrypt = False
        return {"will_encrypt": will_encrypt}

    def delete(self, path: str) -> None:
        p = Path(path).resolve()
        root = self.root.resolve()
        if root not in p.parents:
            raise BackupError(_("Not a backup of this app."))
        if self._active and self._active["status"] == "running":
            raise BackupError(_("Wait for the backup or restore in progress to finish."))
        shutil.rmtree(p)
        self._sizes.pop(str(p), None)

    # ---------- jobs ----------

    @staticmethod
    def _public(job: dict[str, Any]) -> dict[str, Any]:
        return {k: v for k, v in job.items() if not k.startswith("_")}

    def _new_job(self, udid: str, action: str, title: str) -> dict[str, Any]:
        if self._active and self._active["status"] == "running":
            raise BackupError(_("A backup or restore is already running."))
        reason = self.busy()
        if reason:
            raise BackupError(reason)
        job = {"id": uuid.uuid4().hex[:12], "kind": "backup", "action": action, "udid": udid, "title": title,
               "status": "running", "phase": "start", "percent": 0.0, "hint": None, "error": None,
               "started": time.time(), "cancel": False}
        self.jobs[job["id"]] = job
        self._active = job
        return job

    def cancel(self, job_id: str) -> None:
        job = self.jobs.get(job_id)
        if job and job["status"] == "running" and job.get("_task"):
            job["cancel"] = True
            if job.get("phase") == "restore":
                return  # never interrupt a restore halfway: the iPhone would be left half restored
            job["_task"].cancel()

    def _run(self, job: dict[str, Any], coro) -> dict[str, Any]:
        async def runner():
            try:
                await coro
                job["status"] = "done"
                job["percent"] = 100.0
            except asyncio.CancelledError:
                job["status"] = "cancelled"
            except BackupError as e:
                job["status"], job["error"] = "error", str(e)
            except Exception as e:
                log.exception("backup job failed")
                job["status"], job["error"] = "error", _friendly(e)
            finally:
                job["hint"] = None
                job["finished"] = time.time()
        job["_task"] = spawn(runner())
        return self._public(job)

    def _service(self, lockdown: Any, job: dict[str, Any]) -> _Mobilebackup2:
        svc = _Mobilebackup2(lockdown)

        def on_event(name: str) -> None:
            if name == NP_LOCAL_AUTH_PRESENTED:
                job["hint"] = _("Enter the iPhone's passcode on the iPhone to continue.")
            elif name == NP_LOCAL_AUTH_DISMISSED:
                job["hint"] = None
            elif name == NP_SYNC_CANCEL_REQUEST:
                job["cancelled_on_device"] = True
        svc.on_event = on_event
        return svc

    def _progress(self, job: dict[str, Any], phase: str) -> Callable[[Any], None]:
        job["phase"], job["percent"] = phase, 0.0

        def cb(value: Any) -> None:
            try:
                job["percent"] = max(0.0, min(100.0, float(value)))
            except (TypeError, ValueError):
                pass
        return cb

    async def _full_backup(self, job: dict[str, Any], udid: str) -> Path:
        """Create or update (incrementally) the device's full backup; returns its folder."""
        entry = await self.devices.get(udid)
        root = self.root
        root.mkdir(parents=True, exist_ok=True)
        device_dir = root / udid
        if not (device_dir / "Manifest.db").exists():
            disk = entry.info.get("disk") or {}
            used = (disk.get("TotalDataCapacity") or 0) - (disk.get("TotalDataAvailable") or 0)
            free = shutil.disk_usage(root).free
            if used and free < used * 0.9:
                raise BackupError(_("Not enough free space in {path}: the iPhone has {used} of data and the disk "
                                    "has {free} free. Choose another backup folder.",
                                    path=root, used=_gb(used), free=_gb(free)))
        self._mark(device_dir, False)
        async with self._service(entry.lockdown, job) as svc:
            await svc.backup(full=False, backup_directory=root, progress_callback=self._progress(job, "backup"))
        if job.get("cancelled_on_device"):
            raise BackupError(_("The backup was cancelled on the iPhone."))
        self._mark(device_dir, True)
        self._sizes.pop(str(device_dir), None)
        return device_dir

    # ---------- backup ----------

    def start_backup(self, udid: str, items: list[str], password: str = "") -> dict[str, Any]:
        """Full backup, then the partial backups in `items` ("messages", "contacts"..., "app:<bundle>")."""
        for item in items:
            if item not in BUILTIN and not item.startswith("app:"):
                raise BackupError(_("Unknown item: {item}", item=item))
        title = _("Backing up the iPhone") if not items else _("Backing up {what}", what=self._item_names(udid, items))
        job = self._new_job(udid, "backup", title)
        return self._run(job, self._backup(job, udid, items, password))

    def _item_names(self, udid: str, items: list[str]) -> str:
        names = []
        for item in items:
            if item.startswith("app:"):
                names.append(item[4:])
            else:
                names.append(_(BUILTIN[item]["name"]))
        return ", ".join(names)

    async def _backup(self, job: dict[str, Any], udid: str, items: list[str], password: str) -> None:
        if items and password:
            self._check_password(udid, password)  # fail now rather than after a long backup
        groups: dict[str, tuple[str, list[str]]] = {}
        apps = [i[4:] for i in items if i.startswith("app:")]
        if apps:
            groups = await self._app_info(udid, apps)
        device_dir = await self._full_backup(job, udid)
        job["snapshots"] = []
        for item in items:
            job["phase"], job["percent"] = "snapshot", 0.0
            if item.startswith("app:"):
                bundle = item[4:]
                name, app_groups = groups.get(bundle, (bundle, []))
                meta = {"kind": "app", "bundle": bundle, "name": name, "groups": app_groups}
            else:
                meta = {"kind": item, "name": BUILTIN[item]["name"]}
            snap = await asyncio.to_thread(self._make_snapshot, device_dir, meta, password)
            job["snapshots"].append(snap)

    def _check_password(self, udid: str, password: str) -> None:
        device_dir = self.root / udid
        manifest = _plist(device_dir / "Manifest.plist")
        if manifest.get("IsEncrypted"):
            try:
                Keybag.from_manifest(ManifestPlist(manifest), password)
            except (InvalidUnwrap, ValueError, KeyError):
                raise BackupError(_("Wrong backup password."))

    async def _app_info(self, udid: str, bundles: list[str]) -> dict[str, tuple[str, list[str]]]:
        """Display name and app groups (shared containers, e.g. WhatsApp's chats) of each app."""
        entry = await self.devices.get(udid)
        async with InstallationProxyService(lockdown=entry.lockdown) as proxy:
            raw = await proxy.lookup({"ApplicationType": "User", "BundleIDs": bundles, "ReturnAttributes": [
                "CFBundleIdentifier", "CFBundleDisplayName", "CFBundleName", "Entitlements"]}) or {}
        result = {}
        for bundle in bundles:
            a = raw.get(bundle) or {}
            ent = a.get("Entitlements") or {}
            groups = [g for g in ent.get("com.apple.security.application-groups") or [] if isinstance(g, str)]
            result[bundle] = (a.get("CFBundleDisplayName") or a.get("CFBundleName") or bundle, groups)
        return result

    def _make_snapshot(self, device_dir: Path, meta: dict[str, Any], password: str) -> dict[str, Any]:
        snap_id = time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        snap_dir = self.root / "Snapshots" / snap_id
        out = snap_dir / device_dir.name
        out.mkdir(parents=True)
        try:
            for name in METADATA:
                shutil.copy2(device_dir / name, out / name)
            manifest = Manifest(device_dir, snap_dir, password)
            try:
                match = _matcher(meta)
                with closing(manifest.connect()) as c:
                    rows = c.execute("SELECT fileID, domain, relativePath, flags FROM Files").fetchall()
                    keep = [r for r in rows if match(r[1], r[2])]
                    c.execute("CREATE TEMP TABLE keep (fileID TEXT PRIMARY KEY)")
                    c.executemany("INSERT OR IGNORE INTO keep VALUES (?)", [(r[0],) for r in keep])
                    c.execute("DELETE FROM Files WHERE fileID NOT IN (SELECT fileID FROM keep)")
                    c.commit()
                manifest.save(out)
            finally:
                manifest.close()
            files = 0
            for file_id, _domain, _path, flags in keep:
                src = device_dir / file_id[:2] / file_id
                if flags == 1 and src.exists():
                    dst = out / file_id[:2] / file_id
                    dst.parent.mkdir(exist_ok=True)
                    shutil.copy2(src, dst)  # a copy: the next incremental backup rewrites files in place
                    files += 1
            info = _plist(device_dir / "Info.plist")
            meta = {**meta, "id": snap_id, "udid": device_dir.name, "date": time.time(),
                    "device_name": info.get("Device Name"), "ios": info.get("Product Version"),
                    "product_type": info.get("Product Type"), "encrypted": manifest.encrypted,
                    "files": files, "size": _dir_size(out)}
            (snap_dir / "snapshot.json").write_text(json.dumps(meta, indent=2), "utf-8")
            return meta
        except Exception:
            shutil.rmtree(snap_dir, ignore_errors=True)
            raise

    # ---------- restore ----------

    def start_restore_full(self, udid: str, source: str, password: str = "") -> dict[str, Any]:
        """Restore a full backup (of this or another iPhone) onto the device."""
        device_dir = self.root / source
        info = self._full_info(device_dir) if device_dir.is_dir() else None
        if not info:
            raise BackupError(_("Backup not found."))
        if not info["complete"]:
            raise BackupError(_("This backup didn't finish; it can't be restored."))
        if info["encrypted"] and not password:
            raise BackupError(_("This backup is encrypted: enter its password."))
        job = self._new_job(udid, "restore", _("Restoring the backup of {date}", date=_date(info["date"])))

        async def run():
            if info["encrypted"]:
                await asyncio.to_thread(self._check_password, source, password)
            await self._restore(job, udid, self.root, source, password, remove=True)
        return self._run(job, run())

    def start_restore_snapshot(self, udid: str, snap_id: str, password: str = "") -> dict[str, Any]:
        snap_dir = self.root / "Snapshots" / snap_id
        try:
            meta = json.loads((snap_dir / "snapshot.json").read_text("utf-8"))
        except Exception:
            raise BackupError(_("Backup not found."))
        if meta["udid"] != udid:
            raise BackupError(_("This partial backup is from another iPhone ({name}).", name=meta.get("device_name")))
        name = meta["name"] if meta["kind"] == "app" else _(meta["name"])
        job = self._new_job(udid, "restore", _("Restoring {what}", what=name))
        return self._run(job, self._restore_snapshot(job, udid, snap_dir, meta, password))

    async def _restore_snapshot(self, job: dict[str, Any], udid: str, snap_dir: Path, meta: dict[str, Any],
                                password: str) -> None:
        if meta.get("encrypted") and not password:
            raise BackupError(_("This backup is encrypted: enter its password."))
        # 1. the iPhone as it is now
        device_dir = await self._full_backup(job, udid)
        # 2. merged copy: everything from now, with the partial backup's files in place of the current ones
        job["phase"], job["percent"] = "merge", 0.0
        work = self.root / ".restore"
        shutil.rmtree(work, ignore_errors=True)
        try:
            await asyncio.to_thread(self._merge, device_dir, snap_dir / udid, work / udid, meta, password)
            # 3. restore it
            await self._restore(job, udid, work, udid, password, remove=False)
        finally:
            shutil.rmtree(work, ignore_errors=True)

    def _merge(self, current: Path, snap: Path, out: Path, meta: dict[str, Any], password: str) -> None:
        out.mkdir(parents=True)
        for name in METADATA:
            shutil.copy2(current / name, out / name)
        if meta["kind"] == "app":
            # bring the app back too if it was deleted: the iPhone reinstalls it from the App Store
            info = _plist(out / "Info.plist")
            old = _plist(snap / "Info.plist")
            bundle = meta["bundle"]
            if bundle in (old.get("Applications") or {}):
                info.setdefault("Applications", {}).setdefault(bundle, old["Applications"][bundle])
                installed = info.setdefault("Installed Applications", [])
                if bundle not in installed:
                    installed.append(bundle)
                (out / "Info.plist").write_bytes(plistlib.dumps(info))
        work = out.parent
        cur = Manifest(current, work, password)
        old = Manifest(snap, work, password)
        try:
            rewrap = (cur.encrypted and old.encrypted
                      and cur.plist.get("BackupKeyBag") != old.plist.get("BackupKeyBag"))
            if cur.encrypted != old.encrypted:
                raise BackupError(_("The partial backup and the iPhone's backup aren't both encrypted (or both not): "
                                    "turn backup encryption back to how it was when the partial backup was made."))
            match = _matcher(meta)
            with closing(old.connect()) as c:
                incoming = c.execute("SELECT fileID, domain, relativePath, flags, file FROM Files").fetchall()
            with closing(cur.connect()) as c:
                rows = c.execute("SELECT fileID, domain, relativePath, flags FROM Files").fetchall()
                replaced = [(r[0],) for r in rows if match(r[1], r[2])]
                c.executemany("DELETE FROM Files WHERE fileID = ?", replaced)
                for file_id, domain, path, flags, blob in incoming:
                    if rewrap and blob:
                        blob = _rewrap(blob, old.keybag, cur.keybag)
                    c.execute("INSERT OR REPLACE INTO Files (fileID, domain, relativePath, flags, file) "
                              "VALUES (?, ?, ?, ?, ?)", (file_id, domain, path, flags, blob))
                c.commit()
                kept = c.execute("SELECT fileID FROM Files WHERE flags = 1").fetchall()
            cur.save(out)
        finally:
            cur.close()
            old.close()
        from_snap = {r[0] for r in incoming}
        for (file_id,) in kept:
            src = (snap if file_id in from_snap else current) / file_id[:2] / file_id
            if src.exists():
                _link_or_copy(src, out / file_id[:2] / file_id)

    async def _restore(self, job: dict[str, Any], udid: str, backup_root: Path, source: str, password: str,
                       remove: bool) -> None:
        entry = await self.devices.get(udid)
        if entry.connection != "USB":
            raise BackupError(_("To restore, connect the iPhone with the USB cable."))
        async with self._service(entry.lockdown, job) as svc:
            cb = self._progress(job, "restore")
            await svc.restore(backup_directory=backup_root, system=True, reboot=True, copy=False, settings=True,
                              remove=remove, password=password, source=source, progress_callback=cb)
        job["hint"] = None
        job["rebooting"] = True

    # ---------- encryption ----------

    async def set_encryption(self, udid: str, enable: bool, password: str, old_password: str = "") -> None:
        entry = await self.devices.get(udid)
        root = self.root
        root.mkdir(parents=True, exist_ok=True)
        async with Mobilebackup2Service(entry.lockdown) as svc:
            if enable:
                await svc.change_password(root, old=old_password, new=password)
            else:
                await svc.change_password(root, old=password)


def _gb(n: int) -> str:
    return f"{n / 1e9:.1f} GB"


def _date(ts: float) -> str:
    return time.strftime("%Y-%m-%d %H:%M", time.localtime(ts))


def _friendly(e: Exception) -> str:
    text = f"{type(e).__name__}: {e}"
    low = text.lower()
    if "find my" in low or "fmip" in low or "findmy" in low:
        return _("Turn off Find My on the iPhone (Settings › your name › Find My) and try again.")
    if "password" in low:
        return _("Wrong backup password.")
    return text
