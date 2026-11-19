// sync/11: after a token rotation the server still takes the old token and sends the new one
// back in X-Sommet-Token-New; the app saves it so nobody has to paste it.
const mockStore: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  __esModule: true,
  default: {
    getItem: jest.fn(async (k: string) => (k in mockStore ? mockStore[k] : null)),
    setItem: jest.fn(async (k: string, v: string) => { mockStore[k] = v; }),
    removeItem: jest.fn(async (k: string) => { delete mockStore[k]; }),
  },
}));
jest.mock('../../database/db', () => ({ getSommetTombstones: async () => [], getAllActivities: async () => [] }));
jest.mock('../GpxService', () => ({}));
jest.mock('../SportNames', () => ({}));
jest.mock('../StreamsCache', () => ({ streamsCache: new Map() }));

import { adoptNewToken, getSommetSyncCfg, setSommetSyncCfg } from '../SommetSync';

const URL = 'https://nas.example/sommet/sync.php';
const reply = (h: Record<string, string>) => ({ headers: { get: (k: string) => h[k] ?? null } });

beforeEach(async () => { for (const k of Object.keys(mockStore)) delete mockStore[k]; await setSommetSyncCfg({ url: URL, token: 'old' }); });

test('saves the new token the server sends', async () => {
  await adoptNewToken(URL, 'old', reply({ 'X-Sommet-Token-New': 'new' }));
  expect((await getSommetSyncCfg())?.token).toBe('new');
  expect((await getSommetSyncCfg())?.url).toBe(URL);
});

test('leaves the token alone when the server sends none', async () => {
  await adoptNewToken(URL, 'old', reply({}));
  expect((await getSommetSyncCfg())?.token).toBe('old');
});

test('does not touch the settings when the request used other creds (e.g. a Test with typed values)', async () => {
  await adoptNewToken('https://other/sync.php', 'old', reply({ 'X-Sommet-Token-New': 'new' }));
  await adoptNewToken(URL, 'typed', reply({ 'X-Sommet-Token-New': 'new' }));
  expect((await getSommetSyncCfg())?.token).toBe('old');
});

test('fetchT gives up on a request that never answers', async () => {
  jest.useFakeTimers();
  const { fetchT } = require('../SommetSync');
  (global as any).fetch = jest.fn((_u: string, init: any) => new Promise((_res, rej) => {
    init.signal.addEventListener('abort', () => rej(new Error('aborted')));
  }));
  const p = fetchT('https://nas.example/sync.php?blob=1', {}, 1000);
  jest.advanceTimersByTime(1000);
  await expect(p).rejects.toThrow('aborted');
  jest.useRealTimers();
});

test('a run with no server set up reports no error and ends idle', async () => {
  const mod = require('../SommetSync');
  await mod.setSommetSyncCfg(null);
  const seen: any[] = [];
  const off = mod.onSommetSyncProgress((p: any) => seen.push({ ...p }));
  await mod.runSommetSync();
  off();
  expect(seen[0].stage).toBe('moves');
  expect(mod.getSommetSyncProgress()).toEqual(expect.objectContaining({ stage: '', error: '' }));
});

test('an unreachable server leaves an error until it is closed', async () => {
  const mod = require('../SommetSync');
  (global as any).fetch = jest.fn(async () => { throw new Error('offline'); });
  await mod.runSommetSync();
  expect(mod.getSommetSyncProgress().error).not.toBe('');
  expect(mod.getSommetSyncProgress().stage).toBe('');
  mod.clearSommetSyncError();
  expect(mod.getSommetSyncProgress().error).toBe('');
});
