import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  getAllActivities, markActivitySynced, getSommetTombstones, addSommetTombstone,
  deleteActivitiesByUid, getActivityByUid, sommetUid, updateImportedActivity,
  getSommetLedger, markSommetSent, getSommetDeletedSent, markSommetDeletedSent,
  clearSommetServerMarkers, setTrackLocal, clearTrackRemote,
} from '../database/db';
import { canonicalSportName, isJunkActivity } from './SportNames';
import {
  activityFileStem, fitFileExists, listFitStems, readFitById, readFitForGpx, readGpxFile, removeFitForGpx,
  writeFitFile, writeGpxFile,
} from './GpxService';
import { streamsCache } from './StreamsCache';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { sameMove, startUtcOf, SameMoveRow } from './SameMove';
import { getKeepRecentOnly, lightCutoffMinute } from './LibraryPrefs';
import {
  Fetched, cachedToKeep, isServerMove, loadRescue, normalizeServerUrl, planRescue, runRescue, saveRescue,
  uncopiedTrackIds,
} from './ServerMove';

// Sommet Sync (#SYNC-3): two-way sync of activities against the user's OWN self-hosted endpoint
// (sync-server/sync.php), so the same history appears on desktop (Linux/Mac/Windows) and phone.
// The React Native port of the desktop C++ client in ActivityService and a sibling of EmberSync.ts
// (same fetch/token/merge shape). Pull-then-push, last-writer-wins by updated_at, tombstones by
// uid (device|start-minute). Metadata rides in the JSON records; each GPS track rides as a per-uid
// GPX blob. Best-effort: any network failure leaves the local store untouched. See
// docs/shared_app_db_design.md.

const CFG_KEY = 'sommet.sync.cfg';
const LASTPULL_KEY = 'sommet.sync.lastPull';
// One full pull after the 2026-10-03 update, so rows pulled earlier get their calories and the
// shared sport names (a normal pull only asks for what changed since the last one).
const REPULL_KEY = 'sommet.sync.repull.kcal1';
const PUSHED_KEY = 'sommet.sync.blobsPushed';
// sync/08: each move's track version (uid -> ms) as this device holds it. A "Split here" raises
// it; a device that sees a higher one on the NAS fetches the track and the FIT again.
const TRACK_REV_KEY = 'sommet.sync.trackRev';

async function readTrackRevs(): Promise<Record<string, number>> {
  try { return JSON.parse((await AsyncStorage.getItem(TRACK_REV_KEY)) || '{}'); } catch { return {}; }
}

/** sync/08: a split changed these moves' tracks here - raise their versions so others refetch. */
export async function bumpTrackRev(uids: string[]): Promise<void> {
  const revs = await readTrackRevs();
  const now = Date.now();
  for (const u of uids) revs[u] = now;
  await AsyncStorage.setItem(TRACK_REV_KEY, JSON.stringify(revs));
}

/** sync/08: the FIT of a split move changed here, so send it to the NAS again. */
export async function forgetPushedFit(uid: string): Promise<void> {
  const raw = await AsyncStorage.getItem(FIT_PUSHED_KEY);
  const pushed = new Set<string>(raw ? JSON.parse(raw) : []);
  if (pushed.delete(uid)) await AsyncStorage.setItem(FIT_PUSHED_KEY, JSON.stringify(Array.from(pushed)));
}
// sync/03 (2026-11-19): the moves whose original FIT the NAS confirmed. Android pushed only GPX
// before, so the tablet's own FITs (its Charts) never reached desktop or the NAS.
const FIT_PUSHED_KEY = 'sommet.sync.fitsPushed';

export interface SommetSyncCfg { url: string; token: string; }
export interface SommetSyncResult { pulled: number; pushed: number; ok: boolean; skipped?: boolean; }

/** sync/06: a move's track changed here (Split here), so send it to the shared database again. */
export async function forgetPushedBlob(uid: string): Promise<void> {
  const raw = await AsyncStorage.getItem(PUSHED_KEY);
  const pushed = new Set<string>(raw ? JSON.parse(raw) : []);
  if (pushed.delete(uid)) await AsyncStorage.setItem(PUSHED_KEY, JSON.stringify(Array.from(pushed)));
}

