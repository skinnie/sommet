#!/usr/bin/env python3
"""Sommet Sync: a device pushes every record on every sync. A record pushed again unchanged (same
updated_at) must not come back in the next pull (2026-11-12 review: the server accepted the
re-push and stamped a new receive time, so every pull returned the whole collection). A real edit
(newer updated_at) still goes through. Runs against the Python twin of sync-server/sync.php.
"""
import os
import shutil
import sys
import tempfile
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from sommet_sync_devserver import Store  # noqa: E402


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
        recs = [{"uid": f"move-{i}", "updated_at": 1_700_000_000_000 + i, "name": "Run"} for i in range(3)]
        st.post("activities", {"records": recs})
        time.sleep(0.005)
        cursor = st.get("activities", 0)["now"]
        time.sleep(0.005)
        # Device A syncs again: pushes all three, unchanged.
        st.post("activities", {"records": recs})
        check(uids(st.get("activities", cursor)) == set(),
              "unchanged re-push came back in the pull (whole collection resent)")
        # A real edit on one of them still reaches the others.
        time.sleep(0.005)
        edited = dict(recs[1], updated_at=recs[1]["updated_at"] + 60_000, name="Trail run")
        st.post("activities", {"records": [edited] + [recs[0], recs[2]]})
        check(uids(st.get("activities", cursor)) == {"move-1"}, "an edit must still be delivered")
        print("ok: unchanged re-push not resent; edits still delivered")
    finally:
        shutil.rmtree(d, ignore_errors=True)


if __name__ == "__main__":
    main()
