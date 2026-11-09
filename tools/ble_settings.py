#!/usr/bin/env python3
"""Settings over BLE, phone-driven, post-handshake.

Own file, this project's own "one file per format" convention. Reuses `settings_write.py`'s
own read/decode/write pipeline unchanged (`read_all`, `write_one`, `descriptor_for_product_id`,
`settings_table`) against any Link-compatible object - the SBEM schema and per-device
template logic is real, hardware-verified work already done for the USB path, none of it
is re-derived here.

The one gap USB doesn't have: `descriptor_for_product_id()`/`settings_table()` key off a
USB `product_id` this project doesn't have over BLE (no USB descriptor to read it from).
`product_id_from_model()` below closes that the same way Android's own
`AmbitBleModule.guessProductId()` already does - dispatch on the handshake's own `model`
string rather than a USB PID, confirmed for Kailash specifically
(`sommet_kailash_ble_time_sync` memory: "device_info.model=='Hoopoe'", not the guessed
0x1c bucket every other BLE product_id fallback used before that fix).
"""

import sys

from settings_write import (
    KAILASH_PRODUCT_ID, descriptor_for_product_id, read_all, settings_table, write_one,
)
from write_nav import CMD_SETTINGS_READ

import sbem_schema


# The hello's model string -> the product id settings_write.py keys its tables on. A watch
# not listed here is read with the Ambit3 tables (`None`).
_MODEL_PRODUCT_ID = {
    "Hoopoe": KAILASH_PRODUCT_ID,
    # Traverse / Traverse Alpha: their own descriptor and their own settings table. Until
    # 2026-10-14 they fell through to the Ambit3 Peak descriptor here - the same wrong-schema
    # mistake fixed for the cable on 2026-08-16 (write_nav.descriptor_for_product_id).
    "Jabiru": 0x002B,
    "Loon": 0x002D,
    # Ambit3 Vertical: its own entry numbers (ticket 06), read from a descriptor rebuilt from
    # the firmware. The Peak's would decode other settings' bytes.
    "Kaka": 0x002C,
}


def product_id_from_model(model):
    """Which of settings_write.py's watch families a Bluetooth hello's model belongs to."""
    return _MODEL_PRODUCT_ID.get(model)


def read_settings(link, model=None):
    """Real 0x1100 read - settings_write.py's own CLI never simulates this even in its
    dry-run mode (only the WRITE step there is optional), so this doesn't either."""
    product_id = product_id_from_model(model)
    descriptor = descriptor_for_product_id(product_id) or sbem_schema.default_descriptor()
    payload = link.command(CMD_SETTINGS_READ, b"\0\0\0\0")
    return read_all(payload, descriptor, product_id)


def write_setting(link, key, value, model=None):
    """Real 0x1101 (or, on Kailash, the single-entry push) write via write_one() -
    unchanged, hardware-verified logic. `ok` is only true if write_one()'s own re-read
    confirms the change actually landed - never just that a write was accepted."""
    product_id = product_id_from_model(model)
    descriptor = descriptor_for_product_id(product_id) or sbem_schema.default_descriptor()
    return write_one(link, descriptor, key, value, product_id=product_id)


def main():
    import argparse
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--set", metavar="KEY=VALUE", required=True)
    ap.add_argument("--write", action="store_true",
                     help="actually sends 0x1101; without this, shows the current value "
                          "only (a real 0x1100 read, nothing written)")
    args = ap.parse_args()

    sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent.parent
                           / "desktop" / "backend"))
    import ble_bridge                                        # noqa: PLC0415

    bridge = ble_bridge.BleBridge()
    status = bridge.status()
    if not status.get("handshake_done"):
        print("no BLE connection with a completed handshake - connect first")
        return 1
    bridge.set_dry_run(False)
    model = status.get("device_info", {}).get("model")

    key, _, raw_value = args.set.partition("=")
    if not args.write:
        current = read_settings(bridge, model)
        print(f"current: {current.get('settings', {}).get(key)!r}")
        print(f"would write: {raw_value!r} (pass --write to actually send it)")
        return 0
    try:
        value = float(raw_value) if "." in raw_value else int(raw_value)
    except ValueError:
        value = raw_value
    result = write_setting(bridge, key, value, model)
    print(result)
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
