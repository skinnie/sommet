import RNFS from 'react-native-fs';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { getSommetSyncCfg } from './SommetSync';
import {
  LibraryRoute, readRouteIndex, writeRouteIndex, readRouteDeleted, writeRouteDeleted, routeGpxPath,
  thinPreview,
} from './RouteLibrary';
import { cleanPoi, editPoiLibrary, listLibraryPois, samePoiPlace, LibraryPoi } from './PoiLibrary';

// The route library and the POI library shared between devices through Sommet Sync (André,
// 2026-11-22: "these two should be similar and also synced to the nas"). Twin of the desktop's
// tools/library_sync.py; planPull() is checked against the same cases
// (__tests__/librarySync.fixture.json, written by tools/test_library_sync.py --write-fixture).
// One library, the same on every Sommet install. It never touches a watch.
//
// The rules:
// - One record per entry: its id is the `uid`, `updated_at` (ms) says when it last changed, and
//   `created` is in seconds. A route's GPX travels as a blob under the uid "route:<id>".
// - Pull what changed on the server since the last pull. A newer `updated_at` wins. A delete
//   removes the entry here. Something deleted here stays deleted unless it was changed elsewhere
//   after that.
// - The same thing saved on two devices before they met (a route with the same GPX, by its
//   SHA-1; a POI with the same name within 10 m): the older one stays, the other is deleted
//   everywhere.
// - Then send what is new or changed here, and the deletes made here.
// - A server that predates this answers 400 "unknown collection": nothing is done, no error.

export interface SyncItem { uid: string; updated_at: number; created: number; [k: string]: any }
export interface PullPlan {
  put: Record<string, any>;
  remove: string[];
  kill: string[];
  forget_tombstones: string[];
}

/** What a pull changes here. No file access. */
export function planPull(local: SyncItem[], tombstones: { id: string; at: number }[],
                         pulled: { records?: any[]; deleted?: string[] },
                         same: (mine: SyncItem, theirs: any) => boolean): PullPlan {
  const mine = new Map<string, SyncItem>(local.map(x => [x.uid, x]));
  const dead = new Map<string, number>(tombstones.map(t => [t.id, Math.trunc(Number(t.at) || 0) * 1000]));
  const put: Record<string, any> = {};
  const remove: string[] = [], kill: string[] = [], forget: string[] = [];
  for (const uid of pulled.deleted ?? []) {
    if (mine.has(uid)) { remove.push(uid); mine.delete(uid); }
    if (dead.has(uid)) { forget.push(uid); dead.delete(uid); }
  }
  for (const r of pulled.records ?? []) {
    const uid = r?.uid;
    if (!uid) continue;
    const updatedAt = Math.trunc(Number(r.updated_at) || 0);
    if (dead.has(uid)) {
      if ((dead.get(uid) as number) >= updatedAt) continue;       // deleted here after that change
      forget.push(uid);
      dead.delete(uid);
    }
    const have = mine.get(uid);
    if (have) {
      if (updatedAt > have.updated_at) { put[uid] = r; mine.set(uid, { ...have, ...r }); }
      continue;
    }
    let twin: SyncItem | undefined;
    for (const x of mine.values()) { if (same(x, r)) { twin = x; break; } }
    if (!twin) { put[uid] = r; mine.set(uid, { ...r }); continue; }
    // The same thing under two ids. The older one stays (the uid breaks a tie).
    const rc = Math.trunc(Number(r.created) || 0), tc = Math.trunc(Number(twin.created) || 0);
    if (rc < tc || (rc === tc && uid < twin.uid)) {
      put[uid] = r;
      remove.push(twin.uid);
      kill.push(twin.uid);
      mine.delete(twin.uid);
      mine.set(uid, { ...r });
    } else {
      kill.push(uid);
    }
  }
  return { put, remove, kill, forget_tombstones: forget };
}

// ---- SHA-1 of a text as UTF-8 (hex), to match the desktop's hashlib.sha1 ----------------------

