"""Deleting photos the way the iPhone allows it: over PTP (the camera protocol), through Windows.

Removing a /DCIM file over AFC leaves the Photos library pointing at a missing file. Over PTP, the
iPhone's own photo service deletes the asset — what "Delete after import" in the Windows Photos app
and deleting in Explorer under "Apple iPhone > Internal Storage" do. With iCloud Photos on, the
deletion syncs like any deletion made on the iPhone.

Windows exposes the camera roll as Shell items (Windows Portable Devices): month folders such as
"202202_a" holding IMG_2655.JPG... Items are matched to the app's /DCIM items by file name, using the
month folder to break ties, and deleted with IFileOperation (no Explorer confirmation dialog: the app
asks first). Windows only; the iPhone must be unlocked and trust this PC.
"""

import logging
import posixpath
import time
from datetime import datetime
from typing import Any, Optional

from .i18n import _

log = logging.getLogger(__name__)

THIS_PC = "::{20D04FE0-3AEA-1069-A2D8-08002B30309D}"
ENUM = 0x20 | 0x40  # SHCONTF_FOLDERS | SHCONTF_NONFOLDERS


class PtpError(Exception):
    pass


def _com():
    import pythoncom  # pywin32, Windows only

    pythoncom.CoInitialize()
    return pythoncom


def _device_storage() -> tuple[Any, list]:
    """The "Internal Storage" IShellFolder of the one connected iPhone, and its absolute PIDL.

    Items are addressed by PIDL: the text paths Shell.Application gives for portable-device items
    aren't parsing names IFileOperation accepts ("The parameter is incorrect"), and walking with
    IShellFolder is also far faster (one month folder in ~1 s instead of minutes)."""
    from win32com.shell import shell, shellcon

    desktop = shell.SHGetDesktopFolder()
    _eaten, pc_pidl, _attrs = desktop.ParseDisplayName(0, None, THIS_PC)
    pc = desktop.BindToObject(pc_pidl, None, shell.IID_IShellFolder)
    phones = [p for p in pc.EnumObjects(0, ENUM) if "vid_05ac" in pc.GetDisplayNameOf(p, shellcon.SHGDN_FORPARSING).lower()]
    if not phones:
        raise PtpError(_("Windows doesn't see the iPhone as a camera. Unlock it and confirm that it trusts this computer."))
    if len(phones) > 1:
        raise PtpError(_("More than one iPhone is connected: leave only the one you want to change."))
    phone = pc.BindToObject(phones[0], None, shell.IID_IShellFolder)
    storages = list(phone.EnumObjects(0, ENUM) or [])
    if not storages:
        raise PtpError(_("The iPhone didn't show its photos to Windows: unlock it and try again."))
    return phone.BindToObject(storages[0], None, shell.IID_IShellFolder), pc_pidl + phones[0] + storages[0]


def index(months: Optional[set[str]] = None) -> dict[str, list[tuple[str, list]]]:
    """FILE NAME (upper) -> [(month folder, absolute PIDL)], for folders of these YYYYMM months
    only (or all)."""
    from win32com.shell import shell, shellcon

    _com()
    storage, storage_pidl = _device_storage()
    result: dict[str, list[tuple[str, list]]] = {}
    for m in storage.EnumObjects(0, ENUM) or []:
        month = storage.GetDisplayNameOf(m, shellcon.SHGDN_NORMAL)
        if months is not None and month[:6] not in months:
            continue
        folder = storage.BindToObject(m, None, shell.IID_IShellFolder)
        for f in folder.EnumObjects(0, ENUM) or []:
            name = folder.GetDisplayNameOf(f, shellcon.SHGDN_NORMAL)
            result.setdefault(name.upper(), []).append((month, storage_pidl + m + f))
    return result


def _months_around(when: float) -> set[str]:
    """The item's month and its neighbours (file dates and the iPhone's grouping can differ a bit)."""
    if not when:
        return set()
    d = datetime.fromtimestamp(when)
    result = set()
    for delta in (-1, 0, 1):
        m = d.month + delta
        y = d.year + (m - 1) // 12
        result.add(f"{y}{(m - 1) % 12 + 1:02d}")
    return result


def _pick(candidates: list[tuple[str, list]], when: float) -> Optional[list]:
    if len(candidates) == 1:
        return candidates[0][1]
    month = datetime.fromtimestamp(when).strftime("%Y%m") if when else None
    same = [p for f, p in candidates if month and f.startswith(month)]
    return same[0] if len(same) == 1 else None


def delete(items: list[dict[str, Any]]) -> dict[str, Any]:
    """items: [{path, name, time, extra: [names of companion files, e.g. a Live Photo's .MOV]}].
    Returns {deleted: [paths], not_found: [paths], ambiguous: [paths]}."""
    pythoncom = _com()
    from win32com.shell import shell, shellcon

    months: set[str] = set()
    for it in items:
        months |= _months_around(it.get("time") or 0)
    # an item without a date makes every folder a candidate
    idx = index(None if any(not it.get("time") for it in items) else months)
    targets: list[list] = []
    deleted, not_found, ambiguous = [], [], []
    for it in items:
        names = [it["name"], *it.get("extra", [])]
        found = []
        for name in names:
            cands = idx.get(name.upper(), [])
            pick = _pick(cands, it.get("time") or 0)
            if pick is None:
                found = None if cands else found
                break
            found.append(pick)
        if found is None:
            ambiguous.append(it["path"])
        elif len(found) != len(names):
            not_found.append(it["path"])
        else:
            targets += found
            deleted.append(it["path"])
    if targets:
        try:
            op = pythoncom.CoCreateInstance(shell.CLSID_FileOperation, None, pythoncom.CLSCTX_ALL, shell.IID_IFileOperation)
            op.SetOperationFlags(shellcon.FOF_NOCONFIRMATION | shellcon.FOF_SILENT | shellcon.FOF_NOERRORUI)
            for pidl in targets:
                op.DeleteItem(shell.SHCreateItemFromIDList(pidl, shell.IID_IShellItem), None)
            op.PerformOperations()
        except pythoncom.com_error as e:
            raise PtpError(_("Windows refused the deletion ({error}). Nothing or only part was deleted: check on the iPhone.",
                            error=e.strerror))
        if op.GetAnyOperationsAborted():
            raise PtpError(_("Windows interrupted the deletion: check on the iPhone which photos were deleted."))
    return {"deleted": deleted, "not_found": not_found, "ambiguous": ambiguous}


def companion_names(item: dict[str, Any]) -> list[str]:
    """Live Photo: the .MOV goes with the photo."""
    return [posixpath.basename(item["live"])] if item.get("live") else []


if __name__ == "__main__":  # read-only check: python -m app.ptp
    t = time.time()
    idx = index()
    dup = {k: v for k, v in idx.items() if len(v) > 1}
    print(f"{sum(len(v) for v in idx.values())} files, {len(idx)} names, {len(dup)} repeated names, {time.time() - t:.1f}s")
    for k, v in list(dup.items())[:5]:
        print(" ", k, [f for f, _pidl in v])
