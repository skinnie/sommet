# Sommet Sync server (#SYNC-1)

Self-hosted shared app database for Sommet — one SQLite file that `sync.php` is the **sole
writer** of, so every device (Linux/Mac/Windows/phone) can share activities & gear safely over
HTTP. Design: `../docs/shared_app_db_design.md`. Merge model mirrors the app's proven Ember
`sync.php`: per-record `uid`, a `deleted` tombstone set, last-writer-wins by `updated_at`.

## Deploy on a Synology NAS (Web Station)

1. Enable **Web Station** + a **PHP** profile (PHP 7.4+ with PDO-SQLite — the default profile has
   it; the same setup the Ember sync already uses).
2. Copy `sync.php` into a served folder, e.g. `/web/sommet/sync.php` → reachable at
   `http://192.168.1.102/sommet/sync.php`.
3. `cp sommet_sync.config.sample.php sommet_sync.config.php` in that folder and set a token:
   ```
   openssl rand -hex 24
   ```
   Put the same token into each Sommet install: **Settings → Sync → My own server / NAS**.
4. Make sure the web user can write the folder (it creates `sommet.db`, `sommet.db-wal`, and
   `blobs/` next to the script on first request).

The endpoint auto-creates its schema; there is no manual DB setup.

## Verify it works

From any machine that can reach the NAS:
```
python3 ../tools/sommet_sync_smoketest.py \
    --url http://192.168.1.102/sommet/sync.php --token <your-token>
```
Seven checks (auth, upsert, pull, last-writer-wins, incremental cursor, blob round-trip,
tombstone). All must print `ok`.

## API

Token in header `X-Sommet-Token`. Collections: `activities`, `gear`, `gear_reminder`,
`gear_assignment`, `activity_gear`.

| Call | Purpose |
|---|---|
| `GET  sync.php?c=<col>&since=<ms>` | records changed since `<ms>` → `{records, deleted, now}` |
| `POST sync.php?c=<col>` | body `{records:[…], deleted:[uid…]}` → upsert (LWW) |
| `GET  sync.php?blob=1&uid=<uid>&fmt=gpx` | download a track blob |
| `POST sync.php?blob=1&uid=<uid>&fmt=gpx` | upload a track blob (raw body) |

## Security notes

- LAN-only by default. To reach it from the phone off home wifi, put it behind the Synology
  reverse proxy with **HTTPS** (+ DDNS/QuickConnect), or join NAS + phone on **Tailscale**
  (#SYNC-5). Never expose it over plain HTTP.
- `sommet_sync.config.php`, `sommet.db*` and `blobs/` are git-ignored — never commit the token
  or the data.
- Blobs are addressed by `sha256(uid)` on disk, so a `uid` can never escape the `blobs/` dir.
