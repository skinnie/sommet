#!/usr/bin/env python3
"""Planned workouts on a Wahoo ELEMNT from Sommet - send / list / delete - over the USB cable or
Bluetooth, without the Wahoo companion app or a training-site account (André, 2026-10-03: item 4
of the Wahoo plan, "be sure to have them usb and bluetooth").

    ./tools/wahoo_workout.py encode plan.json                      # dry run: print the .plan
    ./tools/wahoo_workout.py send   [--via auto|usb|ble] plan.json [--name "VO2 5x4"]
    ./tools/wahoo_workout.py list   [--via ...]
    ./tools/wahoo_workout.py delete [--via usb] "VO2 5x4"
    ./tools/intervals_workout.py W1.json | ./tools/wahoo_workout.py send -

Input is the project's workout schema (bryton_from_intervals.py documents it: steps with
duration time s / distance m, targets power W, hr bpm, cadence rpm, repeatStart/repeatEnd).

How the ELEMNT (BoltApp 1.77.10.1) takes workouts, worked out 2026-10-03 on the device:
  * Its plan provider "StdPlanProviderSdFolder" (provider type 3) imports every .plan (and ERG /
    MRC / FIT workout) in /sdcard/plans when plans sync, into files/plans/3/ and the CloudPlanDao
    table of BoltApp.sqlite; the plan's id is the file name. A file that disappears from
    /sdcard/plans is deleted from the ELEMNT on the next sync ("checkImportPlans plan gone").
  * Plans sync through the same adb receiver as routes: StdProviderSyncManager.SYNC_LOCAL_PROVIDERS
    with --es category Plans. Over Bluetooth there is no trigger: the ELEMNT imports it when the
    rider presses SYNC on its Workouts screen, or at its next start.
  * .plan is Wahoo's own text format (the ELEMNT's built-in plans are written in it):
        =HEADER=  NAME= DURATION=<s> PLAN_TYPE=0 WORKOUT_TYPE=0 (bike) DESCRIPTION=
        =STREAM=
        =INTERVAL=  INTERVAL_NAME=  <targets>  MESG_DURATION_SEC>=<s>?EXIT  (or MESG_DISTANCE_M)
        a repeat: =INTERVAL= REPEAT=<n> MESG_DURATION_SEC>=0?EXIT, then its steps as =SUBINTERVAL=
    Targets (keywords from libCruxAndroid's plan grammar): PWR_LO/HI watts, PERCENT_FTP_LO/HI,
    HR_LO/HI bpm, CAD_LO/HI rpm. Speed (SPD_LO/HI) exists too but its unit isn't confirmed, so
    speed / pace targets are sent as no target (with a warning) rather than guessed.
"""

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wahoo_pages as P        # noqa: E402 - shared adb helpers

APP = "/data/data/com.wahoofitness.bolt"
DB = APP + "/databases/BoltApp.sqlite"
SD_PLANS = "/sdcard/plans"
IMPORTED = APP + "/files/plans/3"
SD_PROVIDER = 3
SYNC_PLANS = ("am broadcast -a com.wahoofitness.support.cloud.StdProviderSyncManager.SYNC_LOCAL_PROVIDERS"
              " --es category Plans")
TARGET_KEYS = {"power": "PWR", "hr": "HR", "cadence": "CAD"}
PHASE_NAMES = {"warmup": "Warm up", "interval": "Interval", "work": "Interval", "active": "Interval", "cooldown": "Cool down", "recovery": "Recovery", "rest": "Rest"}


def _warn(msg):
    print("wahoo_workout: %s" % msg, file=sys.stderr)


def safe_name(name):
    name = re.sub(r"[^\w .,()'%+-]", "", (name or "").strip(), flags=re.UNICODE)[:60].strip()
    if not name:
        raise ValueError("workout name is empty")
    return name


# ---- encoding ---------------------------------------------------------------------------------

