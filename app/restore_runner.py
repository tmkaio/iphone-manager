"""`python -m app.restore_runner <pymobiledevice3 args>`: pymobiledevice3's CLI with the Recovery-mode
USB transport on MobileDevice.dll on Windows (see irecv_md.py), and on the bundled libusb elsewhere.

The server passes the UI's language in IPHONE_MANAGER_LANG: the lines this process prints (and the
errors it raises) go straight into the update log the UI shows."""

import os
import sys

from . import i18n, system
from .irecv_md import install


def main() -> None:
    # set before anything runs, so asyncio.run and the MobileDevice calls (run_in_lang) inherit it
    i18n.current.set(os.environ.get("IPHONE_MANAGER_LANG") or "en")
    if not install():
        system.use_bundled_libusb()
    from pymobiledevice3.__main__ import main as cli_main

    sys.argv = ["pymobiledevice3", *sys.argv[1:]]
    sys.exit(cli_main())


if __name__ == "__main__":
    main()
