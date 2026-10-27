"""Device discovery and connection management (USB + Wi-Fi)."""

import asyncio
import logging
from dataclasses import dataclass, field
from typing import Any, Optional

from pymobiledevice3 import usbmux
from pymobiledevice3.lockdown import LockdownClient, create_using_usbmux, get_mobdev2_lockdowns
from pymobiledevice3.services.afc import AfcService

from .i18n import _

log = logging.getLogger(__name__)

WIRELESS_DOMAIN = "com.apple.mobile.wireless_lockdown"
# covers the 30 s "Trust this computer" wait plus the value reads
LOCKDOWN_TIMEOUT = 45
OPTIONAL_VALUE_TIMEOUT = 4


@dataclass
class DeviceEntry:
    udid: str
    connection: str  # "USB" | "WiFi"
    devid: Optional[int] = None  # usbmux id: changes every time the cable is replugged
    lockdown: Optional[LockdownClient] = None
    afc: Optional[AfcService] = None
    info: dict[str, Any] = field(default_factory=dict)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)


class DeviceManager:
    def __init__(self) -> None:
        self.devices: dict[str, DeviceEntry] = {}
        # Wi-Fi devices found via Bonjour (mobdev2) when usbmuxd does not expose them
        self._wifi_lockdowns: dict[str, LockdownClient] = {}
        self._scan_lock = asyncio.Lock()
        self.usbmux_error: Optional[str] = None

    async def list_devices(self, scan_wifi: bool = False) -> list[dict[str, Any]]:
        async with self._scan_lock:
            found: dict[str, str] = {}
            devids: dict[str, int] = {}
            try:
                for mux in await usbmux.list_devices():
                    conn = "USB" if mux.is_usb else "WiFi"
                    # Prefer USB when a device shows up both ways
                    if found.get(mux.serial) != "USB":
                        found[mux.serial] = conn
                        devids[mux.serial] = mux.devid
            except Exception as e:  # usbmuxd not running
                log.warning("usbmux unavailable: %s", e)
                self.usbmux_error = _("The Apple Mobile Device service is still starting, wait a few seconds.")
            else:
                self.usbmux_error = None

            if scan_wifi:
                try:
                    async for _ip, ld in get_mobdev2_lockdowns(only_paired=True, timeout=3):
                        if ld.udid and ld.udid not in found:
                            self._wifi_lockdowns[ld.udid] = ld
                            found[ld.udid] = "WiFi"
                except Exception as e:
                    log.warning("Wi-Fi discovery failed: %s", e)
            for udid in self._wifi_lockdowns:
                found.setdefault(udid, "WiFi")

            # Drop devices that went away
            for udid in list(self.devices):
                if udid not in found:
                    await self._close(self.devices.pop(udid))

            result = []
            for udid, conn in found.items():
                entry = self.devices.get(udid)
                # a replug between two polls keeps the udid but kills the old connections
                if entry is None or entry.connection != conn or entry.devid != devids.get(udid):
                    if entry:
                        await self._close(entry)
                    entry = DeviceEntry(udid=udid, connection=conn, devid=devids.get(udid))
                    self.devices[udid] = entry
                try:
                    await asyncio.wait_for(self._ensure_lockdown(entry), LOCKDOWN_TIMEOUT)
                    result.append(self._summary(entry))
                except Exception as e:
                    # don't keep a half-open connection around: retry from scratch on the next poll
                    await self._close(self.devices.pop(udid))
                    if isinstance(e, asyncio.TimeoutError):
                        e = TimeoutError(_("the iPhone didn't answer; unlock it or reconnect the cable"))
                    result.append({"udid": udid, "connection": conn, "error": _friendly_error(e)})
            return result

    async def _ensure_lockdown(self, entry: DeviceEntry) -> LockdownClient:
        if entry.lockdown is not None:
            return entry.lockdown
        ld = await self._connect(entry)
        entry.info = dict(ld.all_values)
        entry.info.update(disk={}, battery={}, wifi_enabled=None)
        # Optional extras. A locked iPhone (e.g. right after a reboot) may never answer some domains:
        # give up on the rest, and reconnect so the late reply doesn't desync the session.
        reads = (
            ("disk", "com.apple.disk_usage", None),
            ("battery", "com.apple.mobile.battery", None),
            ("wifi_enabled", WIRELESS_DOMAIN, "EnableWifiConnections"),
        )
        for name, domain, key in reads:
            try:
                value = await asyncio.wait_for(ld.get_value(domain=domain, key=key), OPTIONAL_VALUE_TIMEOUT)
            except asyncio.TimeoutError:
                log.warning("%s: no answer for %s, skipping the optional values", entry.udid, domain)
                await self._close_quietly(ld)
                entry.lockdown = None
                ld = await self._connect(entry)
                break
            except Exception:
                continue
            entry.info[name] = bool(value) if name == "wifi_enabled" else value
        return ld

    async def _connect(self, entry: DeviceEntry) -> LockdownClient:
        if entry.connection == "WiFi" and entry.udid in self._wifi_lockdowns:
            ld = self._wifi_lockdowns[entry.udid]
        else:
            conn_type = "USB" if entry.connection == "USB" else "Network"
            ld = await create_using_usbmux(serial=entry.udid, connection_type=conn_type, pair_timeout=30)
        entry.lockdown = ld
        return ld

    def _summary(self, entry: DeviceEntry) -> dict[str, Any]:
        i = entry.info
        disk = i.get("disk") or {}
        battery = i.get("battery") or {}
        return {
            "udid": entry.udid,
            "connection": entry.connection,
            "name": i.get("DeviceName"),
            "product_type": i.get("ProductType"),
            "model": i.get("ModelNumber"),
            "hardware_model": i.get("HardwareModel"),
            "ios": i.get("ProductVersion"),
            "build": i.get("BuildVersion"),
            "serial": i.get("SerialNumber"),
            "ecid": i.get("UniqueChipID"),
            "color": i.get("DeviceColor"),
            "disk_total": disk.get("TotalDiskCapacity"),
            "disk_free": disk.get("AmountDataAvailable"),
            # everything outside the data volume: iOS itself
            "system_size": (disk["TotalDiskCapacity"] - disk["TotalDataCapacity"])
            if disk.get("TotalDiskCapacity") and disk.get("TotalDataCapacity") else None,
            "battery": battery.get("BatteryCurrentCapacity"),
            "charging": battery.get("BatteryIsCharging"),
            "wifi_enabled": i.get("wifi_enabled"),
            "phone": i.get("PhoneNumber"),
            "region": i.get("RegionInfo"),
            "carrier": _carrier(i.get("CarrierBundleInfoArray")),
            "activation": i.get("ActivationState"),
            "imei": i.get("InternationalMobileEquipmentIdentity"),
            "imei2": i.get("InternationalMobileEquipmentIdentity2"),
            "wifi_mac": i.get("WiFiAddress"),
            "bt_mac": i.get("BluetoothAddress"),
            "baseband": i.get("BasebandVersion"),
            "iboot": i.get("FirmwareVersion"),
            "passcode": i.get("PasswordProtected"),
            "timezone": i.get("TimeZone"),
        }

    async def get(self, udid: str) -> DeviceEntry:
        entry = self.devices.get(udid)
        if entry is None:
            await self.list_devices()
            entry = self.devices.get(udid)
            if entry is None:
                raise KeyError(udid)
        await self._ensure_lockdown(entry)
        return entry

    async def afc(self, udid: str) -> AfcService:
        """Shared AFC connection for the device (Media partition: DCIM, PhotoData...)."""
        entry = await self.get(udid)
        async with entry.lock:
            if entry.afc is None:
                afc = AfcService(entry.lockdown)
                await afc.__aenter__()
                entry.afc = afc
            return entry.afc

    async def new_afc(self, udid: str) -> AfcService:
        """Dedicated AFC connection (caller must aclose)."""
        entry = await self.get(udid)
        afc = AfcService(entry.lockdown)
        await afc.__aenter__()
        return afc

    async def reset_afc(self, udid: str) -> None:
        entry = self.devices.get(udid)
        if entry and entry.afc:
            try:
                await entry.afc.aclose()
            except Exception:
                pass
            entry.afc = None

    async def set_wifi(self, udid: str, enabled: bool) -> None:
        entry = await self.get(udid)
        await entry.lockdown.set_value(enabled, domain=WIRELESS_DOMAIN, key="EnableWifiConnections")
        entry.info["wifi_enabled"] = enabled

    async def _close(self, entry: DeviceEntry) -> None:
        for obj in (entry.afc, entry.lockdown):
            if obj is not None:
                await self._close_quietly(obj)
        self._wifi_lockdowns.pop(entry.udid, None)

    @staticmethod
    async def _close_quietly(obj: Any) -> None:
        try:
            # closing a connection whose cable was pulled can block forever
            await asyncio.wait_for(obj.aclose() if hasattr(obj, "aclose") else obj.close(), 3)
        except BaseException as e:  # incl. CancelledError raised by pymobiledevice3's own close timeout
            if isinstance(e, (KeyboardInterrupt, SystemExit)):
                raise

    async def close_all(self) -> None:
        for entry in list(self.devices.values()):
            await self._close(entry)
        self.devices.clear()


def _carrier(bundles: Any) -> Optional[str]:
    """CarrierBundleInfoArray -> "Claro BR (72.0.1)" for the first SIM's carrier bundle."""
    if not bundles:
        return None
    b = bundles[0]
    name = (b.get("CFBundleIdentifier") or "").removeprefix("com.apple.").replace("_", " ")
    if " " in name:
        carrier, country = name.rsplit(" ", 1)
        name = f"{carrier} {country.upper()}"
    return f"{name} ({b['CFBundleVersion']})" if b.get("CFBundleVersion") else name or None


def _friendly_error(e: Exception) -> str:
    name = type(e).__name__
    if "PairingDialog" in name or "NotPaired" in name or "PasswordRequired" in name:
        return _("Unlock the iPhone and tap 'Trust This Computer'.")
    return f"{name}: {e}"
