import SQLite, { SQLiteDatabase } from 'react-native-sqlite-storage';
import RNFS from 'react-native-fs';

SQLite.enablePromise(true);

// iOS moves the app's data container (its UUID changes between some installs), so a gpx_path
// stored as an ABSOLUTE path under an old container stops resolving → "can't read gpx". Rebase
// any stored `.../Documents/<rel>` onto the CURRENT DocumentDirectoryPath so the file is found
// wherever this install put it. (André, 2026-08-30.)
function rebaseToDocuments(p: string | null | undefined): string {
  if (!p) return p ?? '';
  const marker = '/Documents/';
  const i = p.indexOf(marker);
  if (i === -1) return p;
  return `${RNFS.DocumentDirectoryPath}/${p.slice(i + marker.length)}`;
}

// ─── Types ────────────────────────────────────────────────────────────────────

export interface ActivityRecord {
  id: string;
  synced_at: number;      // timestamp Unix (ms)
  gpx_path: string;
  date: string;           // ISO 8601, ex: "2024-06-15T09:30:00"
  duration_s: number;     // durée en secondes
  distance_m: number;     // distance en mètres
  d_plus: number;         // dénivelé positif cumulé (m)
  activity_type: string;  // ex: "Orienteering", "Running"…
  // Which device recorded it, when known (2026-08-26, desktop parity: "we also added from what
  // device they came from, and I don't see it"). intervals.icu imports carry a real name here
  // ("GARMIN FR965", "SUUNTO Suunto Race S"); moves read off the connected watch leave it empty,
  // because the watch is implied.
  device?: string;
  // Sommet Sync (#SYNC-3): last-modified time (ms), drives last-writer-wins against the shared
  // self-hosted store. Defaults to synced_at on a watch read; a pulled row carries the origin's.
  updated_at?: number;
}

// ─── Singleton DB ─────────────────────────────────────────────────────────────

let _db: SQLiteDatabase | null = null;

