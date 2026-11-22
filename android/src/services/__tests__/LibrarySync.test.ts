// The route library and the POI library shared through Sommet Sync (LibrarySync.ts).
// 1. The rules give what the desktop's give: librarySync.fixture.json is written by
//    `python3 tools/test_library_sync.py --write-fixture` and checked on both sides.
// 2. Two stand-in devices (each with its own storage, held in memory) against a stand-in
//    server that merges as sync.php does: what one saves, renames or deletes reaches the other.
export {};

type Store = { files: Record<string, string>; kv: Record<string, string> };
const devices: Record<string, Store> = { a: { files: {}, kv: {} }, b: { files: {}, kv: {} } };
let mockOn: Store = devices.a;

jest.mock('react-native-fs', () => ({
  DocumentDirectoryPath: '/docs',
  exists: jest.fn(async (p: string) => p in mockOn.files || Object.keys(mockOn.files).some(k => k.startsWith(p + '/'))),
  mkdir: jest.fn(async () => {}),
  readFile: jest.fn(async (p: string) => { if (!(p in mockOn.files)) throw new Error('ENOENT ' + p); return mockOn.files[p]; }),
  writeFile: jest.fn(async (p: string, text: string) => { mockOn.files[p] = text; }),
  unlink: jest.fn(async (p: string) => { if (!(p in mockOn.files)) throw new Error('ENOENT'); delete mockOn.files[p]; }),
  moveFile: jest.fn(async (from: string, to: string) => { mockOn.files[to] = mockOn.files[from]; delete mockOn.files[from]; }),
}));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(async (k: string) => mockOn.kv[k] ?? null),
  setItem: jest.fn(async (k: string, v: string) => { mockOn.kv[k] = v; }),
  removeItem: jest.fn(async (k: string) => { delete mockOn.kv[k]; }),
}));
jest.mock('../SommetSync', () => ({ getSommetSyncCfg: jest.fn(async () => null) }));

import {
  planPull, sha1Hex, syncLibrary, poisLib, routesLib, LibraryHttp, OlderServer, runLibrarySync,
} from '../LibrarySync';
import { saveLibraryPois, listLibraryPois, renameLibraryPoi, deleteLibraryPoi } from '../PoiLibrary';
import { saveToLibrary, listLibrary, renameLibraryRoute, deleteLibraryRoute, getLibraryGpx, readRouteDeleted } from '../RouteLibrary';

const fixture = require('./librarySync.fixture.json');

describe('the rules, against the desktop', () => {
  const same = (a: any, b: any) => a.key != null && a.key === b.key;
  for (const c of fixture.pulls as any[]) {
    test(c.about, () => {
      expect(planPull(c.local, c.tombstones, { records: c.records, deleted: c.deleted }, same)).toEqual(c.plan);
    });
  }
  test('SHA-1 of a text is the desktop\'s', () => {
    for (const v of fixture.sha1) expect(sha1Hex(v.text)).toBe(v.hex);
  });
});

/** A server that merges as sync.php does: newest updated_at wins, deletes are remembered. */
function server() {
  let clock = 1000;
  const cols: Record<string, { recs: Record<string, any>; dead: Record<string, number> }> = {};
  const blobs: Record<string, string> = {};
  const col = (c: string) => (cols[c] = cols[c] ?? { recs: {}, dead: {} });
  const http: LibraryHttp & { blobs: Record<string, string>; noBlobs: boolean } = {
    blobs, noBlobs: false,
    async pull(c, since) {
      const now = ++clock;
      return {
        now,
        records: Object.values(col(c).recs).filter((r: any) => r.srv > since).map((r: any) => ({ ...r })),
        deleted: Object.entries(col(c).dead).filter(([, ts]) => ts > since).map(([uid]) => uid),
      };
    },
    async push(c, records, deleted) {
      const now = ++clock;
      for (const r of records) {
        if (r.uid in col(c).dead) continue;
        const have = col(c).recs[r.uid];
        if (have && have.updated_at >= r.updated_at) continue;
        col(c).recs[r.uid] = { ...r, srv: now };
      }
      for (const uid of deleted) { delete col(c).recs[uid]; col(c).dead[uid] = now; }
    },
    async getBlob(uid) { return http.noBlobs ? null : (blobs[uid] ?? null); },
    async putBlob(uid, text) { blobs[uid] = text; },
  };
  return http;
}

const gpx = (n: number) => `<gpx version="1.1"><trk><trkseg><trkpt lat="${46 + n}" lon="7"/><trkpt lat="${46.1 + n}" lon="7.1"/></trkseg></trk></gpx>`;
const stats = () => ({ distanceM: 1234, ascentM: 56, points: [{ lat: 46, lon: 7 }, { lat: 46.1, lon: 7.1 }] });
const routes = routesLib(stats);