export async function getSommetSyncCfg(): Promise<SommetSyncCfg | null> {
  try {
    const raw = await AsyncStorage.getItem(CFG_KEY);
    const c = raw ? JSON.parse(raw) : null;
    return c && c.url ? { url: c.url, token: c.token ?? '' } : null;
  } catch { return null; }
}

export async function setSommetSyncCfg(cfg: SommetSyncCfg | null): Promise<void> {
  const before = await getSommetSyncCfg();
  const newUrl = cfg && cfg.url ? cfg.url : '';
  const moving = !!before && isServerMove(before.url, newUrl);
  // A move waits for a sync that is running (it uses the old server's marks), then applies. Only
  // a move: the token adopted in the middle of a sync (adoptNewToken) must not wait for itself.
  if (moving && runSommetSyncInFlight) await runSommetSyncInFlight.catch(() => undefined);
  try {
    await noteServerChange(before, newUrl);
  } catch (e: any) {
    // Never in the way of saving the new address: the old server's marks still go.
    console.log(`[sommet-sync] server change: ${e?.message ?? e}`);
    if (moving) await forgetServerMarks().catch(() => undefined);
  }
  if (!cfg || !cfg.url) { await AsyncStorage.removeItem(CFG_KEY); return; }
  await AsyncStorage.setItem(CFG_KEY, JSON.stringify({ url: cfg.url.trim(), token: (cfg.token ?? '').trim() }));
}

// ── Moving to another server (sync/14, ServerMove.ts) ──────────────────────────────────────
// Every mark of "what the server holds": activities (pull cursor, GPX and FIT sent, ledger,
// deletions sent), gear and reminders (pull cursors) and the route and POI libraries
// (sommet.sync.library.<collection>: their cursor and what they sent).
const SERVER_MARK_KEYS = [LASTPULL_KEY, PUSHED_KEY, FIT_PUSHED_KEY,
  'sommet.sync.gearLastPull', 'sommet.sync.reminderLastPull'];
const LIBRARY_STATE_PREFIX = 'sommet.sync.library.';

async function forgetServerMarks(): Promise<void> {
  const keys = [...SERVER_MARK_KEYS];
  try {
    for (const k of await AsyncStorage.getAllKeys()) if (k.startsWith(LIBRARY_STATE_PREFIX)) keys.push(k);
  } catch { /* none to add */ }
  for (const k of keys) await AsyncStorage.removeItem(k);
  await clearSommetServerMarkers();
}

async function intervalsConnectedNow(): Promise<boolean> {
  try {
    const { getIntervalsIcuCredentials } = await import('./ApiIntervalsIcu');
    return !!(await getIntervalsIcuCredentials());
  } catch { return false; }
}

/** The saved address is about to change from `before` to `newUrl`. On a move: list what only
 *  the old server holds (unless an earlier move's list is still being worked through: the
 *  light library trimmed nothing since, so nothing new is stranded), then forget every mark of
 *  the old server. Moving back to the server still being emptied ends that rescue. */
async function noteServerChange(before: SommetSyncCfg | null, newUrl: string): Promise<void> {
  const pending = await loadRescue();
  if (pending && pending.url === normalizeServerUrl(newUrl)) await saveRescue(null);
  if (!before || !isServerMove(before.url, newUrl)) return;
  if (!pending) {
    const rows = await getAllActivities();
    const onOld = await getPushedBlobs();
    const fitsOnOld = await getPushedFits();
    const uidOf = (r: { device?: string; date: string }) => sommetUid(r.device ?? '', r.date);
    const hasLocalFit = new Set<string>();
    for (const r of rows) {
      const candidate = (r.track_remote === 1 && !r.gpx_path) || (!!r.gpx_path && (r.track_cached_at ?? 0) > 0);
      if (candidate && fitsOnOld.has(uidOf(r)) && await fitFileExists(r.id)) hasLocalFit.add(r.id);
    }
    const items = planRescue(rows, { onOld, fitsOnOld, intervalsOn: await intervalsConnectedNow(), hasLocalFit, uidOf });
    for (const r of cachedToKeep(rows, onOld, uidOf)) await setTrackLocal(r.id, r.gpx_path ?? '', 0);
    await saveRescue(items.length ? { url: normalizeServerUrl(before.url), token: before.token, items } : null);
  }
  await forgetServerMarks();
}

