import { getDb } from './db';

// Local gear store (SQLite). Local-first: rows may exist with no remote_id yet (never pushed).
// The mirror (GearMirrorService) is the only writer of remote_id / last_synced_at / snapshot.
// `parent_id` is derived locally from the remote parent's component_ids on pull (intervals.icu
// has no parentId on the child). `is_primary` is a purely LOCAL display flag — /gear has no
// primary field, so it is never mirrored.

export interface LocalGear {
  id: string;
  remoteId: string | null;
  parentId: string | null;
  name: string;
  type: string;            // "Bike" | "Shoes" | "Chain" | "Tyre" | ...
  distanceM: number;
  timeS: number;
  retired: boolean;
  isPrimary: boolean;      // local-only
  updatedAt: number;
  lastSyncedAt: number;
  remoteSnapshot: string;
  deleted: boolean;
  // Sommet Sync (#SYNC-4): manually-entered mileage baseline + the moment it was set. When
  // baselineAt > 0, GearTotals uses startingDistanceM as the baseline (instead of the intervals
  // total) and counts rides recorded after baselineAt. Synced across devices. Optional on
  // construction (reads always populate them; upsertGear leaves them to the DB default / sync).
  startingDistanceM?: number;
  startingTimeS?: number;
  baselineAt?: number;
}

export interface LocalReminder {
  id: string;
  remoteId: string | null;
  gearId: string;
  name: string;
  distanceM: number;   // interval, meters
  timeS: number;       // interval, seconds
  days: number;        // interval, days
  activities: number;  // interval, activity count
  percentUsed: number; // intervals' own score (cross-check/fallback)
  snoozedUntil: number | null;
  startingDistanceM: number;   // reset-baseline for LOCAL due-ness
  startingTimeS: number;
  startingActivities: number;
  lastReset: number | null;
  updatedAt: number;
  deleted: boolean;
}

// react-native has no crypto.randomUUID in Hermes; a timestamp+random local id is unique enough.
export function newLocalId(prefix = 'local'): string {
  return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
}

function rowToGear(r: any): LocalGear {
  return {
    id: r.id,
    remoteId: r.remote_id ?? null,
    parentId: r.parent_id ?? null,
    name: r.name,
    type: r.type ?? 'Bike',
    distanceM: r.distance_m,
    timeS: r.time_s,
    retired: !!r.retired,
    isPrimary: !!r.is_primary,
    updatedAt: r.updated_at,
    lastSyncedAt: r.last_synced_at,
    remoteSnapshot: r.remote_snapshot ?? '',
    deleted: !!r.deleted,
    startingDistanceM: r.starting_distance_m ?? 0,
    startingTimeS: r.starting_time_s ?? 0,
    baselineAt: r.baseline_at ?? 0,
  };
}

function rowToReminder(r: any): LocalReminder {
  return {
    id: r.id,
    remoteId: r.remote_id ?? null,
    gearId: r.gear_id,
    name: r.name,
    distanceM: r.distance_m,
    timeS: r.time_s,
    days: r.days,
    activities: r.activities,
    percentUsed: r.percent_used,
    snoozedUntil: r.snoozed_until ?? null,
    startingDistanceM: r.starting_distance_m ?? 0,
    startingTimeS: r.starting_time_s ?? 0,
    startingActivities: r.starting_activities ?? 0,
    lastReset: r.last_reset ?? null,
    updatedAt: r.updated_at,
    deleted: !!r.deleted,
  };
}

// ─── Gear ─────────────────────────────────────────────────────────────────────

export async function getAllGear(includeDeleted = false): Promise<LocalGear[]> {
  const db = await getDb();
  const [res] = await db.executeSql(
    `SELECT * FROM gear ${includeDeleted ? '' : 'WHERE deleted = 0'} ORDER BY is_primary DESC, name ASC`
  );
  const out: LocalGear[] = [];
  for (let i = 0; i < res.rows.length; i++) out.push(rowToGear(res.rows.item(i)));
  return out;
}

