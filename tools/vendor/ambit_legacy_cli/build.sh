#!/usr/bin/env bash
# Builds ../openambit_libambit (if not already built) then this CLI against it.
# Real build command - see that directory's README for why this exists.
#
#   ./tools/vendor/ambit_legacy_cli/build.sh
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
LIBAMBIT_DIR="$HERE/../openambit_libambit"

# HIDAPI_DRIVER=libusb, not the default libudev/hidraw backend: real, 2026-08-22 - the
# hidraw backend's hid_enumerate() came back empty/NULL against the real Ambit1 in this
# environment (this device's USB descriptor logs a kernel warning, "config 1 has an invalid
# interface number" - the hidraw backend's descriptor parsing likely trips on it), while the
# libusb backend enumerated and read it every time. write_nav.py hit the exact same choice
# for its own transport and independently settled on libusb-backed HID for the same reason
# (see Link.open()'s own docstring) - matching that, not guessed fresh here.
# macOS needs a different set of choices from Linux, none of which had ever been exercised
# (2026-08-27, first build of this on a Mac):
#   * no <endian.h>: le32toh/htole16/... simply do not exist, so every byte-swapping source
#     in libambit fails to compile. endian_compat_apple.h maps them onto Apple's
#     OSSwapLittleToHostInt* and is force-included, leaving the vendored sources untouched.
#   * HIDAPI_DRIVER=libusb does not build here at all: hid-libusb.c uses pthread_barrier_*,
#     which Darwin does not implement. The vendored hid-mac.c compiles but is a ~2010 hidapi
#     snapshot, so we link the platform's own modern hidapi instead (HIDAPI_DRIVER=system).
#   * libambit's CMakeLists declares a cmake_minimum_required below 3.5, which CMake 4
#     refuses outright; CMAKE_POLICY_VERSION_MINIMUM re-admits it without editing upstream.
# Windows (desktop-release.yml's windows job, 2026-09-04): never had a build step at all -
# the Windows CI job only froze ambit-backend.exe, so ambit_legacy_cli.exe never existed in
# a shipped build and every Ambit1/2 endpoint 502'd with "ambit_legacy_cli is not built" on
# Windows specifically (macOS got its own build step 2026-08-27; Windows was simply missed).
# HIDAPI_DRIVER=windows: the vendored hid-windows.c talks straight to the native Win32 HID
# API (SetupAPI + hid.dll), so - unlike Linux/macOS above - this needs no libusb at all, only
# `iconv` (this family's text fields are ISO-8859, see json_str()'s own comment; MSYS2's
# mingw-w64-x86_64-libiconv ships it) and `setupapi` (part of the MinGW/Windows SDK import
# libs already, no separate install). Expects an MSYS2 MINGW64 shell (uname -s reports
# MINGW64_NT-...) with mingw-w64-x86_64-{toolchain,cmake,libiconv} installed - see the CI
# step that calls this script. UNVERIFIED end-to-end: written and reasoned through, but this
# repo has no Windows machine to actually run it on - the next Windows CI run is the real
# test, not this script by itself.
# libambit's CMakeLists declares a cmake_minimum_required below 3.5, which CMake 4 (and now the
# Windows CI runner's CMake) refuses outright - CMAKE_POLICY_VERSION_MINIMUM re-admits it without
# editing upstream. Set for EVERY platform: macOS already needed it, the Windows job hit the same
# wall on 2026-09-04 (v0.2.30 release), and it future-proofs Linux against the same CMake bump.
EXTRA_CMAKE=(-DCMAKE_POLICY_VERSION_MINIMUM=3.5)
EXTRA_CC=()
EXTRA_LINK=()
OUT="$HERE/ambit_legacy_cli"
if [ "$(uname -s)" = "Darwin" ]; then
    HIDAPI_DRIVER=system
    EXTRA_CMAKE+=(-DCMAKE_C_FLAGS="-include $HERE/endian_compat_apple.h")
    EXTRA_CC+=(-include "$HERE/endian_compat_apple.h")
    if command -v pkg-config >/dev/null; then
        EXTRA_CC+=($(pkg-config --cflags hidapi 2>/dev/null || true))
    fi
    EXTRA_LINK+=(-Wl,-rpath,"$LIBAMBIT_DIR/build")
elif [[ "$(uname -s)" == MINGW* || "$(uname -s)" == MSYS* ]]; then
    HIDAPI_DRIVER=windows
    OUT="$HERE/ambit_legacy_cli.exe"          # legacy_link.py._binary_path() looks for this
    # MinGW has no <endian.h> either, so libambit's le16toh/htole32/... are undeclared and every
    # byte-swapping source fails to compile - same class of break macOS hit, fixed the same way:
    # force-include a Windows endian shim so the vendored sources stay byte-identical to upstream.
    EXTRA_CMAKE+=(-DCMAKE_C_FLAGS="-include $HERE/endian_compat_win.h")
    EXTRA_CC+=(-include "$HERE/endian_compat_win.h")
    # PE has no rpath concept - Windows resolves libambit.dll by searching the loading exe's
    # own directory first, so the CI step copies the built DLL next to ambit_legacy_cli.exe
    # (same directory ambit_backend.spec's glob("libambit*") already bundles wholesale).
else
    # HIDAPI_DRIVER=libusb, not the default libudev/hidraw backend: real, 2026-08-22 - see
    # the note below, kept from the original Linux-only version of this script.
    HIDAPI_DRIVER=libusb
    EXTRA_LINK+=(-Wl,-rpath,"$LIBAMBIT_DIR/build")
fi

cmake -S "$LIBAMBIT_DIR" -B "$LIBAMBIT_DIR/build" -DCMAKE_BUILD_TYPE=Release \
      -DHIDAPI_DRIVER="$HIDAPI_DRIVER" "${EXTRA_CMAKE[@]}"
cmake --build "$LIBAMBIT_DIR/build" -j"$(nproc 2>/dev/null || sysctl -n hw.ncpu)"

"${CC:-cc}" -O2 -Wall "${EXTRA_CC[@]}" -I"$LIBAMBIT_DIR" -o "$OUT" \
    "$HERE/ambit_legacy_cli.c" "$HERE/ambit1_sport_mode.c" \
    -L"$LIBAMBIT_DIR/build" -lambit -lm "${EXTRA_LINK[@]}"

echo ""
echo "Binary: $OUT"