async function rescueFetch(base: string, token: string, uid: string, fmt: 'gpx' | 'fit'): Promise<Response | null> {
  try {
    return await fetchT(base + '?' + new URLSearchParams({ blob: '1', uid, fmt }).toString(),
      { method: 'GET', headers: { 'X-Sommet-Token': token } });
  } catch { return null; }
}

/** Before each sync: copy back from the previous server what only it holds (ServerMove.ts). It
 *  then goes to the current server with the normal send. Never throws. */
async function rescueFromPreviousServer(currentUrl: string): Promise<void> {
  try {
    const r = await loadRescue();
    if (!r) return;
    if (r.url === normalizeServerUrl(currentUrl)) { await saveRescue(null); return; }
    const outcome = async <T>(res: Response | null, read: (x: Response) => Promise<T>): Promise<Fetched<T>> => {
      if (!res) return { status: 'failed' };
      if (res.status === 404) return { status: 'missing' };
      if (!res.ok) return { status: 'failed' };
      try { return { status: 'ok', body: await read(res) }; } catch { return { status: 'failed' }; }
    };
    await runRescue(r, {
      fetchGpx: async uid => outcome(await rescueFetch(r.url, r.token, uid, 'gpx'), x => x.text()),
      fetchFit: async uid => outcome(await rescueFetch(r.url, r.token, uid, 'fit'),
        async x => new Uint8Array(await x.arrayBuffer())),
      storeGpx: async (it, gpx) => {
        const path = await writeGpxFile(it.id, gpx, true);
        if (!path) return false;
        await setTrackLocal(it.id, path, 0);
        return true;
      },
      storeFit: async (it, fit) => !!(await writeFitFile(it.id, bytesToBase64(fit), true)),
      missingGpx: async it => { await clearTrackRemote(it.id); },
      // Not over a rescue that ended meanwhile (forgotten, or the server moved back to).
      save: async next => {
        const cur = await loadRescue();
        if (cur && cur.url === r.url && cur.token === r.token) await saveRescue(next);
      },
    });
  } catch { /* tried again at the next sync */ }
}

/** "Forget the previous server" (Settings): the moves whose Track was not copied no longer count
 *  as kept on a server (they keep their details, without a map or original file), and the
 *  previous server's address and token go. The light library trims again. */
export async function forgetPreviousServer(): Promise<void> {
  for (const id of uncopiedTrackIds(await loadRescue())) await clearTrackRemote(id);
  await saveRescue(null);
}

/** A request that gives up after `ms` (2026-11-19): one blob fetch that never answered held the
 *  whole sync, and the one-run-at-a-time guard then kept every later sync from starting. */
export async function fetchT(input: string, init: RequestInit = {}, ms = 60000): Promise<Response> {
  const controller = new AbortController();
  // Left running on purpose: it also cuts a body that stops arriving (res.text()); an abort
  // after the request has finished does nothing.
  setTimeout(() => controller.abort(), ms);
  return fetch(input, { ...init, signal: controller.signal });
}

/** sync/11: the server rotated its token and still took our old one; save the new one it sent. */
export async function adoptNewToken(url: string, token: string, res: { headers?: any }): Promise<void> {
  const next = (res?.headers?.get?.('X-Sommet-Token-New') ?? '').trim();
  if (!next || next === token) return;
  const cfg = await getSommetSyncCfg();
  if (cfg && cfg.url.trim() === url.trim() && cfg.token === token) await setSommetSyncCfg({ url: cfg.url, token: next });
}

