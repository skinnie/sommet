import {
  ActivityRecord, getAllActivities, getActivityById, sommetUid, setTrackRemote, setTrackLocal,
  clearTrackRemote, getCachedTracks, markTracksRemote,
} from '../database/db';
import {
  deleteGpxFile, removeFitForGpx, writeGpxFile, trackFileBytes, fileExists,
} from './GpxService';
import { getSommetSyncCfg, getPushedBlobs, getPushedFits, fetchGpxBlob } from './SommetSync';
import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';
import { fetchIntervalsGpx, intervalsTrackQueue, loadNoTrack } from './IntervalsTracks';
import { getKeepRecentOnly, getLibraryCacheMb, lightCutoffMinute } from './LibraryPrefs';
import { rescuePending } from './ServerMove';

// Light library (sync/13, André 2026-11-20; the desktop's trimOldTracks / fetchRemoteTrack /
// evictTrackCache in activityservice.cpp). Every move stays in the list. With "Last 90 days" a
// Track older than 90 days is kept here only while it is NOT confirmed elsewhere. Confirmed = the
// NAS holds its GPX (blobsPushed: sent from here or fetched from there), or it is an intervals.icu
// import and intervals.icu is connected (its streams can be asked again). A watch-only setup has
// neither, so nothing is dropped. An older move opened here fetches its Track back into a space
// with a size limit, the oldest fetch dropped first.

export interface TrimRow {
  id: string; date: string; device?: string; gpx_path: string; track_cached_at?: number;
}
export interface TrimPick { id: string; gpxPath: string; dropFit: boolean; }

/** Which local Tracks may leave this device. `nasOn`/`intervalsOn`: whether Sommet Sync and
 *  intervals.icu are set up. Pure, for the tests. */
export function pickTracksToTrim(rows: TrimRow[], o: {
  cutoff: string; nasOn: boolean; intervalsOn: boolean; onNas: Set<string>; fitsOnNas: Set<string>;
}): TrimPick[] {
  if (!o.nasOn && !o.intervalsOn) return [];   // watch only: everything stays here
  // Two moves started in the same minute share a uid and the NAS holds one GPX for both:
  // trimming them would lose the other for good, so they stay.
  const uidCount = new Map<string, number>();
  for (const r of rows) {
    const u = sommetUid(r.device ?? '', r.date);
    uidCount.set(u, (uidCount.get(u) ?? 0) + 1);
  }
  const out: TrimPick[] = [];
  for (const r of rows) {
    if (!r.gpx_path || (r.track_cached_at ?? 0) > 0) continue;   // fetched ones: the size limit's job
    if (!(r.date.slice(0, 16) < o.cutoff)) continue;
    const uid = sommetUid(r.device ?? '', r.date);
    const unique = uidCount.get(uid) === 1;
    const fromIntervals = o.intervalsOn && r.id.startsWith('icu:');
    const nasHasIt = o.nasOn && unique && o.onNas.has(uid);
    if (!fromIntervals && !nasHasIt) continue;
    // The FIT goes only when it can be asked again too (charts fetch it from the NAS, then
    // intervals.icu); otherwise it stays next to where the GPX was.
    const dropFit = fromIntervals || (o.nasOn && unique && o.fitsOnNas.has(uid));
    out.push({ id: r.id, gpxPath: r.gpx_path, dropFit });
  }
  return out;
}

/** Opened older moves to drop so the space stays under `limitBytes`, oldest fetch first. `keep`
 *  is never dropped (the one just opened). Pure, for the tests. */
export function pickEvictions(cached: { id: string; bytes: number; fetchedAt: number }[],
                              limitBytes: number, keep = ''): string[] {
  let total = cached.reduce((s, c) => s + c.bytes, 0);
  const out: string[] = [];
  for (const c of [...cached].sort((a, b) => a.fetchedAt - b.fetchedAt)) {
    if (total <= limitBytes) break;
    if (c.id === keep) continue;
    out.push(c.id);
    total -= c.bytes;
  }
  return out;
}

async function intervalsConnected(): Promise<boolean> {
  try { return !!(await getIntervalsIcuCredentials()); } catch { return false; }
}

let tidying: Promise<void> | null = null;

/** After a sync (and when the app opens): with "Last 90 days", older Tracks the server holds
 *  leave this device; with "Everything", Tracks kept on the server come back, a batch at a time. */
export function trimOldTracks(): Promise<void> {
  if (!tidying) tidying = tidyOnce().finally(() => { tidying = null; });
  return tidying;
}

