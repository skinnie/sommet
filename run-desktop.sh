#!/usr/bin/env bash
# Brings up everything the desktop app needs, then launches it:
#   1. BRouter  (offline routing engine, :17777) - so the Plan page can actually route
#   2. the Python backend (desktop/backend/server.py, http://127.0.0.1:8766)
#   3. the Qt app
#
# Real, live gotcha found 2026-08-09: the Qt app never spawns the backend itself (checked -
# no QProcess for it anywhere in desktop/src/), so running build-desktop.sh's own binary
# directly shows "backend not running" even though the build is fine. This script exists so
# that doesn't happen again. BRouter was added 2026-08-29 for the same reason - without it
# the Plan page draws a straight line between points instead of a real route.
#
#   ./run-desktop.sh
set -euo pipefail
cd "$(dirname "$0")"

BACKEND_PID=""
BROUTER_PID=""

# 1. BRouter (optional - only if it's installed). Skipped silently if not present, so the
#    script still works on a machine that hasn't set up routing yet.
BROUTER_START="${BROUTER_START:-$HOME/brouter/start-brouter.sh}"
if curl -s -o /dev/null http://127.0.0.1:17777/brouter 2>/dev/null; then
  echo "brouter already running on :17777"
elif [ -x "$BROUTER_START" ]; then
  echo "starting brouter..."
  "$BROUTER_START" >/tmp/sommet-brouter.log 2>&1 &
  BROUTER_PID=$!
  for _ in $(seq 1 40); do
    curl -s -o /dev/null http://127.0.0.1:17777/brouter 2>/dev/null && break
    sleep 0.25
  done
else
  echo "note: no BRouter at $BROUTER_START - the Plan page won't route until one is running"
  echo "      (see docs/offline-routing.md)"
fi

# 2. Python backend
if curl -s -o /dev/null -w "" http://127.0.0.1:8766/api/health 2>/dev/null; then
  echo "backend already running on :8766"
else
  echo "starting backend..."
  (cd desktop/backend && python3 server.py) &
  BACKEND_PID=$!
  for _ in $(seq 1 20); do
    curl -s -o /dev/null http://127.0.0.1:8766/api/health 2>/dev/null && break
    sleep 0.25
  done
fi

cleanup() {
  [ -n "$BACKEND_PID" ] && kill "$BACKEND_PID" 2>/dev/null || true
  [ -n "$BROUTER_PID" ] && kill "$BROUTER_PID" 2>/dev/null || true
}
trap cleanup EXIT

# 3. The app. macOS builds a .app bundle; Linux/Windows build a bare binary - pick whichever
#    this build produced (the bundle path was the real reason ./run-desktop.sh failed on the
#    Mac, 2026-08-29).
if [ -x ./desktop/build/Sommet.app/Contents/MacOS/Sommet ]; then
  ./desktop/build/Sommet.app/Contents/MacOS/Sommet
elif [ -x ./desktop/build/Sommet ]; then
  ./desktop/build/Sommet
else
  echo "no built app found - run ./build-desktop.sh first" >&2
  exit 1
fi
