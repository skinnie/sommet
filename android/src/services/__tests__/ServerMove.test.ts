// Moving Sommet Sync to another server (sync/14): what only the old server holds is listed and
// copied back, every mark of the old server is forgotten, and a new token for the same address
// is not a move. Addresses and tokens here are made up.
const mockStore: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: async (k: string) => (k in mockStore ? mockStore[k] : null),
  setItem: async (k: string, v: string) => { mockStore[k] = v; },
  removeItem: async (k: string) => { delete mockStore[k]; },
  getAllKeys: async () => Object.keys(mockStore),
}));

const mockActs: any[] = [];
const mockLedger = new Map<string, number>();
const mockDeletedSent = new Set<string>();
const mockLocalFits = new Set<string>();
const mockFiles: Record<string, string> = {};
jest.mock('../../database/db', () => ({
  sommetUid: (d: string, date: string) => `${d ?? ''}|${(date ?? '').slice(0, 16)}`,
  getAllActivities: async () => mockActs.map(a => ({ ...a })),
  getSommetTombstones: async () => [],
  addSommetTombstone: async () => {},
  deleteActivitiesByUid: async () => {},
  getActivityByUid: async () => null,
  updateImportedActivity: async () => {},
  markActivitySynced: async () => {},
  getSommetLedger: async () => new Map(mockLedger),
  markSommetSent: async (e: { uid: string; updatedAt: number }[]) => e.forEach(x => mockLedger.set(x.uid, x.updatedAt)),
  getSommetDeletedSent: async () => new Set(mockDeletedSent),
  markSommetDeletedSent: async (u: string[]) => u.forEach(x => mockDeletedSent.add(x)),
  clearSommetServerMarkers: async () => { mockLedger.clear(); mockDeletedSent.clear(); },
  setTrackLocal: async (id: string, path: string, cachedAt: number) => {
    const a = mockActs.find(x => x.id === id);
    if (a) { a.gpx_path = path; a.track_cached_at = cachedAt; }
  },
  clearTrackRemote: async (id: string) => {
    const a = mockActs.find(x => x.id === id);
    if (a) a.track_remote = 0;
  },
}));
jest.mock('../GpxService', () => ({
  readGpxFile: async (p: string) => mockFiles[p] ?? '',
  readFitForGpx: async () => null,
  removeFitForGpx: async () => {},
  writeGpxFile: async (id: string, gpx: string) => { mockFiles[`/a/${id}.gpx`] = gpx; return `/a/${id}.gpx`; },
  writeFitFile: async (id: string, b64: string) => { mockFiles[`/a/${id}.fit`] = b64; return `/a/${id}.fit`; },
  fitFileExists: async (id: string) => mockLocalFits.has(id),
  activityFileStem: (id: string) => id.replace(/[^\w.-]/g, '_'),
  listFitStems: async () => new Set(Object.keys(mockFiles).filter(k => k.endsWith('.fit'))
    .map(k => k.slice(3, -4))),
  readFitById: async (id: string) => mockFiles[`/a/${id}.fit`] ?? null,
}));
let mockCreds: any = null;
jest.mock('../ApiIntervalsIcu', () => ({ getIntervalsIcuCredentials: async () => mockCreds }));
jest.mock('../StreamsCache', () => ({ streamsCache: { delete: () => {} } }));
jest.mock('../SportNames', () => ({ canonicalSportName: (s: string) => s, isJunkActivity: () => false }));
jest.mock('../SameMove', () => ({ sameMove: () => false, startUtcOf: () => 0 }));

import {
  RESCUE_KEY, Rescue, RescueIO, isServerMove, normalizeServerUrl, planRescue, rescueNoteText, runRescue,
  cachedToKeep, forgetConfirmText, uncopiedTrackIds,
} from '../ServerMove';
import { forgetPreviousServer, runSommetSync, setSommetSyncCfg } from '../SommetSync';

const OLD = 'http://old-nas/sync.php';
const NEW = 'http://new-nas/sync.php';
const GPX = '<gpx><trk><trkseg><trkpt lat="1" lon="2"/></trkseg></trk></gpx>';
const FIT = (() => { const b = new Uint8Array(16); b.set([0x2e, 0x46, 0x49, 0x54], 8); return b; })();
const uidOf = (r: { device?: string; date: string }) => `${r.device ?? ''}|${r.date.slice(0, 16)}`;

