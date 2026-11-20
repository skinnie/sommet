// "Keep maps of the last 90 days offline" (sync/13 option 7b): which moves, which points, which
// tiles, and that a run fetches each tile once and stops when switched off.
const mockStore: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: async (k: string) => (k in mockStore ? mockStore[k] : null),
  setItem: async (k: string, v: string) => { mockStore[k] = v; },
}));
const mockRows: any[] = [];
jest.mock('../../database/db', () => ({ getAllActivities: async () => mockRows }));
const mockGpx: Record<string, string> = {};
jest.mock('../GpxService', () => ({ readGpxFile: async (p: string) => mockGpx[p] ?? '' }));
jest.mock('../MapProviderService', () => ({ getMapProvider: async () => 'osm' }));
const mockFetched: string[] = [];
jest.mock('../TileCache', () => ({
  ensureTileCached: async (p: string, z: number, x: number, y: number) => { mockFetched.push(`${p}/${z}/${x}/${y}`); },
}));

import {
  recentOutdoorMoves, gpxPositions, thinTrack, corridorTiles, TileSetBuilder, downloadRecentTiles,
  MIN_Z, MAX_Z,
} from '../RecentTiles';
import { setKeepRecentTiles, getKeepRecentTiles, lightCutoffMinute } from '../LibraryPrefs';

beforeEach(() => {
  for (const k of Object.keys(mockStore)) delete mockStore[k];
  mockRows.length = 0; mockFetched.length = 0;
  for (const k of Object.keys(mockGpx)) delete mockGpx[k];
});

test('only outdoor moves of the last 90 days with a Track here', () => {
  const cut = '2026-08-22T00:00';
  const rows = [
    { id: 'a', date: '2026-11-01T08:00:00', gpx_path: '/a.gpx', activity_type: 'Running' },
    { id: 'b', date: '2026-08-21T23:59:00', gpx_path: '/b.gpx', activity_type: 'Running' },   // older
    { id: 'c', date: '2026-08-22T00:00:00', gpx_path: '/c.gpx', activity_type: 'Cycling' },   // on the cut
    { id: 'd', date: '2026-11-01T08:00:00', gpx_path: '', activity_type: 'Running' },         // on the server
    { id: 'e', date: '2026-11-01T08:00:00', gpx_path: '/e.gpx', activity_type: 'Indoor cycling' },
    { id: 'f', date: '2026-11-01T08:00:00', gpx_path: '/f.gpx', activity_type: 'Treadmill' },
    { id: 'g', date: '2026-11-01T08:00:00', gpx_path: '/g.gpx', activity_type: 'Virtual ride' },
    { id: 'h', date: '2026-11-01T08:00:00', gpx_path: '/h.gpx', activity_type: 'Pool swimming' },
  ];
  expect(recentOutdoorMoves(rows, cut).map(r => r.id)).toEqual(['a', 'c']);
});

test('reads trkpt positions in either attribute order', () => {
  const gpx = `<gpx><trk><trkseg><trkpt lat="38.7" lon="-9.1"><ele>3</ele></trkpt>
    <trkpt lon='-9.2' lat='38.8'/><trkpt lat="x" lon="1"/></trkseg></trk><wpt lat="1" lon="1"/></gpx>`;
  expect(gpxPositions(gpx)).toEqual([{ lat: 38.7, lon: -9.1 }, { lat: 38.8, lon: -9.2 }]);
});

test('thins to one point every ~150 m and drops 0,0', () => {
  // 1e-4 deg of latitude ≈ 11.1 m: 100 points over ~1.1 km.
  const pts = Array.from({ length: 100 }, (_, i) => ({ lat: 38.7 + i * 1e-4, lon: -9.1 }));
  const thin = thinTrack([{ lat: 0, lon: 0 }, ...pts]);
  expect(thin[0]).toEqual(pts[0]);
  expect(thin.length).toBe(Math.floor(99 / 14) + 1);   // 14 steps = 155 m, 13 = 144 m (< 150)
  for (let i = 1; i < thin.length; i++) {
    expect((thin[i].lat - thin[i - 1].lat) * 111000).toBeGreaterThanOrEqual(150);
  }
});

test('one point: its tile and the 8 around it, at each zoom 12..16', () => {
  const tiles = corridorTiles([[{ lat: 38.7, lon: -9.1 }]]);
  expect(tiles.length).toBe(9 * (MAX_Z - MIN_Z + 1));
  const z16 = tiles.filter(t => t.z === 16);
  const xs = [...new Set(z16.map(t => t.x))].sort(), ys = [...new Set(z16.map(t => t.y))].sort();
  expect(xs.length).toBe(3); expect(ys.length).toBe(3);
  expect(xs[2] - xs[0]).toBe(2); expect(ys[2] - ys[0]).toBe(2);
});

test('each tile once, however many moves pass through it', () => {
  const p = { lat: 38.7, lon: -9.1 };
  const once = corridorTiles([[p]]).length;
  expect(corridorTiles([[p, p], [p], [{ lat: 38.70001, lon: -9.10001 }]]).length).toBe(once);
  const keys = corridorTiles([[p, { lat: 38.71, lon: -9.11 }]]).map(t => `${t.z}/${t.x}/${t.y}`);
  expect(new Set(keys).size).toBe(keys.length);
});

test('stops at the cap', () => {
  const track = Array.from({ length: 500 }, (_, i) => ({ lat: 38 + i * 0.01, lon: -9 }));
  expect(corridorTiles([track], 100).length).toBe(100);
  const b = new TileSetBuilder(50);
  expect(b.add(track)).toBe(false);
  expect(b.full).toBe(true);
});

test('wraps at the date line, skips 0,0 and the poles', () => {
  const z12 = corridorTiles([[{ lat: 10, lon: 179.99 }]]).filter(t => t.z === 12).map(t => t.x);
  expect(new Set(z12)).toEqual(new Set([4094, 4095, 0]));
  expect(corridorTiles([[{ lat: 0, lon: 0 }, { lat: 86, lon: 10 }]])).toEqual([]);
});

test('off by default: a run does nothing', async () => {
  expect(await getKeepRecentTiles()).toBe(false);
  mockRows.push({ id: 'a', date: '2099-01-01T00:00', gpx_path: '/a.gpx', activity_type: 'Running' });
  mockGpx['/a.gpx'] = '<trkpt lat="38.7" lon="-9.1"/>';
  expect(await downloadRecentTiles()).toBe(0);
  expect(mockFetched).toEqual([]);
});

test('switched on: fetches every tile of the recent outdoor moves once', async () => {
  await setKeepRecentTiles(true);
  const now = new Date().toISOString().slice(0, 16);
  expect(now >= lightCutoffMinute()).toBe(true);
  mockRows.push({ id: 'a', date: now, gpx_path: '/a.gpx', activity_type: 'Running' });
  mockRows.push({ id: 'b', date: now, gpx_path: '/b.gpx', activity_type: 'Running' });   // same place
  mockRows.push({ id: 'c', date: now, gpx_path: '/c.gpx', activity_type: 'Indoor cycling' });
  mockGpx['/a.gpx'] = mockGpx['/b.gpx'] = '<trkpt lat="38.7" lon="-9.1"/>';
  mockGpx['/c.gpx'] = '<trkpt lat="10" lon="10"/>';
  const n = await downloadRecentTiles();
  expect(n).toBe(45);
  expect(mockFetched.length).toBe(45);
  expect(new Set(mockFetched).size).toBe(45);
  expect(mockFetched.every(k => k.startsWith('osm/'))).toBe(true);
});
