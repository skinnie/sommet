#!/usr/bin/env bash
# Brings up what the desktop app needs, then launches it:
#   1. the Python backend (desktop/backend/server.py, http://127.0.0.1:8766)
#   2. the Qt app
#
# Real, live gotcha found 2026-08-09: the Qt app never spawns the backend itself (checked -
# no QProcess for it anywhere in desktop/src/), so running build-desktop.sh's own binary
# directly shows "backend not running" even though the build is fine. This script exists so
# that doesn't happen again.
#
# (The offline BRouter route-planner was removed 2026-08-31 - the Plan page now takes a
# finished GPX and adds weather + climb, so there's no routing engine to start here.)
#
#   ./run-desktop.sh
set -euo pipefail
cd "$(dirname "$0")"

BACKEND_PID=""

# Python backend
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
}
trap cleanup EXIT

# The app. macOS builds a .app bundle; Linux/Windows build a bare binary - pick whichever
# this build produced (the bundle path was the real reason ./run-desktop.sh failed on the
# Mac, 2026-08-29).
if [ -x ./desktop/build/Sommet.app/Contents/MacOS/Sommet ]; then
  ./desktop/build/Sommet.app/Contents/MacOS/Sommet
elif [ -x ./desktop/build/Sommet ]; then
  ./desktop/build/Sommet
else
  echo "no built app found - run ./build-desktop.sh first" >&2
  exit 1
fi