def _one_line(text):
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _step_lines(st):
    """(lines, seconds or None) for one leaf step."""
    dur = st.get("duration") or {}
    value = float(dur.get("value") or 0)
    if value <= 0:
        raise ValueError("step has no duration: %r" % (st,))
    lines = []
    phase = (st.get("type") or {}).get("typeName", "")
    name = _one_line(st.get("name") or st.get("description") or PHASE_NAMES.get(phase, ""))
    if name:
        lines.append("INTERVAL_NAME=" + name[:40])
    target = st.get("target") or {}
    tname = target.get("targetName")
    key = TARGET_KEYS.get(tname)
    if key:
        rng = target.get("valueRange") or {}
        lo, hi = rng.get("min"), rng.get("max")
        if lo is None and hi is None:
            lo = hi = target.get("value")
        lo = hi if lo is None else lo
        hi = lo if hi is None else hi
        if lo is not None:
            lo, hi = sorted((int(round(float(lo))), int(round(float(hi)))))
            lines += ["%s_LO=%d" % (key, lo), "%s_HI=%d" % (key, hi)]
    elif tname not in (None, "none", "open"):
        _warn("%s target isn't sent to the ELEMNT (unit unconfirmed); step has no target" % tname)
    if dur.get("durationName") == "distance":
        lines.append("MESG_DISTANCE_M>=%d?EXIT" % int(round(value)))
        return lines, None
    secs = int(round(value))
    lines.append("MESG_DURATION_SEC>=%d?EXIT" % secs)
    return lines, secs


def _blocks(steps):
    """Project steps -> [("step", st) | ("repeat", n, [st...])], one level of repeats like the
    ELEMNT's own plans (INTERVAL with REPEAT + SUBINTERVALs)."""
    out, i = [], 0
    while i < len(steps):
        st = steps[i]
        tn = (st.get("type") or {}).get("typeName")
        if tn == "repeatStart":
            n = int((st.get("type") or {}).get("value", 1))
            inner, j = [], i + 1
            while j < len(steps) and (steps[j].get("type") or {}).get("typeName") != "repeatEnd":
                if (steps[j].get("type") or {}).get("typeName") == "repeatStart":
                    raise ValueError("nested repeats are not supported")
                inner.append(steps[j])
                j += 1
            out.append(("repeat", n, inner))
            i = j + 1
        elif tn == "repeatEnd":
            i += 1
        else:
            out.append(("step", st))
            i += 1
    return out


def encode(workout, name=None):
    """Project-schema workout -> .plan text."""
    blocks = _blocks(workout.get("steps") or [])
    if not blocks:
        raise ValueError("workout has no steps")
    body, total, timed = [], 0, True
    for b in blocks:
        if b[0] == "step":
            lines, secs = _step_lines(b[1])
            body += ["", "=INTERVAL="] + lines
            timed = timed and secs is not None
            total += secs or 0
        else:
            _tag, n, inner = b
            body += ["", "=INTERVAL=", "REPEAT=%d" % n, "MESG_DURATION_SEC>=0?EXIT"]
            for st in inner:
                lines, secs = _step_lines(st)
                body += ["", "=SUBINTERVAL="] + lines
                timed = timed and secs is not None
                total += (secs or 0) * n
    head = ["=HEADER=", "NAME=" + _one_line(name or workout.get("name") or "Workout")[:60]]
    if timed:
        head.append("DURATION=%d" % total)
    head += ["PLAN_TYPE=0", "WORKOUT_TYPE=0"]
    desc = _one_line(workout.get("description"))
    if desc:
        head.append("DESCRIPTION=" + desc[:1000])
    return "\n".join(head + ["", "=STREAM="] + body) + "\n"


# ---- USB --------------------------------------------------------------------------------------

def _db_rows(serial):
    with tempfile.TemporaryDirectory() as tmp:
        local = os.path.join(tmp, "BoltApp.sqlite")
        with open(local, "wb") as fh:
            fh.write(P._adb("exec-out", "cat " + DB, serial=serial, text=False)[1])
        con = sqlite3.connect(local)
        rows = con.execute("select name, providerId, providerType, planWorkoutType, isDeleted, "
                           "scheduledStartTimeMs from CloudPlanDao").fetchall()
        con.close()
    return [{"name": r[0], "file": r[1], "provider": r[2], "sport": r[3], "deleted": bool(r[4]),
             "scheduled": r[5] or 0, "fromSommet": r[2] == SD_PROVIDER} for r in rows]


def _usb_list(serial):
    return [r for r in _db_rows(serial) if not r["deleted"]]


def _usb_sync(serial):
    P._adb("shell", SYNC_PLANS, serial=serial)


def _usb_send(serial, text, name):
    with tempfile.NamedTemporaryFile("w", suffix=".plan", delete=False) as fh:
        fh.write(text)
    tmp = "/data/local/tmp/sommet_workout.plan"
    try:
        if P._adb("push", fh.name, tmp, serial=serial, timeout=60)[0] != 0:
            raise RuntimeError("adb push failed")
    finally:
        os.unlink(fh.name)
    P._adb("shell", 'mkdir -p %s; cat %s > "%s/%s.plan"; rm %s' % (SD_PLANS, tmp, SD_PLANS, name, tmp),
           serial=serial)
    _usb_sync(serial)
    for _ in range(20):
        time.sleep(1.5)
        rows = [r for r in _usb_list(serial) if r["provider"] == SD_PROVIDER and r["file"] == name]
        if rows:
            return rows[0]
    raise RuntimeError("the ELEMNT didn't import the workout (it may not accept this .plan)")