export async function getDb(): Promise<SQLiteDatabase> {
  if (_db) return _db;
  _db = await SQLite.openDatabase({ name: 'ambitsync.db', location: 'default' });
  await _db.executeSql(`
    CREATE TABLE IF NOT EXISTS activities (
      id            TEXT    PRIMARY KEY,
      synced_at     INTEGER NOT NULL,
      gpx_path      TEXT    NOT NULL,
      date          TEXT    NOT NULL DEFAULT '',
      duration_s    INTEGER NOT NULL DEFAULT 0,
      distance_m    INTEGER NOT NULL DEFAULT 0,
      d_plus        INTEGER NOT NULL DEFAULT 0,
      activity_type TEXT    NOT NULL DEFAULT ''
    )
  `);
  // Table liste noire : activités supprimées volontairement, ne jamais re-importer
  await _db.executeSql(`
    CREATE TABLE IF NOT EXISTS deleted_activities (
      id         TEXT    PRIMARY KEY,
      deleted_at INTEGER NOT NULL
    )
  `);
  // Sommet Sync (#SYNC-3): tombstones keyed by the cross-device uid (device|start-minute), the
  // same identity the desktop uses. Separate from deleted_activities (keyed by the local id): the
  // shared store identifies a record by uid, so a delete pushes/pulls by uid. Rows whose uid is
  // here are never re-created by a pull, and are deleted locally when a remote tombstone arrives.
  await _db.executeSql(`
    CREATE TABLE IF NOT EXISTS sommet_deleted (
      uid TEXT PRIMARY KEY
    )
  `);
  // Migrations
  await _db.executeSql(
    `ALTER TABLE activities ADD COLUMN activity_type TEXT NOT NULL DEFAULT ''`
  ).catch(() => {});
  await _db.executeSql(
    `ALTER TABLE activities ADD COLUMN device TEXT NOT NULL DEFAULT ''`
  ).catch(() => {});
  await _db.executeSql(
    `ALTER TABLE activities ADD COLUMN updated_at INTEGER NOT NULL DEFAULT 0`
  ).catch(() => {});
  // ── Gear tracker (v3) — see gearDb.ts. Local-first, mirrored to intervals.icu. ──
  // A component (part) is a gear row with parent_id set. remote_id is the intervals.icu id
  // once mirrored (null for local-only, not-yet-pushed rows). last_synced_at + a stored
  // snapshot of the remote state let the mirror tell a one-sided edit from a real conflict.
  await _db.executeSql(`
    CREATE TABLE IF NOT EXISTS gear (
      id            TEXT    PRIMARY KEY,           -- local id (uuid or the remote id)
      remote_id     TEXT,                          -- intervals.icu id, null until pushed
      parent_id     TEXT,                          -- parent gear's local id, null for top gear
      name          TEXT    NOT NULL DEFAULT '',
      type          TEXT    NOT NULL DEFAULT 'bike',
      distance_m    INTEGER NOT NULL DEFAULT 0,    -- read-only mirror of remote total
      time_s        INTEGER NOT NULL DEFAULT 0,
      retired       INTEGER NOT NULL DEFAULT 0,
      is_primary    INTEGER NOT NULL DEFAULT 0,
      updated_at    INTEGER NOT NULL DEFAULT 0,    -- local last-edit (ms)
      last_synced_at INTEGER NOT NULL DEFAULT 0,
      remote_snapshot TEXT   NOT NULL DEFAULT '',  -- JSON of remote state at last sync
      deleted       INTEGER NOT NULL DEFAULT 0     -- tombstone (push delete, then purge)
    )
  `);
  // Reminder intervals mirror intervals.icu exactly (a reminder can combine several units):
  // distance (m), time (s), days, activities. percent_used (>=100 => due) comes from the server.
  await _db.executeSql(`
    CREATE TABLE IF NOT EXISTS gear_reminder (
      id            TEXT    PRIMARY KEY,
      remote_id     TEXT,
      gear_id       TEXT    NOT NULL,
      name          TEXT    NOT NULL DEFAULT '',
      distance_m    REAL    NOT NULL DEFAULT 0,
      time_s        REAL    NOT NULL DEFAULT 0,
      days          INTEGER NOT NULL DEFAULT 0,
      activities    INTEGER NOT NULL DEFAULT 0,
      percent_used  REAL    NOT NULL DEFAULT 0,
      snoozed_until INTEGER,                             -- epoch ms, null if not snoozed
      -- reset-baseline: the gear's cumulative counters at last reset, for LOCAL due-ness
      starting_distance_m REAL    NOT NULL DEFAULT 0,
      starting_time_s     REAL    NOT NULL DEFAULT 0,
      starting_activities INTEGER NOT NULL DEFAULT 0,
      last_reset          INTEGER,                       -- epoch ms of last reset
      updated_at    INTEGER NOT NULL DEFAULT 0,
      deleted       INTEGER NOT NULL DEFAULT 0
    )
  `);
  // Idempotent migrations for tables created by an EARLIER gear build (CREATE TABLE IF NOT
  // EXISTS won't add columns to a table that already exists). A DB that predates any of these
  // columns is missing them -> "table gear_reminder has no column named distance_m" on insert.
  // Each ADD COLUMN throws (and is swallowed) when the column is already present, so running
  // them every launch is safe. The reset-baseline set was already here; the core interval +
  // sync columns (distance_m/time_s/days/activities/percent_used/snoozed_until/remote_id) were
  // missing, which is the real bug the gear import hit (André's tablet, 2026-08-18).
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN distance_m REAL NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN time_s REAL NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN days INTEGER NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN activities INTEGER NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN percent_used REAL NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN snoozed_until INTEGER`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN remote_id TEXT`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN starting_distance_m REAL NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN starting_time_s REAL NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN starting_activities INTEGER NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear_reminder ADD COLUMN last_reset INTEGER`).catch(() => {});
  // Sommet Sync (#SYNC-4): manually-entered mileage baseline on a gear + the moment it was set,
  // parity with desktop so a baseline typed on the computer reaches the phone. Additive no-ops.
  await _db.executeSql(`ALTER TABLE gear ADD COLUMN starting_distance_m REAL NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear ADD COLUMN starting_time_s REAL NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE gear ADD COLUMN baseline_at INTEGER NOT NULL DEFAULT 0`).catch(() => {});
  // Default gear per Ambit sport type (e.g. "Cycling" -> a bike's local id). Auto-assign source.
  await _db.executeSql(`
    CREATE TABLE IF NOT EXISTS gear_assignment (
      activity_type TEXT    PRIMARY KEY,
      gear_id       TEXT    NOT NULL
    )
  `);
  // The LOCAL gear-usage ledger: which gear each synced watch move is attributed to, plus its
  // distance/time, so the app tallies gear mileage ITSELF (independence from intervals.icu — the
  // aim is to ditch it). Displayed gear distance = imported baseline (gear.distance_m at last
  // import) + sum of ledger entries recorded AFTER that import (assigned_at > gear.last_synced_at),
  // which avoids double-counting anything intervals already had in its total.
  await _db.executeSql(`
    CREATE TABLE IF NOT EXISTS activity_gear (
      activity_id   TEXT    PRIMARY KEY,
      gear_id       TEXT    NOT NULL,
      distance_m    REAL    NOT NULL DEFAULT 0,
      time_s        REAL    NOT NULL DEFAULT 0,
      activity_date TEXT    NOT NULL DEFAULT '',
      assigned_at   INTEGER NOT NULL DEFAULT 0
    )
  `);
  await _db.executeSql(`ALTER TABLE activity_gear ADD COLUMN distance_m REAL NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE activity_gear ADD COLUMN time_s REAL NOT NULL DEFAULT 0`).catch(() => {});
  await _db.executeSql(`ALTER TABLE activity_gear ADD COLUMN activity_date TEXT NOT NULL DEFAULT ''`).catch(() => {});
  return _db;
}

// ─── API publique ─────────────────────────────────────────────────────────────

/** Vérifie si un log a déjà été synchronisé ET n'est pas dans la liste noire. */
export async function isActivitySynced(id: string): Promise<boolean> {
  const db = await getDb();
  const [[synced], [deleted]] = await Promise.all([
    db.executeSql('SELECT 1 FROM activities WHERE id = ? LIMIT 1', [id]),
    db.executeSql('SELECT 1 FROM deleted_activities WHERE id = ? LIMIT 1', [id]),
  ]);
  return synced.rows.length > 0 || deleted.rows.length > 0;
}

/** Vérifie si un ID est dans la liste noire (supprimé volontairement). */
export async function isActivityDeleted(id: string): Promise<boolean> {
  const db = await getDb();
  const [result] = await db.executeSql(
    'SELECT 1 FROM deleted_activities WHERE id = ? LIMIT 1', [id]
  );
  return result.rows.length > 0;
}

/** Enregistre une activité synchronisée dans la base. */
export async function markActivitySynced(record: ActivityRecord): Promise<void> {
  const db = await getDb();
  await db.executeSql(
    `INSERT OR REPLACE INTO activities
       (id, synced_at, gpx_path, date, duration_s, distance_m, d_plus, activity_type, device, updated_at)
     VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
    [
      record.id,
      record.synced_at,
      record.gpx_path,
      record.date,
      record.duration_s,
      record.distance_m,
      record.d_plus,
      record.activity_type,
      record.device ?? '',
      record.updated_at ?? record.synced_at,
    ]
  );
}