// JSON request. Returns parsed body, {} on 404/empty, or null when unreachable (leave local alone).
async function sommetHttp(url: string, token: string, method: 'GET' | 'POST',
                          params?: Record<string, string>, body?: any): Promise<any | null> {
  const q = params ? '?' + new URLSearchParams(params).toString() : '';
  const headers: Record<string, string> = {};
  if (token) headers['X-Sommet-Token'] = token;
  if (body !== undefined) headers['Content-Type'] = 'application/json';
  try {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 15000);
    let res: Response;
    try {
      res = await fetch(url + q, {
        method, headers, body: body !== undefined ? JSON.stringify(body) : undefined,
        signal: controller.signal,
      });
    } finally { clearTimeout(timer); }
    await adoptNewToken(url, token, res);
    if (res.status === 404) return {};
    if (!res.ok) return null;
    const raw = await res.text();
    return raw.trim() ? JSON.parse(raw) : {};
  } catch { return null; }
}

// Probe the endpoint with the given creds (a GET). Returns a user-facing result for the UI.
export async function testSommetSync(url: string, token: string): Promise<{ ok: boolean; message: string }> {
  if (!url.trim() || !token.trim()) return { ok: false, message: 'Enter both the server address and the token.' };
  try {
    const res = await fetchT(url.trim() + '?c=activities&since=0', {
      method: 'GET', headers: { 'X-Sommet-Token': token.trim() },
    });
    if (res.ok) {
      await adoptNewToken(url, token.trim(), res);
      return { ok: true, message: 'Connected.' };
    }
    if (res.status === 401) return { ok: false, message: 'The token was rejected.' };
    return { ok: false, message: `Couldn't reach the server (HTTP ${res.status}).` };
  } catch (e: any) {
    return { ok: false, message: e?.message ?? 'Network error.' };
  }
}

// The Android local id for a move, derived from its start time exactly like the watch-read path
// (SyncService.ts), so the same move keeps one id across desktop/phone: "YYYYMMDDTHHMMSS".
function idFromDate(date: string, uid: string): string {
  const id = (date || '').replace(/[^0-9T]/g, '').substring(0, 15);
  return id || `sommet_${uid.replace(/[^0-9A-Za-z]/g, '_')}`;
}

/** The moves whose GPX the server confirmed holding (sent from here, or fetched from it). */
export async function getPushedBlobs(): Promise<Set<string>> {
  try { return new Set<string>(JSON.parse((await AsyncStorage.getItem(PUSHED_KEY)) || '[]')); } catch { return new Set(); }
}

/** The moves whose original FIT the server confirmed holding. */
export async function getPushedFits(): Promise<Set<string>> {
  try { return new Set<string>(JSON.parse((await AsyncStorage.getItem(FIT_PUSHED_KEY)) || '[]')); } catch { return new Set(); }
}

/** A move's GPX from the server (light library, sync/13: an older move opened here). Null when
 *  Sommet Sync is off, the server has none, or it can't be reached. */
export async function fetchGpxBlob(uid: string): Promise<string | null> {
  const cfg = await getSommetSyncCfg();
  if (!cfg) return null;
  return fetchBlob(cfg.url, cfg.token, uid);
}

async function fetchBlob(url: string, token: string, uid: string): Promise<string | null> {
  try {
    const res = await fetchT(url + '?' + new URLSearchParams({ blob: '1', uid, fmt: 'gpx' }).toString(),
      { method: 'GET', headers: { 'X-Sommet-Token': token } });
    if (!res.ok) return null;
    return await res.text();
  } catch { return null; }
}

/** The move's original FIT from the NAS (sync/03): what desktop and the Garmin-export backfill
 *  stored there. Null when the NAS has none or can't be reached. */
export async function fetchFitBlob(uid: string): Promise<Uint8Array | null> {
  const cfg = await getSommetSyncCfg();
  if (!cfg) return null;
  try {
    const res = await fetchT(cfg.url + '?' + new URLSearchParams({ blob: '1', uid, fmt: 'fit' }).toString(),
      { method: 'GET', headers: { 'X-Sommet-Token': cfg.token } });
    if (!res.ok) return null;
    const b = new Uint8Array(await res.arrayBuffer());
    return b.length > 12 && String.fromCharCode(b[8], b[9], b[10], b[11]) === '.FIT' ? b : null;
  } catch { return null; }
}

