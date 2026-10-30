#!/usr/bin/env python3
"""GPS prediction data for the Bryton Aero 60 (System/AssitOff.alp + System/date.txt), over USB.

Why (André, 2026-10-30): a GPS that knows where the satellites will be gets a fix in seconds;
without it, minutes. Bryton's tools keep this data fresh; without them it ages out. Sommet now
does it, the same way it keeps the Suunto watches' SGEE fresh.

What the device holds (checked on André's Aero 60, firmware R035): a u-blox "AlmanacPlus" file
(header magic 0x015062B5), the 14-day file u-blox publishes at
    https://alp.u-blox.com/current_14d.alp      (no account, no key; refreshed several times a day)
plus System/date.txt, 4 bytes, big-endian: the moment the data stops being good (Unix time).
The device firmware reads both files (strings \\System\\AssitOff.alp, \\System\\date.txt).

How Bryton's own USB tool (Bryton Update Tool) fills them: download current_14d.alp, date =
midnight (computer's local time) 14 days after today, remove the device's AssitOff.alp +
date.txt, copy the new file, write the date as one big-endian 32-bit number. Sommet does the same, with two additions: the date
is never later than the end the file itself covers, and both old files are saved first.

Safety: nothing is written unless the device already holds an AssitOff.alp of this same kind - a
missing file or anything else is left alone, with the reason. The old files go to
~/AmbitAppBackups/bryton-ee/<time>/, the new ones are written next to them and renamed into
place, then read back and compared.

    ./tools/bryton_ee.py status --mount M        what the device holds, and until when
    ./tools/bryton_ee.py update --mount M        dry run: downloads, checks, says what it would do
    ./tools/bryton_ee.py update --mount M --write
    ./tools/bryton_ee.py check FILE              decode any .alp file (no device)
All print one JSON line.
"""

import argparse
import datetime
import json
import os
import pathlib
import shutil
import struct
import sys
import time
import urllib.request

URL = "https://alp.u-blox.com/current_14d.alp"
DAYS = 14                                       # what Bryton's tool asks for (current_%1d, 14)
DEVICE_FILE = pathlib.Path("System") / "AssitOff.alp"
DATE_FILE = pathlib.Path("System") / "date.txt"
BACKUP_DIR = pathlib.Path.home() / "AmbitAppBackups" / "bryton-ee"
ALP_MAGIC = 0x015062B5
HEADER = 0x54
GPS_EPOCH = datetime.datetime(1980, 1, 6, tzinfo=datetime.timezone.utc)
LEAP_SECONDS = 18                               # GPS - UTC since 2017
MAX_BYTES = 2_000_000
UTC = datetime.timezone.utc


def decode(data):
    """Check a u-blox ALP file: magic, the length its header states, and the time span it covers.
    Returns {ok, bytes, start, end, days, first, last} (UTC ISO times; first/last = UTC days) or
    {ok: False, error}. Header (from real files): u32 magic @0, u16 length/4 @0x44,
    u32 GPS time-of-week s @0x4C, u16 GPS week @0x50, u16 span in 10-minute steps @0x52."""
    if len(data) < HEADER:
        return {"ok": False, "error": "too short to be u-blox GPS data"}
    if struct.unpack_from("<I", data, 0)[0] != ALP_MAGIC:
        return {"ok": False, "error": "not u-blox AlmanacPlus data"}
    words = struct.unpack_from("<H", data, 0x44)[0]
    if words * 4 != len(data):
        return {"ok": False, "error": f"file is {len(data)} bytes but its header says {words * 4}"}
    tow, week, span = struct.unpack_from("<IHH", data, 0x4C)
    if tow >= 7 * 86400 or not 0 < span <= 6 * 1008 or week < 1000:
        return {"ok": False, "error": "header times make no sense"}
    start = GPS_EPOCH + datetime.timedelta(weeks=week, seconds=tow - LEAP_SECONDS)
    end = start + datetime.timedelta(seconds=span * 600)
    return {"ok": True, "bytes": len(data), "start": start.isoformat(timespec="seconds"),
            "end": end.isoformat(timespec="seconds"), "days": round(span / 144, 1),
            "first": start.date().isoformat(), "last": end.date().isoformat()}


def read_date(data):
    """date.txt -> aware UTC datetime, or None when it isn't the 4-byte form."""
    if len(data) != 4:
        return None
    return datetime.datetime.fromtimestamp(struct.unpack(">I", data)[0], UTC)


def good_until(info, date_txt=None, now=None):
    """Adds validUntil / last / daysLeft / expired. The device goes by date.txt when it has one,
    so that wins; otherwise the end of the file. `last` is the last whole local day of data."""
    now = now or datetime.datetime.now(UTC)
    until = date_txt or (datetime.datetime.fromisoformat(info["end"]) if info.get("end") else None)
    if until is None:
        return info
    local_last = (until - datetime.timedelta(seconds=1)).astimezone().date()
    info["validUntil"] = until.isoformat(timespec="seconds")
    info["last"] = local_last.isoformat()
    info["daysLeft"] = max((local_last - now.astimezone().date()).days, 0)
    info["expired"] = now >= until
    return info


