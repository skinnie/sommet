// Weight readings on the NAS (Sommet Sync collection "weight"): pulled, cached, and only the
// readings the server doesn't hold exactly are sent.
const mockStore: Record<string, string> = {
  'sommet.sync.cfg': JSON.stringify({ url: 'http://nas/sync.php', token: 't' }),
};
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: async (k: string) => (k in mockStore ? mockStore[k] : null),
  setItem: async (k: string, v: string) => { mockStore[k] = v; },
  removeItem: async (k: string) => { delete mockStore[k]; },
}));
jest.mock('../../database/db', () => ({}));
jest.mock('../GpxService', () => ({}));
jest.mock('../StreamsCache', () => ({ streamsCache: {} }));

let mockServer: any[] = [
  { uid: '2026-11-18', date: '2026-11-18', weightKg: 71.4, source: 'manual', updated_at: 5, srv: 6 },
];
const posts: any[] = [];
(global as any).fetch = jest.fn(async (url: string, init: any = {}) => {
  if (init.method === 'POST') posts.push(JSON.parse(init.body));
  const body = init.method === 'POST' ? { ok: true } : { records: mockServer, deleted: [], now: 1 };
  return { ok: true, status: 200, headers: { get: () => null }, text: async () => JSON.stringify(body) };
});

import { pullNasWeights, pushNasWeights, loadNasCache } from '../NasWeight';

test('pull keeps the readings without server fields, and caches them', async () => {
  const got = await pullNasWeights();
  expect(got).toEqual([{ date: '2026-11-18', weightKg: 71.4, source: 'manual' }]);
  expect(await loadNasCache()).toEqual(got);
});

test('push sends only what the server lacks or holds differently', async () => {
  const onServer = (await pullNasWeights())!;
  const merged = [
    { date: '2026-11-18', weightKg: 71.4, source: 'manual' as const },          // same: not sent
    { date: '2026-11-20', weightKg: 70.9, bodyFatPct: 14.2, source: 'scale' as const },
  ];
  expect(await pushNasWeights(merged, onServer)).toBe(1);
  expect(posts[0].records).toEqual([expect.objectContaining({ uid: '2026-11-20', weightKg: 70.9 })]);
  posts.length = 0;
  expect(await pushNasWeights(merged, await loadNasCache())).toBe(0);           // now held: nothing
  expect(posts.length).toBe(0);
});
