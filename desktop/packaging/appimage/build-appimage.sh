#!/usr/bin/env bash
# Assemble the Linux AppImage locally. The CI job in .github/workflows/desktop-release.yml is the
# authoritative builder; this mirrors it for a local run. For a PORTABLE result (runs on old
# distros), run this inside an old base, e.g.:
#
#   docker run --rm -v "$PWD":/src -w /src ubuntu:20.04 \
#       bash -lc 'desktop/packaging/appimage/build-appimage.sh'
#
# Run on a current distro it still works, but the AppImage will only run on distros as new as this
# machine (AppImages link against the build host's glibc). See README.md for why this matters.
#
# Prerequisites it expects on PATH / in the environment:
#   - a configured Qt 6.5+ kit (CMAKE_PREFIX_PATH pointing at it, or qmake6 on PATH)
#   - cmake, a C++20-capable compiler, python3 + pip
#   - linuxdeploy, linuxdeploy-plugin-qt, appimagetool (downloaded here if absent)
set -euo pipefail
cd "$(dirname "$0")/../../.."          # repo root
REPO="$PWD"
ARCH="x86_64"
APPDIR="$REPO/dist/appimage/AppDir"
TOOLS="$REPO/dist/appimage/tools"
VERSION="$(sed -n 's/^project(AmbitApp VERSION \([0-9.]*\).*/\1/p' desktop/CMakeLists.txt)"

# Baseline CPU flags so the result runs on an Intel Q9550 (x86-64-v1: no SSE4.2/POPCNT/AVX).
export CFLAGS="${CFLAGS:-} -march=x86-64 -mtune=generic"
export CXXFLAGS="${CXXFLAGS:-} -march=x86-64 -mtune=generic"

echo "== 1/5  Build the Qt app (Release) =="
cmake -S desktop -B desktop/build -DCMAKE_BUILD_TYPE=Release
cmake --build desktop/build --parallel

echo "== 2/5  Freeze the Python backend (PyInstaller) =="
python3 -m pip install --upgrade pip
python3 -m pip install pyinstaller bleak hid
pyinstaller tools/packaging/ambit_backend.spec \
    --distpath dist/backend --workpath build/pyi

echo "== 3/5  Lay out the AppDir =="
rm -rf "$APPDIR"
mkdir -p "$APPDIR/usr/bin" "$APPDIR/usr/bin/backend" "$APPDIR/usr/lib" \
         "$APPDIR/usr/share/applications" \
         "$APPDIR/usr/share/icons/hicolor/256x256/apps"
cp desktop/build/Sommet                         "$APPDIR/usr/bin/Sommet"
cp dist/backend/ambit-backend                   "$APPDIR/usr/bin/backend/ambit-backend"
chmod +x "$APPDIR/usr/bin/backend/ambit-backend"
cp desktop/packaging/appimage/Sommet.desktop    "$APPDIR/usr/share/applications/sommet.desktop"
cp desktop/packaging/icon.png                   "$APPDIR/usr/share/icons/hicolor/256x256/apps/sommet.png"
# The frozen `hid` loads libhidapi by name via ctypes (not linked), so linuxdeploy won't pull it
# in on its own. Place it in usr/lib, which the AppImage's AppRun puts on LD_LIBRARY_PATH; the
# child backend process inherits that and finds it (the Linux analogue of the macOS DYLD step).
for so in /usr/lib/x86_64-linux-gnu/libhidapi-hidraw.so* /usr/lib/x86_64-linux-gnu/libhidapi-libusb.so*; do
    [ -e "$so" ] && cp -L "$so" "$APPDIR/usr/lib/" || true
done

echo "== 4/5  Fetch linuxdeploy if needed =="
mkdir -p "$TOOLS"; export PATH="$TOOLS:$PATH"
get() { [ -x "$TOOLS/$1" ] || { curl -sSL -o "$TOOLS/$1" "$2"; chmod +x "$TOOLS/$1"; }; }
get linuxdeploy-x86_64.AppImage        https://github.com/linuxdeploy/linuxdeploy/releases/download/continuous/linuxdeploy-x86_64.AppImage
get linuxdeploy-plugin-qt-x86_64.AppImage https://github.com/linuxdeploy/linuxdeploy-plugin-qt/releases/download/continuous/linuxdeploy-plugin-qt-x86_64.AppImage

echo "== 5/5  Build the AppImage (with delta-update info) =="
export QML_SOURCES_PATHS="$REPO/desktop/qml"
export UPDATE_INFORMATION="gh-releases-zsync|skinnie|sommet|latest|Sommet-linux-${ARCH}.AppImage.zsync"
export OUTPUT="Sommet-linux-${ARCH}.AppImage"
"$TOOLS/linuxdeploy-x86_64.AppImage" --appdir "$APPDIR" \
    --plugin qt \
    --desktop-file "$APPDIR/usr/share/applications/sommet.desktop" \
    --icon-file "$APPDIR/usr/share/icons/hicolor/256x256/apps/sommet.png" \
    --output appimage

echo
echo "Done: $REPO/$OUTPUT  (version $VERSION)"
echo "Portable only if this ran on an old base — see README.md."
