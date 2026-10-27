"""Signed-IPSW lookup, IPSW download/validation and in-place (no-erase) update."""

import asyncio
import hashlib
import logging
import os
import plistlib
import re
import sys
import time
import uuid
import zipfile
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from . import i18n, system
from .i18n import _
from .tasks import spawn

log = logging.getLogger(__name__)

IPSW_API = "https://api.ipsw.me/v4/device/{identifier}?type=ipsw"
IPSW_DIR = system.DATA_DIR / "ipsw"
LOG_DIR = IPSW_DIR.parent / "logs"
ANSI = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")


def _version_tuple(v: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", v or "0"))


def fetch_firmwares(product_type: str) -> list[dict[str, Any]]:
    r = requests.get(IPSW_API.format(identifier=product_type), timeout=20)
    r.raise_for_status()
    return r.json().get("firmwares", [])


def signed_firmwares(product_type: str, current_version: str) -> list[dict[str, Any]]:
    cur = _version_tuple(current_version)
    result = []
    for fw in fetch_firmwares(product_type):
        if not fw.get("signed"):
            continue
        local = IPSW_DIR / Path(fw["url"]).name
        result.append(
            {
                "version": fw["version"],
                "build": fw["buildid"],
                "url": fw["url"],
                "size": fw.get("filesize"),
                "sha1": fw.get("sha1sum"),
                "released": fw.get("releasedate"),
                # An in-place update can only go to the same or a newer version
                "can_update": _version_tuple(fw["version"]) >= cur,
                "is_current": _version_tuple(fw["version"]) == cur,
                "downloaded": local.exists() and (not fw.get("filesize") or local.stat().st_size == fw["filesize"]),
                "local_path": str(local),
            }
        )
    result.sort(key=lambda f: _version_tuple(f["version"]), reverse=True)
    return result


def inspect_ipsw(path: str, product_type: str, hardware_model: str) -> dict[str, Any]:
    """Read BuildManifest.plist to check the IPSW matches the device, then ask ipsw.me if it is signed."""
    p = Path(path)
    if not p.is_file():
        raise ValueError(_("File not found"))
    with zipfile.ZipFile(p) as z:
        manifest = plistlib.loads(z.read("BuildManifest.plist"))
    version = manifest.get("ProductVersion")
    build = manifest.get("ProductBuildVersion")
    products = manifest.get("SupportedProductTypes", [])
    identities = [bi.get("Info", {}) for bi in manifest.get("BuildIdentities", [])]
    boards = {(i.get("DeviceClass") or "").lower() for i in identities}
    # the update (keep data) identity must exist for this iPhone's board, not just any board in the file
    has_update_identity = any(
        i.get("RestoreBehavior") == "Update" and (not hardware_model or (i.get("DeviceClass") or "").lower() == hardware_model.lower())
        for i in identities
    )
    compatible = product_type in products and (not hardware_model or hardware_model.lower() in boards)
    signed = None
    expected_size = None
    try:
        for fw in fetch_firmwares(product_type):
            if fw["buildid"] == build:
                signed = bool(fw.get("signed"))
                expected_size = fw.get("filesize")
        signed = bool(signed)  # a build ipsw.me doesn't list for this model is not signed for it
    except Exception as e:
        log.warning("signing check failed: %s", e)
    return {
        "path": str(p),
        "version": version,
        "build": build,
        "products": products,
        "compatible": compatible,
        "has_update_identity": has_update_identity,
        "signed": signed,
        "size": p.stat().st_size,
        "expected_size": expected_size,
    }


class FirmwareManager:
    def __init__(self) -> None:
        self.downloads: dict[str, dict[str, Any]] = {}
        self.update_job: Optional[dict[str, Any]] = None
        self._proc: Optional[asyncio.subprocess.Process] = None
        self.on_update_finished: Optional[Callable[[], None]] = None

    # ---------- IPSW download ----------

    def start_download(self, url: str, size: Optional[int], sha1: Optional[str]) -> dict[str, Any]:
        for job in self.downloads.values():
            if job["url"] == url and job["status"] == "running":
                return job
        job = {
            "id": uuid.uuid4().hex[:8],
            "url": url,
            "path": str(IPSW_DIR / Path(url).name),
            "status": "running",
            "bytes_total": size or 0,
            "bytes_done": 0,
            "speed": 0,
            "cancel": False,
            "error": None,
        }
        self.downloads[job["id"]] = job
        # run_in_executor doesn't carry the request's language to the thread (asyncio.to_thread would)
        asyncio.get_running_loop().run_in_executor(None, i18n.run_in_lang(self._download), job, sha1)
        return job

    def _download(self, job: dict[str, Any], sha1: Optional[str]) -> None:
        target = Path(job["path"])
        part = target.with_suffix(".ipsw.part")
        try:
            IPSW_DIR.mkdir(parents=True, exist_ok=True)
            start = part.stat().st_size if part.exists() else 0
            headers = {"Range": f"bytes={start}-"} if start else {}
            with requests.get(job["url"], headers=headers, stream=True, timeout=30) as r:
                r.raise_for_status()
                if start and r.status_code != 206:
                    start = 0  # server ignored Range: restart
                total = start + int(r.headers.get("Content-Length", 0))
                job["bytes_total"] = total or job["bytes_total"]
                job["bytes_done"] = start
                t0, b0 = time.time(), start
                with open(part, "ab" if start else "wb") as out:
                    for chunk in r.iter_content(1024 * 1024):
                        if job["cancel"]:
                            job["status"] = "cancelled"
                            return
                        out.write(chunk)
                        job["bytes_done"] += len(chunk)
                        dt = time.time() - t0
                        if dt >= 1:
                            job["speed"] = (job["bytes_done"] - b0) / dt
                            t0, b0 = time.time(), job["bytes_done"]
            if sha1:
                job["status"] = "verifying"
                h = hashlib.sha1()
                with open(part, "rb") as f:
                    for block in iter(lambda: f.read(8 * 1024 * 1024), b""):
                        h.update(block)
                if h.hexdigest().lower() != sha1.lower():
                    part.unlink(missing_ok=True)
                    raise ValueError(_("SHA1 mismatch — the file is corrupted, download it again"))
            part.replace(target)
            job["status"] = "done"
        except Exception as e:
            job["status"] = "error"
            job["error"] = str(e)

    # ---------- update ----------

    def update_state(self) -> Optional[dict[str, Any]]:
        return self.update_job

    async def start_update(self, ipsw_path: str, ecid: Optional[int]) -> dict[str, Any]:
        if self.update_job and self.update_job["status"] == "running":
            raise RuntimeError(_("An update is already in progress"))
        job = {"status": "running", "ipsw": ipsw_path, "log": [], "started": time.time(), "progress": None}
        self.update_job = job
        spawn(self._run_update(job, ipsw_path, ecid))
        return job

    async def _run_update(self, job: dict[str, Any], ipsw_path: str, ecid: Optional[int]) -> None:
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"  # _add_line decodes UTF-8
        env["NO_COLOR"] = "1"
        env["COLUMNS"] = "120"
        env["IPHONE_MANAGER_LANG"] = i18n.current.get()  # restore_runner prints its own lines in this language
        try:  # pyusb needs libusb-1.0.dll on PATH to talk to the device in Recovery mode
            import libusb

            env["PATH"] = str(Path(libusb.dll._name).parent) + os.pathsep + env.get("PATH", "")
        except Exception:
            pass
        # --no-erase: keep all user data (update, not restore). restore_runner = pymobiledevice3 with
        # the Recovery-mode USB on MobileDevice.dll, since libusb can't open Apple's driver
        args = [sys.executable, "-m", "app.restore_runner", "-vv", "--no-color", "restore", "update", "-i", ipsw_path, "--no-erase"]
        if ecid:
            args += ["--ecid", str(ecid)]
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        log_path = LOG_DIR / time.strftime("update-%Y%m%d-%H%M%S.log")
        job["log_file"] = str(log_path)
        job["_log_fh"] = open(log_path, "a", encoding="utf-8")
        self._add_line(job, ("$ " + " ".join(args[1:])).encode())
        try:
            self._proc = await asyncio.create_subprocess_exec(
                *args, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT, env=env,
                cwd=str(Path(__file__).resolve().parent.parent),
            )
            buf = b""
            assert self._proc.stdout
            while True:
                chunk = await self._proc.stdout.read(4096)
                if not chunk:
                    break
                buf += chunk
                parts = re.split(rb"[\r\n]", buf)
                buf = parts.pop()
                for raw in parts:
                    self._add_line(job, raw)
            if buf:
                self._add_line(job, buf)
            code = await self._proc.wait()
            job["status"] = "done" if code == 0 else "error"
            job["exit_code"] = code
        except Exception as e:
            job["status"] = "error"
            self._add_line(job, _("ERROR: {error}", error=e).encode())
        finally:
            job["finished"] = time.time()
            job.pop("_log_fh").close()
            self._proc = None
            if self.on_update_finished:
                self.on_update_finished()

    def _add_line(self, job: dict[str, Any], raw: bytes) -> None:
        line = ANSI.sub("", raw.decode("utf-8", "replace")).rstrip()
        if not line.strip():
            return
        job["last_output"] = time.time()
        if fh := job.get("_log_fh"):
            fh.write(time.strftime("%H:%M:%S ") + line + "\n")
            fh.flush()
        m = re.search(r"(\d{1,3})%", line)
        if m:
            job["progress"] = {"percent": int(m.group(1)), "text": line.strip()[:160]}
            # progress bars repaint constantly; keep only the latest one in the log
            if job["log"] and job["log"][-1].startswith("⟳ "):
                job["log"][-1] = "⟳ " + line.strip()
                return
            line = "⟳ " + line.strip()
        job["log"].append(line)
        if len(job["log"]) > 3000:
            del job["log"][:1000]

    async def cancel_update(self) -> None:
        if self._proc and self._proc.returncode is None:
            self._proc.terminate()
