#!/usr/bin/env python3
"""One Bluetooth job at a time (desktop/backend/server.py, P5Q 2026-11-21): the watch lock is
re-entrant for the thread holding it (a Bluetooth handler runs tools that take it again),
another thread waits, try_acquire() never waits, and every in-process Bluetooth handler but
the device check is wrapped in it."""
import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "desktop" / "backend"))
import server  # noqa: E402

lock = server._WatchLock()
assert lock.acquire() and lock.acquire()          # same thread: in again, no wait
order = []


def other():
    assert not lock.try_acquire()                 # held elsewhere: no wait, no
    lock.acquire()
    order.append("other")
    lock.release()


t = threading.Thread(target=other)
t.start()
time.sleep(0.1)
lock.release()                                    # one level left: still held
time.sleep(0.1)
assert order == []
order.append("first done")
lock.release()
t.join(2)
assert order == ["first done", "other"], order
assert lock.try_acquire()
lock.release()

wrapped = [n for n in vars(server.Handler) if n.startswith("_handle_") and n.endswith("_ble")
           and getattr(server.Handler, n).__code__.co_name == "run"]
assert "_handle_activities_ble" in wrapped and "_handle_pois_read_ble" in wrapped
assert "_handle_device_ble" not in wrapped
print(f"watch lock: re-entrant, try_acquire, {len(wrapped)} Bluetooth handlers take turns OK")
