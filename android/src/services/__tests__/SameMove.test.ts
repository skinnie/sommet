import { sameMove, utcSeconds } from '../SameMove';
import cases from './sameMove.cases.json';

// The same cases tools/test_same_move.py runs against the desktop's C++ and the Python twin.
describe('sameMove (shared cases)', () => {
  for (const c of cases.cases) {
    it(c.name, () => {
      expect(sameMove(c.a, c.b)).toBe(c.same);
      expect(sameMove(c.b, c.a)).toBe(c.same);
    });
  }
});

describe('utcSeconds', () => {
  it('reads times that carry a zone', () => {
    expect(utcSeconds('2026-10-29T08:20:55Z')).toBe(Date.UTC(2026, 9, 29, 8, 20, 55) / 1000);
    expect(utcSeconds('2026-10-29T09:20:55+01:00')).toBe(Date.UTC(2026, 9, 29, 8, 20, 55) / 1000);
    expect(utcSeconds('2026-10-29T08:20:55.132Z')).toBe(Date.UTC(2026, 9, 29, 8, 20, 55) / 1000);
  });
  it('refuses a local time without a zone', () => {
    expect(utcSeconds('2026-10-29T09:20:55')).toBeNaN();
  });
});