function reset() {
  for (const k of Object.keys(mockStore)) delete mockStore[k];
  for (const k of Object.keys(mockFiles)) delete mockFiles[k];
  mockActs.length = 0; mockLedger.clear(); mockDeletedSent.clear(); mockLocalFits.clear(); mockCreds = null;
}

describe('pure parts', () => {
  test('a move is a different non-empty address; a new token or a trailing slash is not', () => {
    expect(normalizeServerUrl(' http://a/sync.php/ ')).toBe('http://a/sync.php');
    expect(isServerMove(OLD, NEW)).toBe(true);
    expect(isServerMove(OLD, '')).toBe(true);              // sync turned off
    expect(isServerMove(OLD, OLD + '/')).toBe(false);
    expect(isServerMove('', NEW)).toBe(false);             // nothing was synced before
  });

  test('lists Tracks and FITs only the old server holds', () => {
    const rows = [
      { id: 'a', date: '2025-01-01T08:00:00', device: 'w', gpx_path: '', track_remote: 1 },       // trimmed
      { id: 'b', date: '2025-01-02T08:00:00', device: 'w', gpx_path: '', track_remote: 1 },       // never on the old one
      { id: 'c', date: '2025-01-03T08:00:00', device: 'w', gpx_path: '/a/c.gpx', track_cached_at: 5 }, // opened
      { id: 'icu:9', date: '2025-01-04T08:00:00', device: 'x', gpx_path: '', track_remote: 1 },  // intervals.icu import
      { id: 'd', date: '2026-11-01T08:00:00', device: 'w', gpx_path: '/a/d.gpx' },              // recent, here
    ];
    const onOld = new Set(['w|2025-01-01T08:00', 'w|2025-01-03T08:00', 'x|2025-01-04T08:00', 'w|2026-11-01T08:00']);
    const fitsOnOld = new Set(['w|2025-01-01T08:00', 'w|2025-01-03T08:00']);
    const plan = (intervalsOn: boolean, hasLocalFit = new Set<string>()) =>
      planRescue(rows, { onOld, fitsOnOld, intervalsOn, hasLocalFit, uidOf });
    expect(plan(true)).toEqual([
      { id: 'a', uid: 'w|2025-01-01T08:00', gpx: true, fit: true },
      { id: 'c', uid: 'w|2025-01-03T08:00', gpx: false, fit: true },
    ]);
    expect(plan(false).map(i => i.id)).toEqual(['a', 'c', 'icu:9']);   // intervals.icu off: copied too
    expect(plan(true, new Set(['a', 'c']))).toEqual([{ id: 'a', uid: 'w|2025-01-01T08:00', gpx: true, fit: false }]);
    expect(cachedToKeep(rows, onOld, uidOf).map(r => r.id)).toEqual(['c']);
  });

  test('the Settings line, by count and with sync on or off', () => {
    expect(rescueNoteText(null)).toBe('');
    expect(rescueNoteText({ count: 1, url: OLD }, true)).toBe(
      `1 older activity is still only on your previous server (${OLD}). Keep it running: Sommet copies it to the new one at each sync.`);
    expect(rescueNoteText({ count: 3, url: OLD }, true)).toBe(
      `3 older activities are still only on your previous server (${OLD}). Keep it running: Sommet copies them to the new one at each sync.`);
    expect(rescueNoteText({ count: 1, url: OLD }, false)).toBe(
      `1 older activity is only on your previous server (${OLD}). Set up a server again and Sommet copies it over.`);
    expect(rescueNoteText({ count: 3, url: OLD }, false)).toBe(
      `3 older activities are only on your previous server (${OLD}). Set up a server again and Sommet copies them over.`);
  });

  test('the forget confirm, by count', () => {
    expect(forgetConfirmText(1)).toBe(
      'The activity keeps its details here but loses its map and original file. Forget the previous server?');
    expect(forgetConfirmText(4)).toBe(
      'The 4 activities keep their details here but lose their map and original file. Forget the previous server?');
    expect(uncopiedTrackIds({ url: OLD, token: 't', items: [
      { id: 'a', uid: 'u1', gpx: true, fit: true }, { id: 'c', uid: 'u3', gpx: false, fit: true }] })).toEqual(['a']);
  });
});

