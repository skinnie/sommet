const store: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(async (k: string) => store[k] ?? null),
  setItem: jest.fn(async (k: string, v: string) => { store[k] = v; }),
}));
jest.mock('../../native/AmbitUsbModule', () => ({ getDeviceInfo: jest.fn(), readObjectRaw: jest.fn() }));
import { bytesToBase64 } from '../Base64';
import {
  decodeWatchSteps, loadWatchActivity, loadWatchRecovery, loadWatchSteps, mergeWatchActivity,
  mergeWatchSteps, weekActivity, weekSteps,
} from '../AmbitSteps';

// Same shape as tools/test_ambit_steps.py: a DeviceHistory reply as a real Ambit3 Peak sends it
// (fw 2.4.17, 2026-10-18) - short-id ActivityTracking entries, then the 30-day steps record and
// the timestamp as long-id entries [ff][u16 id][len]. The numbers are made up.
function entry(id: number, data: number[]): number[] {
  const head = id < 0xff ? [id] : [0xff, id & 0xff, id >> 8];
  return [...head, data.length, ...data];
}
function u32s(values: number[]): number[] {
  return values.flatMap(v => [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff]);
}
function reply(steps: number[], stamp = '2026-10-18T13:06:02+03:00', cursor = 0): string {
  const magic = Array.from('SBEM0102').map(c => c.charCodeAt(0));
  const bytes = [
    ...u32s([cursor]), 1, 0, ...magic,
    ...entry(0x92, [77, 0, 76, 0]),
    ...entry(0x94, [0x75, 0x0b, 0xa8, 0x02]),            // 2933, 680 kcal
    ...entry(0x12e, u32s(steps)),
    ...entry(0x130, [...Array.from(stamp).map(c => c.charCodeAt(0)), 0]),
  ];
  return bytesToBase64(new Uint8Array(bytes));
}

const counts = [...Array(26).fill(0xffffffff), 17194, 6759, 5798, 5];

describe('decodeWatchSteps', () => {
  it('dates the 30 slots back from the timestamp, oldest first, skipping no-count days', () => {
    const r = decodeWatchSteps(reply(counts))!;
    expect(r.days).toEqual([
      { date: '2026-10-15', steps: 17194, today: false },
      { date: '2026-10-16', steps: 6759, today: false },
      { date: '2026-10-17', steps: 5798, today: false },
      { date: '2026-10-18', steps: 5, today: true },
    ]);
  });
  it('crosses a month boundary', () => {
    const r = decodeWatchSteps(reply([...Array(29).fill(0xffffffff), 100].map((v, i) => (i === 0 ? 1 : v)), '2026-03-01T08:00:00+03:00'))!;
    expect(r.days[0]).toEqual({ date: '2026-01-31', steps: 1, today: false });
    expect(r.days[1]).toEqual({ date: '2026-03-01', steps: 100, today: true });
  });
  it('refuses another layout or an unfinished page instead of guessing', () => {
    expect(decodeWatchSteps(reply(counts.slice(0, 16)))).toBeNull();
    expect(decodeWatchSteps(reply(counts, 'not a date'))).toBeNull();
    expect(decodeWatchSteps(reply(counts, undefined, 0x18))).toBeNull();
  });
});

describe('activity and recovery', () => {
  it('dates the weekly kcal back from today and puts recovery on the last quarter hour', () => {
    const r = decodeWatchSteps(reply(counts))!;
    expect(r.activity).toEqual([
      { date: '2026-10-17', kcal: 2933, today: false },
      { date: '2026-10-18', kcal: 680, today: true },
    ]);
    // 76 and 77 units of 22.5 min; 13:06 -> the 13:00 sample
    expect(r.recovery).toEqual({ time: '2026-10-18T13:00', hours: 28.5, peak: 28.88, spanHours: 0.5 });
  });
  it('stores the days and the latest recovery', async () => {
    await mergeWatchActivity(decodeWatchSteps(reply(counts))!);
    expect(await loadWatchActivity()).toEqual([
      { date: '2026-10-17', kcal: 2933 }, { date: '2026-10-18', kcal: 680 }]);
    expect((await loadWatchRecovery())!.hours).toBe(28.5);
  });
});