async function pushFitBlob(url: string, token: string, uid: string, fit: Uint8Array): Promise<boolean> {
  try {
    const res = await fetchT(url + '?' + new URLSearchParams({ blob: '1', uid, fmt: 'fit' }).toString(),
      { method: 'POST', headers: { 'X-Sommet-Token': token, 'Content-Type': 'application/octet-stream' },
        body: fit.buffer.slice(fit.byteOffset, fit.byteOffset + fit.byteLength) as ArrayBuffer });
    if (!res.ok) return false;
    const j = await res.json().catch(() => null);
    return !!(j && j.ok);   // desktop's rule: recorded only once the server said ok
  } catch { return false; }
}

async function pushBlob(url: string, token: string, uid: string, gpx: string): Promise<boolean> {
  try {
    const res = await fetchT(url + '?' + new URLSearchParams({ blob: '1', uid, fmt: 'gpx' }).toString(),
      { method: 'POST', headers: { 'X-Sommet-Token': token, 'Content-Type': 'application/octet-stream' }, body: gpx });
    return res.ok;
  } catch { return false; }
}

/**
 * Two-way sync against the shared store. Pull first (so we don't re-push what we just received),
 * then push all local rows + tombstones. Best-effort; returns a summary for the UI Alert.
 */
// One run at a time: the sync on opening the app and the one after a watch read can start
// together, and two overlapping runs would push and pull the same rows twice.
let runSommetSyncInFlight: Promise<SommetSyncResult> | null = null;
// ── Progress (sync/12) ─────────────────────────────────────────────────────────────────────
// What the sync is doing, for the banner on every screen. stage '' = idle; 'moves' = saving the
// activities pulled from the server (and fetching their tracks); 'checking' = comparing ours
// with what the server holds; 'sending' = sending the ones it lacks.
// first = nothing pulled before, which takes minutes. error = the last run failed.
export interface SommetSyncProgress { stage: '' | 'moves' | 'checking' | 'sending'; done: number; total: number; first: boolean; error: string; }
let progress: SommetSyncProgress = { stage: '', done: 0, total: 0, first: false, error: '' };
const progressListeners = new Set<(p: SommetSyncProgress) => void>();
export function getSommetSyncProgress(): SommetSyncProgress { return progress; }
export function onSommetSyncProgress(cb: (p: SommetSyncProgress) => void): () => void {
  progressListeners.add(cb);
  return () => { progressListeners.delete(cb); };
}
function setProgress(p: Partial<SommetSyncProgress>): void {
  progress = { ...progress, ...p };
  progressListeners.forEach(cb => { try { cb(progress); } catch { /* a screen's own problem */ } });
}
export function clearSommetSyncError(): void { setProgress({ error: '' }); }
/** Give the screen a turn between batches, so a first sync never freezes it. */
const yieldToUi = () => new Promise<void>(r => setTimeout(r, 0));

export function runSommetSync(): Promise<SommetSyncResult> {
  if (!runSommetSyncInFlight) {
    setProgress({ stage: 'moves', done: 0, total: 0, error: '' });
    runSommetSyncInFlight = runSommetSyncOnce()
      .then(r => {
        setProgress({ stage: '', done: 0, total: 0, first: false,
                      error: r.ok || r.skipped ? '' : "Couldn't reach your server." });
        return r;
      }, e => {
        setProgress({ stage: '', done: 0, total: 0, first: false, error: String(e?.message ?? e) });
        throw e;
      })
      .finally(() => { runSommetSyncInFlight = null; });
  }
  return runSommetSyncInFlight;
}

