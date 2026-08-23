# Vendored: openambit's libambit (GPLv3)

Source: https://github.com/openambitproject/openambit, `src/libambit/`, commit
`ca910d10a3786f706941e250b1f6f5514632c30e` (2023-09-14). Licensed GPLv3 (see `COPYING` in this
directory) - written by Emil Ljungdahl and contributors.

**One deviation from upstream**, `pmem20.c`'s legacy `activity_name` decode: was
`"ISO-8859-15"` upstream, patched to `"UTF-8"` 2026-08-22 - real hardware (André's French
Ambit3 Sport) proved this project's watch name fields are UTF-8 (visible mojibake otherwise;
see `tools/custom_modes.py`'s decode fix for the same evidence). Upstream's own
`device_driver_ambit3.c` already used `"UTF-8"` for the identical field on the newer driver -
this brings the legacy path in line with it, not a fresh guess. See the inline comment at that
line for the full note. Everything else here is unmodified.

## Why this is here

Everything else in this project's own USB code (`write_nav.py`, `ambit_pcap.py`) is a from-
scratch reverse-engineered SBEM/NSP implementation, and 2026-08-22 testing against a real
Ambit1 confirmed it: `CMD_DEVICE_INFO`/`CMD_STATUS` (0x0000/0x0306) are common to the whole
family, so device identity + battery already work for Ambit1/2 with zero new code (just
`write_nav.PRODUCT_IDS`) - but the higher-level SBEM object queries (settings 0x1100, memory
map 0x0b21, POIs 0x0b24) come back **empty**, not an error, on a real Ambit1. Ambit1/2 predate
SBEM entirely: they speak an older, different command set over the same USB transport -
Suunto's own `Devices.xml` calls it `BluebirdDevice_Legacy_2_0`, `log type="PMEM" version="2.0"`.

Nobody has reverse-engineered that older command set fresh in this project, and openambit
already has a real, working implementation of exactly it (`device_driver_ambit.c`,
`personal.c`, `pmem20.c`) - validated live against real hardware here (device info, personal
settings, training-log read, see `tools/vendor/ambit_legacy_cli/`). Rather than re-deriving it
blind, this vendors the proven implementation and builds a small standalone CLI on top
(`tools/vendor/ambit_legacy_cli/`) that the Python backend shells out to via `subprocess`,
exactly like every other `tools/*.py` CLI (`desktop/backend/server.py`'s own `run_tool()`) -
not linked into the app binary, so GPLv3 doesn't reach past this one helper process.

## Scope

Only `device_driver_ambit.c` (the legacy PMEM 2.0 driver) is actually exercised by
`ambit_legacy_cli`. `device_driver_ambit3.c` is vendored alongside only because
`device_support.c`'s static device table references its symbol - it is never invoked (this
project's own Ambit3+ support stays in `write_nav.py`, unrelated to this vendored copy).

## Build

    cmake -S . -B build   # in this directory, or see tools/vendor/ambit_legacy_cli/build.sh
    cmake --build build

Produces `libambit.so`/`.dylib`/`.dll`. `tools/vendor/ambit_legacy_cli/` links against it.
