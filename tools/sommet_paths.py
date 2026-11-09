"""Moves the folders Sommet kept under its old name (AmbitApp, until 0.2.66) to their new names,
once. The desktop app does the same at start-up (desktop/src/services/apppaths.h); this copy is
for the backend and the tools when they run on their own.

    ~/AmbitAppBackups      -> ~/SommetBackups
    ~/AmbitAppPlans        -> ~/SommetPlans
    ~/.config/ambit-app    -> ~/.config/Sommet   (known_watches.json)
    ~/.cache/AmbitApp      -> ~/.cache/Sommet    (Bluetooth log, Karoo captures)

A plain rename when only the old folder exists. When both exist (say a tool already made the
new one), the old folder's contents are moved in item by item; an item that exists on both sides
is left in the old folder rather than overwritten, so nothing is ever lost. Never raises: if a
move fails the old folder simply stays where it is and keeps working as a fallback.
"""

import os
import pathlib
import shutil

HOME = pathlib.Path.home()
MOVES = [
    (HOME / "AmbitAppBackups", HOME / "SommetBackups"),
    (HOME / "AmbitAppPlans", HOME / "SommetPlans"),
    (HOME / ".config" / "ambit-app", HOME / ".config" / "Sommet"),
    (HOME / ".cache" / "AmbitApp", HOME / ".cache" / "Sommet"),
]


def _merge(old, new):
    for item in list(old.iterdir()):
        target = new / item.name
        if not target.exists() and not target.is_symlink():
            shutil.move(str(item), str(target))
        elif item.is_dir() and not item.is_symlink() and target.is_dir():
            _merge(item, target)
    try:
        old.rmdir()                  # only if it ended up empty
    except OSError:
        pass


def move_old_folders():
    for old, new in MOVES:
        try:
            if not old.is_dir() or old.is_symlink():
                continue
            if not new.exists():
                new.parent.mkdir(parents=True, exist_ok=True)
                os.rename(old, new)
            elif new.is_dir():
                _merge(old, new)
        except OSError:
            continue


def backups_dir():
    move_old_folders()
    return HOME / "SommetBackups"