// ─── Sommet Sync (#SYNC-3) DB helpers ───────────────────────────────────────────

/** The cross-device identity of a move: device|start-minute (matches desktop's sommetUid). */
export function sommetUid(device: string, date: string): string {
  return `${device ?? ''}|${(date ?? '').slice(0, 16)}`;
}

/** uid-tombstones (device|start-minute) held for Sommet Sync, mirroring desktop's sommet_deleted. */
export async function getSommetTombstones(): Promise<string[]> {
  const db = await getDb();
  const [r] = await db.executeSql('SELECT uid FROM sommet_deleted');
  const out: string[] = [];
  for (let i = 0; i < r.rows.length; i++) out.push(r.rows.item(i).uid);
  return out;
}

export async function addSommetTombstone(uid: string): Promise<void> {
  const db = await getDb();
  await db.executeSql('INSERT OR IGNORE INTO sommet_deleted (uid) VALUES (?)', [uid]);
}

/** Delete every local row matching a uid (device|start-minute). Used when a remote tombstone
 *  arrives. Compares the first 16 chars of `date` (tolerant of the …Z/ms vs no-Z formats). */
export async function deleteActivitiesByUid(uid: string): Promise<void> {
  const bar = uid.indexOf('|');
  if (bar < 0) return;
  const device = uid.slice(0, bar);
  const minute = uid.slice(bar + 1);
  const db = await getDb();
  await db.executeSql(
    'DELETE FROM activities WHERE device = ? AND substr(date,1,16) = ?', [device, minute]
  );
}

