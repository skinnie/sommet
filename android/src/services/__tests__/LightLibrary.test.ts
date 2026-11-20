// Light library (sync/13): which older Tracks may leave this device, which opened ones are dropped
// first when the space is full, and that a pulled older move is not downloaded.
const mockStore: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: async (k: string) => (k in mockStore ? mockStore[k] : null),
  setItem: async (k: string, v: string) => { mockStore[k] = v; },
  removeItem: async (k: string) => { delete mockStore[k]; },
}));
let mockCreds: any = null;
jest.mock('../ApiIntervalsIcu', () => ({ getIntervalsIcuCredentials: async () => mockCreds }));
const mockRows: any[] = [];
const mockRemote = new Set<string>();
jest.mock('../../database/db', () => ({
  sommetUid: (d: string, date: string) => `${d ?? ''}|${(date ?? '').slice(0, 16)}`,
  getAllActivities: async () => mockRows.map(a => ({ ...a })),
  getActivityById: async (id: string) => mockRows.find(r => r.id === id) ?? null,
  setTrackRemote: async (id: string) => {
    const r = mockRows.find(x => x.id === id); r.gpx_path = ''; r.track_remote = 1; r.track_cached_at = 0;
    mockRemote.add(id);
  },
  setTrackLocal: async (id: string, p: string, at: number) => {
    const r = mockRows.find(x => x.id === id); r.gpx_path = p; r.track_cached_at = at;
  },
  clearTrackRemote: async () => {},
  markTracksRemote: async (ids: string[]) => ids.forEach(id => { mockRows.find(x => x.id === id).track_remote = 1; }),
  getCachedTracks: async () => mockRows.filter(r => r.track_cached_at > 0 && r.gpx_path)
    .sort((a, b) => a.track_cached_at - b.track_cached_at)
    .map(r => ({ id: r.id, gpx_path: r.gpx_path, track_cached_at: r.track_cached_at })),
}));
const mockDeleted: string[] = [];
const mockSizes: Record<string, number> = {};
jest.mock('../GpxService', () => ({
  deleteGpxFile: async (p: string) => { mockDeleted.push(p); },
  removeFitForGpx: async (p: string) => { mockDeleted.push(p.replace(/\.gpx$/, '.fit')); },
  writeGpxFile: async (id: string) => `/a/${id}.gpx`,
  trackFileBytes: async (p: string) => mockSizes[p] ?? 0,
  fileExists: async () => false,
}));
jest.mock('../SommetSync', () => ({
  getSommetSyncCfg: async () => (mockStore['cfg'] ? { url: 'http://nas', token: 't' } : null),
  getPushedBlobs: async () => new Set<string>(JSON.parse(mockStore['pushed'] || '[]')),
  getPushedFits: async () => new Set<string>(JSON.parse(mockStore['fits'] || '[]')),
  fetchGpxBlob: async () => '<gpx><trkpt lat="1" lon="2"/></gpx>',
}));
jest.mock('../IntervalsTracks', () => ({
  fetchIntervalsGpx: async () => 'none',
  loadNoTrack: async () => new Set<string>(),
  intervalsTrackQueue: (all: any[]) => all.filter(a => a.id.startsWith('icu:') && !a.gpx_path && a.distance_m > 0),
}));

import { pickTracksToTrim, pickEvictions, trimOldTracks, evictTrackCache, fetchRemoteTrack } from '../LightLibrary';
import { lightCutoffMinute } from '../LibraryPrefs';

const CUT = '2026-08-22T00:00';
const row = (id: string, date: string, device = 'w', extra: any = {}) =>
  ({ id, date, device, gpx_path: `/a/${id}.gpx`, distance_m: 5000, ...extra });

beforeEach(() => {
  for (const k of Object.keys(mockStore)) delete mockStore[k];
  mockRows.length = 0; mockDeleted.length = 0; mockRemote.clear(); mockCreds = null;
  for (const k of Object.keys(mockSizes)) delete mockSizes[k];
});

