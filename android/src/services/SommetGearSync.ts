import AsyncStorage from '@react-native-async-storage/async-storage';
import { getDb } from '../database/db';
import { getSommetSyncCfg } from './SommetSync';

// Sommet Sync for gear (#SYNC-4b, phone): mirror the local gear rows to the user's own self-hosted
// store (sync-server/sync.php), keyed by gear id, last-writer-wins by updated_at, tombstones via
// the `deleted` flag. The React Native twin of the desktop GearService::sommetGear* methods, so a
// bike/shoe (and its manually-entered starting mileage) appears on every device and survives
// dropping intervals.icu. Reuses the activity sync's config (sommet.sync.cfg). Best-effort.

const GEAR_LASTPULL_KEY = 'sommet.sync.gearLastPull';

async function gearHttp(url: string, token: string, method: 'GET' | 'POST',
                        params?: Record<string, string>, body?: any): Promise<any | null> {
  const q = params ? '?' + new URLSearchParams(params).toString() : '';
  const headers: Record<string, string> = { 'X-Sommet-Token': token };
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    const res = await fetch(url + q, {
      method, headers, body: body !== undefined ? JSON.stringify(body) : undefined,
      signal: controller.signal,
    });
    clearTimeout(timer);
    if (res.status === 404) return {};
    if (!res.ok) return null;
    const raw = await res.text();
    return raw.trim() ? JSON.parse(raw) : {};
  } catch { return null; }
}

export interface GearSyncResult { pulled: number; pushed: number; ok: boolean; }

