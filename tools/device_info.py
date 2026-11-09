#!/usr/bin/env python3
"""Device identity (model/serial/firmware/hardware version) and live battery charge.

Not new reverse-engineering - both commands were already known to this project, just never
used this way. `write_nav.py` already sends `CMD_DEVICE_INFO` (0x0000) on every reset/route
run, but discards the reply. `CMD_NAMES` (`ambit_pcap.py`) already names 0x0306 as "status"
from real captures, but nothing here had ever sent it. The actual reply layout for both -
which bytes are model vs. serial vs. version, and that battery charge sits at offset 1 of
the status reply - comes from openambit's own real, working implementation
(`assets/openambit-master.zip`, `libambit.c`'s `device_info_get()` and
`device_driver_common.c`'s `libambit_device_driver_status_get()`), not guessed. Ambit3's own
driver (`device_driver_ambit3.c`) wires `status_get` to exactly that shared implementation,
so this applies to the reference watch, not just older Ambit/Ambit2 models.

    ./tools/device_info.py                  # reads live from the watch
"""

import argparse
import sys

from write_nav import CMD_DEVICE_INFO, Link

CMD_STATUS = 0x0306  # named "status" in ambit_pcap.py's CMD_NAMES, real captures - never
                      # sent by any tool in this project before this file


def read_device_info(link):
    """0x0000 reply: 16 bytes model + 16 bytes serial (each null-terminated ASCII/UTF-8) +
    4 bytes fw_version + 4 bytes hw_version - byte-for-byte openambit's device_info_get().
    The request payload (`\\x02\\x48\\x03\\x00`) is the exact one `write_nav.py`'s own
    `main()` already sends and gets a valid reply for on the reference watch - reused as-is
    rather than openambit's own "komposti_version" table value for this model, which reads
    differently (`\\x02\\x04\\x59\\x00`) and isn't what this project has actually verified."""
    reply = link.command(CMD_DEVICE_INFO, b"\x02\x48\x03\x00")
    if len(reply) < 40:
        raise RuntimeError(f"device_info reply too short: {len(reply)} bytes")
    model = reply[0:16].split(b"\0")[0].decode("utf-8", "replace")
    serial = reply[16:32].split(b"\0")[0].decode("utf-8", "replace")
    fw = reply[32:36]
    hw = reply[36:40]
    # openambit's own version_string() (libambit.c): "%d.%d.%d", version[0], version[1],
    # version[2] | (version[3] << 8) - the third component is 16-bit little-endian, not a
    # plain single byte. Caught directly against real hardware: without this, hw_version
    # printed "70.2.6" instead of the real "70.2.17414" HANDOFF.md already documents.
    # The model codename from the USB product_id, which stays model-specific even in the
    # bootloader (where `model` above reads "BSL"). This is what lets a bricked watch be
    # identified for recovery without ever having been connected before.
    from write_nav import codename_for_pid
    usb_model = codename_for_pid(getattr(link, "opened_product_id", None))
    return {
        "model": model,
        "serial": serial,
        "fw_version": f"{fw[0]}.{fw[1]}.{fw[2] | (fw[3] << 8)}",
        "hw_version": f"{hw[0]}.{hw[1]}.{hw[2] | (hw[3] << 8)}",
        "usb_model": usb_model,
    }


def read_battery(link):
    """0x0306 reply: charge percentage at byte offset 1 - byte-for-byte
    libambit_device_driver_status_get()."""
    reply = link.command(CMD_STATUS, b"")
    if len(reply) < 2:
        raise RuntimeError(f"status reply too short: {len(reply)} bytes")
    return reply[1]


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--verbose", action="store_true")
    ap.add_argument("--json", action="store_true",
                     help="print one JSON object instead of human-readable lines - for "
                          "desktop/backend/server.py, not meant for a person to read")
    args = ap.parse_args()

    link = Link(dry_run=False, verbose=args.verbose and not args.json)
    if not args.json:
        print("read-only: the 0x0000 and 0x0306 queries, nothing is written")
    link.open()

    info = read_device_info(link)
    battery = read_battery(link)

    # Remember this watch (serial -> codename/hw) so a later BSL recovery can identify it.
    import watch_registry
    watch_registry.record(info)

    if args.json:
        import json
        print(json.dumps({**info, "battery_percent": battery}))
        return 0

    print(f"  model     {info['model']}")
    print(f"  serial    {info['serial']}")
    print(f"  firmware  {info['fw_version']}")
    print(f"  hardware  {info['hw_version']}")
    print(f"  battery   {battery}%")
    return 0


if __name__ == "__main__":
    sys.exit(main())
