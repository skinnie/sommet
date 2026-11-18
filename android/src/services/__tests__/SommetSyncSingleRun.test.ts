// runSommetSync shares one run between overlapping callers (open-sync + watch-sync at once).
let resolveGet: (v: any) => void = () => {};
const mockGetItem = jest.fn((key: string) => key === 'sommet.sync.cfg'
  ? Promise.resolve(JSON.stringify({ url: 'http://nas/sync.php', token: 't' }))
  : Promise.resolve(null));
jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: (k: string) => mockGetItem(k), setItem: jest.fn(async () => {}) }));
jest.mock('../../database/db', () => ({ getSommetTombstones: jest.fn(async () => []) }));
jest.mock('../GpxService', () => ({}));
jest.mock('../SportNames', () => ({}));
jest.mock('../SameMove', () => ({}));

const fetchMock = jest.fn(() => new Promise(r => { resolveGet = r; }));
(global as any).fetch = fetchMock;

import { runSommetSync } from '../SommetSync';

test('two overlapping calls make one request and get the same result', async () => {
  const a = runSommetSync();
  const b = runSommetSync();
  expect(a).toBe(b);
  await new Promise(r => setTimeout(r, 0));
  expect(fetchMock).toHaveBeenCalledTimes(1);
  resolveGet({ ok: false, status: 500, json: async () => ({}), text: async () => '' });
  const [ra, rb] = await Promise.all([a, b]);
  expect(ra).toEqual(rb);
  expect(ra.ok).toBe(false);
});