export async function runSommetGearSync(): Promise<GearSyncResult> {
  const cfg = await getSommetSyncCfg();
  if (!cfg) return { pulled: 0, pushed: 0, ok: false };
  const { url, token } = cfg;
  const db = await getDb();
  let pulled = 0;

  // ── PULL ──────────────────────────────────────────────────────────────────────
  const since = Number((await AsyncStorage.getItem(GEAR_LASTPULL_KEY)) || '0');
  const remote = await gearHttp(url, token, 'GET', { c: 'gear', since: String(since) });
  if (remote === null) return { pulled: 0, pushed: 0, ok: false }; // unreachable: leave local alone

  for (const uid of (remote.deleted ?? [])) {
    if (!uid) continue;
    await db.executeSql('UPDATE gear SET deleted = 1, updated_at = ? WHERE id = ?', [Date.now(), uid]);
  }
  for (const r of (remote.records ?? [])) {
    const id = r.uid || r.id;
    if (!id) continue;
    const remoteUpdated = Number(r.updated_at || 0);
    const [ex] = await db.executeSql(
      'SELECT updated_at, is_primary, last_synced_at, remote_snapshot FROM gear WHERE id = ? LIMIT 1', [id]
    );
    let isPrimary = 0, lastSynced = 0, snapshot = '';
    if (ex.rows.length > 0) {
      const row = ex.rows.item(0);
      if (Number(row.updated_at || 0) >= remoteUpdated) continue; // ours is newer or equal
      isPrimary = row.is_primary ?? 0;
      lastSynced = row.last_synced_at ?? 0;
      snapshot = row.remote_snapshot ?? '';
    }
    // INSERT OR REPLACE writes the whole row, so carry the local-only fields (is_primary,
    // last_synced_at, remote_snapshot) forward from any existing row.
    await db.executeSql(
      `INSERT OR REPLACE INTO gear
         (id, remote_id, parent_id, name, type, distance_m, time_s, retired, is_primary,
          updated_at, last_synced_at, remote_snapshot, deleted,
          starting_distance_m, starting_time_s, baseline_at)
       VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
      [id, r.remote_id ?? null, r.parent_id || null, r.name ?? '', r.type ?? 'Bike',
       Number(r.distance_m || 0), Number(r.time_s || 0), Number(r.retired) ? 1 : 0, isPrimary,
       remoteUpdated, lastSynced, snapshot, Number(r.deleted) ? 1 : 0,
       Number(r.starting_distance_m || 0), Number(r.starting_time_s || 0), Number(r.baseline_at || 0)]
    );
    pulled++;
  }
  if (remote.now) await AsyncStorage.setItem(GEAR_LASTPULL_KEY, String(remote.now));

  // ── PUSH ──────────────────────────────────────────────────────────────────────
  const [res] = await db.executeSql('SELECT * FROM gear');
  const records: any[] = [];
  const deleted: string[] = [];
  for (let i = 0; i < res.rows.length; i++) {
    const g = res.rows.item(i);
    if (!g.id) continue;
    if (g.deleted) { deleted.push(g.id); continue; }
    records.push({
      uid: g.id,
      id: g.id,
      remote_id: g.remote_id ?? '',
      parent_id: g.parent_id ?? '',
      name: g.name ?? '',
      type: g.type ?? 'Bike',
      component: g.parent_id ? 1 : 0,      // desktop uses a component flag; the phone uses parent_id
      distance_m: Number(g.distance_m || 0),
      time_s: Number(g.time_s || 0),
      retired: g.retired ? 1 : 0,
      starting_distance_m: Number(g.starting_distance_m || 0),
      starting_time_s: Number(g.starting_time_s || 0),
      baseline_at: Number(g.baseline_at || 0),
      updated_at: Number(g.updated_at || 0),
    });
  }
  const post = await gearHttp(url, token, 'POST', { c: 'gear' }, { records, deleted });
  if (post === null) return { pulled, pushed: 0, ok: false };

  // ── REMINDERS (same model, collection gear_reminder) ────────────────────────────
  const rSince = Number((await AsyncStorage.getItem('sommet.sync.reminderLastPull')) || '0');
  const rRemote = await gearHttp(url, token, 'GET', { c: 'gear_reminder', since: String(rSince) });
  if (rRemote !== null) {
    for (const uid of (rRemote.deleted ?? [])) {
      if (uid) await db.executeSql('UPDATE gear_reminder SET deleted = 1, updated_at = ? WHERE id = ?', [Date.now(), uid]);
    }
    for (const r of (rRemote.records ?? [])) {
      const id = r.uid || r.id;
      if (!id) continue;
      const remoteUpdated = Number(r.updated_at || 0);
      // Preserve the phone-only reminder fields (percent_used/snoozed_until/starting_activities).
      const [ex] = await db.executeSql(
        'SELECT updated_at, percent_used, snoozed_until, starting_activities FROM gear_reminder WHERE id = ? LIMIT 1', [id]
      );
      let pct = 0, snz: number | null = null, sa = 0;
      if (ex.rows.length > 0) {
        const row = ex.rows.item(0);
        if (Number(row.updated_at || 0) >= remoteUpdated) continue;
        pct = row.percent_used ?? 0; snz = row.snoozed_until ?? null; sa = row.starting_activities ?? 0;
      }
      await db.executeSql(
        `INSERT OR REPLACE INTO gear_reminder
           (id, remote_id, gear_id, name, distance_m, time_s, days, activities, percent_used,
            snoozed_until, starting_distance_m, starting_time_s, starting_activities, last_reset,
            updated_at, deleted)
         VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)`,
        [id, r.remote_id ?? null, r.gear_id ?? '', r.name ?? '', Number(r.distance_m || 0),
         Number(r.time_s || 0), Number(r.days || 0), Number(r.activities || 0), pct, snz,
         Number(r.starting_distance_m || 0), Number(r.starting_time_s || 0), sa,
         r.last_reset ?? null, remoteUpdated, Number(r.deleted) ? 1 : 0]
      );
    }
    if (rRemote.now) await AsyncStorage.setItem('sommet.sync.reminderLastPull', String(rRemote.now));
  }
  const [rr] = await db.executeSql('SELECT * FROM gear_reminder');
  const rRecords: any[] = [];
  const rDeleted: string[] = [];
  for (let i = 0; i < rr.rows.length; i++) {
    const g = rr.rows.item(i);
    if (!g.id) continue;
    if (g.deleted) { rDeleted.push(g.id); continue; }
    rRecords.push({
      uid: g.id, id: g.id, gear_id: g.gear_id ?? '', name: g.name ?? '',
      distance_m: Number(g.distance_m || 0), time_s: Number(g.time_s || 0),
      days: Number(g.days || 0), activities: Number(g.activities || 0),
      starting_distance_m: Number(g.starting_distance_m || 0),
      starting_time_s: Number(g.starting_time_s || 0),
      last_reset: Number(g.last_reset || 0), remote_id: g.remote_id ?? '',
      updated_at: Number(g.updated_at || 0),
    });
  }
  await gearHttp(url, token, 'POST', { c: 'gear_reminder' }, { records: rRecords, deleted: rDeleted });

  return { pulled, pushed: records.length, ok: true };
}
