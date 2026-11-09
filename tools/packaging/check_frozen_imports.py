"""Run INSIDE the frozen backend after PyInstaller: `sommet-backend --tool check_frozen_imports.py`.

Imports every third-party package the tools load lazily (backend-requirements.txt), so a release
can't ship a backend that only fails when the user first touches the feature (Garmin login said
"No module named 'garminconnect'" on the v0.2.63 AppImage, 2026-11-08). Exits non-zero on a miss.
"""
import importlib
import sys

PACKAGES = ["garminconnect", "curl_cffi", "curl_cffi.requests", "ua_generator", "serial",
            "serial.tools.list_ports", "numpy", "bleak", "certifi"]

missing = []
for name in PACKAGES:
    try:
        importlib.import_module(name)
    except Exception as exc:  # noqa: BLE001 - report every failure, not just ImportError
        missing.append(f"{name}: {exc.__class__.__name__}: {exc}")

try:  # what tools/garmin_sync.py actually does on login
    from garminconnect import Garmin  # noqa: F401
except Exception as exc:  # noqa: BLE001
    missing.append(f"garminconnect.Garmin: {exc.__class__.__name__}: {exc}")

if missing:
    print("frozen backend is missing packages:\n  " + "\n  ".join(missing))
    sys.exit(1)
print("frozen backend imports OK: " + ", ".join(PACKAGES))