describe('trim selection', () => {
  const rows = [
    row('old-on-nas', '2026-01-10T08:00:00'),
    row('old-not-sent', '2026-01-11T08:00:00'),
    row('recent-on-nas', '2026-11-01T08:00:00'),
    row('twin-a', '2026-02-01T08:00:00'),           // two moves in the same minute: one uid
    row('twin-b', '2026-02-01T08:00:30'),
    row('icu:5', '2026-03-01T08:00:00', 'GARMIN'),
    row('old-cached', '2026-01-12T08:00:00', 'w', { track_cached_at: 5 }),
  ];
  const onNas = new Set(['w|2026-01-10T08:00', 'w|2026-11-01T08:00', 'w|2026-02-01T08:00', 'w|2026-01-12T08:00']);

  test('only older Tracks the server confirms, never same-minute twins', () => {
    const got = pickTracksToTrim(rows, { cutoff: CUT, nasOn: true, intervalsOn: false, onNas, fitsOnNas: new Set() });
    expect(got).toEqual([{ id: 'old-on-nas', gpxPath: '/a/old-on-nas.gpx', dropFit: false }]);
  });

  test('the FIT goes only when the server holds it too', () => {
    const got = pickTracksToTrim(rows, { cutoff: CUT, nasOn: true, intervalsOn: false, onNas,
                                         fitsOnNas: new Set(['w|2026-01-10T08:00']) });
    expect(got[0].dropFit).toBe(true);
  });

  test('intervals.icu imports can be asked again when it is connected', () => {
    const got = pickTracksToTrim(rows, { cutoff: CUT, nasOn: false, intervalsOn: true, onNas, fitsOnNas: new Set() });
    expect(got.map(p => p.id)).toEqual(['icu:5']);   // the NAS isn't set up: its list counts for nothing
  });

  test('watch only (no Sommet Sync, no intervals.icu) never trims', () => {
    expect(pickTracksToTrim(rows, { cutoff: CUT, nasOn: false, intervalsOn: false, onNas, fitsOnNas: onNas })).toEqual([]);
  });

  test('trimOldTracks deletes the file and marks the Track as kept on the server', async () => {
    mockStore['cfg'] = '1';
    mockStore['pushed'] = JSON.stringify(['w|2025-01-10T08:00']);
    mockRows.push(row('a', '2025-01-10T08:00:00'), row('b', '2025-01-11T08:00:00'));
    await trimOldTracks();
    expect(mockDeleted).toEqual(['/a/a.gpx']);
    expect(mockRows[0]).toMatchObject({ gpx_path: '', track_remote: 1 });
    expect(mockRows[1].gpx_path).toBe('/a/b.gpx');
  });

  test('"Everything" never trims', async () => {
    mockStore['cfg'] = '1';
    mockStore['sommet.library.keep'] = 'all';
    mockStore['pushed'] = JSON.stringify(['w|2025-01-10T08:00']);
    mockRows.push(row('a', '2025-01-10T08:00:00'));
    await trimOldTracks();
    expect(mockDeleted).toEqual([]);
  });

  test('the cutoff is 90 days back', () => {
    expect(lightCutoffMinute(Date.UTC(2026, 10, 20, 12, 0))).toBe('2026-08-22T12:00');
  });
});

describe('cache eviction', () => {
  test('oldest fetch goes first, until under the limit', () => {
    const cached = [
      { id: 'mid', bytes: 40, fetchedAt: 2 },
      { id: 'oldest', bytes: 40, fetchedAt: 1 },
      { id: 'newest', bytes: 40, fetchedAt: 3 },
    ];
    expect(pickEvictions(cached, 100)).toEqual(['oldest']);
    expect(pickEvictions(cached, 40)).toEqual(['oldest', 'mid']);
    expect(pickEvictions(cached, 200)).toEqual([]);
  });

  test('the one just opened is kept', () => {
    const cached = [{ id: 'just', bytes: 90, fetchedAt: 1 }, { id: 'other', bytes: 90, fetchedAt: 2 }];
    expect(pickEvictions(cached, 100, 'just')).toEqual(['other']);
  });

  test('evictTrackCache drops files and marks them as kept on the server', async () => {
    mockStore['sommet.library.cacheMb'] = '100';
    const MB = 1024 * 1024;
    mockRows.push(row('x', '2025-01-01T08:00:00', 'w', { track_cached_at: 10 }),
                  row('y', '2025-01-02T08:00:00', 'w', { track_cached_at: 20 }));
    mockSizes['/a/x.gpx'] = 60 * MB; mockSizes['/a/y.gpx'] = 60 * MB;
    await evictTrackCache();
    expect(mockDeleted).toEqual(['/a/x.fit', '/a/x.gpx']);
    expect(mockRows[0]).toMatchObject({ gpx_path: '', track_remote: 1 });
    expect(mockRows[1].gpx_path).toBe('/a/y.gpx');
  });
});

test('opening an older move fetches its Track from the server and counts it in the cache', async () => {
  mockStore['cfg'] = '1';
  mockRows.push({ id: 'old', date: '2025-01-01T08:00:00', device: 'w', gpx_path: '', track_remote: 1 });
  const p = await fetchRemoteTrack('old');
  expect(p).toBe('/a/old.gpx');
  expect(mockRows[0].gpx_path).toBe('/a/old.gpx');
  expect(mockRows[0].track_cached_at).toBeGreaterThan(0);
});
