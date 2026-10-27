# Third-party notices

iPhone Manager is licensed under the GNU GPL v3.0 or later (see `LICENSE`). The Windows installer
bundles the official CPython embeddable package (PSF License), Tcl/Tk (for tkinter) and the Python
packages below. The installer ships `THIRD-PARTY-NOTICES.txt` with the full license text of each one,
generated at build time by `installer/gen_notices.py` from the installed packages' metadata.

The `av` (PyAV) wheel bundles FFmpeg libraries under the LGPL-2.1-or-later.
Apple's Mobile Device Support and the ffmpeg/ffprobe executables are **not** bundled. When their
components are selected, setup downloads them from their official sources at install time:

- ffmpeg: the "essentials" Windows build by [gyan.dev](https://www.gyan.dev/ffmpeg/builds/)
  ([GPL-3.0](https://www.ffmpeg.org/legal.html); source at [ffmpeg.org](https://ffmpeg.org/download.html)),
  checked against a pinned SHA-256.
- Apple Devices, from the Microsoft Store through `winget`, under Apple's own license terms.

| Package | Version | License |
|---|---|---|
| [aiofiles](https://github.com/Tinche/aiofiles) | 25.1.0 | Apache-2.0 (Apache Software License) |
| [annotated-doc](https://github.com/fastapi/annotated-doc) | 0.0.5 | MIT |
| [annotated-types](https://github.com/annotated-types/annotated-types) | 0.8.0 | MIT |
| [ansilogs](https://github.com/doronz88/ansilogs) | 1.1.0 | MIT License |
| [anyio](https://github.com/agronholm/anyio) | 4.15.1 | MIT |
| [arrow](https://github.com/arrow-py/arrow) | 1.4.0 | Apache Software License |
| [ASGIMiddlewareStaticFile](https://github.com/rexzhang/asgi-middleware-static-file) | 0.7.0 | MIT License (from LICENSE) |
| [asgiref](https://github.com/django/asgiref/) | 3.13.0 | BSD-3-Clause |
| [ASGIWebDAV](https://github.com/rexzhang/asgi-webdav) | 2.1.0 | MIT License (from LICENSE) |
| [asn1](https://github.com/andrivet/python-asn1) | 2.8.0 | BSD (MIT License) |
| [asttokens](https://github.com/gristlabs/asttokens) | 3.0.2 | Apache 2.0 |
| [av](https://github.com/PyAV-Org/PyAV) | 19.0.2 | BSD-3-Clause |
| [backports.zstd](https://github.com/rogdham/backports.zstd) | 1.8.0 | PSF-2.0 |
| [bpylist2](https://github.com/parabolala/bpylist2) | 4.1.1 | MIT |
| [build](https://build.pypa.io) | 1.6.1 | MIT |
| [bytestruct](https://github.com/doronz88/bytestruct) | 0.2.0 | MIT |
| [certifi](https://github.com/certifi/python-certifi) | 2026.10.23 | MPL-2.0 (Mozilla Public License 2.0 (MPL 2.0)) |
| [cffi](https://github.com/python-cffi/cffi) | 2.1.1 | MIT-0 |
| [chardet](https://github.com/chardet/chardet) | 7.7.0 | 0BSD |
| charset-normalizer | 3.5.2 | MIT |
| [click](https://github.com/pallets/click/) | 8.5.0 | BSD-3-Clause |
| [colorama](https://github.com/tartley/colorama) | 0.4.6 | BSD License |
| [construct](http://construct.readthedocs.org) | 2.10.70 | MIT |
| [cryptography](https://github.com/pyca/cryptography) | 50.0.2 | Apache-2.0 OR BSD-3-Clause |
| [daemonize](https://github.com/thesharp/daemonize) | 2.5.0 | MIT |
| [dataclass-wizard](https://github.com/rnag/dataclass-wizard) | 1.0.0 | Apache Software License |
| [defusedxml](https://github.com/tiran/defusedxml) | 0.7.1 | PSFL (Python Software Foundation License) |
| [developer_disk_image](https://github.com/doronz88/DeveloperDiskImage) | 0.3.0 | GNU General Public License v3 or later (GPLv3+) |
| [enum-compat](https://github.com/jstasiak/enum-compat) | 0.0.3 | MIT |
| [executing](https://github.com/alexmojaki/executing) | 2.3.0 | MIT |
| [fastapi](https://github.com/fastapi/fastapi) | 0.162.0 | MIT |
| [fido2](https://github.com/Yubico/python-fido2) | 2.2.1 | BSD License; Apache Software License; Mozilla Public License 2.0 (MPL 2.0) |
| [h11](https://github.com/python-hyper/h11) | 0.16.0 | MIT |
| [httptools](https://github.com/MagicStack/httptools) | 0.9.0 | MIT |
| [hyperframe](https://github.com/python-hyper/hyperframe/) | 6.1.0 | MIT License |
| [idna](https://github.com/kjd/idna) | 3.20 | BSD-3-Clause |
| [ifaddr](https://github.com/pydron/ifaddr) | 0.2.0 | MIT |
| [ipsw-parser](https://github.com/doronz88/ipsw_parser) | 1.8.4 | GNU General Public License v3 or later (GPLv3+) |
| [ipython](https://ipython.org) | 9.17.1 | BSD-3-Clause |
| [ipython_pygments_lexers](https://github.com/ipython/ipython-pygments-lexers) | 1.1.1 | BSD License |
| [jaraco.classes](https://github.com/jaraco/jaraco.classes) | 3.4.0 | MIT License |
| [jaraco.context](https://github.com/jaraco/jaraco.context) | 6.1.2 | MIT |
| [jaraco.functools](https://github.com/jaraco/jaraco.functools) | 4.6.0 | MIT |
| [jedi](https://github.com/davidhalter/jedi) | 0.20.1 | MIT |
| [keyring](https://github.com/jaraco/keyring) | 25.7.0 | MIT |
| [keyrings.alt](https://github.com/jaraco/keyrings.alt) | 5.0.2 | MIT License |
| [libusb](https://pypi.org/project/libusb/) | 1.0.30.post2 | Zlib |
| [lzfse](https://github.com/m1stadev/lzfse) | 0.4.2.post1 | MIT |
| [markdown-it-py](https://github.com/executablebooks/markdown-it-py) | 4.2.0 | MIT License |
| [matplotlib-inline](https://github.com/ipython/matplotlib-inline) | 0.2.2 | BSD-3-Clause |
| [mdurl](https://github.com/executablebooks/mdurl) | 0.1.2 | MIT License |
| [more-itertools](https://github.com/more-itertools/more-itertools) | 11.2.1 | MIT |
| [opack2](https://github.com/doronz88/opack2) | 0.1.0 | GNU General Public License v3 or later (GPLv3+) |
| [opentelemetry-api](https://github.com/open-telemetry/opentelemetry-python/tree/main/opentelemetry-api) | 1.45.1 | Apache-2.0 |
| [packaging](https://github.com/pypa/packaging) | 26.3 | Apache-2.0 OR BSD-2-Clause |
| [parso](https://github.com/davidhalter/parso) | 0.8.7 | MIT |
| [pillow](https://python-pillow.org) | 13.0.0 | MIT-CMU |
| [pillow_heif](https://github.com/bigcat88/pillow_heif) | 1.8.1 | BSD-3-Clause |
| [pkg_about](https://pypi.org/project/pkg-about/) | 2.5.1 | Zlib |
| [pmd-net-addr](https://github.com/ccie18643/PyTCP) | 0.0.3 | GPL-3.0-or-later |
| [pmd-net-proto](https://github.com/ccie18643/PyTCP) | 0.0.3 | GPL-3.0-or-later |
| [pmd-pytcp](https://github.com/ccie18643/PyTCP) | 0.3.8 | GPL-3.0-or-later |
| [prompt_toolkit](https://github.com/prompt-toolkit/python-prompt-toolkit) | 3.0.53 | BSD License |
| [protobuf](https://developers.google.com/protocol-buffers/) | 7.36.2 | 3-Clause BSD License |
| [psutil](https://github.com/giampaolo/psutil) | 7.2.2 | BSD-3-Clause |
| [pure_eval](http://github.com/alexmojaki/pure_eval) | 0.2.4 | MIT |
| [py-utlx](https://pypi.org/project/py-utlx/) | 2.5.4 | Zlib |
| [pycparser](https://github.com/eliben/pycparser) | 3.12 | BSD-3-Clause |
| [pycrashreport](https://github.com/doronz88/pycrashreport) | 2.0.0 | GNU General Public License v3 or later (GPLv3+) |
| [pycryptodome](https://www.pycryptodome.org) | 4.2.0 | BSD-2-Clause AND Unlicense AND MIT |
| [pydantic](https://github.com/pydantic/pydantic) | 2.14.1 | MIT |
| [pydantic_core](https://github.com/pydantic/pydantic) | 2.50.1 | MIT |
| [Pygments](https://pygments.org) | 2.21.0 | BSD-2-Clause |
| [pygnuutils](https://github.com/matan1008/pygnuutils) | 0.1.1 | GNU General Public License v3 or later (GPLv3+) |
| [pyicloud](https://github.com/timlaing/pyicloud) | 2026.10.1 | MIT |
| [pyimg4](https://github.com/m1stadev/PyIMG4) | 0.8.8 | MIT License (see text below) |
| [pyiosbackup](https://github.com/matan1008/pyiosbackup) | 0.2.4 | GNU General Public License v3 or later (GPLv3+) |
| [pykdebugparser](https://github.com/matan1008/pykdebugparser) | 1.10.0 | MIT License (see text below) |
| [pylzss](https://github.com/m1stadev/pylzss) | 0.3.4 | GNU LGPL v3 (GNU Lesser General Public License v3 (LGPLv3)) |
| [pymobiledevice3](https://github.com/doronz88/pymobiledevice3) | 11.45.0 | GPL-3.0-or-later |
| [pyproject_hooks](https://github.com/pypa/pyproject-hooks) | 1.3.3 | MIT |
| [python-dateutil](https://github.com/dateutil/dateutil) | 3.0.0 | Dual License (BSD License; Apache Software License) |
| [python-dotenv](https://github.com/theskumar/python-dotenv) | 1.2.4 | BSD-3-Clause |
| [python-pcapng](https://github.com/rshk/python-pcapng) | 2.1.1 | Apache-2.0 (Apache Software License) |
| [pytun-pmd3](https://github.com/doronz88/pytun-pmd3) | 3.0.3 | GNU General Public License v3 or later (GPLv3+) |
| [pyusb](https://pyusb.github.io/pyusb) | 1.3.1 | BSD License |
| [pywin32](https://github.com/mhammond/pywin32) | 312 | PSF (Python Software Foundation License) |
| [pywin32-ctypes](https://github.com/enthought/pywin32-ctypes) | 0.2.3 | BSD-3-Clause |
| [PyYAML](https://pyyaml.org/) | 6.0.3 | MIT |
| [qh3](https://github.com/jawah/qh3) | 1.9.4 | BSD License |
| [questionary](https://github.com/tmbo/questionary) | 2.1.1 | MIT |
| [remotezip2](https://github.com/doronz88/python-remotezip2) | 0.0.2 | GNU GENERAL PUBLIC LICENSE Version 3, 29 June 2007 (MIT License) |
| [requests](https://github.com/psf/requests) | 2.34.2 | Apache-2.0 (Apache Software License) |
| [rich](https://github.com/Textualize/rich) | 15.0.0 | MIT |
| [shellingham](https://github.com/sarugaku/shellingham) | 1.5.4 | ISC License |
| [six](https://github.com/benjaminp/six) | 1.17.0 | MIT |
| [srp](https://github.com/cocagne/pysrp) | 1.0.22 | MIT (BSD License) |
| [srptools](https://github.com/idlesign/srptools) | 2.0.0 | BSD-3-Clause |
| [stack-data](http://github.com/alexmojaki/stack_data) | 0.6.3 | MIT |
| [starlette](https://github.com/Kludex/starlette) | 1.10.0 | BSD-3-Clause |
| [termcolor](https://github.com/termcolor/termcolor) | 3.3.0 | MIT |
| [tinyhtml](https://github.com/niklasf/python-tinyhtml) | 1.3.0 | MIT/Apache-2.0 (MIT License; Apache Software License) |
| [traitlets](https://github.com/ipython/traitlets) | 5.16.1 | BSD License |
| [typer](https://github.com/fastapi/typer) | 0.27.3 | MIT |
| [typer-injector](https://github.com/BenjyWiener/typer-injector) | 0.3.0 | MIT |
| [typing_extensions](https://github.com/python/typing_extensions) | 4.16.0 | PSF-2.0 |
| [typing-inspection](https://github.com/pydantic/typing-inspection) | 0.4.4 | MIT |
| [tzdata](https://github.com/python/tzdata) | 2026.5 | Apache-2.0 |
| [tzlocal](https://github.com/regebro/tzlocal) | 5.3.1 | MIT |
| urllib3 | 2.8.0 | MIT |
| [uvicorn](https://uvicorn.dev/) | 0.54.0 | BSD-3-Clause |
| [watchfiles](https://github.com/samuelcolvin/watchfiles) | 1.3.0 | MIT |
| [wcwidth](https://github.com/jquast/wcwidth) | 0.9.4 | MIT License |
| [websockets](https://github.com/python-websockets/websockets) | 17.3 | BSD-3-Clause |
| [wsproto](https://github.com/python-hyper/wsproto/) | 1.3.2 | MIT |
| [xmltodict](https://github.com/martinblech/xmltodict) | 1.0.4 | MIT |
