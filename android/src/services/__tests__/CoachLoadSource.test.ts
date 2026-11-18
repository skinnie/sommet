// sync/05: the training-load source (Settings -> Coach -> "Training load from Sommet") and the
// intervals.icu vs Sommet comparison.
const mockStore: Record<string, string> = {};
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(async (k: string) => (k in mockStore ? mockStore[k] : null)),
  setItem: jest.fn(async (k: string, v: string) => { mockStore[k] = v; }),
}));
jest.mock('react-native-fs', () => ({ readFile: jest.fn(async () => '') }));
const mockCreds = jest.fn(async (): Promise<{ athleteId: string; apiKey: string } | null> => ({ athleteId: 'i1', apiKey: 'not-a-real-key' }));
jest.mock('../ApiIntervalsIcu', () => ({ getIntervalsIcuCredentials: () => mockCreds() }));
const DAY = 86400 * 1000;
const mockMoves = () => {
  const now = Date.now();
  return [0, 2, 4, 6].map(d => ({
    id: `m${d}`, date: new Date(now - d * DAY).toISOString(), start_utc: Math.floor((now - d * DAY) / 1000),
    duration_s: 3600, activity_type: 'Running', distance_m: 10000, gpx_path: null, device: 'watch',
  }));
};
jest.mock('../../database/db', () => ({ getAllActivities: jest.fn(async () => mockMoves()) }));

import { compareSeries, getLoadSource, loadCoachData, setLoadSource } from '../CoachService';

const fetchMock = jest.fn();
(global as any).fetch = fetchMock;

beforeEach(() => {
  for (const k of Object.keys(mockStore)) delete mockStore[k];
  fetchMock.mockReset();
  mockCreds.mockClear();
});

describe('load source setting', () => {
  it('defaults to intervals.icu and remembers Sommet', async () => {
    expect(await getLoadSource()).toBe('intervals');
    await setLoadSource('sommet');
    expect(await getLoadSource()).toBe('sommet');
  });

  it('Sommet chosen: never asks intervals.icu, even when it is connected', async () => {
    await setLoadSource('sommet');
    const d = await loadCoachData(90);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(d.readiness?.basis).toBe('local');
    expect(d.readiness?.fallback).toBeFalsy();
  });

  it('intervals.icu chosen but not answering: local estimate, marked as a fallback', async () => {
    fetchMock.mockRejectedValue(new Error('offline'));
    const d = await loadCoachData(90);
    expect(fetchMock).toHaveBeenCalled();
    expect(d.readiness?.basis).toBe('local');
    expect(d.readiness?.fallback).toBe(true);
  });

  it('intervals.icu not connected: local estimate, not a fallback', async () => {
    mockCreds.mockResolvedValue(null);
    const d = await loadCoachData(90);
    expect(fetchMock).not.toHaveBeenCalled();
    expect(d.readiness?.basis).toBe('local');
    expect(d.readiness?.fallback).toBeFalsy();
  });
});

describe('compareSeries', () => {
  it('lines up days by date and averages the gaps', () => {
    const icu = [
      { date: '2026-11-01', ctl: 40, atl: 30 },
      { date: '2026-11-02', ctl: 42, atl: 50 },
      { date: '2026-11-03' },                       // no load that day: skipped
      { date: '2026-10-01', ctl: 10, atl: 10 },     // outside the local window: skipped
    ];
    const local = { days: ['2026-11-01', '2026-11-02', '2026-11-03'], ctl: [36, 44, 45], atl: [30, 40, 41] };
    const c = compareSeries(icu, local);
    expect(c.rows.map(r => r.date)).toEqual(['2026-11-01', '2026-11-02']);
    expect(c.fitnessGap).toBeCloseTo(3);            // |40-36|, |42-44|
    expect(c.fatigueGap).toBeCloseTo(5);            // 0, 10
    expect(c.formGap).toBeCloseTo((4 + 12) / 2);    // |10-6|, |-8-4|
    expect(c.fitnessBias).toBeCloseTo(1);           // (4 + -2) / 2
  });

  it('no days in common: empty rows, zero gaps', () => {
    const c = compareSeries([{ date: '2026-01-01', ctl: 1, atl: 1 }], { days: ['2026-11-01'], ctl: [1], atl: [1] });
    expect(c.rows).toEqual([]);
    expect(c.fitnessGap).toBe(0);
  });
});
