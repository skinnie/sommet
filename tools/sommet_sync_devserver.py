#!/usr/bin/env python3
"""Sommet Sync — pure-Python reference server (stdlib only).

A faithful twin of sync-server/sync.php: same API, same SQLite schema, same last-writer-wins +
tombstone merge. Two uses:
  1. Local dev/CI backend so the protocol and clients can be tested without a PHP runtime
     (run tools/sommet_sync_smoketest.py against it).
  2. A self-host option for users who have Python but not PHP.

    python3 tools/sommet_sync_devserver.py --token <token> [--port 8777] [--db /path/sommet.db]

See docs/shared_app_db_design.md.
"""
import argparse
import hmac
import json
import os
import sqlite3
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

COLLECTIONS = {"activities", "gear", "gear_reminder", "gear_assignment", "activity_gear"}
MAX_BODY = 32 * 1024 * 1024


def now_ms():
    return int(time.time() * 1000)


def blob_name(uid, fmt):
    import hashlib
    fmt = fmt if fmt.isalnum() and len(fmt) <= 8 else "bin"
    return hashlib.sha256(uid.encode()).hexdigest() + "." + fmt


class Store:
    def __init__(self, db_path, blob_dir):
        self.db_path = db_path
        self.blob_dir = blob_dir
        os.makedirs(blob_dir, exist_ok=True)
        db = self._db()
        db.execute(
            "CREATE TABLE IF NOT EXISTS records ("
            " collection TEXT NOT NULL, uid TEXT NOT NULL, updated_at INTEGER NOT NULL,"
            " deleted INTEGER NOT NULL DEFAULT 0, json TEXT NOT NULL DEFAULT '',"
            " PRIMARY KEY (collection, uid))")
        db.execute("CREATE INDEX IF NOT EXISTS idx_records_since ON records(collection, updated_at)")
        db.commit()
        db.close()

    def _db(self):
        db = sqlite3.connect(self.db_path, timeout=5)
        db.execute("PRAGMA journal_mode=WAL")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def get(self, collection, since):
        db = self._db()
        rows = db.execute(
            "SELECT uid, updated_at, deleted, json FROM records WHERE collection=? AND updated_at>?",
            (collection, since)).fetchall()
        db.close()
        records, deleted = [], []
        for uid, ua, dele, js in rows:
            if dele == 1:
                deleted.append(uid)
            else:
                obj = json.loads(js) if js else {}
                obj["uid"] = uid
                obj["updated_at"] = ua
                records.append(obj)
        return {"records": records, "deleted": deleted, "now": now_ms()}

    def post(self, collection, body):
        records = body.get("records") or []
        tombs = body.get("deleted") or []
        now = now_ms()
        db = self._db()
        applied = 0
        try:
            for r in records:
                uid = str(r.get("uid") or "")
                if not uid:
                    continue
                ua = int(r.get("updated_at") or now)
                cur = db.execute("SELECT updated_at FROM records WHERE collection=? AND uid=?",
                                 (collection, uid)).fetchone()
                if cur is not None and int(cur[0]) > ua:
                    continue  # we hold a newer copy
                meta = {k: v for k, v in r.items() if k not in ("uid", "updated_at")}
                db.execute(
                    "INSERT INTO records (collection, uid, updated_at, deleted, json) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(collection, uid) DO UPDATE SET updated_at=excluded.updated_at, "
                    "deleted=excluded.deleted, json=excluded.json",
                    (collection, uid, ua, 0, json.dumps(meta)))
                applied += 1
            for uid in tombs:
                uid = str(uid)
                if not uid:
                    continue
                cur = db.execute("SELECT updated_at FROM records WHERE collection=? AND uid=?",
                                 (collection, uid)).fetchone()
                ua = max(int(cur[0]), now) if cur is not None else now
                db.execute(
                    "INSERT INTO records (collection, uid, updated_at, deleted, json) VALUES (?,?,?,?,?) "
                    "ON CONFLICT(collection, uid) DO UPDATE SET updated_at=excluded.updated_at, "
                    "deleted=excluded.deleted, json=excluded.json",
                    (collection, uid, ua, 1, ""))
                applied += 1
            db.commit()
        finally:
            db.close()
        return {"ok": True, "now": now, "applied": applied}


def make_handler(token, store):
    class H(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass  # quiet

        def _json(self, code, obj):
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(obj).encode())

        def _auth(self):
            got = self.headers.get("X-Sommet-Token", "")
            if not token or not hmac.compare_digest(token, got):
                self._json(401, {"error": "bad or missing token"})
                return False
            return True

        def _read(self):
            n = int(self.headers.get("Content-Length", 0) or 0)
            return self.rfile.read(min(n, MAX_BODY + 1)) if n else b""

        def do_GET(self):
            if not self._auth():
                return
            q = parse_qs(urlparse(self.path).query)
            if "blob" in q:
                uid = (q.get("uid") or [""])[0]
                fmt = (q.get("fmt") or ["gpx"])[0]
                if not uid:
                    return self._json(400, {"error": "blob needs uid"})
                path = os.path.join(store.blob_dir, blob_name(uid, fmt))
                if not os.path.isfile(path):
                    return self._json(404, {"error": "no blob"})
                data = open(path, "rb").read()
                self.send_response(200)
                self.send_header("Content-Type", "application/octet-stream")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            c = (q.get("c") or [""])[0]
            if c not in COLLECTIONS:
                return self._json(400, {"error": "unknown collection"})
            since = int((q.get("since") or ["0"])[0])
            self._json(200, store.get(c, since))

        def do_POST(self):
            if not self._auth():
                return
            q = parse_qs(urlparse(self.path).query)
            if "blob" in q:
                uid = (q.get("uid") or [""])[0]
                fmt = (q.get("fmt") or ["gpx"])[0]
                if not uid:
                    return self._json(400, {"error": "blob needs uid"})
                raw = self._read()
                if len(raw) > MAX_BODY:
                    return self._json(413, {"error": "blob too large"})
                open(os.path.join(store.blob_dir, blob_name(uid, fmt)), "wb").write(raw)
                return self._json(200, {"ok": True})
            c = (q.get("c") or [""])[0]
            if c not in COLLECTIONS:
                return self._json(400, {"error": "unknown collection"})
            raw = self._read()
            if len(raw) > MAX_BODY:
                return self._json(413, {"error": "body too large"})
            try:
                body = json.loads(raw or b"{}")
            except Exception:
                return self._json(400, {"error": "bad json"})
            self._json(200, store.post(c, body))
    return H


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--token", required=True)
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--db", default=os.path.join(os.path.dirname(__file__), "sommet_dev.db"))
    ap.add_argument("--blobs", default=None)
    a = ap.parse_args()
    blobs = a.blobs or (a.db + ".blobs")
    store = Store(a.db, blobs)
    srv = ThreadingHTTPServer(("127.0.0.1", a.port), make_handler(a.token, store))
    print("Sommet Sync dev server on http://127.0.0.1:%d  (db=%s)" % (a.port, a.db))
    srv.serve_forever()


if __name__ == "__main__":
    main()
