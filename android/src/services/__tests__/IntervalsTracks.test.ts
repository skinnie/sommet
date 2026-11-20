// The intervals.icu trace backfill: outdoor rides get a track file, indoor ones are never asked
// for one, and one found to have no positions is asked once, not on every run.
const mockStore: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(async (k: string) => mockStore[k] ?? null),
  setItem: jest.fn(async (k: string, v: string) => { mockStore[k] = v; }),
}));
jest.mock('../ApiIntervalsIcu', () => ({ getIntervalsIcuCredentials: jest.fn(async () => ({ apiKey: 'k' })) }));
const mockWritten: Record<string, string> = {};
jest.mock('../GpxService', () => ({
  writeGpxFile: jest.fn(async (id: string, gpx: string) => {
    const path = `/files/activities/${id.replace(/[^\w.-]/g, '_')}.gpx`;
    mockWritten[path] = gpx;
    return path;
  }),
}));
const mockRows: any[] = [];
jest.mock('../../database/db', () => ({
  getAllActivities: jest.fn(async () => mockRows.map(r => ({ ...r }))),
  markActivitySynced: jest.fn(async (rec: any) => {
    const i = mockRows.findIndex(r => r.id === rec.id);
    mockRows[i] = { ...mockRows[i], ...rec };
  }),
}));

import { backfillIntervalsTracks, intervalsTrackQueue } from '../IntervalsTracks';

const row = (id: string, activity_type: string, date: string, distance_m = 10000) =>
  ({ id, activity_type, date, distance_m, gpx_path: '', duration_s: 3600, synced_at: 1, d_plus: 0 });

const streams = (withPositions: boolean) => withPositions
  ? [{ type: 'latlng', data: [50.6, 50.61], data2: [3.06, 3.07] }, { type: 'altitude', data: [20, 21] }]
  : [{ type: 'altitude', data: [20, 21] }];

let fetched: string[] = [];
let answers: Record<string, { status: number; body?: any }> = {};
beforeEach(() => {
  for (const k of Object.keys(mockStore)) delete mockStore[k];
  mockStore['sommet.library.keep'] = 'all';   // these cases are about the queue, not the 90 days
  for (const k of Object.keys(mockWritten)) delete mockWritten[k];
  mockRows.length = 0;
  fetched = [];
  answers = {};
  (globalThis as any).fetch = jest.fn(async (url: string) => {
    const id = /activity\/([^/]+)\/streams/.exec(url)![1];
    fetched.push(id);
    const a = answers[id] ?? { status: 200, body: streams(true) };
    return { ok: a.status >= 200 && a.status < 300, status: a.status, json: async () => a.body };
  });
});

test('indoor sports are not queued, whatever their distance', () => {
  const all = [
    row('icu:i1', 'Cycling', '2026-10-01T08:00:00'),
    row('icu:i2', 'Indoor cycling', '2026-10-02T08:00:00'),     // Zwift: virtual positions
    row('icu:i3', 'Treadmill', '2026-10-03T08:00:00'),
    row('icu:i4', 'Indoor rowing', '2026-10-04T08:00:00'),
    row('icu:i5', 'Indoor training', '2026-10-05T08:00:00'),
    row('icu:i6', 'Crosstrainer', '2026-10-06T08:00:00'),
    row('icu:i7', 'Pool swimming', '2026-10-07T08:00:00'),
    row('icu:i8', 'Running', '2026-10-08T08:00:00'),
    row('icu:i9', 'Walking', '2026-10-09T08:00:00', 0),          // no distance
    row('20261010T080000', 'Cycling', '2026-10-10T08:00:00'),    // a watch move
  ];
  expect(intervalsTrackQueue(all, new Set()).map(a => a.id)).toEqual(['icu:i8', 'icu:i1']);
  expect(intervalsTrackQueue(all, new Set(['icu:i8'])).map(a => a.id)).toEqual(['icu:i1']);
});

test('an outdoor ride gets its track, an indoor one is never fetched', async () => {
  mockRows.push(row('icu:i10', 'Cycling', '2026-10-01T08:00:00'),
                row('icu:i11', 'Indoor cycling', '2026-10-02T08:00:00'));
  const r = await backfillIntervalsTracks(40);
  expect(fetched).toEqual(['i10']);
  expect(r).toEqual({ filled: 1, remaining: 0 });
  expect(mockRows[0].gpx_path).toBe('/files/activities/icu_i10.gpx');
  expect(mockWritten['/files/activities/icu_i10.gpx']).toContain('<trkpt lat="50.6" lon="3.06"><ele>20</ele>');
  expect(mockRows[1].gpx_path).toBe('');
});

test('no positions: asked once; an error: asked again', async () => {
  mockRows.push(row('icu:i20', 'Cycling', '2026-10-03T08:00:00'),   // trainer ride filed as Ride
                row('icu:i21', 'Running', '2026-10-02T08:00:00'),   // streams gone
                row('icu:i22', 'Hiking', '2026-10-01T08:00:00'));   // server hiccup
  answers = { i20: { status: 200, body: streams(false) }, i21: { status: 404 }, i22: { status: 500 } };
  expect(await backfillIntervalsTracks(40)).toEqual({ filled: 0, remaining: 1 });
  expect(fetched).toEqual(['i20', 'i21', 'i22']);
  fetched = [];
  await backfillIntervalsTracks(40);
  expect(fetched).toEqual(['i22']);
  expect(mockRows.every(r => r.gpx_path === '')).toBe(true);
});

test('trackless rides no longer hold the queue: older outdoor rides are reached', async () => {
  for (let d = 1; d <= 5; d++) mockRows.push(row(`icu:n${d}`, 'Cycling', `2026-10-1${d}T08:00:00`));
  mockRows.push(row('icu:old', 'Cycling', '2026-09-01T08:00:00'));
  for (let d = 1; d <= 5; d++) answers[`n${d}`] = { status: 200, body: streams(false) };
  await backfillIntervalsTracks(5);           // the 5 newest have no positions
  expect(mockRows.find(r => r.id === 'icu:old').gpx_path).toBe('');
  await backfillIntervalsTracks(5);           // next run moves past them
  expect(mockRows.find(r => r.id === 'icu:old').gpx_path).toBe('/files/activities/icu_old.gpx');
});
