import AsyncStorage from '@react-native-async-storage/async-storage';
import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';
import { backfillIntervalsTracks } from './IntervalsTracks';
import {
  markActivitySynced, getAllSyncedIds, getAllActivities, ActivityRecord, updateImportedActivity,
  rejectDuplicateImport,
} from '../database/db';
import { sportNameForIntervalsType, canonicalSportName, isJunkActivity } from './SportNames';

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

// What this file's own table produced before 2026-10-03 (a short private copy that had drifted
// from the desktop's). Kept only to recognise rows it wrote, so a re-import can give them the
// shared name without touching a sport the user changed by hand.
const OLD_TYPE_MAP: Record<string, string> = {
  Ride: 'Cycling', VirtualRide: 'Cycling', MountainBikeRide: 'Mountain biking',
  GravelRide: 'Cycling', CyclocrossRide: 'Cycling', TrackRide: 'Cycling',
  Run: 'Running', VirtualRun: 'Running', TrailRun: 'Trail running',
  Walk: 'Walking', Hike: 'Hiking', Swim: 'Swimming', OpenWaterSwim: 'Swimming',
};
const oldMapType = (t: string) => OLD_TYPE_MAP[t] || t || 'Other';

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
  const all = await getAllActivities();
  const watchActs = all.filter(e => !e.id.startsWith('icu:'));
  const icuById = new Map(all.filter(e => e.id.startsWith('icu:')).map(e => [e.id, e] as [string, ActivityRecord]));

  let imported = 0;
  let skipped = 0;
  for (const a of acts) {
    const icuId = `icu:${a?.id}`;
    if (!a?.id) { skipped++; continue; }
    const rawType = String(a.type || '');
    const type = sportNameForIntervalsType(rawType);
    const energy_kcal = Math.round(Number(a.calories ?? 0)) || 0;
    const date = String(a.start_date_local || a.start_date || '').slice(0, 19);
    const distance_m = Math.round(Number(a.icu_distance ?? a.distance ?? 0)) || 0;
    const duration_s = Math.round(Number(a.moving_time ?? a.elapsed_time ?? 0)) || 0;
    // Is this really a watch move already synced locally? Either the same day, type and
    // ~distance, or - whatever the type, since a move retyped on intervals.icu ("Walking" ->
    // "Hiking") is still the same move - the same start within two minutes and a similar
    // duration (seen on the tablet, 2026-10-04: four such pairs).
    const day = date.slice(0, 10);
    const startMs = Date.parse(date);
    const dupOfWatch = watchActs.some(e => {
      if (e.date.slice(0, 10) !== day) return false;
      if (e.activity_type === type && Math.abs(e.distance_m - distance_m) <= Math.max(50, distance_m * 0.01)) return true;
      const eMs = Date.parse(e.date.slice(0, 19));
      return isFinite(startMs) && isFinite(eMs) && Math.abs(eMs - startMs) <= 120000
        && Math.abs(e.duration_s - duration_s) <= Math.max(60, duration_s * 0.15);
    });
    if (knownIds.has(icuId)) {
      // An earlier import kept the intervals.icu copy of a move this device read off the watch
      // itself (the type differed, so the old same-type test missed it): drop the copy.
      if (dupOfWatch && icuById.has(icuId)) { await rejectDuplicateImport(icuId); skipped++; continue; }
      // Already here. Bring an earlier import up to date: calories (never stored before
      // 2026-10-03) and the shared sport name - the latter only where the row still holds what
      // the old import wrote, so a sport the user changed by hand stays.
      const have = icuById.get(icuId);
      if (have) {
        const fix: { activity_type?: string; energy_kcal?: number } = {};
        if (energy_kcal > 0 && !have.energy_kcal) fix.energy_kcal = energy_kcal;
        if (have.activity_type !== type
            && (have.activity_type === oldMapType(rawType) || have.activity_type === canonicalSportName(oldMapType(rawType))))
          fix.activity_type = type;
        if (fix.activity_type !== undefined || fix.energy_kcal !== undefined)
          await updateImportedActivity(icuId, fix);
      }
      skipped++; continue;
    }

    // Junk/test entries never come in (desktop rule since 2026-08-24).
    if (isJunkActivity(duration_s, distance_m)) { skipped++; continue; }

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
      energy_kcal,
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

// One quiet re-import after the 2026-10-03 update, so activities imported earlier get their
// calories and the shared sport names without the user finding the button in Settings. No-op
// when intervals.icu is not connected (it then runs on the first launch after connecting).
const BACKFILL_KEY = 'intervals.import.backfill.kcal2';   // 2: also drops watch-move duplicates
export async function backfillIntervalsImportOnce(): Promise<void> {
  if (await AsyncStorage.getItem(BACKFILL_KEY)) return;
  if (!(await getIntervalsIcuCredentials())) return;
  await importActivitiesFromIntervals();
  await AsyncStorage.setItem(BACKFILL_KEY, '1');
}
