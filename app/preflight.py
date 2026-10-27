"""Checks run before an IPSW update. Every one must pass: anything that can't be confirmed blocks the
update instead of letting it through (wrong IPSW, wrong iPhone, unsigned build, low battery, no space)."""

import asyncio
import logging
import shutil
import tempfile
import zipfile
from typing import Any, Optional

from pymobiledevice3.lockdown import create_using_usbmux

from .device import DeviceEntry
from .firmware import _version_tuple, inspect_ipsw
from .i18n import _

log = logging.getLogger(__name__)

MIN_BATTERY = 50  # %, or MIN_BATTERY_CHARGING while charging
MIN_BATTERY_CHARGING = 30
MIN_DEVICE_FREE = 3 * 1000**3  # the update rewrites the system volume and keeps a copy until it boots
MIN_PC_FREE = 2 * 1000**3  # temporary files while personalizing the firmware
READ_TIMEOUT = 6


def _check(id: str, ok: Optional[bool], label: str, detail: str = "") -> dict[str, Any]:
    # ok=None means "couldn't verify", which blocks just like a failure
    return {"id": id, "ok": ok is True, "unknown": ok is None, "label": label, "detail": detail}


async def _fresh_values(udid: str) -> dict[str, Any]:
    """Battery and free space read on a separate lockdown connection, so a domain the iPhone doesn't
    answer (it happens while it's locked) can't stall the app's own session."""
    values: dict[str, Any] = {}
    ld = await asyncio.wait_for(create_using_usbmux(serial=udid, connection_type="USB"), 20)
    try:
        for key, domain in (("battery", "com.apple.mobile.battery"), ("disk", "com.apple.disk_usage")):
            try:
                values[key] = await asyncio.wait_for(ld.get_value(domain=domain), READ_TIMEOUT)
            except Exception as e:
                log.warning("preflight: %s unavailable (%s)", domain, type(e).__name__)
                break  # the session may be out of sync now
        values["all"] = dict(ld.all_values)
    finally:
        try:
            await asyncio.wait_for(ld.close(), 3)
        except BaseException:
            pass
    return values


async def run(entry: DeviceEntry, ipsw_path: str) -> dict[str, Any]:
    checks: list[dict[str, Any]] = []
    info = entry.info
    product_type = info.get("ProductType", "")
    hardware_model = info.get("HardwareModel", "")
    current = info.get("ProductVersion", "")

    checks.append(_check("usb", entry.connection == "USB", _("iPhone connected with a USB cable"),
                         "" if entry.connection == "USB" else _("The update doesn't work over Wi-Fi")))

    # ---- the IPSW ----
    ipsw: Optional[dict[str, Any]] = None
    try:
        ipsw = await asyncio.to_thread(inspect_ipsw, ipsw_path, product_type, hardware_model)
    except Exception as e:
        checks.append(_check("ipsw", False, _("IPSW file readable"), f"{type(e).__name__}: {e}"))
    if ipsw:
        checks.append(_check("model", ipsw["compatible"],
                             _("IPSW made for this model ({product_type} / {hardware_model})",
                               product_type=product_type, hardware_model=hardware_model),
                             "" if ipsw["compatible"] else _("The IPSW is for: {products}", products=", ".join(ipsw["products"]))))
        checks.append(_check("update_identity", ipsw["has_update_identity"], _("IPSW has the update mode (keeps the data)"),
                             "" if ipsw["has_update_identity"] else _("This IPSW only allows a restore that erases everything")))
        newer = _version_tuple(ipsw["version"]) >= _version_tuple(current)
        checks.append(_check("version", newer,
                             _("Version {version} same as or newer than the current one ({current})",
                               version=ipsw["version"], current=current),
                             "" if newer else _("Going back to an earlier version requires erasing the iPhone")))
        checks.append(_check("signed", ipsw["signed"],
                             _("Apple still signs iOS {version} ({build})", version=ipsw["version"], build=ipsw["build"]),
                             _("Couldn't check with Apple/ipsw.me") if ipsw["signed"] is None else
                             "" if ipsw["signed"] else _("Apple no longer signs this version: the install would fail")))
        if ipsw.get("expected_size"):
            same = ipsw["size"] == ipsw["expected_size"]
            checks.append(_check("size", same, _("IPSW file complete (size matches Apple's)"),
                                 "" if same else _("It has {size} bytes, it should have {expected}: download it again",
                                                   size=ipsw["size"], expected=ipsw["expected_size"])))
        try:
            ok = await asyncio.to_thread(_zip_ok, ipsw_path)
            checks.append(_check("zip", ok, _("IPSW intact (archive index readable)")))
        except Exception as e:
            checks.append(_check("zip", False, _("IPSW intact (archive index readable)"), str(e)))

    # ---- the iPhone, read fresh ----
    values: dict[str, Any] = {}
    try:
        values = await _fresh_values(entry.udid)
    except Exception as e:
        checks.append(_check("connect", False, _("Communication with the iPhone"), f"{type(e).__name__}: {e}"))
    if values:
        same_device = values["all"].get("UniqueChipID") == info.get("UniqueChipID")
        checks.append(_check("same_device", same_device, _("It is the same iPhone that was selected (ECID matches)")))
        battery = values.get("battery") or {}
        level, charging = battery.get("BatteryCurrentCapacity"), bool(battery.get("BatteryIsCharging"))
        if level is None:
            checks.append(_check("battery", None, _("Enough battery"),
                                 _("The iPhone didn't report its battery: unlock it and check again")))
        else:
            need = MIN_BATTERY_CHARGING if charging else MIN_BATTERY
            label = (_("Enough battery ({level}%, charging)", level=level) if charging
                     else _("Enough battery ({level}%)", level=level))
            detail = "" if level >= need else (_("Needs at least {need}% while charging", need=need) if charging
                                               else _("Needs at least {need}%", need=need))
            checks.append(_check("battery", level >= need, label, detail))
        free = (values.get("disk") or {}).get("AmountDataAvailable")
        if free is None:
            checks.append(_check("device_space", None, _("Free space on the iPhone"),
                                 _("The iPhone didn't report its free space: unlock it and check again")))
        else:
            checks.append(_check("device_space", free >= MIN_DEVICE_FREE,
                                 _("Free space on the iPhone ({gb} GB)", gb=f"{free / 1e9:.1f}"),
                                 "" if free >= MIN_DEVICE_FREE else _("Free up at least {gb} GB", gb=f"{MIN_DEVICE_FREE / 1e9:.0f}")))

    pc_free = shutil.disk_usage(tempfile.gettempdir()).free
    checks.append(_check("pc_space", pc_free >= MIN_PC_FREE, _("Free space on the computer ({gb} GB)", gb=f"{pc_free / 1e9:.1f}"),
                         "" if pc_free >= MIN_PC_FREE else
                         _("Free up at least {gb} GB on the Windows disk", gb=f"{MIN_PC_FREE / 1e9:.0f}")))

    return {"ok": all(c["ok"] for c in checks), "checks": checks, "ipsw": ipsw}


def _zip_ok(path: str) -> bool:
    with zipfile.ZipFile(path) as z:
        names = set(z.namelist())
        return "BuildManifest.plist" in names and "Restore.plist" in names