describe('mergeWatchSteps', () => {
  it('keeps old days and lets a newer read replace a day', async () => {
    await mergeWatchSteps([{ date: '2026-09-01', steps: 9000, today: false }]);
    await mergeWatchSteps([{ date: '2026-10-18', steps: 5, today: true }]);
    await mergeWatchSteps([{ date: '2026-10-18', steps: 4200, today: true }]);
    expect(await loadWatchSteps()).toEqual([
      { date: '2026-09-01', steps: 9000 }, { date: '2026-10-18', steps: 4200 }]);
  });
});

describe('several watches', () => {
  beforeEach(() => { for (const k of Object.keys(store)) delete store[k]; });
  const day = (date: string, steps: number) => ({ date, steps, today: false });

  it('a watch left on the desk does not erase the one that was worn', async () => {
    await mergeWatchSteps([day('2026-10-20', 9000), day('2026-10-21', 0)], 'PEAK');
    await mergeWatchSteps([day('2026-10-20', 0), day('2026-10-21', 7000), day('2026-10-22', 300)], 'SPORT');
    expect(await loadWatchSteps()).toEqual([
      { date: '2026-10-20', steps: 9000 }, { date: '2026-10-21', steps: 7000 },
      { date: '2026-10-22', steps: 300 }]);
    // a newer read of the same watch still replaces its own day
    await mergeWatchSteps([day('2026-10-22', 4100)], 'SPORT');
    expect((await loadWatchSteps()).pop()).toEqual({ date: '2026-10-22', steps: 4100 });
  });

  it('keeps the days stored before watches were told apart', async () => {
    store['health.watchSteps'] = JSON.stringify([{ date: '2026-09-01', steps: 12000 }]);
    await mergeWatchSteps([day('2026-09-01', 0), day('2026-10-18', 50)], 'PEAK');
    expect(await loadWatchSteps()).toEqual([
      { date: '2026-09-01', steps: 12000 }, { date: '2026-10-18', steps: 50 }]);
  });

  it('shows the recovery of the watch that was worn', async () => {
    const worn = decodeWatchSteps(reply(counts))!;                   // 2026-10-18, 5 steps, 28.5 h
    const desk = { ...worn, days: [{ date: '2026-10-18', steps: 0, today: true }],
                   recovery: { time: '2026-10-18T14:00', hours: 0, peak: 0, spanHours: 24 } };
    await mergeWatchActivity(worn, 'PEAK', await mergeWatchSteps(worn.days, 'PEAK'));
    await mergeWatchActivity(desk, 'SPORT', await mergeWatchSteps(desk.days, 'SPORT'));
    expect((await loadWatchRecovery())!.hours).toBe(28.5);
    expect(await loadWatchActivity()).toEqual([
      { date: '2026-10-17', kcal: 2933 }, { date: '2026-10-18', kcal: 680 }]);
  });
});

describe('weekActivity', () => {
  const kcal = [
    { date: '2026-10-18', kcal: 500 },    // Sunday, last week
    { date: '2026-10-19', kcal: 300 },    // Monday
    { date: '2026-10-21', kcal: 250 },
    { date: '2026-10-22' },               // a day with steps but no kcal
    { date: '2026-10-26', kcal: 999 },    // next Monday
  ];
  it('adds Monday to today', () => {
    expect(weekActivity(kcal, new Date(2026, 9, 22, 18, 0))).toEqual(
      { kcal: 550, days: 2, from: '2026-10-19' });
  });
  it('starts again on Monday and counts a Sunday in its own week', () => {
    expect(weekActivity(kcal, new Date(2026, 9, 26, 7, 0))).toEqual(
      { kcal: 999, days: 1, from: '2026-10-26' });
    expect(weekActivity(kcal, new Date(2026, 9, 25, 23, 0))).toEqual(
      { kcal: 550, days: 2, from: '2026-10-19' });
  });
});