export function sha1Hex(text: string): string {
  const bytes: number[] = [];
  for (let i = 0; i < text.length; i++) {
    let c = text.charCodeAt(i);
    if (c >= 0xd800 && c <= 0xdbff && i + 1 < text.length) {
      const d = text.charCodeAt(i + 1);
      if (d >= 0xdc00 && d <= 0xdfff) { c = 0x10000 + ((c - 0xd800) << 10) + (d - 0xdc00); i++; }
    }
    if (c < 0x80) bytes.push(c);
    else if (c < 0x800) bytes.push(0xc0 | (c >> 6), 0x80 | (c & 63));
    else if (c < 0x10000) bytes.push(0xe0 | (c >> 12), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
    else bytes.push(0xf0 | (c >> 18), 0x80 | ((c >> 12) & 63), 0x80 | ((c >> 6) & 63), 0x80 | (c & 63));
  }
  const bitLen = bytes.length * 8;
  bytes.push(0x80);
  while (bytes.length % 64 !== 56) bytes.push(0);
  const hi = Math.floor(bitLen / 0x100000000), lo = bitLen >>> 0;
  bytes.push((hi >>> 24) & 255, (hi >>> 16) & 255, (hi >>> 8) & 255, hi & 255,
             (lo >>> 24) & 255, (lo >>> 16) & 255, (lo >>> 8) & 255, lo & 255);
  let h0 = 0x67452301, h1 = 0xefcdab89, h2 = 0x98badcfe, h3 = 0x10325476, h4 = 0xc3d2e1f0;
  const w = new Array<number>(80);
  const rol = (v: number, n: number) => ((v << n) | (v >>> (32 - n))) >>> 0;
  for (let o = 0; o < bytes.length; o += 64) {
    for (let i = 0; i < 16; i++) {
      w[i] = ((bytes[o + 4 * i] << 24) | (bytes[o + 4 * i + 1] << 16) | (bytes[o + 4 * i + 2] << 8) | bytes[o + 4 * i + 3]) >>> 0;
    }
    for (let i = 16; i < 80; i++) w[i] = rol(w[i - 3] ^ w[i - 8] ^ w[i - 14] ^ w[i - 16], 1);
    let a = h0, b = h1, c = h2, d = h3, e = h4;
    for (let i = 0; i < 80; i++) {
      const f = i < 20 ? ((b & c) | (~b & d)) : i < 40 ? (b ^ c ^ d) : i < 60 ? ((b & c) | (b & d) | (c & d)) : (b ^ c ^ d);
      const k = i < 20 ? 0x5a827999 : i < 40 ? 0x6ed9eba1 : i < 60 ? 0x8f1bbcdc : 0xca62c1d6;
      const tmp = (rol(a, 5) + (f >>> 0) + e + k + w[i]) >>> 0;
      e = d; d = c; c = rol(b, 30); b = a; a = tmp;
    }
    h0 = (h0 + a) >>> 0; h1 = (h1 + b) >>> 0; h2 = (h2 + c) >>> 0; h3 = (h3 + d) >>> 0; h4 = (h4 + e) >>> 0;
  }
  return [h0, h1, h2, h3, h4].map(v => v.toString(16).padStart(8, '0')).join('');
}

// ---- the server ----------------------------------------------------------------------------------

export class OlderServer extends Error {}

export interface LibraryHttp {
  pull(collection: string, since: number): Promise<{ records?: any[]; deleted?: string[]; now?: number }>;
  push(collection: string, records: any[], deleted: string[]): Promise<void>;
  /** A route's GPX, or null when the server does not have it (yet). */
  getBlob(uid: string): Promise<string | null>;
  putBlob(uid: string, text: string): Promise<void>;
}

export function libraryHttp(url: string, token: string): LibraryHttp {
  async function call(method: 'GET' | 'POST', params: Record<string, string>, body?: string, type?: string) {
    const headers: Record<string, string> = { 'X-Sommet-Token': token };
    if (type) headers['Content-Type'] = type;
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 20000);
    try {
      return await fetch(url + '?' + new URLSearchParams(params).toString(),
        { method, headers, body, signal: controller.signal });
    } catch (e: any) {
      throw new Error(`the sync server could not be reached (${e?.message ?? e})`);
    } finally {
      clearTimeout(timer);
    }
  }
  async function okOrThrow(res: Response): Promise<Response> {
    if (res.ok) return res;
    const text = await res.text().catch(() => '');
    if (res.status === 400 && text.includes('unknown collection')) throw new OlderServer();
    throw new Error(`the sync server answered ${res.status}`);
  }
  return {
    async pull(collection, since) {
      return (await okOrThrow(await call('GET', { c: collection, since: String(Math.trunc(since)) }))).json();
    },
    async push(collection, records, deleted) {
      await okOrThrow(await call('POST', { c: collection }, JSON.stringify({ records, deleted }), 'application/json'));
    },
    async getBlob(uid) {
      const res = await call('GET', { blob: '1', uid, fmt: 'gpx' });
      if (res.status === 404) return null;
      return (await okOrThrow(res)).text();
    },
    async putBlob(uid, text) {
      await okOrThrow(await call('POST', { blob: '1', uid, fmt: 'gpx' }, text, 'application/octet-stream'));
    },
  };
}

// ---- the two libraries, seen the same way -----------------------------------------------------

