// The Training Program sync and an app install rewrite the watch's whole App Zone; Android did it
// with no copy (tablet audit F24, 2026-10-31). A copy of what was read is now saved first, and
// nothing is written when it can't be saved.
export {};

import { buildAppsRegion, decodeApps } from '../AppsCodec';
import { entryLabel } from '../TrainingCalendarCore';

const mockLog: string[] = [];
const mockSaved: { kind: string; serial: string; b64: string }[] = [];
let mockFlash = new Uint8Array(0);
let mockBackupFails = false;

function fakeAppsRegion(names: string[]): Uint8Array {
  let region: Uint8Array = new Uint8Array(0);
  let existing: Uint8Array[] = [];
  for (const name of names) {
    region = buildAppsRegion(existing, { binary: new Uint8Array([1, 2, 3]), activityId: 3, name }, 1);
    existing = decodeApps(region).map((e) => e.rawBlock);
  }
  return region;
}

jest.mock('../../native/AmbitUsbModule', () => {
  const { base64ToBytes, bytesToBase64 } = jest.requireActual('../Base64');
  return {
    isBleTransportActive: () => false,
    connect: jest.fn(async () => { mockLog.push('connect'); }),
    disconnect: jest.fn(async () => { mockLog.push('disconnect'); }),
    getDeviceInfo: jest.fn(async () => ({ model: 'Emu', serial: '8A153C5111000900' })),
    readRegion: jest.fn(async (_base: number, len: number) => {
      const out = new Uint8Array(len).fill(0xff);
      out.set(mockFlash.subarray(0, len));
      return bytesToBase64(out);
    }),
    writeRegion: jest.fn(async (_base: number, b64: string) => {
      mockLog.push('writeApps');
      const data = base64ToBytes(b64);
      const next = new Uint8Array(Math.max(mockFlash.length, data.length)).fill(0xff);
      next.set(mockFlash); next.set(data);
      mockFlash = next;
      return true;
    }),
    readCustomModesRaw: jest.fn(async () => ''),
    writeCustomModesRaw: jest.fn(async () => { mockLog.push('writeModes'); return true; }),
  };
});
jest.mock('../MemoryMap', () => ({ resolveRegion: jest.fn(async () => ({ base: 0x0927c0, size: 0x10000 })) }));
jest.mock('../SportModeCodec', () => ({ decode: jest.fn(() => ({ modes: [] })), encodeRegion: jest.fn() }));
jest.mock('../WatchBackup', () => ({
  saveWatchBackup: jest.fn(async (kind: string, b64: string, serial: string) => {
    if (mockBackupFails) throw new Error('disk full');
    mockLog.push(`backup:${kind}`);
    mockSaved.push({ kind, serial, b64 });
    return `/files/backups/${kind}/${serial}/${kind}_pre_x.bin`;
  }),
}));

const { syncCalendar } = require('../TrainingCalendar') as typeof import('../TrainingCalendar');
const { base64ToBytes } = jest.requireActual('../Base64') as typeof import('../Base64');

// André's Peak on 31/10: a workout he installed himself, and the two 10 s tests of 24/10.
const T10 = entryLabel('2026-10-24', 'T10s');
const B10 = entryLabel('2026-10-24', 'B10s');
const before = fakeAppsRegion(['Couch-to-5K', T10, B10]);
const plan = [
  { date: '2026-10-24', mode: 'Running', workoutName: 'T10s' },
  { date: '2026-10-24', mode: 'Running', workoutName: 'B10s' },
];
const today = new Date(2026, 9, 31);

beforeEach(() => {
  mockLog.length = 0; mockSaved.length = 0; mockBackupFails = false;
  mockFlash = before.slice();
});

test('the sync saves a copy of the workout list before it rewrites it', async () => {
  const states: string[] = [];
  const result = await syncCalendar(plan, today, true, (s) => states.push(s.phase));
  expect(result?.removed.sort()).toEqual([B10, T10].sort());
  expect(mockLog.indexOf('backup:Apps')).toBeGreaterThan(-1);
  expect(mockLog.indexOf('backup:Apps')).toBeLessThan(mockLog.indexOf('writeApps'));
  expect(mockSaved[0].serial).toBe('8A153C5111000900');
  expect(Array.from(base64ToBytes(mockSaved[0].b64))).toEqual(Array.from(before));
  expect(decodeApps(mockFlash).map((e) => e.name)).toEqual(['Couch-to-5K']);
  expect(states[states.length - 1]).toBe('done');
});

test('no copy, no write', async () => {
  mockBackupFails = true;
  const states: { phase: string; error?: string }[] = [];
  expect(await syncCalendar(plan, today, true, (s) => states.push(s))).toBeNull();
  expect(mockLog).not.toContain('writeApps');
  expect(states[states.length - 1]).toMatchObject({ phase: 'error', error: 'disk full' });
  expect(decodeApps(mockFlash).map((e) => e.name)).toEqual(['Couch-to-5K', T10, B10]);
});

test('a check without writing saves nothing', async () => {
  const result = await syncCalendar(plan, today, false, () => {});
  expect(result?.removed).toHaveLength(2);
  expect(mockLog).not.toContain('backup:Apps');
  expect(mockLog).not.toContain('writeApps');
});
