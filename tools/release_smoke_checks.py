"""Endpoint checks for tools/release_smoke.sh. Runs ON the test machine with its system python3
(stdlib only), against the release's own backend on 127.0.0.1:<port>.

    python3 -I release_smoke_checks.py <port> <smoke.fit.b64>

Each check calls one feature the way the app does and accepts the answers a healthy backend
gives without a device or an account (e.g. "no device found"), but not a crash, a 5xx from a
missing module, or a CPU-instruction error. Exit 0 = all passed.
"""
import json
import sys
import urllib.error
import urllib.request

PORT, FIT_B64 = sys.argv[1], open(sys.argv[2]).read().strip()
BASE = f"http://127.0.0.1:{PORT}"
BAD = ("ModuleNotFoundError", "No module named", "Illegal instruction", "X86_V2", "x86-64-v2",
       "Traceback", "ImportError")


def call(method, path, body=None, timeout=120):
    data = None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data=data, method=method,
                                 headers={"Content-Type": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except Exception as e:  # noqa: BLE001 - a dead backend is a failure, report it
        return 0, f"{e.__class__.__name__}: {e}"


def sleep_rr():
    # 3 hours of plausible R-R intervals (ms): enough for sleep_stage.py to run its numpy path.
    rr, t = [], 0
    while t < 3 * 3600 * 1000:
        v = 1000 + (len(rr) % 7) * 12 - (len(rr) % 11) * 9
        rr.append(v)
        t += v
    return {"start_time": "2026-11-08T23:00:00", "rr_ms": rr}


def streams_ok(status, text):
    try:
        return status == 200 and json.loads(text).get("ok") is True
    except ValueError:
        return False


def no_watch_ok(status, text):
    # With no watch on the cable the tool exits with "no supported Suunto watch" - that is the
    # healthy answer here (the tool ran inside the package); anything else in stderr is not.
    try:
        o = json.loads(text)
    except ValueError:
        return False
    return status == 200 or (o.get("noWatch") is True and "no supported Suunto watch" in o.get("stderr", "")
                             and not any(b in o.get("stderr", "") for b in BAD[:5]))


CHECKS = [
    # label, method, path, body, accepted statuses, extra test on (status, text)
    ("watch list", "GET", "/api/devices", None, {200}, None),
    ("watch settings tool (no watch plugged)", "GET", "/api/settings", None, None, no_watch_ok),
    ("FIT decode (activity charts)", "POST", "/api/activity/streams",
     {"fit_base64": FIT_B64, "points": 200}, {200}, streams_ok),
    ("sleep / HRV analysis (numpy)", "POST", "/api/sleep/process", sleep_rr(), {200, 422}, None),
    ("Bryton backups list", "GET", "/api/bryton/backups", None, {200}, None),
    ("watch backups list", "GET", "/api/backups", None, {200}, None),
    ("Bluetooth status", "GET", "/api/ble/status", None, {200}, None),
]

fail = 0
for label, method, path, body, okset, extra in CHECKS:
    status, text = call(method, path, body)
    if okset is None:                  # the extra test alone decides (expected tool errors)
        bad, good = None, extra(status, text)
    else:
        bad = next((b for b in BAD if b in text), None)
        good = status in okset and not bad and (extra is None or extra(status, text))
    if good:
        print(f"  ok    {label}")
    else:
        fail = 1
        why = f"HTTP {status}" + (f", '{bad}' in reply" if bad else "")
        print(f"  FAIL  {label} ({why})")
        print("        " + text[-400:].replace("\n", "\n        "))
sys.exit(fail)
