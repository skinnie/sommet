// Health tiles (desktop picks #12): the 7-day comparison and the "N days old" tag, same rules
// as desktop HealthPage.qml.
import { oldBadge, tileCaption, trend } from '../HealthTrend';

const pts = (pairs: Array<[string, number]>) => pairs.map(([date, value]) => ({ date, value }));

test('the latest value against the mean of the 7 days before it', () => {
  const s = pts([['2026-10-25', 50], ['2026-10-29', 52], ['2026-10-30', 54], ['2026-10-31', 50], ['2026-11-01', 56]]);
  // 10-25 is 7 days before 11-01 and counts; mean (50+52+54+50)/4 = 51.5 -> +4.5 -> "5" (toFixed(0))
  expect(trend(s, 'bpm', 0)).toBe('▲ 5 bpm vs 7-day average');
  expect(trend(pts([['2026-10-29', 60], ['2026-10-30', 60], ['2026-10-31', 60], ['2026-11-01', 57]]), 'ms')).toBe('▼ 3 ms vs 7-day average');
});

test('decimals and units follow the tile', () => {
  const s = pts([['2026-10-29', 7.0], ['2026-10-30', 7.5], ['2026-10-31', 8.0], ['2026-11-01', 6.9]]);
  expect(trend(s, 'h', 1)).toBe('▼ 0.6 h vs 7-day average');
  expect(trend(s, '', 1)).toBe('▼ 0.6 vs 7-day average');
});

test('equal to the average says so', () => {
  expect(trend(pts([['2026-10-29', 50], ['2026-10-30', 50], ['2026-10-31', 50], ['2026-11-01', 50.2]]), 'bpm', 0))
    .toBe('Same as the 7-day average');
});

test('fewer than 3 readings in the 7 days before: no trend, the date instead', () => {
  const s = pts([['2026-10-01', 50], ['2026-10-02', 50], ['2026-10-30', 50], ['2026-10-31', 52], ['2026-11-01', 60]]);
  expect(trend(s, 'bpm', 0)).toBe('');
  expect(tileCaption(s, 'bpm', 0)).toBe('01/11/2026');
  expect(trend(pts([['2026-10-30', 1], ['2026-10-31', 2], ['2026-11-01', 3]]), 'bpm')).toBe('');
});

test('readings more than 7 days before are left out', () => {
  const s = pts([['2026-10-20', 100], ['2026-10-29', 50], ['2026-10-30', 50], ['2026-10-31', 50], ['2026-11-01', 54]]);
  expect(trend(s, 'bpm', 0)).toBe('▲ 4 bpm vs 7-day average');
});

test('an old value says how old it is', () => {
  const now = new Date(2026, 10, 2, 15, 0, 0);
  expect(oldBadge(pts([['2026-10-28', 1]]), now)).toBe('5 days old');
  expect(oldBadge(pts([['2026-10-31', 1]]), now)).toBe('');       // 2 days: still fresh
  expect(oldBadge(pts([['2026-10-30', 1]]), now)).toBe('3 days old');
  expect(oldBadge([], now)).toBe('');
});
