// sync/06 "Split here" on Android: two moves from one file, the original kept, the first move
// keeping its id and start (so Sommet Sync sees the same move), nothing written on a bad split.
const mockFiles: Record<string, string> = {};
jest.mock('react-native-fs', () => ({
  DocumentDirectoryPath: '/doc',
  exists: jest.fn(async (p: string) => p in mockFiles),
  readFile: jest.fn(async (p: string) => mockFiles[p]),
  writeFile: jest.fn(async (p: string, d: string, enc?: string) => {
    mockFiles[p] = enc === 'base64' ? d : d;
  }),
  copyFile: jest.fn(async (a: string, b: string) => { mockFiles[b] = mockFiles[a]; }),
  mkdir: jest.fn(async () => {}),
}));
const mockRows: any[] = [];
jest.mock('../../database/db', () => ({
  markActivitySynced: jest.fn(async (r: any) => { mockRows.push(r); }),
  sommetUid: (d: string, date: string) => `${d}|${date.slice(0, 16)}`,
}));
jest.mock('../SommetSync', () => ({
  forgetPushedBlob: jest.fn(async () => {}),
  forgetPushedFit: jest.fn(async () => {}),
  bumpTrackRev: jest.fn(async () => {}),
}));
jest.mock('../GpxService', () => ({
  fitPathForGpx: jest.fn(async (g: string) => {
    const p = g.replace(/\.gpx$/, '.fit');
    return p in mockFiles ? p : null;
  }),
  writeGpxFile: jest.fn(async (id: string, xml: string) => { const p = `/doc/a/${id}.gpx`; mockFiles[p] = xml; return p; }),
  writeFitFile: jest.fn(async (id: string, b64: string) => { const p = `/doc/a/${id}.fit`; mockFiles[p] = b64; return p; }),
}));

import { splitMove } from '../SplitMove';
const fixture = require('./twoRidesInOne.fixture.json');

const activity = {
  id: 'edge_B6E15513', synced_at: 1, gpx_path: '/doc/a/edge_B6E15513.gpx', date: new Date((1_100_000_000 + 631065600) * 1000).toISOString(),   // FIT time counts from 1989-12-31
  duration_s: 59457, distance_m: 548, d_plus: 0, activity_type: 'Cycling', device: '', moving_s: 845,
};

beforeEach(() => {
  for (const k of Object.keys(mockFiles)) delete mockFiles[k];
  mockRows.length = 0;
  mockFiles['/doc/a/edge_B6E15513.gpx'] = '<gpx/>';
  mockFiles['/doc/a/edge_B6E15513.fit'] = fixture.fit_b64;
});

test('two moves from one file; the first keeps its id and start, the file as recorded is kept', async () => {
  const r = await splitMove(activity as any, 535);
  expect(r).toEqual({ ok: true, secondId: 'edge_B6E15513-2' });
  // sync/08: both rides get a new track version, and the first ride's FIT is sent again
  const sync = require('../SommetSync');
  expect(sync.bumpTrackRev).toHaveBeenCalledTimes(1);
  expect(sync.bumpTrackRev.mock.calls[0][0]).toHaveLength(2);
  expect(sync.forgetPushedFit).toHaveBeenCalledTimes(1);
  expect(mockFiles['/doc/a/edge_B6E15513.orig.fit']).toBe(fixture.fit_b64);
  expect(mockRows).toHaveLength(2);
  const [a, b] = mockRows;
  expect(a.id).toBe(activity.id);
  expect(a.date).toBe(activity.date);
  expect([a.duration_s, a.moving_s, a.distance_m]).toEqual([535, 535, 269]);
  expect(b.id).toBe('edge_B6E15513-2');
  expect(Date.parse(b.date) - Date.parse(activity.date)).toBe((535 + 58612) * 1000);
  expect([b.duration_s, b.moving_s, b.distance_m]).toEqual([310, 310, 279]);
  expect(mockFiles['/doc/a/edge_B6E15513.fit']).not.toBe(fixture.fit_b64);
});

test('no long stop at that point: nothing is written', async () => {
  const r = await splitMove(activity as any, 1234);
  expect(r.ok).toBe(false);
  expect(mockRows).toHaveLength(0);
  expect(mockFiles['/doc/a/edge_B6E15513.fit']).toBe(fixture.fit_b64);
  expect('/doc/a/edge_B6E15513.orig.fit' in mockFiles).toBe(false);
});

test('a move with no FIT on the device', async () => {
  delete mockFiles['/doc/a/edge_B6E15513.fit'];
  const r = await splitMove(activity as any, 535);
  expect(r.ok).toBe(false);
  expect(mockRows).toHaveLength(0);
});