interface Lib {
  collection: string;
  items(): Promise<SyncItem[]>;
  tombstones(): Promise<{ id: string; at: number }[]>;
  same(mine: SyncItem, theirs: any): boolean;
  /** What a route needs before the library is touched: the GPX of each one this device lacks. */
  prefetch(records: any[], http: LibraryHttp): Promise<Record<string, string>>;
  /** Applies a plan; gives back the uids of `put` that are now here. */
  apply(plan: PullPlan, fetched: Record<string, string>): Promise<Set<string>>;
  /** The record to send (a route's GPX goes up first, the first time). */
  record(item: SyncItem, http: LibraryHttp, first: boolean): Promise<any>;
}

const poiItem = (p: LibraryPoi): SyncItem => ({
  uid: p.id, updated_at: Math.trunc(p.updated || p.created || 0) * 1000, created: Math.trunc(p.created || 0),
  name: p.name, lat: p.lat, lon: p.lon, ele: p.ele ?? null, type: p.type, typeList: p.typeList ?? 'ambit',
});

export const poisLib: Lib = {
  collection: 'pois',
  async items() { return (await listLibraryPois()).map(poiItem); },
  tombstones() { return editPoiLibrary(d => d.deleted.map(t => ({ ...t }))); },
  same(mine, theirs) { return samePoiPlace({ name: mine.name, lat: mine.lat, lon: mine.lon }, theirs); },
  async prefetch() { return {}; },
  apply(plan) {
    return editPoiLibrary(data => {
      const byId = new Map(data.pois.map(p => [p.id, p]));
      for (const uid of plan.remove) byId.delete(uid);
      const done = new Set<string>();
      for (const [uid, r] of Object.entries(plan.put)) {
        const c = cleanPoi(r);
        if (!c.ok) continue;
        byId.set(uid, { id: uid, ...c.poi, created: Math.trunc(Number(r.created) || 0),
                        updated: Math.trunc((Number(r.updated_at) || 0) / 1000) });
        done.add(uid);
      }
      data.pois = Array.from(byId.values());
      const gone = new Set(plan.forget_tombstones);
      data.deleted = data.deleted.filter(t => !gone.has(t.id));
      return done;
    });
  },
  async record(item) { return { ...item }; },
};

/** Figures and preview for a route that came from another device. */
export type RouteStats = (gpx: string, name: string) => { distanceM: number; ascentM: number; points: { lat: number; lon: number }[] };

export function routesLib(stats: RouteStats): Lib {
  const item = (r: LibraryRoute): SyncItem => ({
    uid: r.id, created: Math.floor((r.created || 0) / 1000),
    updated_at: Math.floor((r.updated || r.created || 0) / 1000) * 1000,
    name: r.name, sha1: r.sha1 ?? '',
  });
  // The desktop's SHA-1 for every entry, worked out once from its GPX (older entries lack it).
  async function withSha1(): Promise<LibraryRoute[]> {
    const index = await readRouteIndex();
    let changed = false;
    for (const r of index) {
      if (r.sha1) continue;
      try { r.sha1 = sha1Hex(await RNFS.readFile(routeGpxPath(r.id), 'utf8')); changed = true; } catch { /* no file */ }
    }
    if (changed) await writeRouteIndex(index);
    return index;
  }
  return {
    collection: 'routes',
    async items() { return (await withSha1()).map(item); },
    tombstones: readRouteDeleted,
    same(mine, theirs) { return !!mine.sha1 && mine.sha1 === theirs?.sha1; },
    async prefetch(records, http) {
      const have = new Set((await readRouteIndex()).map(r => r.id));
      const out: Record<string, string> = {};
      for (const r of records) {
        if (!r?.uid || have.has(r.uid)) continue;
        try {
          const gpx = await http.getBlob('route:' + r.uid);
          if (gpx != null) out[r.uid] = gpx;
        } catch { /* asked again next time */ }
      }
      return out;
    },
    async apply(plan, fetched) {
      const byId = new Map((await readRouteIndex()).map(r => [r.id, r]));
      for (const uid of plan.remove) {
        if (byId.delete(uid)) await RNFS.unlink(routeGpxPath(uid)).catch(() => {});
      }
      const done = new Set<string>();
      for (const [uid, r] of Object.entries(plan.put)) {
        let entry = byId.get(uid);
        const name = (String(r.name ?? 'Route').trim() || 'Route').slice(0, 80);
        if (!entry) {
          const gpx = fetched[uid];
          if (gpx == null) continue;                       // its GPX did not come: next time
          let s = { distanceM: 0, ascentM: 0, points: [] as { lat: number; lon: number }[] };
          try { s = stats(gpx, name); } catch { /* listed without figures */ }
          await RNFS.writeFile(routeGpxPath(uid), gpx, 'utf8');
          entry = { id: uid, name, created: 0, distanceMeters: Math.round(s.distanceM),
                    ascentMeters: Math.round(s.ascentM), hash: '', preview: thinPreview(s.points),
                    sha1: sha1Hex(gpx) };
          byId.set(uid, entry);
        }
        entry.name = name;
        entry.created = Math.trunc(Number(r.created) || 0) * 1000 || entry.created;
        entry.updated = Math.trunc(Number(r.updated_at) || 0);
        done.add(uid);
      }
      await writeRouteIndex(Array.from(byId.values()));
      if (plan.forget_tombstones.length > 0) {
        const gone = new Set(plan.forget_tombstones);
        await writeRouteDeleted((await readRouteDeleted()).filter(t => !gone.has(t.id)));
      }
      return done;
    },
    async record(it, http, first) {
      if (first) await http.putBlob('route:' + it.uid, await RNFS.readFile(routeGpxPath(it.uid), 'utf8'));
      return { ...it };
    },
  };
}

