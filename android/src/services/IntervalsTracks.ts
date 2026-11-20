import AsyncStorage from '@react-native-async-storage/async-storage';
import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';
import { writeGpxFile } from './GpxService';
import { isIndoorSport } from './SportNames';
import { getKeepRecentOnly, lightCutoffMinute } from './LibraryPrefs';
import { getAllActivities, markActivitySynced } from '../database/db';

// Backfill the GPS trace for activities imported from intervals.icu (André, 2026-08-26:
// "android and desktop should have feature parity").
//
// IntervalsImport.ts pulls the activity LIST, which carries summary numbers and no positions at
// all, so every imported outdoor move landed with an empty gpx_path and rendered as having no
// map. Its own comment called the trace "a follow-up" - this is that follow-up. Same gap, same
// fix, and the same verified stream shape as the desktop's ActivityService::fetchNextTrack().
//
// The positions live behind a SEPARATE per-activity endpoint, so this is one GET per activity:
// far too many to fire at once across a full history. It therefore walks a bounded, resumable
// queue, newest first, one request at a time.
//
// Stream shape, verified against the live API (2026-08-26) rather than assumed: the response is
// an ARRAY of stream objects and for "latlng" the positions are split across TWO parallel
// arrays - `data` holds the latitudes and `data2` the longitudes. They are NOT [lat,lon] pairs;
// treating them as pairs yields garbage coordinates.

const API_BASE = 'https://intervals.icu/api/v1';

// Activities intervals.icu answered for with no positions (a pool swim, a trainer ride filed as
// a plain "Ride"). Asked once, not on every run: they used to stay pending for good, so once the
// 40 newest pending were such rides, the backfill never reached an older outdoor one.
const NO_TRACK_KEY = 'intervals_no_track_v1';

export async function loadNoTrack(): Promise<Set<string>> {
  try {
    const v = JSON.parse((await AsyncStorage.getItem(NO_TRACK_KEY)) || '[]');
    return new Set(Array.isArray(v) ? v.map(String) : []);
  } catch { return new Set(); }
}

export interface BackfillResult {
  filled: number;
  remaining: number;
}

function esc(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

// Build the same minimal GPX the rest of the app already reads (GpxParser only needs trkpt
// lat/lon plus optional ele), so imported traces work with the existing map and elevation views
// without any parser change.
function buildGpx(name: string, lat: number[], lon: number[], alt: number[]): string {
  const pts: string[] = [];
  const n = Math.min(lat.length, lon.length);
  for (let i = 0; i < n; i++) {
    const la = lat[i], lo = lon[i];
    if (typeof la !== 'number' || typeof lo !== 'number') continue;   // real gaps in a trace
    const ele = typeof alt[i] === 'number' ? `<ele>${alt[i]}</ele>` : '';
    pts.push(`<trkpt lat="${la}" lon="${lo}">${ele}</trkpt>`);
  }
  if (pts.length === 0) return '';
  return `<?xml version="1.0" encoding="UTF-8"?>
<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">
<trk><name>${esc(name)}</name><trkseg>${pts.join('')}</trkseg></trk>
</gpx>`;
}

// Fetch and store the trace for one intervals activity. Returns the written file's FULL path
// (what gpx_path must hold - MapScreen calls readGpxFile(activity.gpx_path) directly), 'none'
// when intervals.icu has no positions for it, or null on an error worth trying again later.
async function backfillOne(
  icuId: string, apiKey: string, name: string,
): Promise<string | 'none' | null> {
  const gpx = await fetchIntervalsGpx(icuId, apiKey, name);
  if (gpx === null || gpx === 'none') return gpx;
  // overwrite=true: a re-run should replace a partial file rather than silently keep it.
  return await writeGpxFile(icuId, gpx, true);
}

/** One intervals.icu activity's trace as a GPX (the light library also asks for an older move's
 *  Track this way when it is opened, sync/13): 'none' when it has no positions, null on an error
 *  worth trying again later. */
export async function fetchIntervalsGpx(
  icuId: string, apiKey: string, name: string,
): Promise<string | 'none' | null> {
  const raw = icuId.replace(/^icu:/, '');
  const resp = await fetch(`${API_BASE}/activity/${encodeURIComponent(raw)}/streams?types=latlng,altitude`, {
    headers: { Authorization: 'Basic ' + btoa(`API_KEY:${apiKey}`), 'User-Agent': 'Sommet/1.0' },
  });
  // No streams for it, gone, or refused for good: don't ask again. Login, rate-limit and server
  // errors can clear up, so those are asked again on the next run.
  if ([400, 404, 410, 422].includes(resp.status)) return 'none';
  if (!resp.ok) return null;
  const streams = await resp.json();
  if (!Array.isArray(streams)) return null;

  let lat: number[] = [], lon: number[] = [], alt: number[] = [];
  for (const s of streams) {
    if (s?.type === 'latlng') { lat = s.data ?? []; lon = s.data2 ?? []; }
    else if (s?.type === 'altitude') { alt = s.data ?? []; }
  }
  return buildGpx(name, lat, lon, alt) || 'none';
}

// Walk up to `maxCount` imported activities that still have no trace, newest first.
// Deliberately sequential: one in-flight request at a time, so a long history trickles in
// rather than hammering intervals.icu. Call again to continue where it left off.
export async function backfillIntervalsTracks(maxCount = 40): Promise<BackfillResult> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) return { filled: 0, remaining: 0 };

  const all = await getAllActivities();
  const noTrack = await loadNoTrack();
  // Light library (sync/13): only the last 90 days; an older one loads when it is opened.
  const pending = intervalsTrackQueue(all, noTrack, (await getKeepRecentOnly()) ? lightCutoffMinute() : '');

  let filled = 0, none = 0;
  for (const act of pending.slice(0, maxCount)) {
    try {
      const path = await backfillOne(act.id, creds.apiKey, act.activity_type || 'Activity');
      if (path === 'none') {
        noTrack.add(act.id);
        none++;
      } else if (path) {
        // Store the REAL path the writer returned - MapScreen reads gpx_path directly.
        await markActivitySynced({ ...act, gpx_path: path, synced_at: Date.now() });
        filled++;
      }
    } catch {
      // One bad activity must not abort the whole backfill - keep going.
    }
  }
  if (none > 0) {
    try { await AsyncStorage.setItem(NO_TRACK_KEY, JSON.stringify([...noTrack])); } catch { /* asked again next run */ }
  }
  return { filled, remaining: Math.max(0, pending.length - filled - none) };
}

/** The imported activities still worth asking intervals.icu for a trace, newest first: a real
 *  distance (so it plausibly has positions), no trace yet, not done indoors (a Zwift ride's
 *  positions are a virtual world: no map, no weather, not "time outside" in Totals - same as an
 *  indoor move read off the watch), and not already found to have none. */
export function intervalsTrackQueue<T extends { id: string; distance_m?: number; gpx_path?: string;
                                               activity_type?: string; date: string }>(
  all: T[], noTrack: Set<string>, cutoff = '',
): T[] {
  return all
    .filter(a => a.id.startsWith('icu:') && (a.distance_m ?? 0) > 0 && !a.gpx_path
                 && !isIndoorSport(a.activity_type || '') && !noTrack.has(a.id)
                 && (!cutoff || a.date.slice(0, 16) >= cutoff))
    .sort((a, b) => b.date.localeCompare(a.date));
}
