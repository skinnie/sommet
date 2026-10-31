// The orphan-file scan on Activities (tablet audit F21, 2026-10-31): an intervals.icu move
// "icu:<n>" keeps its track as "icu_<n>.gpx", and the scan used to take every such file for a
// lost move - 816 copies without a date on the tablet. A file now matches its row by the name
// the row's file is saved under.
export {};

const mockSaved: any[] = [];
const mockFiles: Record<string, string> = {};
const mockIds: string[] = [];

jest.mock('../GpxService', () => ({
  ...jest.requireActual('../GpxService'),
  listGpxFiles: jest.fn(async () => Object.keys(mockFiles)),
  readGpxFile: jest.fn(async (p: string) => mockFiles[p]),
}));
jest.mock('react-native-fs', () => ({ DocumentDirectoryPath: '/files' }));
jest.mock('../GpxParser', () => ({
  extractGpxMetadata: jest.fn((xml: string) => JSON.parse(xml)),
}));
jest.mock('../../database/db', () => ({
  getAllSyncedIds: jest.fn(async () => mockIds),
  isActivityDeleted: jest.fn(async () => false),
  rejectJunkActivity: jest.fn(async () => false),
  markActivitySynced: jest.fn(async (r: any) => { mockSaved.push(r); }),
}));

const { adoptOrphanGpx } = require('../OrphanGpx') as typeof import('../OrphanGpx');

const meta = (date: string) => JSON.stringify({
  date, durationS: 3052, distanceM: 5763, dPlus: 48, activityType: 'Running',
});

beforeEach(() => {
  mockSaved.length = 0;
  mockIds.length = 0;
  for (const k of Object.keys(mockFiles)) delete mockFiles[k];
});

test('an intervals.icu track is not taken for a lost move', async () => {
  mockIds.push('icu:i179621975');
  mockFiles['/files/activities/icu_i179621975.gpx'] = meta('');
  expect(await adoptOrphanGpx()).toBe(0);
  expect(mockSaved).toHaveLength(0);
});

test('the track of a deleted intervals.icu move is not brought back', async () => {
  mockIds.push('icu:i184855329');   // getAllSyncedIds also lists deleted ids
  mockFiles['/files/activities/icu_i184855329.gpx'] = meta('2026-09-09T08:50:22');
  expect(await adoptOrphanGpx()).toBe(0);
});

test('a watch move left over from an interrupted sync becomes a row', async () => {
  mockFiles['/files/activities/20261031T082101.gpx'] = meta('2026-10-31T08:21:01.000Z');
  expect(await adoptOrphanGpx()).toBe(1);
  expect(mockSaved[0]).toMatchObject({ id: '20261031T082101', date: '2026-10-31T08:21:01.000Z' });
});

test('a track with no time never becomes a row without a date', async () => {
  mockFiles['/files/activities/someroute.gpx'] = meta('');
  expect(await adoptOrphanGpx()).toBe(0);
});