export async function getGearById(id: string): Promise<LocalGear | null> {
  const db = await getDb();
  const [res] = await db.executeSql('SELECT * FROM gear WHERE id = ? LIMIT 1', [id]);
  return res.rows.length ? rowToGear(res.rows.item(0)) : null;
}

export async function upsertGear(g: LocalGear): Promise<void> {
  const db = await getDb();
  // Upsert (not INSERT OR REPLACE) so an intervals re-import never wipes the local mileage
  // baseline (starting_distance_m / baseline_at) - those are managed only by setGearBaseline and
  // by the NAS sync, never reset here. On first insert they take the column defaults (0).
  await db.executeSql(
    `INSERT INTO gear
       (id, remote_id, parent_id, name, type, distance_m, time_s, retired, is_primary,
        updated_at, last_synced_at, remote_snapshot, deleted)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
     ON CONFLICT(id) DO UPDATE SET remote_id=excluded.remote_id, parent_id=excluded.parent_id,
       name=excluded.name, type=excluded.type, distance_m=excluded.distance_m,
       time_s=excluded.time_s, retired=excluded.retired, is_primary=excluded.is_primary,
       updated_at=excluded.updated_at, last_synced_at=excluded.last_synced_at,
       remote_snapshot=excluded.remote_snapshot, deleted=excluded.deleted`,
    [g.id, g.remoteId, g.parentId, g.name, g.type, g.distanceM, g.timeS,
     g.retired ? 1 : 0, g.isPrimary ? 1 : 0, g.updatedAt, g.lastSyncedAt,
     g.remoteSnapshot, g.deleted ? 1 : 0]
  );
}

/** Set a gear's manually-entered mileage baseline (km + optional hours) as of NOW. From here,
 *  rides count on top; the typed number is never lost. Local + synced (latest change wins). */
export async function setGearBaseline(id: string, km: number, hours = 0): Promise<void> {
  const db = await getDb();
  const now = Date.now();
  await db.executeSql(
    'UPDATE gear SET starting_distance_m = ?, starting_time_s = ?, baseline_at = ?, updated_at = ? WHERE id = ?',
    [km * 1000, hours * 3600, now, now, id]
  );
}

/** Mark a gear (and its components + reminders) as a tombstone to push a delete next mirror. */
export async function softDeleteGear(id: string): Promise<void> {
  const db = await getDb();
  const now = Date.now();
  await db.executeSql('UPDATE gear SET deleted = 1, updated_at = ? WHERE id = ? OR parent_id = ?', [now, id, id]);
  await db.executeSql('UPDATE gear_reminder SET deleted = 1, updated_at = ? WHERE gear_id = ?', [now, id]);
}

/** Physically remove a row once its delete has been mirrored. */
export async function purgeGear(id: string): Promise<void> {
  const db = await getDb();
  await db.executeSql('DELETE FROM gear WHERE id = ?', [id]);
}

// ─── Reminders ──────────────────────────────────────────────────────────────

export async function getReminders(gearId: string, includeDeleted = false): Promise<LocalReminder[]> {
  const db = await getDb();
  const [res] = await db.executeSql(
    `SELECT * FROM gear_reminder WHERE gear_id = ? ${includeDeleted ? '' : 'AND deleted = 0'}`, [gearId]
  );
  const out: LocalReminder[] = [];
  for (let i = 0; i < res.rows.length; i++) out.push(rowToReminder(res.rows.item(i)));
  return out;
}

