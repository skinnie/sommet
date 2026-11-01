// The tablet and the PC keep separate plans. On 31/10 the tablet's Sync to Watch erased the PC's
// "Sat 31/10 W9 Final Workout" on its day, because it was not in the tablet's plan. Each now
// erases only past workouts and its own (CalendarRecord), and leaves the other's upcoming ones.
export {};

import { buildAppsRegion, decodeApps } from '../AppsCodec';
import { entryLabel } from '../TrainingCalendarCore';

const mockFiles = new Map<string, string>();
let mockFlash: Uint8Array = new Uint8Array(0);
let mockSerial: string | null = '8A153C5111000900';

function fakeAppsRegion(names: string[]): Uint8Array {
  let region: Uint8Array = new Uint8Array(0);
  let existing: Uint8Array[] = [];
  for (const name of names) {
    region = buildAppsRegion(existing, { binary: new Uint8Array([1, 2, 3]), activityId: 3, name }, 1);
    existing = decodeApps(region).map((e) => e.rawBlock);
  }
  return region;
}

jest.mock('react-native-fs', () => ({
  DocumentDirectoryPath: '/files',
  mkdir: jest.fn(async () => {}),
  readFile: jest.fn(async (p: string) => {
    if (!mockFiles.has(p)) throw new Error('ENOENT');
    return mockFiles.get(p);
  }),
  writeFile: jest.fn(async (p: string, d: string) => { mockFiles.set(p, d); }),
}));
jest.mock('../../native/AmbitUsbModule', () => {
  const { base64ToBytes, bytesToBase64 } = jest.requireActual('../Base64');
  return {
    isBleTransportActive: () => false,
    connect: jest.fn(async () => {}),
    disconnect: jest.fn(async () => {}),
    getDeviceInfo: jest.fn(async () => {
      if (mockSerial == null) throw new Error('no answer');
      return { model: 'Emu', serial: mockSerial };
    }),
    readRegion: jest.fn(async (_base: number, len: number) => {
      const out = new Uint8Array(len).fill(0xff);
      out.set(mockFlash.subarray(0, len));
      return bytesToBase64(out);
    }),
    writeRegion: jest.fn(async (_base: number, b64: string) => {
      const data = base64ToBytes(b64);
      const next = new Uint8Array(Math.max(mockFlash.length, data.length)).fill(0xff);
      next.set(mockFlash); next.set(data);
      mockFlash = next;
      return true;
    }),
    readCustomModesRaw: jest.fn(async () => ''),
    writeCustomModesRaw: jest.fn(async () => true),
  };
});
jest.mock('../MemoryMap', () => ({ resolveRegion: jest.fn(async () => ({ base: 0x0927c0, size: 0x10000 })) }));
jest.mock('../SportModeCodec', () => ({ decode: jest.fn(() => ({ modes: [] })), encodeRegion: jest.fn() }));
jest.mock('../WatchBackup', () => ({ saveWatchBackup: jest.fn(async () => '/files/backups/x.bin') }));
jest.mock('../GuidedWorkoutCore', () => ({
  ...jest.requireActual('../GuidedWorkoutCore'),
  modeActivityId: () => 3,
  findModeIndex: () => 0,
  ensureGuidanceDisplay: (decoded: unknown) => ({ decoded, added: false }),
}));

const { syncCalendar } = require('../TrainingCalendar') as typeof import('../TrainingCalendar');
const { recordPath } = require('../CalendarRecord') as typeof import('../CalendarRecord');

// A compiled workout the way the compiler hands it over: version words, then one "rule0" symbol.
const compiled = {
  binary: new Uint8Array([8, 0, 0, 0, 1, 0, 0, 0, 1, 0, 0, 0, 0, 0, 0, 0, 7, 7,
    0x72, 0x75, 0x6c, 0x65, 0x30, 0]),
  activityId: 3,
};
const onWatch = () => decodeApps(mockFlash).map((e) => e.name);
const record = () => JSON.parse(mockFiles.get(recordPath('8A153C5111000900')) ?? 'null');
const sync = (plan: any[], today: Date) => syncCalendar(plan, today, true, () => {});

const W9 = entryLabel('2026-10-31', 'W9 Final Workout');   // put there by the PC

beforeEach(() => {
  mockFiles.clear();
  mockSerial = '8A153C5111000900';
});

test("31/10 again: the PC's workout of the day stays when the tablet syncs", async () => {
  mockFlash = fakeAppsRegion(['Couch-to-5K', W9]);
  const tabletPlan = [
    { date: '2026-10-24', mode: 'Running', workoutName: 'T10s' },
    { date: '2026-10-24', mode: 'Cycling', workoutName: 'B10s' },
  ];
  const r = await sync(tabletPlan, new Date(2026, 9, 31));
  expect(r).toMatchObject({ removed: [], othersKept: [W9] });
  expect(onWatch()).toEqual(['Couch-to-5K', W9]);
  expect(record()).toEqual([]);                               // not the tablet's
  // The next day it is past, and either device erases it.
  const next = await sync(tabletPlan, new Date(2026, 10, 1));
  expect(next).toMatchObject({ removed: [W9], othersKept: [] });
  expect(onWatch()).toEqual(['Couch-to-5K']);
});

test("the tablet erases its own workout taken out of its plan, never the PC's", async () => {
  const pcs = entryLabel('2026-11-04', 'PC tempo');
  mockFlash = fakeAppsRegion([pcs]);
  const own = entryLabel('2026-11-02', 'Tablet run');
  const today = new Date(2026, 10, 1);
  const r = await sync([{ date: '2026-11-02', mode: 'Running', workoutName: 'Tablet run', compiled }], today);
  expect(r).toMatchObject({ added: [own], removed: [], othersKept: [pcs] });
  expect(onWatch().sort()).toEqual([own, pcs].sort());
  expect(record()).toEqual([own]);

  const after = await sync([], today);
  expect(after).toMatchObject({ removed: [own], othersKept: [pcs] });
  expect(onWatch()).toEqual([pcs]);
  expect(record()).toEqual([]);
});

test("another watch's record is not this one's", async () => {
  const own = entryLabel('2026-11-02', 'Tablet run');
  mockFiles.set(recordPath('OTHERWATCH'), JSON.stringify([own]));
  mockFlash = fakeAppsRegion([own]);
  const r = await sync([], new Date(2026, 10, 1));
  expect(r).toMatchObject({ removed: [], othersKept: [own] });
  expect(onWatch()).toEqual([own]);
});

test('without the serial nothing is recorded and nothing upcoming outside the plan goes', async () => {
  mockSerial = null;
  const pcs = entryLabel('2026-11-04', 'PC tempo');
  mockFlash = fakeAppsRegion([pcs]);
  const r = await sync([], new Date(2026, 10, 1));
  expect(r).toMatchObject({ removed: [], othersKept: [pcs] });
  expect(mockFiles.size).toBe(0);
});
