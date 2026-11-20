// Light library (sync/13) in the pull: with "Last 90 days" an older move's Track is not
// downloaded; the row is marked as kept on the server and counts as held there.
const mockStore: Record<string, string> = {
  'sommet.sync.cfg': JSON.stringify({ url: 'http://nas/sync.php', token: 't' }),
  'sommet.sync.repull.kcal1': '1',
};
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: async (k: string) => (k in mockStore ? mockStore[k] : null),
  setItem: async (k: string, v: string) => { mockStore[k] = v; },
  removeItem: async (k: string) => { delete mockStore[k]; },
}));
const mockSaved: any[] = [];
jest.mock('../../database/db', () => ({
  sommetUid: (d: string, date: string) => `${d ?? ''}|${(date ?? '').slice(0, 16)}`,
  getAllActivities: async () => mockSaved.map(a => ({ ...a })),
  getSommetTombstones: async () => [],
  addSommetTombstone: async () => {},
  deleteActivitiesByUid: async () => {},
  getActivityByUid: async () => null,
  updateImportedActivity: async () => {},
  markActivitySynced: async (r: any) => { mockSaved.push(r); },
  getSommetLedger: async () => new Map(),
  markSommetSent: async () => {},
  getSommetDeletedSent: async () => new Set(),
  markSommetDeletedSent: async () => {},
}));
jest.mock('../GpxService', () => ({ readGpxFile: async () => '', readFitForGpx: async () => null,
  removeFitForGpx: async () => {}, writeGpxFile: async (id: string) => `/a/${id}.gpx` }));
jest.mock('../StreamsCache', () => ({ streamsCache: { delete: () => {} } }));
jest.mock('../SportNames', () => ({ canonicalSportName: (s: string) => s, isJunkActivity: () => false }));
jest.mock('../SameMove', () => ({ sameMove: () => false, startUtcOf: () => 0 }));

const recent = new Date(Date.now() - 5 * 86400_000).toISOString().slice(0, 19);
const blobs: string[] = [];
(globalThis as any).fetch = jest.fn(async (url: string, init: any = {}) => {
  if (url.includes('blob=1')) {
    blobs.push(url);
    return { ok: true, status: 200, headers: { get: () => null }, text: async () => '<gpx><trkpt/></gpx>' };
  }
  const body = init.method === 'POST' ? { ok: true } : {
    records: [
      { uid: 'w|2025-01-10T08:00', device: 'w', start_time: '2025-01-10T08:00:00', has_track: true, updated_at: 5 },
      { uid: `w|${recent.slice(0, 16)}`, device: 'w', start_time: recent, has_track: true, updated_at: 6 },
    ], deleted: [], now: 1 };
  return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body) };
});

import { runSommetSync } from '../SommetSync';

test('an older pulled move keeps its Track on the server; a recent one downloads it', async () => {
  const r = await runSommetSync();
  expect(r.ok).toBe(true);
  expect(blobs.length).toBe(1);
  expect(blobs[0]).toContain(encodeURIComponent(`w|${recent.slice(0, 16)}`));
  const old = mockSaved.find(x => x.date === '2025-01-10T08:00:00');
  expect(old).toMatchObject({ gpx_path: '', track_remote: 1 });
  expect(mockSaved.find(x => x.date === recent)).toMatchObject({ track_remote: 0 });
  expect(JSON.parse(mockStore['sommet.sync.blobsPushed'])).toContain('w|2025-01-10T08:00');
});
