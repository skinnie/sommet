import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';
import { writeGpxFile } from './GpxService';
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
// (what gpx_path must hold - MapScreen calls readGpxFile(activity.gpx_path) directly), or null
// when the activity has no usable trace.
async function backfillOne(
  icuId: string, apiKey: string, name: string,
): Promise<string | null> {
  const raw = icuId.replace(/^icu:/, '');
  const resp = await fetch(`${API_BASE}/activity/${encodeURIComponent(raw)}/streams?types=latlng,altitude`, {
    headers: { Authorization: 'Basic ' + btoa(`API_KEY:${apiKey}`), 'User-Agent': 'Sommet/1.0' },
  });
  if (!resp.ok) return null;                     // indoor/streamless activity, or a transient error
  const streams = await resp.json();
  if (!Array.isArray(streams)) return null;

  let lat: number[] = [], lon: number[] = [], alt: number[] = [];
  for (const s of streams) {
    if (s?.type === 'latlng') { lat = s.data ?? []; lon = s.data2 ?? []; }
    else if (s?.type === 'altitude') { alt = s.data ?? []; }
  }
  const gpx = buildGpx(name, lat, lon, alt);
  if (!gpx) return null;

  // overwrite=true: a re-run should replace a partial file rather than silently keep it.
  return await writeGpxFile(icuId, gpx, true);
}

// Walk up to `maxCount` imported activities that still have no trace, newest first.
// Deliberately sequential: one in-flight request at a time, so a long history trickles in
// rather than hammering intervals.icu. Call again to continue where it left off.
export async function backfillIntervalsTracks(maxCount = 40): Promise<BackfillResult> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) return { filled: 0, remaining: 0 };

  const all = await getAllActivities();
  // Only intervals imports, with a real distance (so they plausibly have positions - an indoor
  // trainer ride has none), and no trace yet.
  const pending = all
    .filter(a => a.id.startsWith('icu:') && (a.distance_m ?? 0) > 0 && !a.gpx_path)
    .sort((a, b) => b.date.localeCompare(a.date));

  let filled = 0;
  for (const act of pending.slice(0, maxCount)) {
    try {
      const path = await backfillOne(act.id, creds.apiKey, act.activity_type || 'Activity');
      if (path) {
        // Store the REAL path the writer returned - MapScreen reads gpx_path directly.
        await markActivitySynced({ ...act, gpx_path: path, synced_at: Date.now() });
        filled++;
      }
    } catch {
      // One bad activity must not abort the whole backfill - keep going.
    }
  }
  return { filled, remaining: Math.max(0, pending.length - filled) };
}
