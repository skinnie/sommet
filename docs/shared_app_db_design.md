# Sommet Sync — one shared app database across Linux / Mac / Windows / phone

Requested by André (2026-09-13): *"can't we have a db hosted somewhere (NAS / Dropbox /
whatever) that the app points to and reads?"* — keep intervals.icu, **and** add a self-hosted
shared store so all four Sommet installs see the same data. This is **Option B** (a NAS-hosted
shared store with merge), the phone included.

Related but different: `multi_source_sync_design.md` is about de-duplicating *upstream*
providers (Garmin/Suunto/intervals). This doc is about **converging André's own devices** onto
one store he owns.

---

## 0. Why not just put the .db in a Dropbox/NAS folder

A live SQLite file inside a Dropbox/Drive sync folder, or on an SMB/NFS share written directly
by the app, **corrupts** — sync services copy mid-write and network file locking is unreliable.
This is a documented SQLite failure mode, not a maybe. So the file is never shared directly.

**Instead:** the database lives on the NAS and a small endpoint is its *only* writer. Every
device talks to the endpoint over HTTP(S). One writer ⇒ no corruption; the store is still
"a database hosted on the NAS that the app points to," exactly as asked.

## 1. Architecture

```
  Linux  ┐
  macOS  ┤  Sommet (Qt + Python backend)  ─┐
  Windows┘                                  │  HTTPS + token
                                            ├────────────►  NAS 192.168.1.102
  Android/iPhone  Sommet (React Native) ────┘              /sommet/sync.php
                                                            └── sommet.db (SQLite, PHP is sole writer)
                                                            └── blobs/<uid>.gpx | .fit

  intervals.icu stays as-is  ◄── export/analysis hub (unchanged; NOT the fleet's source of truth)
```

- **Source of truth for the fleet = the NAS store.** intervals.icu remains an export target and
  the aggregator for *other* gear (Race S, Karoo, Garmin), exactly as today.
- Mirrors the proven **Ember `sync.php`** merge (`server.py:877-1002`, `EmberSync.ts`):
  per-record `uid`, a `deleted` tombstone set, union-merge, `X-…-Token` auth.

## 2. What syncs (collections)

App-**owned** state only. Things read live off the watch (POIs, routes, settings, sport modes)
are already identical across devices because it's the same watch — they are **not** in scope.

| Collection | Desktop today | Android today | Notes |
|---|---|---|---|
| `activities` | `activities.db` (GPX/FIT inline) | `ambitsync.db` + loose GPX files | metadata as records, track as a blob |
| `deleted_activities` | table (tombstones) | table (tombstones) | carried as the `deleted` set |
| `gear`, `gear_reminder`, `gear_assignment`, `activity_gear` | `gear.db` | `ambitsync.db` | local-first; already mirror intervals |

Wellness / sleep / HRV / planned workouts stay on intervals.icu (already shared). Ember keeps
its own store (already synced); may fold into this endpoint later.

## 3. The record model & the cross-platform `uid` (the linchpin)

Desktop keys activities `(idx, device)` (int); Android keys `id TEXT`; the schemas diverge
(GPX inline vs file, `ascent_m` vs `d_plus`). They can't share a file — a sync record bridges
them. Every record carries a **stable, platform-independent `uid`**:

- **Activity `uid` = `<device-serial>|<start-time-to-the-minute>`** (the same identity
  `dedupeActivities()` already collapses on — see `multi_source_sync_design.md §3`). A watch
  move read on Linux and the same move read on the phone produce the *same* uid ⇒ one record,
  no duplicate. Watch moves (no device tag historically) fall back to `start-minute` alone.
- **Gear `uid`** = the intervals `remote_id` when present, else the local uuid (Android `gear.id`
  already works this way).
- Each record also carries `updated_at` (ms). **Conflict = highest `updated_at` wins (LWW).**
  Safe here: activities are immutable once recorded; only name / gear-assignment get edited.

### Wire record (activity)
```json
{ "uid": "0x1b:12345|2026-09-13T07:14", "updated_at": 1789200000000,
  "name": "Morning run", "start_time": "2026-09-13T07:14:22Z",
  "duration_s": 3600, "distance_m": 10500, "ascent_m": 120,
  "energy_kcal": 640, "sport_type_raw": 3, "device": "0x1b:12345",
  "source": "watch", "external_id": null,
  "has_track": true, "track_fmt": "gpx" }
```
The GPX/FIT itself is **not** in the record — it's a blob fetched/pushed separately by `uid`, so
metadata syncs are tiny and a big track transfers once.

## 4. Protocol (PHP endpoint on the NAS)

Token auth: header `X-Sommet-Token: <token>`.

