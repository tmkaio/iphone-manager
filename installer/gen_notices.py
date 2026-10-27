"""Write THIRD-PARTY-NOTICES for every package installed in a site-packages folder.

Reads the license metadata (License-Expression / License / classifiers) and the license files
shipped in each *.dist-info, so the notices always match what is actually bundled.

    python gen_notices.py <site-packages> <output.txt> [--summary <output.md>]
"""

import argparse
import sys
from importlib.metadata import PathDistribution
from pathlib import Path

HEADER = """iPhone Manager - third-party notices
=====================================

iPhone Manager is free software, licensed under the GNU General Public License v3.0 or later
(see LICENSE). It is distributed together with the components listed below, each under its own
license. The full license texts shipped by each package follow the summary.

{runtime}

Note: the "av" (PyAV) wheel bundles FFmpeg libraries licensed under the LGPL-2.1-or-later; FFmpeg
source code is available at https://ffmpeg.org/download.html and PyAV's build scripts at
https://github.com/PyAV-Org/pyav.

Not included and not distributed: Apple Mobile Device Support / MobileDevice.dll (installed by
Apple Devices, iTunes or iMazing) and ffmpeg/ffprobe executables (optional, looked up on PATH).
"""

WINDOWS_RUNTIME = """Bundled runtime: the official CPython "embeddable package" for Windows (Python Software Foundation
License; see python/LICENSE.txt), plus the Tcl/Tk libraries used by tkinter (Tcl/Tk license,
reproduced at the end of this file)."""

STANDALONE_RUNTIME = """Bundled runtime: CPython from python-build-standalone (https://github.com/astral-sh/python-build-standalone;
Python Software Foundation License, see python/lib/python3.*/LICENSE.txt, which also lists the licenses
of the libraries it is built with, such as OpenSSL, SQLite, zlib and Tcl/Tk)."""

LICENSE_CLASSIFIER = "License :: "


def license_of(meta) -> str:
    expr = meta.get("License-Expression")
    if expr:
        return expr.strip()
    classifiers = [c[len(LICENSE_CLASSIFIER):].replace("OSI Approved :: ", "")
                   for c in meta.get_all("Classifier") or [] if c.startswith(LICENSE_CLASSIFIER)]
    classifiers = [c for c in classifiers if c != "OSI Approved"]
    lic = (meta.get("License") or "").strip()
    if lic and "\n" not in lic and len(lic) < 80:
        return lic + (f" ({'; '.join(classifiers)})" if classifiers and lic not in classifiers[0] else "")
    if classifiers:
        return "; ".join(classifiers)
    if lic:
        return lic.splitlines()[0][:80] + " (see text below)"
    return "UNKNOWN (see project page)"


def license_files(dist_dir: Path) -> list[Path]:
    found = []
    for p in sorted(dist_dir.rglob("*")):
        name = p.name.upper()
        if p.is_file() and any(k in name for k in ("LICENSE", "LICENCE", "COPYING", "NOTICE", "AUTHORS")):
            found.append(p)
    return found


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("site_packages", type=Path)
    ap.add_argument("output", type=Path)
    ap.add_argument("--summary", type=Path, help="also write a Markdown summary table")
    ap.add_argument("--standalone", action="store_true", help="the macOS/Linux builds (python-build-standalone)")
    args = ap.parse_args()

    rows = []
    for dist_dir in sorted(args.site_packages.glob("*.dist-info"), key=lambda p: p.name.lower()):
        dist = PathDistribution(dist_dir)
        meta = dist.metadata
        name, version = meta["Name"], meta["Version"]
        url = meta.get("Home-page") or ""
        if not url:
            for entry in meta.get_all("Project-URL") or []:
                label, _, link = entry.partition(",")
                if label.strip().lower() in ("homepage", "home", "source", "repository", "source code", "github"):
                    url = link.strip()
                    break
        files = license_files(dist_dir)
        lic = license_of(meta)
        if lic.startswith("UNKNOWN") and files:
            # no license metadata: name it after the first line of the shipped license file
            first = next((ln.strip() for ln in files[0].read_text(encoding="utf-8", errors="replace").splitlines()
                          if ln.strip()), "")
            if first and len(first) < 80:
                lic = f"{first} (from {files[0].name})"
        rows.append((name, version, lic, url, files))

    out = [HEADER.format(runtime=STANDALONE_RUNTIME if args.standalone else WINDOWS_RUNTIME), "", "Summary", "-------", ""]
    width = max(len(r[0]) for r in rows)
    for name, version, lic, url, _ in rows:
        out.append(f"{name.ljust(width)}  {version:<14}  {lic}" + (f"  <{url}>" if url else ""))
    out.append("")
    for name, version, lic, url, files in rows:
        out += ["", "=" * 78, f"{name} {version}", f"License: {lic}"]
        if url:
            out.append(f"Project: {url}")
        out.append("=" * 78)
        if not files:
            out.append("(no license file shipped in the package metadata)")
        for f in files:
            out += ["", f"--- {f.name} ---", ""]
            out.append(f.read_text(encoding="utf-8", errors="replace").rstrip())
    # Tcl/Tk (bundled for tkinter): <python>/tcl/tk8.6/license.terms, same terms as Tcl
    tcl_terms = args.site_packages.parent.parent / "tcl" / "tk8.6" / "license.terms"
    if tcl_terms.exists():
        out += ["", "=" * 78, "Tcl/Tk 8.6 (used by tkinter)", "License: Tcl/Tk License (TCL)", "=" * 78, ""]
        out.append(tcl_terms.read_text(encoding="utf-8", errors="replace").rstrip())
    args.output.write_text("\n".join(out) + "\n", encoding="utf-8")

    if args.summary:
        md = [
            "# Third-party notices",
            "",
            "iPhone Manager is licensed under the GNU GPL v3.0 or later (see `LICENSE`). The Windows installer",
            "bundles the official CPython embeddable package (PSF License), Tcl/Tk (for tkinter) and the Python",
            "packages below. The installer ships `THIRD-PARTY-NOTICES.txt` with the full license text of each one,",
            "generated at build time by `installer/gen_notices.py` from the installed packages' metadata.",
            "",
            "The `av` (PyAV) wheel bundles FFmpeg libraries under the LGPL-2.1-or-later.",
            "Apple's Mobile Device Support and ffmpeg/ffprobe executables are **not** bundled.",
            "",
            "| Package | Version | License |",
            "|---|---|---|",
        ]
        for name, version, lic, url, _ in rows:
            label = f"[{name}]({url})" if url else name
            md.append(f"| {label} | {version} | {lic.replace('|', '/')} |")
        args.summary.write_text("\n".join(md) + "\n", encoding="utf-8")
    print(f"{len(rows)} packages -> {args.output}")


if __name__ == "__main__":
    main()
