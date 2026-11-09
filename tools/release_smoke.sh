#!/usr/bin/env bash
# Smoke test of a published Linux release on a real (ideally old) machine, before the release is
# announced. Two release bugs reached users only when they touched the feature: a Garmin library
# that was never bundled, and a numpy that needs a newer CPU than a Core 2. The build passed both
# times because nothing ever ran the *packaged* app on *old hardware*. This does.
#
#     tools/release_smoke.sh <ssh-host> [version]      # version like 0.2.66; default: latest
#     SMOKE_BACKEND=<path to a local sommet-backend> tools/release_smoke.sh <ssh-host>
#         tests an unreleased backend build BEFORE tagging (it still takes the shared libraries
#         from the latest release's AppImage).
#
# On <ssh-host> it downloads the release AppImage from GitHub, unpacks only its backend, and runs
# that backend on a spare port with a throwaway home folder - an open Sommet and the user's data
# there are not touched. Then it calls each feature once and prints pass/fail. Exit 0 = all passed.
# Needs on the host: bash, curl, python3 (stdlib only). Nothing is installed there.
set -euo pipefail
HOST="${1:?usage: tools/release_smoke.sh <ssh-host> [version]}"
VER="${2:-}"
REPO="skinnie/sommet"
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

if [ -z "$VER" ]; then
    VER="$(gh release view --repo "$REPO" --json tagName -q .tagName)"
fi
VER="v${VER#v}"
URL="https://github.com/$REPO/releases/download/$VER/Sommet-linux-x86_64.AppImage"
echo "Release smoke test: $VER on $HOST"

# What goes to the host: the import probe, the endpoint checks, and a small FIT built here.
PAYLOAD="$(mktemp -d)"
trap 'rm -rf "$PAYLOAD"' EXIT
if [ -n "${SMOKE_BACKEND:-}" ]; then
    cp "$SMOKE_BACKEND" "$PAYLOAD/sommet-backend.local"
    echo "Testing the local backend $SMOKE_BACKEND (not the release's own)"
fi
cp "$ROOT/tools/packaging/check_frozen_imports.py" "$ROOT/tools/release_smoke_checks.py" "$PAYLOAD/"
(cd "$ROOT/tools" && python3 -I -c '
import base64, sys
sys.path.insert(0, ".")
import exercise_log as E
h = {"year": 2026, "month": 9, "day": 23, "hour": 18, "minute": 0, "msec": 0,
     "duration_ms": 600_000, "distance": 5000, "ascent": 0, "descent": 0,
     "activity_type": 17, "activity_name": "smoke", "heartrate_avg": 130, "heartrate_max": 160}
s = [{"type": "periodic", "time": t * 1000,
      "values": [{"type": 6, "name": "time", "value": t * 1000},
                 {"type": 1, "name": "hr", "value": 120 + t % 30},
                 {"type": 0, "name": "bikepower", "value": 180 + t % 40}]} for t in range(0, 600, 5)]
sys.stdout.write(base64.b64encode(E.to_fit(h, s)).decode())
') > "$PAYLOAD/smoke.fit.b64"

W="$(ssh "$HOST" 'mktemp -d /tmp/sommet-smoke.XXXXXX')"
tar -C "$PAYLOAD" -cf - . | ssh "$HOST" "tar -C '$W' -xf -"
set +e
ssh "$HOST" "URL='$URL' W='$W' bash -s" <<'REMOTE'
set -u
BPID=
trap '[ -n "$BPID" ] && kill "$BPID" 2>/dev/null; rm -rf "$W"' EXIT
cd "$W"
if ! curl -fsSL -o Sommet.AppImage "$URL"; then
    echo "  FAIL  download $URL"; exit 1
fi
chmod +x Sommet.AppImage
./Sommet.AppImage --appimage-extract 'usr/bin/backend/*' >/dev/null 2>&1
./Sommet.AppImage --appimage-extract 'usr/lib/libhidapi*' >/dev/null 2>&1
B="$W/squashfs-root/usr/bin/backend/sommet-backend"
# Releases up to v0.2.66 named it ambit-backend (the app's old name).
[ -x "$B" ] || B="$W/squashfs-root/usr/bin/backend/ambit-backend"
# Same environment the app gives its backend (backendprocess.cpp): the AppImage's usr/lib first.
export LD_LIBRARY_PATH="$W/squashfs-root/usr/lib${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}"
[ -x "$B" ] || { echo "  FAIL  no backend inside the AppImage"; exit 1; }
if [ -f "$W/sommet-backend.local" ]; then cp "$W/sommet-backend.local" "$B"; chmod +x "$B"; fi
echo "  ok    downloaded and unpacked ($(du -h Sommet.AppImage | cut -f1))"
echo "  host  $(grep -m1 'model name' /proc/cpuinfo | cut -d: -f2 | xargs) / $(. /etc/os-release; echo "$PRETTY_NAME")"

fail=0
# 1) Every lazily imported library is inside the package and loads on this CPU.
if out="$("$B" --tool "$W/check_frozen_imports.py" 2>&1)"; then
    echo "  ok    libraries load (Garmin, numpy, Bluetooth, serial, ...)"
else
    echo "  FAIL  libraries load"; printf '%s\n' "$out" | tail -8 | sed 's/^/        /'; fail=1
fi

# 2) The backend itself, on a spare port with a throwaway home.
mkdir -p "$W/home"
PORT=8799
HOME="$W/home" "$B" --port "$PORT" > "$W/backend.log" 2>&1 &
BPID=$!
for _ in $(seq 1 60); do
    curl -s -o /dev/null "http://127.0.0.1:$PORT/api/devices" && break
    kill -0 "$BPID" 2>/dev/null || break
    sleep 0.5
done
if ! kill -0 "$BPID" 2>/dev/null; then
    echo "  FAIL  backend starts"; tail -15 "$W/backend.log" | sed 's/^/        /'; exit 1
fi
echo "  ok    backend starts"
HOME="$W/home" python3 -I "$W/release_smoke_checks.py" "$PORT" "$W/smoke.fit.b64" || fail=1
exit $fail
REMOTE
rc=$?
echo
WHAT="Release $VER"
[ -n "${SMOKE_BACKEND:-}" ] && WHAT="Local backend (with $VER's libraries)"
[ $rc -eq 0 ] && echo "$WHAT passed on $HOST." || echo "$WHAT FAILED on $HOST."
exit $rc
