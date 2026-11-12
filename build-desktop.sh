#!/usr/bin/env bash
# Builds the desktop app (Qt6/QML) and prints the resulting binary's path.
# Real build command, from desktop/README.md - see that file for the full prerequisites
# (Qt 6.5+, CMake) if this fails on a fresh machine.
#
#   ./build-desktop.sh
set -euo pipefail
cd "$(dirname "$0")/desktop"

# On Debian/Ubuntu/Mint, check the system Qt first and say exactly what to install, instead of
# failing halfway (or building an app that runs with blank icons). Skipped when you point CMake at
# your own Qt (CMAKE_PREFIX_PATH / Qt6_DIR, e.g. one from aqtinstall), or with SOMMET_SKIP_DEPS=1.
# Package list checked on Debian 13 (Qt 6.8.2), 2026-11-12.
own_qt=""   # an earlier build already set up against a Qt outside /usr
grep -qE '^Qt6_DIR:PATH=/' build/CMakeCache.txt 2>/dev/null \
  && ! grep -qE '^Qt6_DIR:PATH=/usr/' build/CMakeCache.txt && own_qt=1
if command -v dpkg-query >/dev/null && [ -z "${CMAKE_PREFIX_PATH:-}${Qt6_DIR:-}${SOMMET_SKIP_DEPS:-}$own_qt" ]; then
  need="build-essential cmake ca-certificates qt6-base-dev qt6-declarative-dev
    qt6-declarative-dev-tools qt6-tools-dev libqt6sql6-sqlite qt6-svg-plugins
    qml6-module-qtquick qml6-module-qtquick-controls qml6-module-qtquick-dialogs
    qml6-module-qtquick-layouts qml6-module-qtcore qml6-module-qtquick-window
    qml6-module-qtquick-templates qml6-module-qtqml-workerscript qml6-module-qtquick-effects
    qml6-module-qtquick-shapes"
  qtv=$(apt-cache policy qt6-base-dev 2>/dev/null | sed -n 's/ *Candidate: *\([0-9]*\.[0-9]*\).*/\1/p')
  if [ -n "$qtv" ] && [ "$(printf '%s\n6.5\n' "$qtv" | sort -V | head -1)" != "6.5" ]; then
    echo "This system's Qt is $qtv; Sommet needs 6.5 or newer (Debian 13, Ubuntu 25.04 and newer have it)."
    echo "Easiest: use the AppImage from https://github.com/skinnie/sommet/releases"
    echo "Or install Qt with aqtinstall and build with CMAKE_PREFIX_PATH=<that Qt> ./build-desktop.sh"
    echo "(don't also install the system qt6-* packages: two Qt copies in one build break it)."
    exit 1
  fi
  missing=""
  for p in $need; do
    dpkg-query -W -f='${Status}' "$p" 2>/dev/null | grep -q "install ok installed" || missing="$missing $p"
  done
  if [ -n "$missing" ]; then
    echo "Missing packages. Install them with:"
    echo "  sudo apt install$missing"
    echo "(or set SOMMET_SKIP_DEPS=1 to build anyway)"
    exit 1
  fi
fi

cmake -S . -B build
cmake --build build

echo ""
echo "Binary: $(pwd)/build/Sommet"
