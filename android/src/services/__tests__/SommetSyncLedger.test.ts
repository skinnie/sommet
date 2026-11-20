// Sync mockLedger (#SYNC-8/9/11): a sync sends only what the server doesn't hold yet, in requests of
// 200, and each tombstone once. Mirrors the desktop check against the local test server.
const mockStore: Record<string, string> = {
  'sommet.sync.cfg': JSON.stringify({ url: 'http://nas/sync.php', token: 't' }),
  'sommet.sync.repull.kcal1': '1',
};
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: async (k: string) => (k in mockStore ? mockStore[k] : null),
  setItem: async (k: string, v: string) => { mockStore[k] = v; },
  removeItem: async (k: string) => { delete mockStore[k]; },
}));

const mockActs: any[] = [];
const mockLedger = new Map<string, number>();
const mockDeletedSent = new Set<string>();
const mockTombs = new Set<string>();
jest.mock('../../database/db', () => ({
  sommetUid: (d: string, date: string) => `${d ?? ''}|${(date ?? '').slice(0, 16)}`,
  getAllActivities: async () => mockActs.map(a => ({ ...a })),
  getSommetTombstones: async () => Array.from(mockTombs),
  addSommetTombstone: async (u: string) => { mockTombs.add(u); },
  deleteActivitiesByUid: async () => {},
  getActivityByUid: async () => null,
  updateImportedActivity: async () => {},
  markActivitySynced: async () => {},
  getSommetLedger: async () => new Map(mockLedger),
  markSommetSent: async (e: { uid: string; updatedAt: number }[]) => e.forEach(x => mockLedger.set(x.uid, x.updatedAt)),
  getSommetDeletedSent: async () => new Set(mockDeletedSent),
  markSommetDeletedSent: async (u: string[]) => u.forEach(x => mockDeletedSent.add(x)),
}));
jest.mock('../GpxService', () => ({ readGpxFile: async () => '', readFitForGpx: async () => null,
  removeFitForGpx: async () => {}, writeGpxFile: async () => null }));
jest.mock('../StreamsCache', () => ({ streamsCache: { delete: () => {} } }));
jest.mock('../SportNames', () => ({ canonicalSportName: (s: string) => s, isJunkActivity: () => false }));
jest.mock('../SameMove', () => ({ sameMove: () => false, startUtcOf: () => 0 }));

const posts: { records: any[]; deleted: string[] }[] = [];
(global as any).fetch = jest.fn(async (url: string, init: any = {}) => {
  if (init.method === 'POST') posts.push(JSON.parse(init.body));
  const body = init.method === 'POST' ? { ok: true } : { records: [], deleted: [], now: 1 };
  return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body) };
});

import { runSommetSync } from '../SommetSync';

function move(i: number) {
  const d = new Date(Date.UTC(2026, 0, 1) + i * 3600_000).toISOString().slice(0, 19);
  return { id: `m${i}`, date: d, device: 'w', activity_type: 'Running', duration_s: 600, distance_m: 2000,
           d_plus: 0, energy_kcal: 0, updated_at: 1000 + i, synced_at: 1, gpx_path: '' };
}

test('sends everything once, then only what changed', async () => {
  for (let i = 0; i < 450; i++) mockActs.push(move(i));

  let r = await runSommetSync();
  expect(r.ok).toBe(true);
  expect(posts.map(p => p.records.length)).toEqual([200, 200, 50]);
  expect(r.pushed).toBe(450);

  posts.length = 0;
  r = await runSommetSync();
  expect(posts.length).toBe(0);          // nothing changed: no request at all
  expect(r.pushed).toBe(0);

  mockActs[7].updated_at = 99999;            // one edit
  const gone = mockActs.splice(3, 1)[0];     // one deletion
  mockTombs.add(`w|${gone.date.slice(0, 16)}`);
  r = await runSommetSync();
  expect(posts.length).toBe(1);
  expect(posts[0].records.map((x: any) => x.uid)).toEqual([`w|${mockActs[6].date.slice(0, 16)}`]);
  expect(posts[0].deleted).toEqual([`w|${gone.date.slice(0, 16)}`]);

  posts.length = 0;
  r = await runSommetSync();
  expect(posts.length).toBe(0);          // the deletion went once
});