def _usb_delete(serial, name):
    mine = [r for r in _usb_list(serial) if r["provider"] == SD_PROVIDER and name in (r["file"], r["name"])]
    if not mine:
        raise RuntimeError("no workout named %r that Sommet put on the ELEMNT" % name)
    for r in mine:
        P._adb("shell", 'rm -f "%s/%s.plan"' % (SD_PLANS, r["file"]), serial=serial)
    _usb_sync(serial)
    for _ in range(15):
        time.sleep(1.5)
        if not any(r["provider"] == SD_PROVIDER and name in (r["file"], r["name"])
                   for r in _usb_list(serial)):
            return {"deleted": name}
    raise RuntimeError("the workout is still listed after the sync")


# ---- Bluetooth --------------------------------------------------------------------------------

def _ble_tool(*args, timeout=240):
    here = os.path.dirname(os.path.abspath(__file__))
    out = subprocess.run([sys.executable, os.path.join(here, "wahoo_ble_files.py"), *args],
                         capture_output=True, text=True, timeout=timeout, stdin=subprocess.DEVNULL)
    res = json.loads(out.stdout.strip().splitlines()[-1]) if out.stdout.strip() else {"ok": False}
    if not res.get("ok"):
        raise RuntimeError(res.get("error") or "Bluetooth file transfer failed")
    return res


def _ble_list():
    imported = {os.path.splitext(f["name"])[0] for f in _ble_tool("list", IMPORTED + "/")["files"]
                if not f["dir"]}
    pending = [os.path.splitext(f["name"])[0] for f in _ble_tool("list", SD_PLANS + "/")["files"]
               if not f["dir"] and f["name"].lower().endswith(".plan")]
    out = [{"name": n, "file": n, "fromSommet": True} for n in sorted(imported)]
    out += [{"name": n, "file": n, "fromSommet": True, "pendingSync": True}
            for n in pending if n not in imported]
    return out


def _ble_send(text, name):
    with tempfile.NamedTemporaryFile("w", suffix=".plan", delete=False) as fh:
        fh.write(text)
    try:
        res = _ble_tool("send", fh.name, "%s/%s.plan" % (SD_PLANS, name))
    finally:
        os.unlink(fh.name)
    return {"name": name, "file": name, "bytes": res.get("bytes"), "pendingSync": True,
            "note": "On the ELEMNT: Workouts -> SYNC to import it (or it imports at next power-on)."}


# ---- front door -------------------------------------------------------------------------------

def _pick(via):
    if via in ("auto", "usb"):
        s = P.find_serial()
        if s:
            return "usb", s
        if via == "usb":
            raise RuntimeError("No Wahoo ELEMNT on adb - press power twice and re-plug the USB cable")
    return "ble", None


def _load(path):
    return json.load(sys.stdin if path == "-" else open(path))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("command", choices=["encode", "send", "list", "delete"])
    ap.add_argument("args", nargs="*")
    ap.add_argument("--name")
    ap.add_argument("--via", choices=["auto", "usb", "ble"], default="auto")
    a = ap.parse_intermixed_args()
    if a.command == "encode":
        sys.stdout.write(encode(_load(a.args[0]), a.name))
        return 0
    try:
        if a.command == "send":
            workout = _load(a.args[0])                 # before any adb call (adb eats stdin)
            name = safe_name(a.name or workout.get("name") or "Workout")
            text = encode(workout, name)
        how, serial = _pick(a.via)
        if a.command == "list":
            out = {"workouts": _usb_list(serial) if how == "usb" else _ble_list()}
        elif a.command == "send":
            out = {"workout": _usb_send(serial, text, name) if how == "usb" else _ble_send(text, name)}
        else:
            if how != "usb":
                raise RuntimeError("deleting a workout needs the USB cable")
            out = _usb_delete(serial, safe_name(a.args[0]))
        out.update(ok=True, via=how)
    except Exception as e:                     # noqa: BLE001 - one JSON error line for the UI
        out = {"ok": False, "error": str(e)}
    print(json.dumps(out))
    return 0 if out.get("ok") else 1


if __name__ == "__main__":
    sys.exit(main())
