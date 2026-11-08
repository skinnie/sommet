#!/usr/bin/env python3
"""Bryton Aero 60 paired-sensor read + sensors backup, without a device.

Built from a btsnoop capture of Bryton Active reading an Aero 60's sensors (2026-11-08; the capture
itself stays private, it holds device addresses). The request frames below are byte-for-byte what
the app sent; the reply layout is the device's, with the sensor id / MAC swapped for made-up ones.
"""
import json
import os
import shutil
import sys
import tempfile

import bryton_ble as B
import bryton_backup as K


def reply(bike, slot, hw, status, payload):
    body = [B.CMD_SENSOR_STATUS, 1, bike, slot, hw, status] + list(payload)
    body += [0] * (18 - len(body))                 # the device always answers 19 bytes
    return B._frame(*body)


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


def main():
    # Requests, exactly as captured from the app.
    check(B._frame(B.CMD_SENSOR_LIST, 2) == bytes.fromhex("2b022d"), "sensor-list request")
    check(B._frame(B.CMD_SENSOR_STATUS, 2, 0, 0) == bytes.fromhex("2c0200002e"), "bike1 slot0 request")
    check(B._frame(B.CMD_SENSOR_STATUS, 2, 0, 4) == bytes.fromhex("2c02000432"), "bike1 slot4 request")

    # ANT+ heart-rate strap, on: id is a little-endian u16 at bytes 6-7.
    s = B._decode_sensor(reply(0, 0, 0, 1, [0x34, 0x12]))
    check(s == {"bike": 0, "slot": 0, "type": "heartRate", "hw": "ant", "status": 1,
                "id": "4660", "paired": True}, f"ANT HR decode: {s}")

    # Empty slot: hw 0, status 0, id 0 -> not paired.
    s = B._decode_sensor(reply(1, 3, 0, 0, []))
    check(s["type"] == "speedCadence" and s["bike"] == 1 and not s["paired"], f"empty slot: {s}")

    # BLE sensor: the id is the MAC as ASCII, ':' every two characters, upper-cased.
    s = B._decode_sensor(reply(0, 4, 1, 1, b"aabbccddeeff"))
    check(s["type"] == "power" and s["hw"] == "ble" and s["id"] == "AA:BB:CC:DD:EE:FF" and s["paired"],
          f"BLE decode: {s}")

    # Sensors-only backup (Bluetooth, no USB): recorded, listed, never restored as screens.
    root = tempfile.mkdtemp()
    try:
        sj = os.path.join(root, "in.json")
        json.dump({"ok": True, "sensors": [B._decode_sensor(reply(0, 0, 0, 1, [0x34, 0x12])),
                                           B._decode_sensor(reply(0, 1, 0, 0, []))]}, open(sj, "w"))
        res = K.backup_ble("AA:BB:CC:DD:EE:FF", sj, root)
        check(res["ok"] and res["deviceId"] == "ble-AABBCCDDEEFF", f"backup_ble: {res}")
        check(res["manifest"]["sensors"] == [{"bike": 1, "type": "heartRate", "hw": "ant", "id": "4660"}],
              f"only paired sensors summarised: {res['manifest']['sensors']}")
        listed = K.list_backups(root)["backups"]
        check(len(listed) == 1 and listed[0]["hasSensors"] and listed[0]["screenCount"] == 0, f"list: {listed}")
        try:
            K.restore(root, res["path"])
            check(False, "restore of a sensors-only backup should refuse")
        except FileNotFoundError:
            pass
        bad = os.path.join(root, "bad.json")
        json.dump({"ok": False, "error": "x"}, open(bad, "w"))
        try:
            K.backup_ble("AA:BB:CC:DD:EE:FF", bad, root)
            check(False, "a failed sensors read must not become a backup")
        except ValueError:
            pass
    finally:
        shutil.rmtree(root)
    print("ok")


if __name__ == "__main__":
    main()