/** The local row (updated_at + gpx_path) for a uid, or null if we don't have it. */
export async function getActivityByUid(
  uid: string
): Promise<{ id: string; updated_at: number; gpx_path: string } | null> {
  const bar = uid.indexOf('|');
  if (bar < 0) return null;
  const device = uid.slice(0, bar);
  const minute = uid.slice(bar + 1);
  const db = await getDb();
  const [r] = await db.executeSql(
    'SELECT id, updated_at, gpx_path FROM activities WHERE device = ? AND substr(date,1,16) = ? LIMIT 1',
    [device, minute]
  );
  if (r.rows.length === 0) return null;
  const row = r.rows.item(0);
  return { id: row.id, updated_at: Number(row.updated_at || 0), gpx_path: row.gpx_path };
}

/** Retourne toutes les activités triées par date décroissante. */
export async function getAllActivities(): Promise<ActivityRecord[]> {
  const db = await getDb();
  const [result] = await db.executeSql(
    'SELECT * FROM activities ORDER BY date DESC'
  );
  const activities: ActivityRecord[] = [];
  for (let i = 0; i < result.rows.length; i++) {
    const a = result.rows.item(i);
    a.gpx_path = rebaseToDocuments(a.gpx_path);   // survive an iOS container-UUID change
    activities.push(a);
  }
  return activities;
}

/** Retourne tous les IDs connus (synchro + liste noire) pour éviter re-import. */
export async function getAllSyncedIds(): Promise<string[]> {
  const db = await getDb();
  const [[synced], [deleted]] = await Promise.all([
    db.executeSql('SELECT id FROM activities'),
    db.executeSql('SELECT id FROM deleted_activities'),
  ]);
  const ids: string[] = [];
  for (let i = 0; i < synced.rows.length; i++) ids.push(synced.rows.item(i).id);
  for (let i = 0; i < deleted.rows.length; i++) ids.push(deleted.rows.item(i).id);
  return ids;
}

/** Just the user-deleted blacklist IDs — used by a forced re-download (runSync forceRefresh) so
 * kept activities are re-read from the watch while deleted ones are still never resurrected. */
export async function getDeletedIds(): Promise<string[]> {
  const db = await getDb();
  const [result] = await db.executeSql('SELECT id FROM deleted_activities');
  const ids: string[] = [];
  for (let i = 0; i < result.rows.length; i++) ids.push(result.rows.item(i).id);
  return ids;
}

/** Empties the deleted-activities blacklist — used by a "re-download incl. deleted" so a move
 * removed by mistake can come back from the watch on the next sync. */
export async function clearDeletedActivities(): Promise<void> {
  const db = await getDb();
  await db.executeSql('DELETE FROM deleted_activities');
}

/** Met à jour uniquement le type d'activité d'un enregistrement existant. */
export async function updateActivityType(id: string, activityType: string): Promise<void> {
  const db = await getDb();
  await db.executeSql(
    'UPDATE activities SET activity_type = ? WHERE id = ?',
    [activityType, id]
  );
}

/** Supprime une activité de la base et l'ajoute à la liste noire pour ne pas la re-importer. */
export async function deleteActivity(id: string): Promise<void> {
  const db = await getDb();
  // Sommet Sync (#SYNC-3): before removing the row, capture its uid (device|start-minute) so the
  // delete can propagate to the shared store and stay gone across a watch re-read. Rows deleted
  // before this feature (no device recorded) yield an empty-device uid, still fine locally.
  let uid: string | null = null;
  const [pre] = await db.executeSql(
    'SELECT device, date FROM activities WHERE id = ? LIMIT 1', [id]
  );
  if (pre.rows.length > 0) {
    const row = pre.rows.item(0);
    uid = sommetUid(row.device ?? '', row.date ?? '');
  }
  await db.executeSql('DELETE FROM activities WHERE id = ?', [id]);
  await db.executeSql(
    'INSERT OR IGNORE INTO deleted_activities (id, deleted_at) VALUES (?, ?)',
    [id, Date.now()]
  );
  if (uid) await db.executeSql('INSERT OR IGNORE INTO sommet_deleted (uid) VALUES (?)', [uid]);
}