**Storage = flat JSON files, not SQLite** (decided at deploy, 2026-09-13). Synology's Web Station
PHP 8.3 profile ships with `pdo_sqlite` **disabled** and can't be toggled without fragile GUI/ini
edits — while the app's Ember `sync.php` already proves a file-based store (JSON + `LOCK_EX` +
atomic tmp→rename) is reliable on this exact NAS. So the endpoint stores one JSON file per
collection (`sommet-data/<collection>.json` = `{records:{uid:rec}, deleted:{uid:ts}}`) plus track
blobs as separate files (`sommet-data/blobs/<sha256(uid)>.<fmt>`), each write serialized by an
exclusive lock. Same wire protocol below, so clients and the smoke-test are unaffected. (The
Python reference server `tools/sommet_sync_devserver.py` keeps its SQLite storage — API-identical,
dev-only.)

**Deploy gotcha (fixed):** a folder created under Synology `/web` inherits an ACL granting the
web user `http` only `r-x`. Grant write with
`synoacltool -add <dir> user:http:allow:rwxpdDaARWcCo:fd--` (or create it "Linux mode" like the
ember folder). Verified: all 7 smoke-test checks pass against `http://192.168.1.102/sommet/sync.php`.

| Method / path | Purpose |
|---|---|
| `GET  /sync.php?c=<collection>&since=<ms>` | records with `updated_at > since` + `{deleted:[uid…]}` |
| `POST /sync.php?c=<collection>` | upsert `{records:[…], deleted:[uid…]}` (LWW by `updated_at`) |
| `GET  /sync.php/blob?uid=<uid>` | download the track blob (gpx/fit) |
| `POST /sync.php/blob?uid=<uid>&fmt=gpx` | upload a track blob (only when `has_track` & not present) |

Merge per client round (mirrors `_ember_sync`):
1. `GET ?since=<last_pull>` → for each remote record not locally present (or newer), upsert local;
   absorb remote tombstones and drop matching local rows.
2. Fetch blobs for new activities lacking a local track.
3. `POST` local records newer than `last_pull` + the local tombstone set; upload any missing blobs.
4. Store `last_pull = server now`.

Idempotent, offline-tolerant (unreachable round = leave local untouched, retry later), and a
brand-new device converges by pulling with `since=0`.

## 5. Client integration

- **Desktop:** the client is **C++ inside `ActivityService`**, NOT Python. `activities.db` is
  owned and held open by the C++ `ActivityService` singleton (`activityservice.cpp:418-597`); a
  Python writer would be a *second* writer on that SQLite file — the hazard §0 exists to avoid.
  So mirror the intervals.icu path, which is already 100% C++ in ActivityService (direct
  `QNetworkAccessManager` HTTP, LWW + tombstones against `m_db`): add `sommetSyncNow()` /
  `sommetPush()` / `sommetPull()` reusing `dbInsert()`, the `deleted_activities` tombstones, and
  `dbLoadAll()`. Hook it onto the watch-read completion (`requestActivities()` ~:412 and
  `readWatchActivities` `finishOne` ~:244), the import completions (`:1175/:1276/:1478`), and
  `deleteActivity()` (~:828, push the tombstone). Config in QSettings
  `connections/sommet_sync/{url,token}` + a `lastPull` cursor. (Python/PHP stays server-side only:
  `sync-server/sync.php` + `tools/sommet_sync_devserver.py`.)
  **Naming:** `SyncPage.qml` and the C++ `SyncService` already exist for the two-watch
  settings-COPY feature — do NOT reuse those names. The new activities feature is the **"Sync"
  settings section** (per §5b) backed by ActivityService methods; if a dedicated type is wanted,
  call it `CloudSyncService`, never `SyncService`.
- **Android:** new `SommetSync.ts` (mirror of `EmberSync.ts`) over `ambitsync.db`; same triggers;
  config in AsyncStorage. Reuse `AppDataBackupService`'s table-dump helpers for record shaping.
- **Settings UI:** one "Sommet Sync" row on both platforms (URL + token + "Sync now" + status),
  next to the existing Ember sync config.

## 5b. GUI flow — how the user chooses where their data lives

The **Sync** section = *"own your shared database."* It is deliberately separate from
**Connections** (= *"talk to external services"* — Garmin, Strava, Runalyze, **intervals.icu**).
**Decision (André, 2026-09-13): intervals.icu does NOT appear in the Sync picker.** It stays a
Connection (already account-bound and configured there); a user with an intervals account
already gets cross-device convergence through it, so nothing is lost — it simply isn't relabelled
as "sync". This keeps intervals configured in one place, not two.

