// A move synced from the watch keeps the watch's own calories and is saved under the watch
// serial, the name the desktop uses (tablet audit, 2026-10-31: all 847 watch moves on the
// tablet had 0 kcal and no device). The sync also says whether it set the clock, so Home
// doesn't set it a second time.
export {};

const mockSaved: any[] = [];
jest.mock('../devices/AmbitDeviceProvider', () => ({ ambitDeviceProvider: {} }));
jest.mock('../GpxService', () => ({ writeGpxFile: jest.fn(async () => '/files/a.gpx'), writeFitFile: jest.fn() }));
jest.mock('../GpxParser', () => ({
  extractGpxMetadata: jest.fn(() => ({
    date: '2026-10-29T08:20:55.000Z', durationS: 2653, distanceM: 1325, dPlus: 0,
    activityType: 'Pool swimming', energyKcal: 461,
  })),
}));
jest.mock('../MarkSynced', () => ({ isMarkSyncedEnabled: jest.fn(async () => false) }));
jest.mock('../GearAutoAssign', () => ({ attributeMoveToGear: jest.fn() }));
jest.mock('../../database/db', () => ({
  isActivitySynced: jest.fn(async () => false),
  isActivityDeleted: jest.fn(async () => false),
  markActivitySynced: jest.fn(async (r: any) => { mockSaved.push(r); }),
  getAllSyncedIds: jest.fn(async () => []),
  clearDeletedActivities: jest.fn(),
  rejectJunkActivity: jest.fn(async () => false),
}));

const { runSync } = require('../SyncService') as typeof import('../SyncService');

function watch(extra: Record<string, any> = {}) {
  return {
    deviceName: 'test',
    connect: async () => ({ name: 'Suunto Ambit3 Peak', vendorId: 0x1493, productId: 0x1c }),
    disconnect: async () => {},
    getLogs: async () => ['<gpx/>'],
    onSyncProgress: () => () => {},
    setClock: async () => true,
    ...extra,
  };
}

beforeEach(() => { mockSaved.length = 0; });

test('the move keeps the watch calories and is saved under the watch serial', async () => {
  await runSync(() => {}, watch({ serial: async () => '8A153C5111000900' }));
  expect(mockSaved).toHaveLength(1);
  expect(mockSaved[0]).toMatchObject({ energy_kcal: 461, device: '8A153C5111000900' });
});

test('without a serial, the USB product id stands in, as on the desktop', async () => {
  await runSync(() => {}, watch());
  expect(mockSaved[0].device).toBe('0x1c');
});

test('the done state says the sync set the clock', async () => {
  const states: any[] = [];
  await runSync(s => states.push(s), watch());
  expect(states[states.length - 1]).toMatchObject({ phase: 'done', clockSet: true });
});

test('a clock the watch refused is reported as not set', async () => {
  const states: any[] = [];
  await runSync(s => states.push(s), watch({ setClock: async () => { throw new Error('TIME_SYNC_FAILED'); } }));
  expect(states[states.length - 1]).toMatchObject({ phase: 'done', clockSet: false });
});
