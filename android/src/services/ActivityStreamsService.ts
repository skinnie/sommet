import RNFS from 'react-native-fs';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { ActivityRecord } from '../database/db';
import { fitPathForGpx, writeFitFile } from './GpxService';
import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';
import { base64ToBytes, bytesToBase64 } from './Base64';
// The FIT decoder and the activity-screen maths are SHARED with the desktop (edit shared/, then
// run tools/gen_activity_view.py): the same JavaScript the desktop's QML runs, and a decoder that
// tools/test_activity_streams_parity.js checks against the desktop's Python one.
// eslint-disable-next-line @typescript-eslint/no-var-requires
const { streamsFromFit } = require('../config/activityStreams');

// The activity screen's data (André, 2026-09-27 - desktop parity, plan ACT-6): what the charts,
// zones, laps and pool lengths are drawn from. A watch move has its FIT next to its GPX (written
// at sync); an intervals.icu import keeps no file, so its FIT is fetched once and kept.

export type Streams = any;   // tools/activity_streams.py's JSON shape (see shared/activity_streams.js)

const API = 'https://intervals.icu/api/v1';
const cache = new Map<string, Streams>();

function authHeader(apiKey: string): Record<string, string> {
  return { Authorization: 'Basic ' + btoa(`API_KEY:${apiKey}`), 'User-Agent': 'Sommet/1.0' };
}

function isFit(b: Uint8Array): boolean {
  return b.length > 12 && String.fromCharCode(b[8], b[9], b[10], b[11]) === '.FIT';
}

/** The activity's FIT bytes: on disk next to the GPX, else (intervals.icu import) downloaded -
 *  the original upload when it's a FIT, else intervals.icu's own - and kept for next time. */
async function fitBytes(activity: ActivityRecord): Promise<Uint8Array | null> {
  if (activity.gpx_path) {
    const p = await fitPathForGpx(activity.gpx_path);
    if (p) return base64ToBytes(await RNFS.readFile(p, 'base64'));
  }
  const kept = `${RNFS.DocumentDirectoryPath}/activities/${activity.id.replace(/[^\w.-]/g, '_')}.fit`;
  if (await RNFS.exists(kept)) return base64ToBytes(await RNFS.readFile(kept, 'base64'));
  if (!activity.id.startsWith('icu:')) return null;
  const creds = await getIntervalsIcuCredentials();
  if (!creds) throw new Error('Connect intervals.icu in Settings to see this activity\'s charts.');
  const id = activity.id.slice(4);
  for (const kind of ['file', 'fit-file']) {
    const res = await fetch(`${API}/activity/${encodeURIComponent(id)}/${kind}`, { headers: authHeader(creds.apiKey) });
    if (!res.ok) continue;
    const b = new Uint8Array(await res.arrayBuffer());
    if (isFit(b)) {
      await writeFitFile(activity.id.replace(/[^\w.-]/g, '_'), bytesToBase64(b), true);
      return b;
    }
  }
  throw new Error('intervals.icu returned no FIT for this activity.');
}

/** Decoded streams for an activity (memory-cached). Null when the move has no file at all. */
export async function getStreams(activity: ActivityRecord): Promise<Streams | null> {
  const hit = cache.get(activity.id);
  if (hit) return hit;
  const b = await fitBytes(activity);
  if (!b) return null;
  const st = streamsFromFit(b, 2000);
  cache.set(activity.id, st);
  if (cache.size > 12) cache.delete(cache.keys().next().value as string);
  return st;
}

const ZONES_KEY = 'activity:zoneGroups';

/** intervals.icu per-sport settings (HR zones, FTP, power zones), cached for offline use. */
export async function getZoneGroups(refresh = false): Promise<any[]> {
  const cached = await AsyncStorage.getItem(ZONES_KEY).catch(() => null);
  if (cached && !refresh) {
    getZoneGroups(true).catch(() => undefined);      // refresh in the background
    return JSON.parse(cached);
  }
  const creds = await getIntervalsIcuCredentials();
  if (!creds) return cached ? JSON.parse(cached) : [];
  const res = await fetch(`${API}/athlete/${encodeURIComponent(creds.athleteId)}/sport-settings`, { headers: authHeader(creds.apiKey) });
  if (!res.ok) return cached ? JSON.parse(cached) : [];
  const raw = await res.json();
  const keep = ['types', 'ftp', 'power_zones', 'power_zone_names', 'hr_zones', 'hr_zone_names', 'lthr', 'max_hr',
                'threshold_pace', 'pace_zones', 'pace_zone_names'];
  const groups = (Array.isArray(raw) ? raw : []).map((g: any) => Object.fromEntries(keep.map(k => [k, g[k] ?? null])));
  await AsyncStorage.setItem(ZONES_KEY, JSON.stringify(groups)).catch(() => undefined);
  return groups;
}

/** Structured workouts planned on intervals.icu for a day (YYYY-MM-DD), with their steps. */
export async function getPlanned(date: string): Promise<{ name: string; type: string; workout_doc: any }[]> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds || !/^\d{4}-\d{2}-\d{2}$/.test(date)) return [];
  const res = await fetch(`${API}/athlete/${encodeURIComponent(creds.athleteId)}/events?oldest=${date}&newest=${date}&category=WORKOUT`,
                          { headers: authHeader(creds.apiKey) });
  if (!res.ok) return [];
  const ev = await res.json();
  return (Array.isArray(ev) ? ev : []).filter((e: any) => e.workout_doc && (e.workout_doc.steps || []).length)
    .map((e: any) => ({ name: e.name, type: e.type, workout_doc: e.workout_doc }));
}
