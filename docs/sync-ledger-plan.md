# Sync only what changed (sync ledger) + calories at sync

André, 2026-10-31: *"why don't we do like freefilesync and maybe had "db" of synced activities so
we don't re-do everything..because usually i have like 4000!!! why calories appear later? they are
calculated on the watch."*

Status: **plan.** #SYNC-13 and #SYNC-16 built 31/10 (Android, not yet on a device), the rest not built. Found during the tablet audit of 30-31/10 (Android 0.2.56 + Ambit3
Peak). Numbers continue the Sommet Sync list in `shared_app_db_design.md` (#SYNC-1..7).

## What happens today

| Step | What it does now | Cost with ~4 700 activities (desktop has 4 705) |
|---|---|---|
| Watch read | Only new moves: the app hands the watch the ids it already has and the watch skips them. | Fine. |
| NAS pull | Only records newer than the last pull (`since`). | Fine. |
| NAS push, Android (`SommetSync.ts` 175-212) | After **every** watch sync: loads every activity, **opens every track file** just to say "this move has a track", then sends all records in one request. The list of tracks already sent is one big text in app storage, read and rewritten each time. | Thousands of file reads + one huge request on every sync. |
| NAS push, desktop (`activityservice.cpp` `sommetPushAll` ~3055) | Reads every row **including the full track text** of each, then sends all records. | Every track loaded in memory on every sync. |
| Deletions | The full list of deleted moves is sent every time. | Grows forever. |
| Calories, Android (`jni_bridge.cpp` 258-263) | The watch calculates them and keeps them in each move's header, but the tablet's own track writer copies only duration, distance, ascent and sport into the track. Calories, average/max heart rate, cadence, speed, descent, recovery time, peak training effect, pool lengths and max altitude are left out. The desktop's writer (`tools/exercise_log.py` 813-840) copies them all. `SyncService.ts` 179 would not save calories even if they were there. | Checked on the tablet 31/10: **all 847 watch moves have 0 kcal**, also in the list cache. The 29/10 swim: desktop 461 kcal, tablet 0. Daily kcal is 0 (audit F3). |
| Device name, Android (`SyncService.ts` 179) | Not saved (empty). | All 847 watch moves on the tablet have no device. See next row. |
| Identity in the NAS store | A move is known as `device|start minute`. The desktop writes the Peak as `8A153C5111000900|2026-10-29T08:20`, the tablet as `|2026-10-29T08:20`. | Once the tablet shares through the NAS, the same move would exist twice. (NAS sharing isn't set up on the tablet today, so no duplicates yet. The two 29/10 "Running" are two different moves, 11:48 and 13:19, that the desktop renamed "Other".) |

The NAS server already keeps the newest version of each record by its own timestamp
(`sync-server/sync.php`, `tools/sommet_sync_devserver.py`), so sending only the changed records
needs **no server change**.

## The idea (as FreeFileSync does)

FreeFileSync keeps a small database of what both sides looked like at the end of the last sync.
Next time it compares today against that, and only copies the differences. Sommet does the same
with a **sync ledger** table, one row per move:

```
sync_ledger(uid PRIMARY KEY, sent_updated_at, track_sent, deleted_sent)
```

- "What changed since last time" becomes one database query: moves whose `updated_at` is newer
  than `sent_updated_at`, or with no ledger row yet. No file is opened.
- "Has a track" comes from the database (`gpx_path` on Android, the track column's length on
  desktop), never by opening the file.
- A track file is read only for a move whose track hasn't been sent yet.

A normal sync after a watch read then sends the 1-6 new moves, not 4 700.

## Tasks

- **#SYNC-8 Ledger table, both apps.** Add `sync_ledger` to the Android SQLite (`db.ts`) and the
  desktop activities DB. The push picks changed moves with one query and reads only their tracks.
- **#SYNC-9 Small batches that resume.** Send 200 records per request; after each one the server
  accepts, write those moves into the ledger. If the tablet sleeps or Wi-Fi drops mid-way, the
  next sync carries on where it stopped. The very first run is one full send, once, with a
  progress line; every run after that sends only the changes.
- **#SYNC-10 One-time move-over.** Fill the ledger from the existing "tracks already sent" lists
  (Android `sommet.sync.blobsPushed` in app storage, desktop `sommet_sync/blobsPushed` in
  settings), so the first run after the update doesn't send every track again. Records (no
  tracks, small) are sent once more.
- **#SYNC-11 Deletions once.** Mark each deletion as sent in the ledger and send only new ones.
- **#SYNC-12 Gear too.** Same pattern for gear (`SommetGearSync.ts`, `gearservice.cpp`). Gear lists
  are small, so check first whether it's worth it.
- **#SYNC-13 Calories, summary values and device at watch sync (Android).** *Built 31/10.* The tablet's track
  writer (`jni_bridge.cpp`) writes the same summary values as the desktop's (`<energy>`,
  `<avg_hr>`, `<max_hr>`, cadence, speed, descent, recovery, peak training effect, pool lengths,
  max altitude; libambit already has them in `entry->header`). `SyncService.ts` saves
  `energy_kcal` and the device in **the same form the desktop writes** (the watch serial, e.g.
  `8A153C5111000900`), so a move has one identity everywhere. Moves already on the tablet: a
  one-time re-read of what is still on the watch fills them in. Older ones get calories from the
  desktop or the NAS store once sharing is on.
- **#SYNC-14 Duplicates.** Before the tablet starts sharing: give its 847 watch moves the
  watch serial (from the watch that made them; the Peak for most), so they match the desktop's.
  Then check the NAS store for moves with an empty device and merge any pair.
- **#SYNC-15 No whole-library loads elsewhere (Android)**, per the list paging rule. Home loads
  every activity three times just for the "Last activity" card (`HomeScreen.tsx` 320 / 390 / 404):
  use one "newest move" query. Totals: only the year shown. Calendar: only the months shown.
  Coach: sums in SQL.
- **#SYNC-16 One sync at a time.** *Built 31/10, with one shared watch session (changelog 31/10).* A single guard so two connects can't start two syncs at once
  (audit F1: the clip bounced, two syncs ran together, the GPS orbit write failed and steps were
  not read).

## How it gets checked

- Unit tests with made-up data: 4 000 moves → first push sends all in batches; second push sends
  0 and opens 0 track files; change one → sends 1; stop after batch 3 → next push starts at
  batch 4; a delete is sent once.
- Against the local test server (`tools/sommet_sync_devserver.py` + `sommet_sync_smoketest.py`):
  desktop and tablet converge with no duplicate.
- On the tablet with the Peak: a sync after the first one finishes in a few seconds, and the
  app's main thread goes back to idle straight after (the audit saw it busy for minutes, F2).
- New watch moves show calories and the watch serial right after the sync, before the list is
  opened.

## Not part of this

- The cause of the long busy spell after a connect (audit F2) isn't found yet. It is **not** the
  NAS push: NAS sharing isn't set up on the tablet. It needs a measured run (debug build with the
  profiler) before a fix.
