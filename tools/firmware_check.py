#!/usr/bin/env python3
"""Checks Suunto's real, live device-info service for the latest firmware available for a
given watch, and optionally downloads it.

Real recipe, not derived here - a public gist by marguslt (an established contributor
already cited elsewhere in this project, e.g. HANDOFF.md's Finding 11/`training_program_
andre.md`): https://gist.github.com/marguslt/8cffaa78152503b29b91920de845e536
("suuntolink-firmware-download-links.ipynb"). Confirmed live and working 2026-08-07,
against both the gist's own example (Emu/69.2.17410) and this project's actual connected
reference watch's real hardware version.

Same host as the already-confirmed-unauthenticated AGPS orbital endpoint
(`devices.suunto-operations.com`, see `sgee_andre.md`) - but this one does need the app key
the gist itself embeds, a public web-app key rather than something derived by this project.

    ./tools/firmware_check.py                                # reads the connected watch
    ./tools/firmware_check.py --model Emu --hw 70.2.17414     # or ask about any model/hw
    ./tools/firmware_check.py --download firmware.bin
"""

import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

from device_info import read_device_info
from write_nav import Link

BASE_URL = "https://devices.suunto-operations.com/devices"
APP_KEY = "DbCBVqja20NKdrimBHQxtYIdczUJ56WHIWlC6A7vp6NPC0D0a8wA5d0ODyywFKe6"


def check_firmware(model, hw_version):
    query = urllib.parse.urlencode({"appkey": APP_KEY})
    url = f"{BASE_URL}/{urllib.parse.quote(model)}/{urllib.parse.quote(hw_version)}?{query}"
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        raise RuntimeError(f"firmware check failed: HTTP {e.code}") from None
    except urllib.error.URLError as e:
        raise RuntimeError(f"couldn't reach {BASE_URL}: {e.reason}") from None


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--model", help="codename e.g. Emu - skips reading the watch live")
    ap.add_argument("--hw", help="hardware version e.g. 70.2.17414 - skips reading the watch")
    ap.add_argument("--download", metavar="FILE", help="also download the firmware to FILE")
    ap.add_argument("--json", action="store_true",
                     help="print one JSON object instead of human-readable lines - for "
                          "desktop/backend/server.py, not meant for a person to read")
    args = ap.parse_args()

    current_fw = serial = None
    in_bsl = False
    if args.model and args.hw:
        model, hw = args.model, args.hw
    elif args.model or args.hw:
        ap.error("--model and --hw must be given together")
    else:
        link = Link(dry_run=False, verbose=not args.json)
        if not args.json:
            print("read-only: the 0x0000 query, nothing is written")
        link.open()
        info = read_device_info(link)
        # In the bootloader the model string reads "BSL", but the USB product_id still names
        # the real model - use it so a bricked watch fetches the right firmware even if it was
        # never connected to this app before.
        in_bsl = info["model"] == "BSL"
        model = (info["usb_model"] if in_bsl and info.get("usb_model") else info["model"])
        hw = info["hw_version"]
        current_fw, serial = info.get("fw_version"), info.get("serial")
        import watch_registry
        watch_registry.record(info)  # remember serial -> codename/hw for BSL recovery

    if not args.json:
        print(f"  checking {model} / {hw} ...")
    info = check_firmware(model, hw)

    downloaded_to = None
    if args.download:
        url = info.get("LatestFirmwareURI")
        if not url:
            if args.json:
                print(json.dumps({"ok": False, "error": "no download URL in the response"}))
            else:
                print("  no download URL in the response")
            return 1
        if not args.json:
            print(f"  downloading to {args.download} ...")
        urllib.request.urlretrieve(url, args.download)
        downloaded_to = args.download
        if not args.json:
            print(f"  wrote {args.download}")

    if args.json:
        print(json.dumps({
            "ok": True,
            "model": model,
            "in_bsl": in_bsl,
            "hw_version": hw,
            "serial": serial,
            "current_firmware": current_fw,
            "latest_firmware_version": info.get("LatestFirmwareVersion"),
            "upload_date": info.get("FirmwareUploadDate"),
            "release_type": info.get("ReleaseType"),
            "download_url": info.get("LatestFirmwareURI"),
            "downloaded_to": downloaded_to,
        }))
        return 0

    print(f"  latest firmware  {info.get('LatestFirmwareVersion')}")
    print(f"  uploaded         {info.get('FirmwareUploadDate')}")
    print(f"  release type     {info.get('ReleaseType')}")
    print(f"  download URL     {info.get('LatestFirmwareURI')}")
    if downloaded_to:
        # Despite the .zip name the file is NOT a standard zip (starts with the "SFI2ST"
        # magic; `unzip -l` fails). It is a plain [32-byte header][raw payload] container -
        # decoded 2026-08-08 and flashed straight to the watch by firmware_write.py, no
        # SuuntoLink needed. To flash what was just downloaded:
        #   ./tools/firmware_write.py <FILE> --expect-model <model> --commit
        print("  note: this is an SFI2ST firmware container (not a zip). Flash it with "
              "tools/firmware_write.py <file> --expect-model <model> --commit")

    return 0


if __name__ == "__main__":
    sys.exit(main())