describe('two devices', () => {
  let http: ReturnType<typeof server>;
  let now = 1_795_000_000_000;
  const use = (name: 'a' | 'b') => { mockOn = devices[name]; };
  const sync = async (name: 'a' | 'b') => { use(name); return [await syncLibrary(routes, http), await syncLibrary(poisLib, http)]; };
  const names = async (name: 'a' | 'b') => {
    use(name);
    return [(await listLibrary()).map(r => r.name).sort(), (await listLibraryPois()).map(p => p.name)];
  };

  beforeEach(() => {
    for (const d of Object.values(devices)) { d.files = {}; d.kv = {}; }
    http = server();
    jest.spyOn(Date, 'now').mockImplementation(() => now);
  });
  afterEach(() => { jest.restoreAllMocks(); });
  const later = (seconds: number) => { now += seconds * 1000; };

  test('what one saves the other gets, and nothing bounces', async () => {
    use('a');
    await saveToLibrary('Tour du Lac', gpx(1), stats());
    await saveLibraryPois([{ name: 'Refuge', lat: 45.85, lon: 6.83, ele: 3835, type: 10 }]);
    let [r, p] = await sync('a');
    expect([r.sent, p.sent]).toEqual([1, 1]);
    [r, p] = await sync('b');
    expect([r.changed, p.changed]).toEqual([1, 1]);
    expect(await names('b')).toEqual([['Tour du Lac'], ['Refuge']]);
    const [route] = await listLibrary();
    expect(await getLibraryGpx(route.id)).toBe(gpx(1));
    expect([route.distanceMeters, route.ascentMeters, route.preview.length]).toEqual([1234, 56, 2]);
    const [poi] = await listLibraryPois();
    expect([poi.lat, poi.lon, poi.ele, poi.type, poi.typeList]).toEqual([45.85, 6.83, 3835, 10, 'ambit']);
    for (const d of ['b', 'a', 'b'] as const) {
      [r, p] = await sync(d);
      expect([r.changed, r.sent, p.changed, p.sent]).toEqual([0, 0, 0, 0]);
    }
  });

  test('a rename and a delete travel, and the delete marks are cleared', async () => {
    use('a');
    const rid = (await saveToLibrary('Old name', gpx(2), stats())).id;
    const [saved] = await saveLibraryPois([{ name: 'Col', lat: 46, lon: 7 }]);
    const pid = saved.ok ? saved.id : '';
    await sync('a'); await sync('b');
    later(5);
    use('b');
    await renameLibraryRoute(rid, 'New name');
    await renameLibraryPoi(pid, 'Col du Midi');
    await sync('b'); await sync('a');
    expect(await names('a')).toEqual([['New name'], ['Col du Midi']]);
    use('a');
    await deleteLibraryRoute(rid);
    await deleteLibraryPoi(pid);
    const [r, p] = await sync('a');
    expect([r.deletedSent, p.deletedSent]).toEqual([1, 1]);
    await sync('b');
    expect(await names('b')).toEqual([[], []]);
    await sync('a'); await sync('b'); await sync('a');
    expect(await names('a')).toEqual([[], []]);
    use('a');
    expect(await readRouteDeleted()).toEqual([]);
  });

  test('the same thing saved on both ends up once, the older one', async () => {
    use('a');
    const ra = (await saveToLibrary('Loop', gpx(3), stats())).id;
    const [sa] = await saveLibraryPois([{ name: 'Spring', lat: 46.5, lon: 7.5 }]);
    later(5);
    use('b');
    await saveToLibrary('Loop (b)', gpx(3), stats());
    await saveLibraryPois([{ name: 'spring', lat: 46.50004, lon: 7.5 }]);
    await sync('a'); await sync('b'); await sync('a'); await sync('b');
    for (const d of ['a', 'b'] as const) {
      use(d);
      expect((await listLibrary()).map(x => x.id)).toEqual([ra]);
      expect((await listLibraryPois()).map(x => x.id)).toEqual([sa.ok ? sa.id : '']);
      expect(await names(d)).toEqual([['Loop'], ['Spring']]);
    }
  });

  test('a route whose GPX did not come is asked again', async () => {
    use('a');
    await saveToLibrary('Late', gpx(4), stats());
    await sync('a');
    http.noBlobs = true;
    let [r] = await sync('b');
    expect([r.changed, (await names('b'))[0]]).toEqual([0, []]);
    http.noBlobs = false;
    [r] = await sync('b');
    expect([r.changed, (await names('b'))[0]]).toEqual([1, ['Late']]);
  });

  test('an older server is skipped quietly', async () => {
    use('a');
    await saveLibraryPois([{ name: 'Kept here', lat: 46, lon: 7 }]);
    const old: LibraryHttp = { ...http, pull: async () => { throw new OlderServer(); } };
    expect(await syncLibrary(poisLib, old)).toEqual({ changed: 0, sent: 0, deletedSent: 0, olderServer: true });
    expect((await listLibraryPois()).map(p => p.name)).toEqual(['Kept here']);
  });

  test('Sommet Sync not set up: nothing happens', async () => {
    expect(await runLibrarySync(stats)).toEqual({ ok: false, changed: false });
  });
});