def new_date(info, now=None):
    """Bryton's rule: midnight, local time, DAYS days after today - capped at the file's own end."""
    now = now or datetime.datetime.now(UTC)
    day = now.astimezone().date() + datetime.timedelta(days=DAYS)
    midnight = datetime.datetime.combine(day, datetime.time()).astimezone()
    end = datetime.datetime.fromisoformat(info["end"])
    return min(midnight, end).astimezone(UTC).replace(microsecond=0)


def device_model(mount):
    try:
        text = (pathlib.Path(mount) / "System" / "device.txt").read_bytes().replace(b"\0", b"")
    except OSError:
        return None
    for line in text.decode("utf-8", "replace").splitlines():
        if line.strip().lower().startswith("model="):
            return line.split("=", 1)[1].strip()
    return None


def device_status(mount, now=None):
    path = pathlib.Path(mount) / DEVICE_FILE
    date_path = pathlib.Path(mount) / DATE_FILE
    base = {"ok": True, "path": str(path), "model": device_model(mount)}
    if not path.exists():
        return {**base, "present": False}
    info = decode(path.read_bytes())
    readable = info.pop("ok")
    info.update(base, present=True, readable=readable,
                modified=datetime.datetime.fromtimestamp(path.stat().st_mtime).isoformat(timespec="seconds"))
    if not readable:
        info["reason"] = info.pop("error")
        return info
    date_txt = read_date(date_path.read_bytes()) if date_path.exists() else None
    info["dateFile"] = date_txt.isoformat(timespec="seconds") if date_txt else None
    return good_until(info, date_txt, now)


def download(url=URL, timeout=60):
    req = urllib.request.Request(url, headers={"User-Agent": "Sommet"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        data = r.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ValueError("download is larger than any GPS prediction file")
    return data


def _write_replace(target, data):
    tmp = target.with_name(target.name + ".sommet-new")
    with open(tmp, "wb") as f:
        f.write(data)
        f.flush()
        os.fsync(f.fileno())
    os.replace(tmp, target)


def install(mount, data, write, now=None):
    """Put `data` on the device as System/AssitOff.alp with a matching System/date.txt. Refuses
    unless the new data is a valid, current ALP file AND the device already holds one."""
    now = now or datetime.datetime.now(UTC)
    new = decode(data)
    if not new["ok"]:
        return {"ok": False, "error": f"the downloaded file is not usable: {new['error']}"}
    if datetime.datetime.fromisoformat(new["end"]) <= now:
        return {"ok": False, "error": "the downloaded file is already out of date", "new": new}
    target = pathlib.Path(mount) / DEVICE_FILE
    date_target = pathlib.Path(mount) / DATE_FILE
    if not target.parent.is_dir():
        return {"ok": False, "error": f"{target.parent} not found - is this a Bryton?"}
    old = device_status(mount, now)
    if old.get("present") and not old.get("readable"):
        return {"ok": False, "old": old,
                "error": "the device's GPS file is in a format Sommet doesn't know, so it was "
                         "left alone"}
    if not old.get("present"):
        # Bryton's tool would add one; but with nothing there we can't tell that this model's
        # firmware reads it. Leave it to Bryton's tools.
        return {"ok": False, "old": old,
                "error": "the device has no GPS file yet, so Sommet doesn't add one"}
    until = new_date(new, now)
    new = good_until(dict(new), until, now)
    result = {"ok": True, "old": old, "new": new, "wrote": False}
    if old.get("dateFile") and datetime.datetime.fromisoformat(old["dateFile"]) >= until:
        result["note"] = "the device already has today's GPS data"
        return result
    if not write:
        result["dryRun"] = True
        return result
    date_bytes = struct.pack(">I", int(until.timestamp()))
    backup = BACKUP_DIR / time.strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup / target.name)
    if date_target.exists():
        shutil.copy2(date_target, backup / date_target.name)
    _write_replace(target, data)
    _write_replace(date_target, date_bytes)
    try:
        os.sync()
    except AttributeError:
        pass
    verified = target.read_bytes() == data and date_target.read_bytes() == date_bytes
    result.update(wrote=True, backup=str(backup), verified=verified)
    if not verified:
        result.update(ok=False, error="read-back differs from what was written")
    return result


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("status")
    s.add_argument("--mount", required=True)
    u = sub.add_parser("update")
    u.add_argument("--mount", required=True)
    u.add_argument("--write", action="store_true")
    u.add_argument("--from-file", help="use this file instead of downloading")
    c = sub.add_parser("check")
    c.add_argument("file")
    args = ap.parse_args(argv)
    try:
        if args.cmd == "status":
            out = device_status(args.mount)
        elif args.cmd == "check":
            out = decode(pathlib.Path(args.file).read_bytes())
            if out["ok"]:
                out = good_until(out)
        else:
            data = pathlib.Path(args.from_file).read_bytes() if args.from_file else download()
            out = install(args.mount, data, args.write)
    except Exception as exc:  # noqa: BLE001 - one JSON line, whatever went wrong
        out = {"ok": False, "error": str(exc)}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