async function runSommetSyncOnce(): Promise<SommetSyncResult> {
  const cfg = await getSommetSyncCfg();
  if (!cfg) return { pulled: 0, pushed: 0, ok: false, skipped: true };
  const { url, token } = cfg;
  // A server move (sync/14): what only the previous server holds comes back first, and goes out
  // to this one with the send below.
  await rescueFromPreviousServer(url);

  const tombstones = new Set<string>(await getSommetTombstones());
  const trackRevs = await readTrackRevs();
  let revsChanged = false;
  let pulled = 0;

  // ── PULL ──────────────────────────────────────────────────────────────────────
  const repullDone = await AsyncStorage.getItem(REPULL_KEY);
  const since = repullDone ? Number((await AsyncStorage.getItem(LASTPULL_KEY)) || '0') : 0;
  setProgress({ first: since === 0 });
  const remote = await sommetHttp(url, token, 'GET', { c: 'activities', since: String(since) });
  if (remote === null) return { pulled: 0, pushed: 0, ok: false }; // unreachable: leave local alone

  // remote tombstones first, so an upsert can't resurrect a just-deleted move
  const deletedSent = await getSommetDeletedSent();
  const pulledDeleted: string[] = [];
  for (const uid of (remote.deleted ?? [])) {
    if (!uid) continue;
    if (!deletedSent.has(uid)) { deletedSent.add(uid); pulledDeleted.push(uid); }   // the server has it
    if (tombstones.has(uid)) continue;
    tombstones.add(uid);
    await addSommetTombstone(uid);
    await deleteActivitiesByUid(uid);
  }
  await markSommetDeletedSent(pulledDeleted);
  // Versions that came from the server count as held there (sync ledger): they are not sent back.
  const pulledSent: { uid: string; updatedAt: number }[] = [];
  // The same move from another source (a watch move here, its intervals.icu or Suunto copy from
  // another device) is not added twice: the shared rule, SameMove.ts. The desktop keeps every
  // copy and shows one; this app keeps one.
  const localRows: SameMoveRow[] = (await getAllActivities()).map(a => ({
    start: startUtcOf(a), durationS: a.duration_s, distanceM: a.distance_m,
    watch: !a.id.startsWith('icu:') && !a.device, device: a.device ?? '',
  }));
  const pulledRecs: any[] = remote.records ?? [];
  setProgress({ stage: 'moves', done: 0, total: pulledRecs.length });
  let seen = 0;
  const fetchedNow: string[] = [];
  // Light library (sync/13): an older move's Track stays on the server, fetched when opened.
  const lightCutoff = (await getKeepRecentOnly()) ? lightCutoffMinute() : '';
  for (const rec of pulledRecs) {
    if (++seen % 25 === 0) { setProgress({ done: seen }); await yieldToUi(); }
    const device = String(rec.device ?? '');
    const start = String(rec.start_time ?? '');
    if (!start) continue;
    const uid = rec.uid || sommetUid(device, start);
    if (tombstones.has(uid)) continue;
    const remoteUpdated = Number(rec.updated_at || 0);
    const existing = await getActivityByUid(uid);
    const sport = canonicalSportName(String(rec.activity_type || rec.name || ''));
    const energy = Math.round(Number(rec.energy_kcal || 0)) || 0;
    const startUtc = Math.round(Number(rec.start_utc || 0)) || 0;   // UTC start, see SameMove.ts
    const moving = Math.round(Number(rec.moving_s || 0)) || 0;      // moving time, sync/06
    // Junk/test entries (under a minute AND under 100 m) are not kept - same rule as the
    // desktop's import; they reach the shared store from a watch read on the desktop.
    if (isJunkActivity(Number(rec.duration_s || 0), Number(rec.distance_m || 0))) continue;
    const remoteRev = Number(rec.track_rev || 0);
    const newerTrack = !!rec.has_track && remoteRev > (trackRevs[uid] || 0);   // sync/08: split elsewhere
    if (existing && remoteUpdated > 0 && existing.updated_at === remoteUpdated)
      pulledSent.push({ uid, updatedAt: remoteUpdated });   // the server holds our version
    const haveTrack = !!existing && (!!existing.gpx_path || existing.track_remote === 1);   // here or kept there
    if (existing && existing.updated_at >= remoteUpdated && haveTrack && !newerTrack) { // ours is newer & has track
      // ...but a row pulled before 2026-10-03 has no calories and may hold a raw sport name.
      const fix: { activity_type?: string; energy_kcal?: number; start_utc?: number; moving_s?: number } = {};
      if (energy > 0 && !existing.energy_kcal) fix.energy_kcal = energy;
      if (startUtc > 0 && !existing.start_utc) fix.start_utc = startUtc;
      if (moving > 0 && !existing.moving_s) fix.moving_s = moving;
      if (sport && existing.activity_type !== sport
          && canonicalSportName(existing.activity_type) === sport) fix.activity_type = sport;
      if (fix.activity_type !== undefined || fix.energy_kcal !== undefined || fix.start_utc !== undefined
          || fix.moving_s !== undefined)
        await updateImportedActivity(existing.id, fix);
      continue;
    }

    if (!existing) {
      const src = String(rec.source || '');
      const me: SameMoveRow = {
        start: startUtc > 0 ? startUtc : startUtcOf({ date: start }),
        durationS: Number(rec.duration_s || 0), distanceM: Number(rec.distance_m || 0),
        watch: src === '' || src === 'watch', device,
      };
      if (localRows.some(r => sameMove(me, r))) continue;   // already here from another source
      localRows.push(me);
    }
    const id = idFromDate(start, uid);
    let gpxPath = existing?.gpx_path ?? '';
    let trackRemote = existing?.track_remote === 1 && !gpxPath ? 1 : 0;
    if (rec.has_track && !gpxPath && lightCutoff && start.slice(0, 16) < lightCutoff) {
      // Older than 90 days: not downloaded. The server holds it, so it counts as sent there.
      trackRemote = 1;
      fetchedNow.push(uid);
      if (newerTrack) { trackRevs[uid] = remoteRev; revsChanged = true; }
    } else if (rec.has_track && (!gpxPath || newerTrack)) {
      const gpx = await fetchBlob(url, token, uid);
      if (gpx) {
        if (newerTrack && gpxPath) {
          // the old FIT covers the whole file: drop it, the Charts fetch the NAS's new one
          await removeFitForGpx(gpxPath);
          if (existing) streamsCache.delete(existing.id);
        }
        gpxPath = (await writeGpxFile(id, gpx, true)) ?? '';
        if (gpxPath) { fetchedNow.push(uid); trackRemote = 0; }   // sync/12: came from the server, don't send it back
        if (newerTrack && gpxPath) { trackRevs[uid] = remoteRev; revsChanged = true; }
      }
    }
    await markActivitySynced({
      id,
      synced_at: Date.now(),
      gpx_path: gpxPath,
      date: start,
      duration_s: Number(rec.duration_s || 0),
      distance_m: Number(rec.distance_m || 0),
      d_plus: Number(rec.ascent_m || 0),
      activity_type: sport,
      device,
      energy_kcal: energy,
      updated_at: remoteUpdated || Date.now(),
      start_utc: startUtc || existing?.start_utc || 0,
      moving_s: moving || existing?.moving_s || 0,
      track_remote: trackRemote,
    });
    pulledSent.push({ uid, updatedAt: remoteUpdated || Date.now() });
    pulled++;
  }
  await markSommetSent(pulledSent);
  if (revsChanged) await AsyncStorage.setItem(TRACK_REV_KEY, JSON.stringify(trackRevs));
  if (remote.now) await AsyncStorage.setItem(LASTPULL_KEY, String(remote.now));
  if (!repullDone) await AsyncStorage.setItem(REPULL_KEY, '1');

  // ── PUSH ──────────────────────────────────────────────────────────────────────
  // Only what the server doesn't hold yet (sync ledger, #SYNC-8/9/11): a move whose updated_at
  // is newer than the one recorded as sent, or whose track still has to go. Sent in requests of
  // 200, each recorded once the server accepts it, so an interrupted sync resumes where it stopped.
  const acts = await getAllActivities();
  const ledger = await getSommetLedger();
  // Most syncs send nothing: say "Checking" while comparing, "Sending" only for what goes
  // (André, 2026-11-21: the banner said "Sending 3,600 / 4,696" when nothing was sent).
  setProgress({ stage: 'checking', done: 0, total: acts.length });
  let readCount = 0;
  const records: any[] = [];
  const blobsToPush: { uid: string; gpx: string }[] = [];
  const pushedRaw = await AsyncStorage.getItem(PUSHED_KEY);
  const pushed = new Set<string>(pushedRaw ? JSON.parse(pushedRaw) : []);
  for (const u of fetchedNow) pushed.add(u);

  for (const a of acts) {
    if (++readCount % 50 === 0) { setProgress({ done: readCount }); await yieldToUi(); }
    const device = a.device ?? '';
    const uid = sommetUid(device, a.date);
    if (tombstones.has(uid)) continue;
    // Read the file only when it still has to go to the server (sync/12): reading all ~3,700
    // tracks on every sync kept the tablet's CPU busy for minutes.
    let gpx = '';
    const updatedAt = a.updated_at || a.synced_at;
    const trackPending = !!a.gpx_path && !pushed.has(uid);
    if ((ledger.get(uid) ?? -1) >= updatedAt && !trackPending) continue;   // the server has this one
    if (trackPending) { try { gpx = await readGpxFile(a.gpx_path); } catch { gpx = ''; } }
    // A Track kept on the server only (light library) is still this move's Track there.
    const hasTrack = !!gpx || ((!!a.gpx_path || a.track_remote === 1) && pushed.has(uid));
    records.push({
      uid,
      device,
      name: a.activity_type,
      activity_type: a.activity_type,
      start_time: a.date,
      duration_s: a.duration_s,
      distance_m: a.distance_m,
      ascent_m: a.d_plus,
      energy_kcal: a.energy_kcal || 0,
      source: a.id.startsWith('icu:') ? 'intervals' : 'watch',
      has_track: hasTrack,
      track_fmt: 'gpx',
      updated_at: updatedAt,
      ...(a.start_utc ? { start_utc: a.start_utc } : {}),
      ...(a.moving_s ? { moving_s: a.moving_s } : {}),
      ...(trackRevs[uid] ? { track_rev: trackRevs[uid] } : {}),
    });
    if (gpx && !pushed.has(uid)) blobsToPush.push({ uid, gpx });
  }

  const deletions = Array.from(tombstones).filter(u => !deletedSent.has(u));
  let sentCount = 0;
  if (records.length > 0 || deletions.length > 0 || blobsToPush.length > 0)
    setProgress({ stage: 'sending', done: 0, total: records.length + blobsToPush.length });
  for (let i = 0; i < records.length || (i === 0 && deletions.length > 0); i += 200) {
    const batch = records.slice(i, i + 200);
    const dels = i === 0 ? deletions : [];
    const post = await sommetHttp(url, token, 'POST', { c: 'activities' }, { records: batch, deleted: dels });
    if (post === null) return { pulled, pushed: sentCount, ok: false };
    await markSommetSent(batch.map(r => ({ uid: r.uid, updatedAt: r.updated_at })));
    await markSommetDeletedSent(dels);
    sentCount += batch.length;
    setProgress({ done: sentCount });
    await yieldToUi();
  }

  let blobsDone = 0;
  for (const b of blobsToPush) {
    if (await pushBlob(url, token, b.uid, b.gpx)) pushed.add(b.uid);
    setProgress({ done: sentCount + ++blobsDone });
  }
  await AsyncStorage.setItem(PUSHED_KEY, JSON.stringify(Array.from(pushed)));

  // Original FITs, one per request, read only when not yet confirmed (a few KB to a few MB each).
  const fitRaw = await AsyncStorage.getItem(FIT_PUSHED_KEY);
  const fitPushed = new Set<string>(fitRaw ? JSON.parse(fitRaw) : []);
  // A FIT with no Track here (one copied back from a previous server whose GPX it lacked, sync/14)
  // is found by the move's id: one directory read, not one per move.
  let fitStems: Set<string> | null = null;
  for (const a of acts) {
    const uid = sommetUid(a.device ?? '', a.date);
    if (tombstones.has(uid) || fitPushed.has(uid)) continue;
    let b64: string | null = null;
    if (a.gpx_path) b64 = await readFitForGpx(a.gpx_path);
    else {
      if (!fitStems) { try { fitStems = await listFitStems(); } catch { fitStems = new Set(); } }
      if (fitStems.size && fitStems.has(activityFileStem(a.id))) {
        try { b64 = await readFitById(a.id); } catch { b64 = null; }
      }
    }
    if (!b64) continue;
    if (await pushFitBlob(url, token, uid, base64ToBytes(b64))) fitPushed.add(uid);
  }
  await AsyncStorage.setItem(FIT_PUSHED_KEY, JSON.stringify(Array.from(fitPushed)));

  return { pulled, pushed: sentCount, ok: true };
}
