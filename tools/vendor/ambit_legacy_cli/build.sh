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
cmake -S "$LIBAMBIT_DIR" -B "$LIBAMBIT_DIR/build" -DCMAKE_BUILD_TYPE=Release -DHIDAPI_DRIVER=libusb
cmake --build "$LIBAMBIT_DIR/build" -j"$(nproc 2>/dev/null || sysctl -n hw.ncpu)"

gcc -O2 -Wall -I"$LIBAMBIT_DIR" -o "$HERE/ambit_legacy_cli" "$HERE/ambit_legacy_cli.c" "$HERE/ambit1_sport_mode.c" \
    -L"$LIBAMBIT_DIR/build" -lambit -lm -Wl,-rpath,"$LIBAMBIT_DIR/build"

echo ""
echo "Binary: $HERE/ambit_legacy_cli"