export async function upsertReminder(r: LocalReminder): Promise<void> {
  const db = await getDb();
  await db.executeSql(
    `INSERT OR REPLACE INTO gear_reminder
       (id, remote_id, gear_id, name, distance_m, time_s, days, activities, percent_used,
        snoozed_until, starting_distance_m, starting_time_s, starting_activities, last_reset,
        updated_at, deleted)
     VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
    [r.id, r.remoteId, r.gearId, r.name, r.distanceM, r.timeS, r.days, r.activities,
     r.percentUsed, r.snoozedUntil, r.startingDistanceM, r.startingTimeS, r.startingActivities,
     r.lastReset, r.updatedAt, r.deleted ? 1 : 0]
  );
}

export async function purgeReminder(id: string): Promise<void> {
  const db = await getDb();
  await db.executeSql('DELETE FROM gear_reminder WHERE id = ?', [id]);
}

// ─── Assignment (sport type -> default gear) & per-activity tagging ────────────

export async function getAssignments(): Promise<Record<string, string>> {
  const db = await getDb();
  const [res] = await db.executeSql('SELECT activity_type, gear_id FROM gear_assignment');
  const map: Record<string, string> = {};
  for (let i = 0; i < res.rows.length; i++) {
    const row = res.rows.item(i);
    map[row.activity_type] = row.gear_id;
  }
  return map;
}

export async function setAssignment(activityType: string, gearId: string | null): Promise<void> {
  const db = await getDb();
  if (!gearId) {
    await db.executeSql('DELETE FROM gear_assignment WHERE activity_type = ?', [activityType]);
  } else {
    await db.executeSql(
      'INSERT OR REPLACE INTO gear_assignment (activity_type, gear_id) VALUES (?, ?)',
      [activityType, gearId]
    );
  }
}

export async function getDefaultGearFor(activityType: string): Promise<string | null> {
  const db = await getDb();
  const [res] = await db.executeSql(
    'SELECT gear_id FROM gear_assignment WHERE activity_type = ? LIMIT 1', [activityType]
  );
  return res.rows.length ? res.rows.item(0).gear_id : null;
}

export async function isActivityTagged(activityId: string): Promise<boolean> {
  const db = await getDb();
  const [res] = await db.executeSql('SELECT 1 FROM activity_gear WHERE activity_id = ? LIMIT 1', [activityId]);
  return res.rows.length > 0;
}

/** Attribute a synced move to a gear in the local usage ledger (idempotent per activity_id).
 * distance_m/time_s feed the local distance tally (GearTotals). */
export async function recordActivityGear(
  activityId: string, gearId: string, distanceM: number, timeS: number, activityDate = '',
): Promise<void> {
  const db = await getDb();
  await db.executeSql(
    `INSERT OR REPLACE INTO activity_gear
       (activity_id, gear_id, distance_m, time_s, activity_date, assigned_at)
     VALUES (?, ?, ?, ?, ?, ?)`,
    [activityId, gearId, distanceM, timeS, activityDate, Date.now()]
  );
}

/** The gear a specific activity is currently attributed to (manual pick or sport default), or null. */
export async function getActivityGear(activityId: string): Promise<string | null> {
  const db = await getDb();
  const [res] = await db.executeSql('SELECT gear_id FROM activity_gear WHERE activity_id = ? LIMIT 1', [activityId]);
  return res.rows.length ? res.rows.item(0).gear_id : null;
}

/** Remove an activity's gear attribution (clear). */
export async function clearActivityGear(activityId: string): Promise<void> {
  const db = await getDb();
  await db.executeSql('DELETE FROM activity_gear WHERE activity_id = ?', [activityId]);
}

export interface LedgerRow { gearId: string; distanceM: number; timeS: number; assignedAt: number }

export async function getGearLedger(): Promise<LedgerRow[]> {
  const db = await getDb();
  const [res] = await db.executeSql('SELECT gear_id, distance_m, time_s, assigned_at FROM activity_gear');
  const out: LedgerRow[] = [];
  for (let i = 0; i < res.rows.length; i++) {
    const r = res.rows.item(i);
    out.push({ gearId: r.gear_id, distanceM: r.distance_m, timeS: r.time_s, assignedAt: r.assigned_at });
  }
  return out;
}
