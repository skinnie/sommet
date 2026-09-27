import { computeSleepScore, midSleepHour, midSleepSd, scoreDuration, scoreTiming } from '../SleepScore';

// Expected values worked by hand from Train Libre's documented formulas
// (documentation/features/sleep_scoring_engine.md) - the port must reproduce them.

describe('domains', () => {
  it('duration: plateau 7-9 h, Gaussian outside, 0 past 10.5 h', () => {
    expect(scoreDuration(8 * 60)).toBe(1);
    expect(scoreDuration(6 * 60)).toBeCloseTo(Math.exp(-0.5), 10);
    expect(scoreDuration(11 * 60)).toBe(0);
  });
  it('mid-sleep crosses midnight: 23:00 + 8 h -> 03:00', () => {
    expect(midSleepHour(23, 480)).toBeCloseTo(3, 10);
    expect(scoreTiming(23.5, 480)).toBeCloseTo(1, 10);   // mid-sleep exactly 03:30
  });
});

describe('computeSleepScore', () => {
  it('duration only (intervals.icu) - 5.47 h night, real 2026-08-25', () => {
    const h = 5.466666666666667, D = Math.exp(-((h - 7) ** 2) / 2);
    const C = 0.9 + 0.1 * D;
    const base = (0.3 * D + 0.2 * C) / 0.5;
    const mult = 0.5 + ((h - 5) / 1.5) * 0.5;
    const s = computeSleepScore({ durationMin: h * 60 })!;
    expect(s.score).toBeCloseTo(base * 100 * mult, 8);
    expect(s.bottleneck).toBe('tst');
    expect(s.completeness).toBeCloseTo(0.5, 10);
  });

  it('a full Garmin night scores near the top', () => {
    const s = computeSleepScore({
      durationMin: 480, efficiencyPct: 95, wasoMin: 15,
      lightPct: 55, deepPct: 20, remPct: 22, onsetHourLocal: 23.5,
      midSleepSdHours: 0.3, regularityDays: 7,
    })!;
    expect(s.score).toBeGreaterThan(90);
    expect(s.state).toBe('good');
    expect(s.completeness).toBeCloseTo(1, 10);
  });

  it('short deep sleep caps the score (deep multiplier)', () => {
    const s = computeSleepScore({ durationMin: 480, efficiencyPct: 95, wasoMin: 10, lightPct: 70, deepPct: 5, remPct: 25, onsetHourLocal: 23 })!;
    expect(s.bottleneck).toBe('n3');   // 24 min deep -> multiplier 0.60
  });

  it('no duration -> no score', () => {
    expect(computeSleepScore({ onsetHourLocal: 23 })).toBeNull();
  });
});

describe('midSleepSd', () => {
  it('handles the midnight wrap', () => {
    expect(midSleepSd([23.9, 0.1])).toBeCloseTo(0.1, 10);
    expect(midSleepSd([3])).toBeNull();
  });
});