describe('copying back', () => {
  function io(answers: Record<string, 'ok' | 'missing' | 'failed'>, saved: { r: Rescue | null }): RescueIO & { stored: string[] } {
    const stored: string[] = [];
    return {
      stored,
      fetchGpx: async uid => answers['gpx:' + uid] === 'ok' ? { status: 'ok', body: GPX }
        : { status: answers['gpx:' + uid] === 'missing' ? 'missing' : 'failed' } as any,
      fetchFit: async uid => answers['fit:' + uid] === 'ok' ? { status: 'ok', body: FIT }
        : { status: answers['fit:' + uid] === 'missing' ? 'missing' : 'failed' } as any,
      storeGpx: async it => { stored.push('gpx:' + it.id); return true; },
      storeFit: async it => { stored.push('fit:' + it.id); return true; },
      save: async r => { saved.r = r; },
    };
  }
  const rescue: Rescue = { url: OLD, token: 'old-t', items: [
    { id: 'a', uid: 'u1', gpx: true, fit: true },
    { id: 'b', uid: 'u2', gpx: true, fit: false },
    { id: 'c', uid: 'u3', gpx: true, fit: false },
  ] };

  test('a file the server has is stored, one it lacks is crossed off', async () => {
    const saved = { r: rescue as Rescue | null };
    const x = io({ 'gpx:u1': 'ok', 'fit:u1': 'ok', 'gpx:u2': 'missing', 'gpx:u3': 'ok' }, saved);
    expect(await runRescue(rescue, x)).toEqual({ done: 3, left: 0, stopped: false });
    expect(x.stored).toEqual(['gpx:a', 'fit:a', 'gpx:c']);
    expect(saved.r).toBeNull();                      // nothing left: the old token is forgotten
  });

  test('a Track the server lacks (404, or no points) is reported so the move stops counting as kept there', async () => {
    const saved = { r: rescue as Rescue | null };
    const missing: string[] = [];
    const x = io({ 'gpx:u1': 'missing', 'fit:u1': 'missing', 'gpx:u2': 'missing', 'gpx:u3': 'missing' }, saved);
    x.fetchGpx = async uid => uid === 'u3' ? { status: 'ok', body: '<gpx></gpx>' } : { status: 'missing' };
    x.missingGpx = async it => { missing.push(it.id); };
    expect(await runRescue(rescue, x)).toEqual({ done: 3, left: 0, stopped: false });
    expect(missing).toEqual(['a', 'b', 'c']);
    expect(x.stored).toEqual([]);
  });

  test('a failure stops the run and keeps the rest for the next sync', async () => {
    const saved = { r: rescue as Rescue | null };
    const x = io({ 'gpx:u1': 'ok', 'fit:u1': 'failed' }, saved);
    expect(await runRescue(rescue, x)).toEqual({ done: 0, left: 3, stopped: true });
    expect(saved.r!.items).toEqual([
      { id: 'a', uid: 'u1', gpx: false, fit: true },   // its Track came over, its FIT not yet
      { id: 'b', uid: 'u2', gpx: true, fit: false },
      { id: 'c', uid: 'u3', gpx: true, fit: false },
    ]);
  });
});

