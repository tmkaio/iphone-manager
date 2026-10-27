#!/usr/bin/env bash
# Builds iPhone Manager for macOS (.app in a .dmg) or Linux (AppImage).
#
#   installer/build-unix.sh            # on a Mac: dist/iPhoneManager-<version>-macOS-<arch>.dmg
#                                      # on Linux: dist/iPhoneManager-<version>-<arch>.AppImage
#
# 1. A relocatable CPython 3.13 (python-build-standalone, installed with uv) goes into the bundle.
# 2. requirements.txt is pip-installed into it, locked by installer/constraints.txt.
# 3. The app, run.py, LICENSE and THIRD-PARTY-NOTICES.txt are copied next to it; imports and the
#    server are smoke-tested.
# 4. macOS: a small native launcher, Info.plist and icon make the .app, which is signed (Developer
#    ID if MACOS_SIGN_IDENTITY is set, ad hoc otherwise) and put in a .dmg; installer/notarize.sh
#    notarizes it. Linux: AppRun + .desktop + icon, packed with appimagetool.
#
# Needs: uv, curl. macOS: Xcode command line tools (clang, codesign, hdiutil, iconutil).
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
BUILD="$ROOT/build"
DIST="$ROOT/dist"
PY_VERSION="${PY_VERSION:-3.13}"
SMOKE_PORT="${SMOKE_PORT:-8799}"
OS="$(uname -s)"
ARCH="$(uname -m)"   # arm64 / x86_64 (macOS), x86_64 / aarch64 (Linux)
VERSION="$(sed -n 's/^__version__ = "\(.*\)"/\1/p' "$ROOT/app/__init__.py")"
[ -n "$VERSION" ] || { echo "__version__ not found in app/__init__.py" >&2; exit 1; }
echo "iPhone Manager $VERSION ($OS $ARCH)"

rm -rf "$BUILD/unix"
mkdir -p "$BUILD/unix" "$DIST"

if [ "$OS" = "Darwin" ]; then
  APP="$BUILD/unix/iPhone Manager.app"
  RES="$APP/Contents/Resources"
  mkdir -p "$APP/Contents/MacOS" "$RES"
  APPDIR="$RES/app"       # run.py + app/
  PYDIR="$RES/python"
else
  APPDIR_ROOT="$BUILD/unix/AppDir"
  APPDIR="$APPDIR_ROOT/usr/app"
  PYDIR="$APPDIR_ROOT/usr/python"
fi
mkdir -p "$APPDIR"

# ---------- Python ----------
uv python install "$PY_VERSION"
BASE_PY="$(uv python find --managed-python "$PY_VERSION")"
# -P: uv links cpython-3.13-<platform> to the patch-version folder; copy the real one
BASE_DIR="$(cd "$(dirname "$BASE_PY")/.." && pwd -P)"
echo "Python: $BASE_DIR ($("$BASE_PY" --version))"
mkdir -p "$PYDIR"
cp -R "$BASE_DIR/." "$PYDIR/"
PY="$PYDIR/bin/python3"
PYVER="$("$PY" -c 'import sys; print(f"{sys.version_info[0]}.{sys.version_info[1]}")')"
STDLIB="$PYDIR/lib/python$PYVER"
# build-time only: headers, static library, tests
rm -rf "$PYDIR/include" "$PYDIR/share" "$STDLIB"/config-* "$STDLIB/test" "$STDLIB/idlelib/idle_test" \
       "$STDLIB/tkinter/test" "$PYDIR/lib/pkgconfig"
rm -f "$PYDIR"/lib/libpython*.a
# uv marks its interpreters as externally managed; this copy is ours
rm -f "$STDLIB/EXTERNALLY-MANAGED"

# ---------- dependencies ----------
"$PY" -m ensurepip --upgrade >/dev/null
"$PY" -m pip install --disable-pip-version-check --no-input --only-binary :all: --no-compile \
  -r "$ROOT/requirements.txt" -c "$ROOT/installer/constraints.txt"
"$PY" -m pip uninstall -y pip >/dev/null
SITE="$STDLIB/site-packages"
rm -rf "$SITE/jedi/third_party"   # ~25 MB of type stubs, only for IPython's completion
# console-script launchers point at this build machine's path
find "$PYDIR/bin" -type f ! -name "python*" -delete

# ---------- app ----------
cp -R "$ROOT/app" "$APPDIR/app"
find "$APPDIR/app" -name "__pycache__" -type d -prune -exec rm -rf {} +
cp "$ROOT/run.py" "$ROOT/LICENSE" "$ROOT/README.md" "$APPDIR/"
"$PY" "$ROOT/installer/gen_notices.py" --standalone "$SITE" "$APPDIR/THIRD-PARTY-NOTICES.txt"
"$PY" -m compileall -q -j 0 "$STDLIB" "$APPDIR/app" >/dev/null || true

