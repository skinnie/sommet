import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  getAllActivities, markActivitySynced, getSommetTombstones, addSommetTombstone,
  deleteActivitiesByUid, getActivityByUid, sommetUid,
} from '../database/db';
import { readGpxFile, writeGpxFile } from './GpxService';

// Sommet Sync (#SYNC-3): two-way sync of activities against the user's OWN self-hosted endpoint
// (sync-server/sync.php), so the same history appears on desktop (Linux/Mac/Windows) and phone.
// The React Native port of the desktop C++ client in ActivityService and a sibling of EmberSync.ts
// (same fetch/token/merge shape). Pull-then-push, last-writer-wins by updated_at, tombstones by
// uid (device|start-minute). Metadata rides in the JSON records; each GPS track rides as a per-uid
// GPX blob. Best-effort: any network failure leaves the local store untouched. See
// docs/shared_app_db_design.md.

const CFG_KEY = 'sommet.sync.cfg';
const LASTPULL_KEY = 'sommet.sync.lastPull';
const PUSHED_KEY = 'sommet.sync.blobsPushed';

export interface SommetSyncCfg { url: string; token: string; }
export interface SommetSyncResult { pulled: number; pushed: number; ok: boolean; }

export async function getSommetSyncCfg(): Promise<SommetSyncCfg | null> {
  try {
    const raw = await AsyncStorage.getItem(CFG_KEY);
    const c = raw ? JSON.parse(raw) : null;
    return c && c.url ? { url: c.url, token: c.token ?? '' } : null;
  } catch { return null; }
}

export async function setSommetSyncCfg(cfg: SommetSyncCfg | null): Promise<void> {
  if (!cfg || !cfg.url) { await AsyncStorage.removeItem(CFG_KEY); return; }
  await AsyncStorage.setItem(CFG_KEY, JSON.stringify({ url: cfg.url.trim(), token: (cfg.token ?? '').trim() }));
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

// Probe the endpoint with the given creds (a GET). Returns a user-facing result for the UI.
export async function testSommetSync(url: string, token: string): Promise<{ ok: boolean; message: string }> {
  if (!url.trim() || !token.trim()) return { ok: false, message: 'Enter both the server address and the token.' };
  try {
    const res = await fetch(url.trim() + '?c=activities&since=0', {
      method: 'GET', headers: { 'X-Sommet-Token': token.trim() },
    });
    if (res.ok) return { ok: true, message: 'Connected.' };
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

async function fetchBlob(url: string, token: string, uid: string): Promise<string | null> {
  try {
    const res = await fetch(url + '?' + new URLSearchParams({ blob: '1', uid, fmt: 'gpx' }).toString(),
      { method: 'GET', headers: { 'X-Sommet-Token': token } });
    if (!res.ok) return null;
    return await res.text();
  } catch { return null; }
}

async function pushBlob(url: string, token: string, uid: string, gpx: string): Promise<boolean> {
  try {
    const res = await fetch(url + '?' + new URLSearchParams({ blob: '1', uid, fmt: 'gpx' }).toString(),
      { method: 'POST', headers: { 'X-Sommet-Token': token, 'Content-Type': 'application/octet-stream' }, body: gpx });
    return res.ok;
  } catch { return false; }
}

/**
 * Two-way sync against the shared store. Pull first (so we don't re-push what we just received),
 * then push all local rows + tombstones. Best-effort; returns a summary for the UI Alert.
 */
export async function runSommetSync(): Promise<SommetSyncResult> {
  const cfg = await getSommetSyncCfg();
  if (!cfg) return { pulled: 0, pushed: 0, ok: false };
  const { url, token } = cfg;

  const tombstones = new Set<string>(await getSommetTombstones());
  let pulled = 0;

  // ── PULL ──────────────────────────────────────────────────────────────────────
  const since = Number((await AsyncStorage.getItem(LASTPULL_KEY)) || '0');
  const remote = await sommetHttp(url, token, 'GET', { c: 'activities', since: String(since) });
  if (remote === null) return { pulled: 0, pushed: 0, ok: false }; // unreachable: leave local alone

  // remote tombstones first, so an upsert can't resurrect a just-deleted move
  for (const uid of (remote.deleted ?? [])) {
    if (!uid || tombstones.has(uid)) continue;
    tombstones.add(uid);
    await addSommetTombstone(uid);
    await deleteActivitiesByUid(uid);
  }
  for (const rec of (remote.records ?? [])) {
    const device = String(rec.device ?? '');
    const start = String(rec.start_time ?? '');
    if (!start) continue;
    const uid = rec.uid || sommetUid(device, start);
    if (tombstones.has(uid)) continue;
    const remoteUpdated = Number(rec.updated_at || 0);
    const existing = await getActivityByUid(uid);
    if (existing && existing.updated_at >= remoteUpdated && existing.gpx_path) continue; // ours is newer & has track

    const id = idFromDate(start, uid);
    let gpxPath = existing?.gpx_path ?? '';
    if (rec.has_track && !gpxPath) {
      const gpx = await fetchBlob(url, token, uid);
      if (gpx) gpxPath = (await writeGpxFile(id, gpx, true)) ?? '';
    }
    await markActivitySynced({
      id,
      synced_at: Date.now(),
      gpx_path: gpxPath,
      date: start,
      duration_s: Number(rec.duration_s || 0),
      distance_m: Number(rec.distance_m || 0),
      d_plus: Number(rec.ascent_m || 0),
      activity_type: String(rec.activity_type || rec.name || ''),
      device,
      updated_at: remoteUpdated || Date.now(),
    });
    pulled++;
  }
  if (remote.now) await AsyncStorage.setItem(LASTPULL_KEY, String(remote.now));

  // ── PUSH ──────────────────────────────────────────────────────────────────────
  const acts = await getAllActivities();
  const records: any[] = [];
  const blobsToPush: { uid: string; gpx: string }[] = [];
  const pushedRaw = await AsyncStorage.getItem(PUSHED_KEY);
  const pushed = new Set<string>(pushedRaw ? JSON.parse(pushedRaw) : []);

  for (const a of acts) {
    const device = a.device ?? '';
    const uid = sommetUid(device, a.date);
    if (tombstones.has(uid)) continue;
    let gpx = '';
    if (a.gpx_path) { try { gpx = await readGpxFile(a.gpx_path); } catch { gpx = ''; } }
    records.push({
      uid,
      device,
      name: a.activity_type,
      activity_type: a.activity_type,
      start_time: a.date,
      duration_s: a.duration_s,
      distance_m: a.distance_m,
      ascent_m: a.d_plus,
      source: a.id.startsWith('icu:') ? 'intervals' : 'watch',
      has_track: !!gpx,
      track_fmt: 'gpx',
      updated_at: a.updated_at || a.synced_at,
    });
    if (gpx && !pushed.has(uid)) blobsToPush.push({ uid, gpx });
  }

  const post = await sommetHttp(url, token, 'POST', { c: 'activities' },
    { records, deleted: Array.from(tombstones) });
  if (post === null) return { pulled, pushed: 0, ok: false };

  for (const b of blobsToPush) {
    if (await pushBlob(url, token, b.uid, b.gpx)) pushed.add(b.uid);
  }
  await AsyncStorage.setItem(PUSHED_KEY, JSON.stringify(Array.from(pushed)));

  return { pulled, pushed: records.length, ok: true };
}
