"""`python -m app.mount_ddi <udid>`: download (if needed) and mount the Developer Disk Image.

Runs apart from the server: inside it, the ~16 MB transfer over the userspace tunnel stalled (the
iPhone took longer than pymobiledevice3's 30 s to reopen its flow-control window). Alone, with a
longer window timeout, it mounts in ~25 s.
"""

import asyncio
import sys

import pymobiledevice3.remote.remotexpc as remotexpc
from pymobiledevice3.exceptions import AlreadyMountedError
from pymobiledevice3.remote.userspace_tunnel import UserspaceRsdTunnel
from pymobiledevice3.services.mobile_image_mounter import auto_mount

remotexpc.FILE_TRANSFER_WINDOW_TIMEOUT = 180


async def main(udid: str) -> None:
    async with UserspaceRsdTunnel(serial=udid) as rsd:
        try:
            await auto_mount(rsd)
        except AlreadyMountedError:
            pass
    print("developer disk image mounted", flush=True)


if __name__ == "__main__":
    try:
        asyncio.run(main(sys.argv[1]))
    except Exception as e:
        print(f"{type(e).__name__}: {e}", flush=True)
        sys.exit(1)