describe('changing the saved server', () => {
  beforeEach(reset);

  function seedOldServer() {
    mockStore['sommet.sync.cfg'] = JSON.stringify({ url: OLD, token: 'old-t' });
    mockStore['sommet.sync.lastPull'] = '123';
    mockStore['sommet.sync.blobsPushed'] = JSON.stringify(['w|2025-01-01T08:00', 'w|2026-11-01T08:00']);
    mockStore['sommet.sync.fitsPushed'] = JSON.stringify(['w|2025-01-01T08:00']);
    mockStore['sommet.sync.gearLastPull'] = '5';
    mockStore['sommet.sync.reminderLastPull'] = '6';
    mockStore['sommet.sync.library.routes'] = JSON.stringify({ since: 9, pushed: { r1: 1 } });
    mockStore['sommet.sync.library.pois'] = JSON.stringify({ since: 9, pushed: {} });
    mockStore['sommet.sync.repull.kcal1'] = '1';
    mockLedger.set('w|2025-01-01T08:00', 1); mockDeletedSent.add('w|2024-01-01T08:00');
    mockActs.push(
      { id: 'a', date: '2025-01-01T08:00:00', device: 'w', gpx_path: '', track_remote: 1, updated_at: 1,
        activity_type: 'Running', duration_s: 60, distance_m: 100, d_plus: 0, synced_at: 1 },
      { id: 'd', date: '2026-11-01T08:00:00', device: 'w', gpx_path: '/a/d.gpx', updated_at: 2,
        activity_type: 'Running', duration_s: 60, distance_m: 100, d_plus: 0, synced_at: 1 });
    mockFiles['/a/d.gpx'] = GPX;
  }

  test('a move lists what only the old server holds and forgets every mark of it', async () => {
    seedOldServer();
    await setSommetSyncCfg({ url: NEW, token: 'new-t' });
    const r = JSON.parse(mockStore[RESCUE_KEY]);
    expect(r).toEqual({ url: OLD, token: 'old-t', items: [{ id: 'a', uid: 'w|2025-01-01T08:00', gpx: true, fit: true }] });
    for (const k of ['sommet.sync.lastPull', 'sommet.sync.blobsPushed', 'sommet.sync.fitsPushed',
                     'sommet.sync.gearLastPull', 'sommet.sync.reminderLastPull',
                     'sommet.sync.library.routes', 'sommet.sync.library.pois']) {
      expect(mockStore[k]).toBeUndefined();
    }
    expect(mockLedger.size).toBe(0);
    expect(mockDeletedSent.size).toBe(0);
    expect(mockStore['sommet.sync.repull.kcal1']).toBe('1');   // a one-off migration flag, not a mark
    expect(JSON.parse(mockStore['sommet.sync.cfg'])).toEqual({ url: NEW, token: 'new-t' });
  });

  test('a new token for the same address is not a move', async () => {
    seedOldServer();
    await setSommetSyncCfg({ url: OLD + '/', token: 'rotated' });
    expect(mockStore[RESCUE_KEY]).toBeUndefined();
    expect(mockStore['sommet.sync.lastPull']).toBe('123');
    expect(mockStore['sommet.sync.library.routes']).toBeDefined();
    expect(mockLedger.size).toBe(1);
  });

  test('moving back to the old server ends the copying', async () => {
    seedOldServer();
    await setSommetSyncCfg({ url: NEW, token: 'new-t' });
    expect(mockStore[RESCUE_KEY]).toBeDefined();
    await setSommetSyncCfg({ url: OLD, token: 'old-t' });
    expect(mockStore[RESCUE_KEY]).toBeUndefined();
  });

  test('the next sync copies from the old server and sends to the new one', async () => {
    seedOldServer();
    await setSommetSyncCfg({ url: NEW, token: 'new-t' });
    const calls: { url: string; method: string; token: string }[] = [];
    (globalThis as any).fetch = jest.fn(async (url: string, init: any = {}): Promise<any> => {
      const method = init.method ?? 'GET';
      calls.push({ url, method, token: init.headers?.['X-Sommet-Token'] ?? '' });
      if (url.startsWith(OLD) && url.includes('blob=1')) {
        if (url.includes('fmt=gpx')) return { ok: true, status: 200, headers: { get: () => null }, text: async () => GPX };
        return { ok: true, status: 200, headers: { get: () => null }, arrayBuffer: async () => FIT.buffer };
      }
      const body = method === 'POST' ? { ok: true } : { records: [], deleted: [], now: 1 };
      return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body),
               json: async () => body };
    });
    const res = await runSommetSync();
    expect(res.ok).toBe(true);
    const fromOld = calls.filter(c => c.url.startsWith(OLD));
    expect(fromOld.map(c => c.method)).toEqual(['GET', 'GET']);
    expect(fromOld.every(c => c.token === 'old-t')).toBe(true);
    expect(mockStore[RESCUE_KEY]).toBeUndefined();
    expect(mockActs.find(a => a.id === 'a')).toMatchObject({ gpx_path: '/a/a.gpx', track_cached_at: 0 });
    // Both moves' Tracks go to the new server: the copied one and the one that was here.
    const gpxPosts = calls.filter(c => c.url.startsWith(NEW) && c.method === 'POST' && c.url.includes('fmt=gpx'));
    expect(gpxPosts.length).toBe(2);
    expect(calls.filter(c => c.url.startsWith(NEW)).every(c => c.token === 'new-t')).toBe(true);
  });

  function serverAnswers(opts: { gpx?: 'ok' | 'missing'; fit?: 'ok' | 'missing'; slowOld?: Promise<void>;
                                 slowPull?: Promise<void> } = {}) {
    const calls: { url: string; method: string; token: string }[] = [];
    (globalThis as any).fetch = jest.fn(async (url: string, init: any = {}): Promise<any> => {
      const method = init.method ?? 'GET';
      calls.push({ url, method, token: init.headers?.['X-Sommet-Token'] ?? '' });
      if (url.startsWith(OLD) && url.includes('blob=1')) {
        if (opts.slowOld) await opts.slowOld;
        const kind = url.includes('fmt=gpx') ? opts.gpx ?? 'ok' : opts.fit ?? 'ok';
        if (kind === 'missing') return { ok: false, status: 404, headers: { get: () => null }, text: async () => '' };
        if (url.includes('fmt=gpx')) return { ok: true, status: 200, headers: { get: () => null }, text: async () => GPX };
        return { ok: true, status: 200, headers: { get: () => null }, arrayBuffer: async () => FIT.buffer };
      }
      if (method === 'GET' && !url.includes('blob=1') && opts.slowPull) await opts.slowPull;
      const body = method === 'POST' ? { ok: true } : { records: [], deleted: [], now: 777 };
      return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body),
               json: async () => body };
    });
    return calls;
  }

  test('A: a move while a sync runs applies once that sync has ended', async () => {
    seedOldServer();
    let release!: () => void;
    const slowPull = new Promise<void>(res => { release = res; });
    serverAnswers({ slowPull });
    const order: string[] = [];
    const run = runSommetSync().then(r => { order.push('sync'); return r; });
    await new Promise<void>(res => setTimeout(res, 10));
    const move = setSommetSyncCfg({ url: NEW, token: 'new-t' }).then(() => { order.push('move'); });
    await new Promise<void>(res => setTimeout(res, 10));
    expect(order).toEqual([]);                       // the move waits
    release();
    await Promise.all([run, move]);
    expect(order).toEqual(['sync', 'move']);
    expect(mockStore['sommet.sync.lastPull']).toBeUndefined();   // the old server's cursor went after the run
    expect(JSON.parse(mockStore[RESCUE_KEY]).url).toBe(OLD);
  });

  test('B: forgetting the previous server leaves uncopied moves without a kept Track', async () => {
    seedOldServer();
    mockActs.push({ id: 'c', date: '2025-01-03T08:00:00', device: 'w', gpx_path: '/a/c.gpx', track_remote: 1,
                    track_cached_at: 0, updated_at: 3, activity_type: 'Running', duration_s: 60, distance_m: 100,
                    d_plus: 0, synced_at: 1 });
    await setSommetSyncCfg({ url: NEW, token: 'new-t' });
    mockStore[RESCUE_KEY] = JSON.stringify({ url: OLD, token: 'old-t', items: [
      { id: 'a', uid: 'w|2025-01-01T08:00', gpx: true, fit: true },
      { id: 'c', uid: 'w|2025-01-03T08:00', gpx: false, fit: true }] });
    await forgetPreviousServer();
    expect(mockActs.find(a => a.id === 'a').track_remote).toBe(0);
    expect(mockActs.find(a => a.id === 'c').track_remote).toBe(1);   // its Track is here
    expect(mockStore[RESCUE_KEY]).toBeUndefined();
  });

  test('C and F1: a Track the old server lacks stops counting as kept there; its copied FIT still goes', async () => {
    seedOldServer();
    await setSommetSyncCfg({ url: NEW, token: 'new-t' });
    const calls = serverAnswers({ gpx: 'missing', fit: 'ok' });
    expect((await runSommetSync()).ok).toBe(true);
    const a = mockActs.find(x => x.id === 'a');
    expect(a.track_remote).toBe(0);
    expect(a.gpx_path).toBe('');
    expect(mockFiles['/a/a.fit']).toBeDefined();
    expect(mockStore[RESCUE_KEY]).toBeUndefined();
    const fitPosts = calls.filter(c => c.url.startsWith(NEW) && c.method === 'POST' && c.url.includes('fmt=fit'));
    expect(fitPosts.map(c => decodeURIComponent(c.url))).toEqual([expect.stringContaining('uid=w|2025-01-01T08:00')]);
  });

  test('a rescue forgotten during a sync is not written back by that sync', async () => {
    seedOldServer();
    await setSommetSyncCfg({ url: NEW, token: 'new-t' });
    let release!: () => void;
    const slowOld = new Promise<void>(res => { release = res; });
    serverAnswers({ gpx: 'ok', fit: 'missing', slowOld });
    const run = runSommetSync();
    await new Promise<void>(res => setTimeout(res, 10));
    mockStore[RESCUE_KEY] = JSON.stringify({ url: OLD, token: 'old-t', items: [
      { id: 'a', uid: 'w|2025-01-01T08:00', gpx: true, fit: true },
      { id: 'z', uid: 'w|2025-02-01T08:00', gpx: true, fit: false }] });
    await forgetPreviousServer();
    release();
    await run;
    expect(mockStore[RESCUE_KEY]).toBeUndefined();
  });
});
