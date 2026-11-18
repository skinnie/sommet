# Sommet Sync server (#SYNC-1)

Self-hosted shared app database for Sommet. `sync.php` is the **sole writer** of its store, so
every device (Linux/Mac/Windows/phone) can share activities & gear safely over
HTTP. Design: `../docs/shared_app_db_design.md`. Merge model mirrors the app's proven Ember
`sync.php`: per-record `uid`, a `deleted` tombstone set, last-writer-wins by `updated_at`.

## Deploy on a Synology NAS (Web Station)

1. Enable **Web Station** + a **PHP** profile (PHP 7.4+).
2. Copy `sync.php` and `sommet_store.php` into a served folder, e.g. `/web/sommet/` → reachable
   at `http://<nas>/sommet/sync.php`.
3. `cp sommet_sync.config.sample.php sommet_sync.config.php` in that folder and set a token:
   ```
   openssl rand -hex 24
   ```
   Put the same token into each Sommet install: **Settings → Sync → My own server / NAS**.
4. Make sure the web user can write the folder (it creates `sommet-data/` next to the script on
   first request).

## Storage: JSON files or SQLite

Set `'store'` in `sommet_sync.config.php`:

| Value | What it does |
|---|---|
| `json` (default) | One JSON file per collection. Works on any PHP. Every request reads the whole file. |
| `sqlite` | One SQLite file (`sommet-data/sommet.db`). Reads and writes touch only the records involved. Needs **pdo_sqlite**: on Synology, Web Station → Script Language Settings → PHP → edit the profile → Extensions → tick `pdo_sqlite` and `sqlite3`. |
| `shadow` | Serves from JSON, then replays each request on SQLite and logs both timings and whether the answers matched to `sommet-data/shadow.log`. Use it to try SQLite before switching. |

The first SQLite request copies the JSON store in once (in one transaction). The JSON files are
left as they are, so going back to `json` is safe, though writes made in `sqlite` mode stay in
SQLite.

## Verify it works

From any machine that can reach the NAS:
```
python3 ../tools/sommet_sync_smoketest.py \
    --url http://<nas>/sommet/sync.php --token <your-token>
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
- `sommet_sync.config.php` and `sommet-data/` are git-ignored — never commit the token
  or the data.
- Blobs are addressed by `sha256(uid)` on disk, so a `uid` can never escape the `blobs/` dir.
