#!/usr/bin/env python3
"""Sommet Sync: a record uploaded LATE with an OLD updated_at still reaches devices that already
pulled (2026-11-08). Runs against the Python twin of sync-server/sync.php (same rules).

The bug: pulls filtered on the device's own updated_at, so a move read off a watch by one device
and pushed later (carrying the read's timestamp) was older than every other device's since-cursor
and was never delivered - ~35 watch moves and 81 eTrex rides sat on the NAS but not on the ThinkPad.
"""
import os
import shutil
import sys
import tempfile
import time

from sommet_sync_devserver import Store


def check(cond, msg):
    if not cond:
        print("FAIL:", msg)
        sys.exit(1)


def uids(resp):
    return {r["uid"] for r in resp["records"]}


def main():
    d = tempfile.mkdtemp()
    try:
        st = Store(os.path.join(d, "s.db"), os.path.join(d, "blobs"))
        day = 86_400_000

        # Device B syncs: nothing yet; it keeps the server's `now` as its cursor.
        cursor = st.get("activities", 0)["now"]
        time.sleep(0.005)

        # Device A uploads a move it read off the watch 3 days ago (old updated_at).
        old = cursor - 3 * day
        st.post("activities", {"records": [{"uid": "move-1", "updated_at": old, "name": "Pool swimming"}]})

        got = st.get("activities", cursor)
        check("move-1" in uids(got), "a late upload with an old updated_at must reach a device that already pulled")
        check([r["updated_at"] for r in got["records"] if r["uid"] == "move-1"] == [old],
              "the record's own updated_at is passed through unchanged")
        cursor = got["now"]
        time.sleep(0.005)

        # Last-writer-wins is unchanged: an older edit doesn't replace a newer one...
        st.post("activities", {"records": [{"uid": "move-1", "updated_at": old - day, "name": "stale"}]})
        check("move-1" not in uids(st.get("activities", cursor)), "an older edit must not be re-sent as a change")
        # ...and a newer edit does, and is delivered.
        st.post("activities", {"records": [{"uid": "move-1", "updated_at": old + day, "name": "renamed"}]})
        got = st.get("activities", cursor)
        check([r["name"] for r in got["records"] if r["uid"] == "move-1"] == ["renamed"], "newer edit delivered")
        cursor = got["now"]
        time.sleep(0.005)

        # Deletions still propagate through the cursor.
        st.post("activities", {"deleted": ["move-1"]})
        check("move-1" in st.get("activities", cursor)["deleted"], "tombstone delivered")

        # A full pull (since=0) returns everything that's live.
        st.post("activities", {"records": [{"uid": "move-2", "updated_at": old}]})
        check(uids(st.get("activities", 0)) == {"move-2"}, "full pull")
    finally:
        shutil.rmtree(d)
    print("ok")


if __name__ == "__main__":
    main()