One new single-select picker (same pattern as the Weight/Health source selectors), off by
default (preserves the "no account, offline-first" promise — sync is always opt-in):

```
┌─ Sync ─────────────────────────────────────────────┐
│   ○ Not syncing yet                   (default)     │
│   ○ A cloud folder (Dropbox, Drive, iCloud…)        │
│   ○ My own server / NAS                             │
│   [ status area — see below ]                        │
└─────────────────────────────────────────────────────┘
```

The default reads **"Not syncing yet"** (not "Off") — friendlier, and frames sync as something
to turn on, not a disabled feature.

**Status area** (shown once a provider is chosen): last-synced time · a **Sync now** button ·
device count ("Syncing with 3 devices") · and any error inline ("Couldn't reach server — will
retry"), never a blocking popup. Conflicts (rare, LWW resolves them) surface as a quiet note.

- **Cloud folder** — reveals a **[ Choose folder… ]** button. Desktop: native `FolderDialog`
  (the one `BackupPage.qml` already uses) → user points it inside their existing Dropbox/Drive/
  iCloud folder; their cloud app does the uploading, Sommet just reads/writes a bundle there — no
  credentials. Mobile: system document picker (Android SAF → persisted `content://` permission;
  iOS Files folder picker) — one extra tap, folder must be one the cloud app exposes. This is why
  cloud-folder is built AFTER the NAS provider (desktop trivial, mobile needs SAF/Files plumbing).
- **My own server / NAS** — reveals **URL + Token + [ Test connection ]** (identical shape to the
  existing Ember sync config row). Later nicety: NAS setup page prints a QR that fills both fields.

**When sync runs** (all providers, same triggers as Ember): on app open (throttled), after a
watch sync, after add/edit/delete, and a manual **Sync now**. Errors show inline in the status
line ("Couldn't reach server — will retry"), never a blocking popup.

**Desktop vs mobile:** identical except the cloud-folder picker (native dialog vs document
picker). The NAS provider is pixel-parallel on both.

## 6. Reachability — SOLVED via existing Tailscale (2026-09-14)

A LAN-only NAS (`http://192.168.1.102`) means the phone only syncs on home wifi. André's NAS
**already runs Tailscale**, and Tailscale **Serve** already exposes the web server over HTTPS on
the tailnet with a real `.ts.net` Let's Encrypt cert:
`https://ds220-xvc.tail585540.ts.net → http://127.0.0.1:80` (tailnet-only, not public). The
iPhone, MacBook Air and Windows box are all on the tailnet.

**Recommended Sync URL for every tailnet device (works at home and on cellular):**
`https://ds220-xvc.tail585540.ts.net/sommet/sync.php`. HTTPS here sidesteps the iOS ATS and
Android cleartext-HTTP blocks entirely — no `network_security_config`/ATS exceptions needed.
Confirmed: the iPhone on cellular reached it (401 = endpoint reached, token refused). A device
NOT on the tailnet uses the LAN URL `http://192.168.1.102/sommet/sync.php` at home.

For a user WITHOUT Tailscale, the fallbacks stand: Synology reverse proxy + HTTPS (+ DDNS/
QuickConnect), or stay LAN-only (syncs on home wifi).

## 7. Phased plan (issues)

1. **#SYNC-1** NAS endpoint: `sync.php` + `sommet.db` schema + token; deploy skeleton on
   192.168.1.102. Blob store. Smoke-test with curl.
2. **#SYNC-2** Desktop activities sync (metadata + blobs): `sync_store.py`, wire ActivityService,
   uid derivation, LWW, tombstones. Test with the fleet (Ambit3 + a second watch).
3. **#SYNC-3** Android activities sync client → parity. Verify a phone-read move appears on Linux
   and vice-versa; deleted stays deleted everywhere.
4. **#SYNC-4** Gear + reminders + assignment sync (both platforms).
5. **#SYNC-5** Reachability: reverse-proxy/HTTPS or Tailscale per §6; verify phone off-wifi.
6. **#SYNC-6** Mac + Windows validation via CI packaging; three-desktop + phone convergence test.
7. **#SYNC-7** (later) Settings/prefs sync; fold Ember into the same endpoint.

## 8. Open decisions for André

1. **Endpoint tech on the Synology** — PHP + SQLite (mirrors Ember, runs on the NAS's stock web
   server, fastest to ship) *[recommended]*, or a Docker container running a small service?
2. **Reachability (§6)** — phone syncs **only on home wifi** (LAN-only, simplest), or expose it
   for **anywhere** via Synology reverse-proxy+HTTPS or Tailscale?
3. **Scope of first cut** — activities only to start, then gear? *[recommended]* or all at once?
