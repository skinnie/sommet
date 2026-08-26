import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';
import { backfillIntervalsTracks } from './IntervalsTracks';
import {
  markActivitySynced, getAllSyncedIds, getAllActivities, ActivityRecord,
} from '../database/db';

// Import activities FROM intervals.icu INTO the app's local DB (André, 2026-08-18: "I would
// prefer to have all activities living on my app"). Pull-only. Brings in EVERY activity as a
// lightweight record (date/type/duration/distance/ascent) so it shows in the activity list,
// Totals and Calendar - including indoor/Zwift rides that have no GPS. The map trace for an
// outdoor ride is NOT in that payload - it needs a separate per-activity streams call, which
// IntervalsTracks.ts does as a throttled backfill (2026-08-26; this used to be an unimplemented
// "follow-up" comment, so every imported outdoor move showed no map at all).
//
// De-dup is two-layered: intervals activities are namespaced `icu:<id>` so re-importing is a
// no-op, and an intervals activity that actually ORIGINATED from the watch (same day + type +
// distance) is skipped so it never double-counts a move already synced off the watch.
const API_BASE = 'https://intervals.icu/api/v1';

// intervals.icu activity `type` -> this app's activity_type label (matches the watch-side
// labels used for gear auto-assign etc.). Unknown types pass through as-is.
const TYPE_MAP: Record<string, string> = {
  Ride: 'Cycling', VirtualRide: 'Cycling', MountainBikeRide: 'Mountain biking',
  GravelRide: 'Cycling', CyclocrossRide: 'Cycling', TrackRide: 'Cycling',
  Run: 'Running', VirtualRun: 'Running', TrailRun: 'Trail running',
  Walk: 'Walking', Hike: 'Hiking', Swim: 'Swimming', OpenWaterSwim: 'Swimming',
};

function mapType(t: string): string {
  return TYPE_MAP[t] || t || 'Other';
}

export interface ImportResult {
  imported: number;
  skipped: number;
}

/**
 * Import every intervals.icu activity (optionally only those on/after `afterDate`, an ISO
 * "YYYY-MM-DD") into the local DB. Idempotent - already-imported (`icu:<id>`) and blacklisted
 * activities are skipped, as are ones that match a watch move already in the DB.
 */
export async function importActivitiesFromIntervals(afterDate?: string): Promise<ImportResult> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) throw new Error('intervals.icu is not connected');

  const oldest = (afterDate && /^\d{4}-\d{2}-\d{2}/.test(afterDate)) ? afterDate.slice(0, 10) : '2010-01-01';
  const newest = new Date().toISOString().slice(0, 10);
  const url = `${API_BASE}/athlete/${encodeURIComponent(creds.athleteId)}/activities`
    + `?oldest=${oldest}&newest=${newest}`;
  const resp = await fetch(url, {
    headers: { Authorization: 'Basic ' + btoa(`API_KEY:${creds.apiKey}`), 'User-Agent': 'Sommet/1.0' },
  });
  if (!resp.ok) throw new Error(`intervals.icu activities: HTTP ${resp.status}`);
  const acts = await resp.json();
  if (!Array.isArray(acts)) return { imported: 0, skipped: 0 };

  const knownIds = new Set(await getAllSyncedIds());
  const watchActs = (await getAllActivities()).filter(e => !e.id.startsWith('icu:'));

  let imported = 0;
  let skipped = 0;
  for (const a of acts) {
    const icuId = `icu:${a?.id}`;
    if (!a?.id || knownIds.has(icuId)) { skipped++; continue; }

    const date = String(a.start_date_local || a.start_date || '').slice(0, 19);
    const distance_m = Math.round(Number(a.icu_distance ?? a.distance ?? 0)) || 0;
    const duration_s = Math.round(Number(a.moving_time ?? a.elapsed_time ?? 0)) || 0;
    const type = mapType(String(a.type || ''));

    // Skip if this is really a watch move already synced locally (same day, type, ~distance).
    const day = date.slice(0, 10);
    const dupOfWatch = watchActs.some(e =>
      e.date.slice(0, 10) === day && e.activity_type === type &&
      Math.abs(e.distance_m - distance_m) <= Math.max(50, distance_m * 0.01));
    if (dupOfWatch) { skipped++; continue; }

    // Which device recorded it (2026-08-26, desktop parity). intervals.icu gives a real name
    // for nearly every activity; fall back to a friendly form of the upload source when the
    // device itself is unnamed.
    const device = String(a.device_name || a.source || '').trim();

    const record: ActivityRecord = {
      id: icuId,
      synced_at: Date.now(),
      gpx_path: '',                       // GPS trace is backfilled by IntervalsTracks.ts
      date,
      duration_s,
      distance_m,
      d_plus: Math.round(Number(a.total_elevation_gain ?? a.icu_elevation_gain ?? 0)) || 0,
      activity_type: type,
      device,
    };
    await markActivitySynced(record);
    knownIds.add(icuId);
    imported++;
  }

  // Pull the GPS traces for what was just imported (2026-08-26, desktop parity). Bounded per
  // run so this stays a background trickle rather than one request per activity across the whole
  // history; it is resumable, so calling import again continues where it left off. Failure here
  // must not fail the import itself - the records are already saved and usable without a map.
  try {
    await backfillIntervalsTracks(40);
  } catch {
    /* non-fatal: traces fill in on a later run */
  }

  return { imported, skipped };
}