async function tidyOnce(): Promise<void> {
  const recent = await getKeepRecentOnly();
  const nasOn = !!(await getSommetSyncCfg());
  const intervalsOn = await intervalsConnected();
  const rows = await getAllActivities();
  if (!recent) { await restoreRemoteTracks(rows, nasOn); return; }

  const cutoff = lightCutoffMinute();
  // Sommet Sync moved to another server (sync/14) and the old one still holds some Tracks alone:
  // nothing more leaves this device until they are copied over, so none ends up on neither.
  const picks = await rescuePending() ? [] : pickTracksToTrim(rows, {
    cutoff, nasOn, intervalsOn, onNas: await getPushedBlobs(), fitsOnNas: await getPushedFits(),
  });
  for (const p of picks) {
    try {
      if (p.dropFit) await removeFitForGpx(p.gpxPath);
      await deleteGpxFile(p.gpxPath);
      await setTrackRemote(p.id);
    } catch { /* this one stays; tried again next time */ }
  }
  // intervals.icu imports older than 90 days that never got their Track: asked when opened.
  if (intervalsOn) {
    const noTrack = await loadNoTrack();
    const older = intervalsTrackQueue(rows.filter(r => !r.track_remote), noTrack)
      .filter(r => r.date.slice(0, 16) < cutoff).map(r => r.id);
    await markTracksRemote(older);
  }
}

/** "Everything": bring back from the NAS the Tracks kept there only (100 per run). */
async function restoreRemoteTracks(rows: ActivityRecord[], nasOn: boolean): Promise<void> {
  if (!nasOn) return;   // intervals.icu imports come back through IntervalsTracks' backfill
  let n = 0;
  for (const r of rows) {
    if (r.track_remote !== 1 || r.gpx_path) continue;
    if (++n > 100) break;
    const gpx = await fetchGpxBlob(sommetUid(r.device ?? '', r.date));
    if (!gpx || gpx.indexOf('<trkpt') < 0) continue;
    const path = await writeGpxFile(r.id, gpx, true);
    if (path) await setTrackLocal(r.id, path, 0);
  }
}

/** Keeps the opened older moves' Tracks under the size limit (Settings), oldest fetch first. */
export async function evictTrackCache(keep = ''): Promise<void> {
  const limit = (await getLibraryCacheMb()) * 1024 * 1024;
  const cached = await getCachedTracks();
  const sized = [];
  for (const c of cached) sized.push({ id: c.id, bytes: await trackFileBytes(c.gpx_path), fetchedAt: c.track_cached_at });
  const drop = new Set(pickEvictions(sized, limit, keep));
  for (const c of cached) {
    if (!drop.has(c.id)) continue;
    await removeFitForGpx(c.gpx_path);
    await deleteGpxFile(c.gpx_path).catch(() => undefined);
    await setTrackRemote(c.id);
  }
}

// ── Fetching an older move's Track when it is opened ──────────────────────────────────────

type Listener = (id: string, gpxPath: string) => void;
const listeners = new Set<Listener>();
/** A screen showing a move hears when its Track arrives (the desktop's trackFetched signal). */
export function onTrackFetched(cb: Listener): () => void {
  listeners.add(cb);
  return () => { listeners.delete(cb); };
}

const inFlight = new Map<string, Promise<string | null>>();
// One fetch at a time: scrolling the list past many older moves must not start them all at once.
let queue: Promise<unknown> = Promise.resolve();

/** Whether this move's Track is kept on the server and not here (the list and the move page then
 *  ask for it with fetchRemoteTrack). */
export function isTrackRemote(a: { gpx_path?: string; track_remote?: number }): boolean {
  return !a.gpx_path && a.track_remote === 1;
}

/** The local path of a move's Track kept on the server: fetched from the NAS (its GPX), else from
 *  intervals.icu (its streams), written like a pulled one and counted against the size limit.
 *  Null when it can't be fetched now (offline), or nowhere has it. */
export function fetchRemoteTrack(activityId: string): Promise<string | null> {
  const running = inFlight.get(activityId);
  if (running) return running;
  const p = queue.then(() => fetchOne(activityId)).catch(() => null)
    .finally(() => { inFlight.delete(activityId); });
  queue = p;
  inFlight.set(activityId, p);
  return p;
}

async function fetchOne(id: string): Promise<string | null> {
  const row = await getActivityById(id);
  if (!row) return null;
  if (row.gpx_path && await fileExists(row.gpx_path)) return row.gpx_path;   // already here
  if (row.track_remote !== 1) return null;

  let gpx = await fetchGpxBlob(sommetUid(row.device ?? '', row.date));
  if (gpx && gpx.indexOf('<trkpt') < 0) gpx = null;
  if (!gpx && id.startsWith('icu:')) {
    const creds = await getIntervalsIcuCredentials().catch(() => null);
    if (creds) {
      const got = await fetchIntervalsGpx(id, creds.apiKey, row.activity_type || 'Activity').catch(() => null);
      if (got === 'none') { await clearTrackRemote(id); return null; }   // nowhere has it after all
      gpx = got;
    }
  }
  if (!gpx) return null;
  const path = await writeGpxFile(id, gpx, true);
  if (!path) return null;
  const recent = await getKeepRecentOnly();
  await setTrackLocal(id, path, recent ? Date.now() : 0);
  if (recent) await evictTrackCache(id);
  listeners.forEach(cb => { try { cb(id, path); } catch { /* a screen's own problem */ } });
  return path;
}
