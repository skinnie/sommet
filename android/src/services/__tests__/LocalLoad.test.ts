import { banisterTrimp, computeLocalSeries, dedupeMoves, loadPerHour, TRIMP_TO_LOAD } from '../LocalLoad';

const P = { maxHr: 190, restHr: 50, male: true };

describe('banisterTrimp', () => {
  it('matches the published formula for a moderate hour', () => {
    // HRr = (134-50)/140 = 0.6 -> 60 * 0.6 * 0.64 * e^(1.152)
    expect(banisterTrimp(60, 134, P)).toBeCloseTo(60 * 0.6 * 0.64 * Math.exp(1.152), 6);
  });
  it('uses the female coefficients', () => {
    expect(banisterTrimp(60, 134, { ...P, male: false })).toBeCloseTo(60 * 0.6 * 0.86 * Math.exp(1.67 * 0.6), 6);
  });
  it('is 0 at or below resting HR and for a nonsense profile', () => {
    expect(banisterTrimp(60, 50, P)).toBe(0);
    expect(banisterTrimp(60, 150, { maxHr: 50, restHr: 60, male: true })).toBe(0);
  });
});

describe('dedupeMoves', () => {
  it('merges the watch copy (UTC) with its intervals.icu import (local, +2 h) - real 2026-08-31 pair', () => {
    const watch = { startMs: Date.parse('2026-08-31T19:29:42Z'), durationS: 1922, load: 60, hr: true };
    const icu = { startMs: Date.parse('2026-08-31T21:29:40Z'), durationS: 1921, load: 32, hr: false };
    const out = dedupeMoves([icu, watch]);
    expect(out).toHaveLength(1);
    expect(out[0]).toBe(watch);   // HR load wins
  });
  it('keeps two different moves of the same length on the same day', () => {
    const a = { startMs: Date.parse('2026-09-07T07:02:29Z'), durationS: 1343, load: 22, hr: false };
    const b = { startMs: Date.parse('2026-09-07T07:46:23Z'), durationS: 1343, load: 22, hr: false };
    expect(dedupeMoves([a, b])).toHaveLength(2);
  });
});

describe('computeLocalSeries', () => {
  const now = Date.parse('2026-09-27T10:00:00Z');
  it('ignores multi-day corrupt moves', () => {
    const s = computeLocalSeries([{ startMs: Date.parse('2026-06-21T08:00:00Z'), durationS: 59841 * 60 }], P, now);
    expect(Math.max(...s.ctl)).toBe(0);
  });
  it('counts HR vs duration moves and builds a rising CTL for daily training', () => {
    const moves = [];
    for (let i = 1; i <= 30; i++) {
      moves.push({ startMs: now - i * 86400000, durationS: 3600, avgHr: i % 2 ? 134 : 0 });
    }
    const s = computeLocalSeries(moves, P, now);
    expect(s.hrMoves).toBe(15);
    expect(s.durationMoves).toBe(15);
    expect(s.ctl[s.ctl.length - 1]).toBeGreaterThan(20);
    expect(s.days[s.days.length - 1]).toBe('2026-09-27');
  });
  it('falls back to duration for every move without a profile', () => {
    const s = computeLocalSeries([{ startMs: now - 86400000, durationS: 3600, avgHr: 150 }], null, now);
    expect(s.hrMoves).toBe(0);
    expect(s.durationMoves).toBe(1);
  });
});

describe('calibration to intervals.icu scale (2026-09-28)', () => {
  const now = Date.parse('2026-09-27T10:00:00Z');
  it('rates duration-only moves by sport', () => {
    expect(loadPerHour('Walking')).toBe(9);
    expect(loadPerHour('Hiking')).toBe(9);
    expect(loadPerHour('Cycling')).toBe(70);
    expect(loadPerHour('Indoor cycling')).toBe(55);
    expect(loadPerHour('Running')).toBe(40);
    expect(loadPerHour('Trail Running')).toBe(40);
    expect(loadPerHour('Pool swimming')).toBe(40);
    expect(loadPerHour('Yoga / pilates')).toBe(15);
    expect(loadPerHour(undefined)).toBe(30);
  });
  it('one 2 h ride yesterday = 140 load on the day (CTL gains 140/42-ish)', () => {
    const s = computeLocalSeries([{ startMs: now - 86400000, durationS: 7200, name: 'Cycling' }], null, now);
    const aCtl = 1 - Math.exp(-1 / 42);
    expect(s.ctl[s.ctl.length - 2]).toBeCloseTo(140 * aCtl, 6);
  });
  it('scales TRIMP by 0.8', () => {
    const P = { maxHr: 190, restHr: 50, male: true };
    const s = computeLocalSeries([{ startMs: now - 86400000, durationS: 3600, avgHr: 134, name: 'Cycling' }], P, now);
    const aCtl = 1 - Math.exp(-1 / 42);
    expect(s.ctl[s.ctl.length - 2]).toBeCloseTo(TRIMP_TO_LOAD * banisterTrimp(60, 134, P) * aCtl, 6);
  });
});
