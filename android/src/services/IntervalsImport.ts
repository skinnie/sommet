import AsyncStorage from '@react-native-async-storage/async-storage';
import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';
import { backfillIntervalsTracks } from './IntervalsTracks';
import {
  markActivitySynced, getAllSyncedIds, getAllActivities, ActivityRecord, updateImportedActivity,
  rejectDuplicateImport, removeImportedActivity,
} from '../database/db';
import { sportNameForIntervalsType, isJunkActivity } from './SportNames';
import { importFromIntervals as importGearFromIntervals } from './GearMirrorService';

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
const DUP_WINDOW_MS = 180000;   // desktop kWindowSecs

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

  // An activity deleted on intervals.icu goes here too (André, 2026-10-04: 14 multi-day GPS track
  // logs were deleted there - up to 39,310 h each - and would otherwise have stayed in this
  // app's totals for good). Only on a full-history import, and only when the answer is clearly
  // the whole list (at least half of what is stored), so a cut-short reply cannot empty the app.
  if (!afterDate && acts.length > 0 && acts.length >= icuById.size / 2) {
    const remote = new Set(acts.map((a: any) => `icu:${a?.id}`));
    for (const id of icuById.keys()) {
      if (!remote.has(id)) { await removeImportedActivity(id); icuById.delete(id); }
    }
  }

  // Oldest upload first, so of two copies of one ride the first uploaded is the one kept.
  const idNum = (a: any) => Number(String(a?.id ?? '').replace(/\D/g, '')) || 0;
  acts.sort((x: any, y: any) => idNum(x) - idNum(y));
  const keptIcu: { ms: number; dur: number }[] = [];

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
    // Is this the same move as one already kept? The desktop's rule (dedupeActivities): starts
    // within three minutes and durations within 0.66-1.5x of each other - whatever the sport,
    // since a move retyped on intervals.icu ("Walking" -> "Hiking") is still the same move.
    //   * against moves this device read off a watch (plus the older same day/type/distance test);
    //   * against another intervals.icu copy already taken in this run - one ride uploaded twice
    //     (André, 2026-10-04: the 300 km of 04/04/2026 was there from the Edge 1040 and again as
    //     "Tacx Training", "likely an export from rungap ... maybe a duplicate"). The copy with the
    //     lower intervals.icu id - the first one uploaded - is the one kept.
    const day = date.slice(0, 10);
    const startMs = Date.parse(date);
    const sameMove = (otherMs: number, otherDur: number) => {
      if (!isFinite(startMs) || !isFinite(otherMs) || Math.abs(otherMs - startMs) > DUP_WINDOW_MS) return false;
      if (duration_s > 0 && otherDur > 0) { const r = duration_s / otherDur; if (r > 1.5 || r < 0.66) return false; }
      return true;
    };
    const dupOfWatch = watchActs.some(e => {
      if (e.date.slice(0, 10) !== day) return false;
      if (e.activity_type === type && Math.abs(e.distance_m - distance_m) <= Math.max(50, distance_m * 0.01)) return true;
      return sameMove(Date.parse(e.date.slice(0, 19)), e.duration_s);
    }) || keptIcu.some(k => sameMove(k.ms, k.dur));
    if (knownIds.has(icuId)) {
      // An earlier import kept the intervals.icu copy of a move this device read off the watch
      // itself (the type differed, so the old same-type test missed it): drop the copy.
      if (dupOfWatch && icuById.has(icuId)) { await rejectDuplicateImport(icuId); skipped++; continue; }
      // Already here. Bring it up to date: calories (never stored before 2026-10-03) and the
      // sport.
      const have = icuById.get(icuId);
      if (have) {
        const fix: { activity_type?: string; energy_kcal?: number } = {};
        if (energy_kcal > 0 && !have.energy_kcal) fix.energy_kcal = energy_kcal;
        // The sport follows intervals.icu: one changed there (2026-10-05: 23 handheld tracks
        // retyped from Run to Hike still read "Running" here), or a name from the old private
        // table. Nothing in the app edits the sport of an imported activity, so there is no
        // local choice to overwrite.
        if (rawType && have.activity_type !== type) fix.activity_type = type;
        if (fix.activity_type !== undefined || fix.energy_kcal !== undefined)
          await updateImportedActivity(icuId, fix);
      }
      if (isFinite(startMs) && !isJunkActivity(duration_s, distance_m)) keptIcu.push({ ms: startMs, dur: duration_s });
      skipped++; continue;
    }

    // Junk/test entries never come in (desktop rule since 2026-08-24).
    if (isJunkActivity(duration_s, distance_m)) { skipped++; continue; }

    if (dupOfWatch) { skipped++; continue; }
    if (isFinite(startMs)) keptIcu.push({ ms: startMs, dur: duration_s });

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

// Automatic refresh from intervals.icu, like the desktop's autoIntervalsCloudSync (André,
// 2026-10-04, on a bike showing 50,166 km here and 50,559 km on the desktop: "make it as the
// desktop"). The desktop pulls gear and activities by itself; Android only did on a button in
// Settings, so the tablet's gear was from 18/08. Called when the app opens and when Gear opens:
//   * gear: pull-only import (GearMirrorService.importFromIntervals - no pushes, no deletes);
//   * activities: the last 30 days each time, the whole history once a day (that is the run that
//     also applies deletions and the one-time fixes of 2026-10-03/04).
// At most once every 15 minutes - intervals.icu rate-limits (HTTP 429). No-op when not connected.
const AUTO_LAST_KEY = 'intervals.autosync.last';
const AUTO_FULL_KEY = 'intervals.autosync.fullImport.v4';   // bump = one full pass after an update
const AUTO_MIN_GAP_MS = 15 * 60 * 1000;
const AUTO_FULL_GAP_MS = 24 * 3600 * 1000;
let autoSyncRunning = false;
export async function runIntervalsAutoSync(): Promise<boolean> {
  if (autoSyncRunning) return false;
  if (!(await getIntervalsIcuCredentials())) return false;
  const now = Date.now();
  if (now - Number((await AsyncStorage.getItem(AUTO_LAST_KEY)) || 0) < AUTO_MIN_GAP_MS) return false;
  autoSyncRunning = true;
  try {
    await AsyncStorage.setItem(AUTO_LAST_KEY, String(now));
    await importGearFromIntervals({ keepLocalEdits: true }).catch(() => {});
    const lastFull = Number((await AsyncStorage.getItem(AUTO_FULL_KEY)) || 0);
    if (now - lastFull >= AUTO_FULL_GAP_MS) {
      await importActivitiesFromIntervals();
      await AsyncStorage.setItem(AUTO_FULL_KEY, String(now));
    } else {
      const since = new Date(now - 30 * 24 * 3600 * 1000).toISOString().slice(0, 10);
      await importActivitiesFromIntervals(since);
    }
    return true;
  } finally {
    autoSyncRunning = false;
  }
}