// ---- one library against the server ---------------------------------------------------------

export interface LibrarySyncResult { changed: number; sent: number; deletedSent: number; olderServer?: boolean }
interface SyncState { since: number; pushed: Record<string, number> }
const stateKey = (collection: string) => `sommet.sync.library.${collection}`;

async function loadState(collection: string): Promise<SyncState> {
  try {
    const v = JSON.parse((await AsyncStorage.getItem(stateKey(collection))) || '{}');
    return { since: Number(v?.since) || 0, pushed: v?.pushed && typeof v.pushed === 'object' ? v.pushed : {} };
  } catch {
    return { since: 0, pushed: {} };
  }
}

export async function syncLibrary(lib: Lib, http: LibraryHttp): Promise<LibrarySyncResult> {
  const state = await loadState(lib.collection);
  let pulled;
  try {
    pulled = await http.pull(lib.collection, state.since);
  } catch (e) {
    if (e instanceof OlderServer) return { changed: 0, sent: 0, deletedSent: 0, olderServer: true };
    throw e;
  }
  const fetched = await lib.prefetch(pulled.records ?? [], http);
  const plan = planPull(await lib.items(), await lib.tombstones(), pulled, lib.same);
  const applied = await lib.apply(plan, fetched);
  const complete = Object.keys(plan.put).every(uid => applied.has(uid));
  const pushed = state.pushed;
  for (const uid of plan.remove) delete pushed[uid];

  const items = await lib.items();
  for (const x of items) if (applied.has(x.uid)) pushed[x.uid] = x.updated_at;   // not sent back
  const out: any[] = [];
  for (const x of items) {
    if (pushed[x.uid] !== x.updated_at) out.push(await lib.record(x, http, !(x.uid in pushed)));
  }
  const dead = Array.from(new Set([...plan.kill, ...(await lib.tombstones()).map(t => t.id)])).sort();
  if (out.length > 0 || dead.length > 0) {
    await http.push(lib.collection, out, dead);
    for (const rec of out) pushed[rec.uid] = rec.updated_at;
    for (const uid of dead) delete pushed[uid];
    await lib.apply({ put: {}, remove: [], kill: [], forget_tombstones: dead }, {});
  }
  if (complete) state.since = Number(pulled.now) || state.since;
  await AsyncStorage.setItem(stateKey(lib.collection), JSON.stringify(state));
  return { changed: applied.size + plan.remove.length, sent: out.length, deletedSent: dead.length };
}

// One run at a time (the sync on opening the app and the one after a change can start together).
let inFlight: Promise<{ ok: boolean; changed: boolean; error?: string }> | null = null;

/** Both libraries against Sommet Sync. Quiet when it is not set up. Never throws. */
export function runLibrarySync(stats: RouteStats): Promise<{ ok: boolean; changed: boolean; error?: string }> {
  if (!inFlight) {
    inFlight = (async () => {
      try {
        const cfg = await getSommetSyncCfg();
        if (!cfg) return { ok: false, changed: false };
        const http = libraryHttp(cfg.url, cfg.token);
        const r = await syncLibrary(routesLib(stats), http);
        const p = await syncLibrary(poisLib, http);
        return { ok: true, changed: r.changed + p.changed > 0 };
      } catch (e: any) {
        console.log(`[library-sync] ${e?.message ?? e}`);
        return { ok: false, changed: false, error: e?.message ?? String(e) };
      }
    })().finally(() => { inFlight = null; });
  }
  return inFlight;
}