# every module the app loads lazily, so a missing package fails the build
(cd "$APPDIR" && "$PY" -s -c '
import importlib, sys
mods = ["app.server", "app.mirror", "app.mount_ddi", "app.restore_runner", "app.modes_usb",
        "pymobiledevice3.__main__", "pymobiledevice3.cli.restore", "pymobiledevice3.restore.recovery",
        "pyicloud", "pillow_heif", "av", "usb.core", "uvicorn", "httptools", "websockets"]
for m in mods:
    importlib.import_module(m)
from app import system
system.use_bundled_libusb()
import usb.core
usb.core.find(find_all=True)  # loads libusb (the bundled one on macOS)
print("imports ok:", len(mods), "modules; python", sys.version.split()[0])
')

# ---------- smoke test ----------
if [ -z "${SKIP_SMOKE:-}" ]; then
  LOG="$BUILD/unix/smoke.log"
  (cd "$APPDIR" && "$PY" -s run.py --no-window --port "$SMOKE_PORT" >"$LOG" 2>&1) &
  SERVER=$!
  trap 'kill $SERVER 2>/dev/null || true' EXIT
  for _ in $(seq 60); do
    curl -fs -o /dev/null "http://127.0.0.1:$SMOKE_PORT/" && break
    kill -0 $SERVER 2>/dev/null || break
    sleep 1
  done
  curl -fs -o /dev/null "http://127.0.0.1:$SMOKE_PORT/" || { cat "$LOG"; echo "server didn't serve /" >&2; exit 1; }
  # 200 = device list; 503 = the handler ran but there is no usbmuxd on this machine
  CODE="$(curl -s -o /dev/null -w '%{http_code}' --max-time 60 "http://127.0.0.1:$SMOKE_PORT/api/devices")"
  case "$CODE" in
    200|503) echo "GET / -> 200, GET /api/devices -> $CODE" ;;
    *) tail -40 "$LOG"; echo "/api/devices failed ($CODE)" >&2; exit 1 ;;
  esac
  kill $SERVER; wait $SERVER 2>/dev/null || true
  trap - EXIT
fi

# ---------- icon ----------
ICON_PNG="$BUILD/unix/icon.png"
"$PY" -c '
import sys
from PIL import Image
im = Image.open(sys.argv[1]); im.size = max(im.info["sizes"]); im.load()
im.convert("RGBA").resize((512, 512), Image.LANCZOS).save(sys.argv[2])
' "$ROOT/installer/app.ico" "$ICON_PNG"

if [ "$OS" = "Darwin" ]; then
  # ---------- .app ----------
  ICONSET="$BUILD/unix/AppIcon.iconset"
  mkdir -p "$ICONSET"
  for s in 16 32 128 256 512; do
    sips -z $s $s "$ICON_PNG" --out "$ICONSET/icon_${s}x${s}.png" >/dev/null
    d=$((s * 2)); [ $d -le 512 ] && sips -z $d $d "$ICON_PNG" --out "$ICONSET/icon_${s}x${s}@2x.png" >/dev/null
  done
  iconutil -c icns "$ICONSET" -o "$RES/AppIcon.icns"

  clang -O2 -arch "$ARCH" -mmacosx-version-min=11.0 -o "$APP/Contents/MacOS/iPhoneManager" \
    "$ROOT/installer/macos/launcher.c"

  sed -e "s/@VERSION@/$VERSION/g" "$ROOT/installer/macos/Info.plist" > "$APP/Contents/Info.plist"

  # ---------- signing ----------
  IDENTITY="${MACOS_SIGN_IDENTITY:--}"   # "-" = ad hoc (Apple Silicon refuses unsigned code)
  SIGN=(codesign --force --sign "$IDENTITY")
  if [ "$IDENTITY" != "-" ]; then
    SIGN+=(--timestamp --options runtime --entitlements "$ROOT/installer/macos/entitlements.plist")
  fi
  # inside out: every Mach-O file, then the bundle
  while IFS= read -r -d '' f; do
    if file -b "$f" | grep -q "Mach-O"; then "${SIGN[@]}" "$f"; fi
  done < <(find "$RES" -type f -print0)
  "${SIGN[@]}" "$APP"
  codesign --verify --deep --strict "$APP"
  echo "signed with: $IDENTITY"

  # ---------- .dmg ----------
  STAGE="$BUILD/unix/dmg"
  mkdir -p "$STAGE"
  cp -R "$APP" "$STAGE/"
  ln -s /Applications "$STAGE/Applications"
  DMG="$DIST/iPhoneManager-$VERSION-macOS-$ARCH.dmg"
  rm -f "$DMG"
  hdiutil create -volname "iPhone Manager" -srcfolder "$STAGE" -ov -format UDZO "$DMG" >/dev/null
  if [ "$IDENTITY" != "-" ]; then codesign --force --sign "$IDENTITY" --timestamp "$DMG"; fi
  echo "DMG: $DMG ($(du -h "$DMG" | cut -f1))"
else
  # ---------- AppImage ----------
  cp "$ROOT/installer/linux/AppRun" "$APPDIR_ROOT/AppRun"
  chmod +x "$APPDIR_ROOT/AppRun"
  cp "$ROOT/installer/linux/iphone-manager.desktop" "$APPDIR_ROOT/"
  cp "$ICON_PNG" "$APPDIR_ROOT/iphone-manager.png"
  TOOL="$BUILD/cache/appimagetool-$ARCH.AppImage"
  if [ ! -x "$TOOL" ]; then
    mkdir -p "$BUILD/cache"
    curl -fL -o "$TOOL" "https://github.com/AppImage/appimagetool/releases/download/continuous/appimagetool-$ARCH.AppImage"
    chmod +x "$TOOL"
  fi
  OUT="$DIST/iPhoneManager-$VERSION-$ARCH.AppImage"
  ARCH="$ARCH" "$TOOL" --appimage-extract-and-run --no-appstream "$APPDIR_ROOT" "$OUT"
  echo "AppImage: $OUT ($(du -h "$OUT" | cut -f1))"
fi
