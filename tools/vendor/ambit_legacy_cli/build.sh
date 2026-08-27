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
EXTRA_CMAKE=()
EXTRA_CC=()
if [ "$(uname -s)" = "Darwin" ]; then
    HIDAPI_DRIVER=system
    EXTRA_CMAKE+=(-DCMAKE_POLICY_VERSION_MINIMUM=3.5
                  -DCMAKE_C_FLAGS="-include $HERE/endian_compat_apple.h")
    EXTRA_CC+=(-include "$HERE/endian_compat_apple.h")
    if command -v pkg-config >/dev/null; then
        EXTRA_CC+=($(pkg-config --cflags hidapi 2>/dev/null || true))
    fi
else
    # HIDAPI_DRIVER=libusb, not the default libudev/hidraw backend: real, 2026-08-22 - see
    # the note below, kept from the original Linux-only version of this script.
    HIDAPI_DRIVER=libusb
fi

cmake -S "$LIBAMBIT_DIR" -B "$LIBAMBIT_DIR/build" -DCMAKE_BUILD_TYPE=Release \
      -DHIDAPI_DRIVER="$HIDAPI_DRIVER" "${EXTRA_CMAKE[@]}"
cmake --build "$LIBAMBIT_DIR/build" -j"$(nproc 2>/dev/null || sysctl -n hw.ncpu)"

"${CC:-cc}" -O2 -Wall "${EXTRA_CC[@]}" -I"$LIBAMBIT_DIR" -o "$HERE/ambit_legacy_cli" \
    "$HERE/ambit_legacy_cli.c" "$HERE/ambit1_sport_mode.c" \
    -L"$LIBAMBIT_DIR/build" -lambit -lm -Wl,-rpath,"$LIBAMBIT_DIR/build"

echo ""
echo "Binary: $HERE/ambit_legacy_cli"