describe('weekSteps', () => {
  it('lays out Monday to Sunday, leaving days to come empty', () => {
    const w = weekSteps([
      { date: '2026-10-18', steps: 99999 },   // last week
      { date: '2026-10-19', steps: 8000 },
      { date: '2026-10-21', steps: 12000 },
      { date: '2026-10-23', steps: 5000 },    // after "today": not counted yet
    ], new Date(2026, 9, 22, 12, 0));
    expect(w.days.map(d => [d.label, d.steps, d.today])).toEqual([
      ['Mon', 8000, false], ['Tue', -1, false], ['Wed', 12000, false], ['Thu', -1, true],
      ['Fri', -1, false], ['Sat', -1, false], ['Sun', -1, false]]);
    expect([w.total, w.max, w.days[0].date, w.days[6].date])
      .toEqual([20000, 12000, '2026-10-19', '2026-10-25']);
  });
});

// watch-protocol/10 batch B (2026-11-26): the Vertical and the Traverse keep the same 30-day
// record under their own ids (their descriptors; tools/test_ambit_steps.py checks the table).
describe('steps ids per model', () => {
  const { stepsIdsFor, STEPS_IDS } = require('../AmbitSteps');
  function replyWith(ids: any, steps: number[], rings: boolean): string {
    const magic = Array.from('SBEM0102').map(c => c.charCodeAt(0));
    const stamp = '2026-11-26T09:20:00+03:00';
    const bytes = [
      ...u32s([0]), 1, 0, ...magic,
      ...(rings ? [...entry(ids.recovery, [40, 0, 41, 0]), ...entry(ids.weekly, [0x84, 0x03, 0x78, 0x00])] : []),
      ...entry(ids.steps, u32s(steps)),
      ...entry(ids.timestamp, [...Array.from(stamp).map(c => c.charCodeAt(0)), 0]),
    ];
    return bytesToBase64(new Uint8Array(bytes));
  }
  const twoDays = [...Array(28).fill(0xffffffff), 9100, 412];

  it('picks each model by codename or name; none for the Kailash and Ambit1/2', () => {
    expect(stepsIdsFor('Emu Suunto Ambit3 Peak')).toBe(STEPS_IDS.emu);
    expect(stepsIdsFor('Kaka Suunto Ambit3 Vertical')).toBe(STEPS_IDS.kaka);
    expect(stepsIdsFor('Loon Suunto Traverse Alpha')).toBe(STEPS_IDS.loon);
    expect(stepsIdsFor('Suunto Traverse')).toBe(STEPS_IDS.jabiru);
    expect(stepsIdsFor('Hoopoe Suunto Kailash')).toBeNull();
    expect(stepsIdsFor('Duck Suunto Ambit2')).toBeNull();
    expect(stepsIdsFor('Bluebird Suunto Ambit')).toBeNull();
  });

  it('decodes a Vertical reply with its own ids', () => {
    const r = decodeWatchSteps(replyWith(STEPS_IDS.kaka, twoDays, true), STEPS_IDS.kaka)!;
    expect(r.days.map((d: any) => [d.date, d.steps])).toEqual([['2026-11-25', 9100], ['2026-11-26', 412]]);
    expect(r.activity[r.activity.length - 1].kcal).toBe(120);
    expect(r.recovery).not.toBeNull();
  });

  it('decodes a Traverse reply: days, no Activity display, no recovery', () => {
    const r = decodeWatchSteps(replyWith(STEPS_IDS.jabiru, twoDays, false), STEPS_IDS.jabiru)!;
    expect(r.days.map((d: any) => [d.date, d.steps])).toEqual([['2026-11-25', 9100], ['2026-11-26', 412]]);
    expect(r.activity).toEqual([]);
    expect(r.recovery).toBeNull();
  });

  it('refuses a Vertical reply read with the Peak ids (no wrong numbers)', () => {
    expect(decodeWatchSteps(replyWith(STEPS_IDS.kaka, twoDays, true))).toBeNull();
  });
});
