#!/usr/bin/env python3
"""Upload one recorded activity file (FIT/GPX/TCX) to intervals.icu.

Feature request (André, 2026-08-20): a toggle to push our watch activities to intervals.icu,
**Off / Manual / Automatic, default Off** — because people who use SuuntoLink already get their
moves into intervals via Suunto's own integration (so Auto would DUPLICATE for them), while
people who don't (and eTrex users) have no other path. Off is the safe default; Manual suits
eTrex; Auto covers non-SuuntoLink Suunto + eTrex hands-free.

This is the *upload* half only (the "push" direction). It pairs with:
  - exercise_log.py --fit-out / --gpx-out  (reads a recorded move off the watch -> a file)
  - intervals_stats.py                      (the *pull* direction: athlete stats -> watch)
and reuses intervals_stats.py's exact auth: HTTP Basic with the literal user "API_KEY" and the
personal API key as the password (intervals.icu's documented scheme).

Endpoint: POST /api/v1/athlete/{id}/activities/file  (multipart/form-data, field "file").
intervals.icu de-duplicates on the file's own timestamps, so re-uploading the same move is
harmless — but a move that ALSO arrives via SuuntoLink->Suunto->intervals can still show twice
(different source files), which is exactly why Auto defaults Off for SuuntoLink users.

    ./tools/intervals_upload.py ATHLETE_ID API_KEY move.fit
    ./tools/intervals_upload.py ATHLETE_ID API_KEY move.fit --name "Morning ride" --dry-run
"""
from __future__ import annotations
import argparse
import base64
import json
import mimetypes
import os
import sys
import urllib.request
import urllib.error

API_BASE = "https://intervals.icu/api/v1"


def _multipart(fields: dict[str, str], file_field: str, filename: str,
               data: bytes) -> tuple[bytes, str]:
    """Build a multipart/form-data body by hand (urllib has no multipart helper, and the rest
    of this project deliberately avoids the `requests` dependency - see intervals_stats.py)."""
    boundary = "----sommet" + base64.urlsafe_b64encode(os.urandom(9)).decode()
    ctype = mimetypes.guess_type(filename)[0] or "application/octet-stream"
    out = bytearray()
    for k, v in fields.items():
        out += f"--{boundary}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n".encode()
    out += (f"--{boundary}\r\nContent-Disposition: form-data; name=\"{file_field}\"; "
            f"filename=\"{filename}\"\r\nContent-Type: {ctype}\r\n\r\n").encode()
    out += data + b"\r\n"
    out += f"--{boundary}--\r\n".encode()
    return bytes(out), f"multipart/form-data; boundary={boundary}"


def upload(athlete_id: str, api_key: str, path: str,
           name: str | None = None, description: str | None = None) -> dict:
    """Uploads `path` to intervals.icu; returns the created-activity JSON on success."""
    with open(path, "rb") as f:
        data = f.read()
    fields: dict[str, str] = {}
    if name:
        fields["name"] = name
    if description:
        fields["description"] = description
    body, ctype = _multipart(fields, "file", os.path.basename(path), data)

    req = urllib.request.Request(
        f"{API_BASE}/athlete/{athlete_id}/activities/file", data=body, method="POST")
    token = base64.b64encode(f"API_KEY:{api_key}".encode()).decode()
    req.add_header("Authorization", f"Basic {token}")
    req.add_header("Content-Type", ctype)
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            raw = resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", "replace")[:300]
        raise SystemExit(f"upload failed: HTTP {e.code} {e.reason} - {detail}") from None
    except urllib.error.URLError as e:
        raise SystemExit(f"upload failed: no connection to intervals.icu ({e.reason})") from None
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("athlete_id")
    ap.add_argument("api_key")
    ap.add_argument("file", help="the activity file to upload (.fit / .gpx / .tcx)")
    ap.add_argument("--name")
    ap.add_argument("--description")
    ap.add_argument("--dry-run", action="store_true",
                    help="show what would be sent, upload nothing")
    ap.add_argument("--json", action="store_true", help="print the API response as JSON")
    args = ap.parse_args()

    if not os.path.isfile(args.file):
        ap.error(f"no such file: {args.file}")
    size = os.path.getsize(args.file)

    if args.dry_run:
        print(f"[dry-run] would POST {args.file} ({size} B) to "
              f"{API_BASE}/athlete/{args.athlete_id}/activities/file"
              + (f' as name={args.name!r}' if args.name else ""))
        return 0

    result = upload(args.athlete_id, args.api_key, args.file, args.name, args.description)
    if args.json:
        print(json.dumps(result))
    else:
        aid = result.get("id") or result.get("icu_id") or "?"
        print(f"uploaded {os.path.basename(args.file)} -> intervals.icu activity {aid}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
