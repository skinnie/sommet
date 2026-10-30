// BrytonEe.ts (desktop twin tools/bryton_ee.py): u-blox AlmanacPlus GPS prediction files and when
// Sommet replaces the Aero 60's System/AssitOff.alp + date.txt. Made-up files only.
import { decodeAlp, encodeDate, goodUntil, newDate, planEe, readDate } from '../BrytonEe';

const NOW = new Date(Date.UTC(2026, 9, 30, 9, 0, 0));
const DAY = 86400000;

function alp(startMs: number, days = 14, size = 1024): Uint8Array {
  const gps = (startMs - Date.UTC(1980, 0, 6)) / 1000 + 18;
  const week = Math.floor(gps / (7 * 86400)), tow = gps - week * 7 * 86400;
  const b = new Uint8Array(size), dv = new DataView(b.buffer);
  dv.setUint32(0, 0x015062b5, true);
  dv.setUint16(0x44, size / 4, true);
  dv.setUint32(0x4c, tow, true); dv.setUint16(0x50, week, true); dv.setUint16(0x52, days * 144, true);
  return b;
}

test('reads the span a file covers', () => {
  expect(decodeAlp(alp(NOW.getTime()))).toMatchObject({ ok: true, days: 14,
    start: '2026-10-30T09:00:00+00:00', end: '2026-11-13T09:00:00+00:00' });
});

test('rejects anything that is not intact u-blox ALP data', () => {
  expect(decodeAlp(new Uint8Array([0xb5, 0x62, 0x13, 0, ...new Array(100).fill(0)])).error).toMatch('not u-blox');
  expect(decodeAlp(alp(NOW.getTime()).slice(0, -4)).error).toMatch('header says');
  expect(decodeAlp(new Uint8Array(10)).error).toMatch('too short');
});

test('date.txt is 4 big-endian bytes and wins over the file end', () => {
  const until = NOW.getTime() + 2 * DAY;
  expect(readDate(encodeDate(until))).toBe(until);
  expect(Array.from(encodeDate(Date.UTC(2026, 9, 6, 22)))).toEqual([0x6a, 0xc5, 0x6f, 0x60]); // André's Aero 60
  const info = goodUntil(decodeAlp(alp(NOW.getTime())), until, NOW);
  expect(info).toMatchObject({ expired: false, validUntil: new Date(until).toISOString().replace('.000Z', '+00:00') });
  expect(goodUntil(decodeAlp(alp(NOW.getTime() - 37 * DAY)), null, NOW)).toMatchObject({ expired: true, daysLeft: 0 });
});

test('new date: local midnight 14 days on, never past the file end', () => {
  const d = new Date(newDate(decodeAlp(alp(NOW.getTime())), NOW));
  expect([d.getHours(), d.getMinutes(), d.getSeconds()]).toEqual([0, 0, 0]);
  expect(newDate(decodeAlp(alp(NOW.getTime(), 7)), NOW)).toBe(NOW.getTime() + 7 * DAY);
});

test('replaces an old file of the same kind, with a matching date', () => {
  const p = planEe(alp(NOW.getTime() - 37 * DAY, 14, 2048), encodeDate(NOW.getTime() - 24 * DAY), alp(NOW.getTime()), NOW);
  expect(p.write).toBe(true);
  if (p.write) expect(readDate(p.date)).toBe(newDate(decodeAlp(alp(NOW.getTime())), NOW));
});

test('leaves an unknown file, a missing file and a current file alone', () => {
  const next = alp(NOW.getTime());
  expect(planEe(new Uint8Array([0xb5, 0x62, 0x13, 0, ...new Array(100).fill(0)]), null, next, NOW))
    .toMatchObject({ write: false, error: expect.stringMatching('left alone') });
  expect(planEe(null, null, next, NOW)).toMatchObject({ write: false, error: expect.stringMatching('no GPS file') });
  const current = encodeDate(newDate(decodeAlp(next), NOW));
  expect(planEe(next, current, next, NOW)).toMatchObject({ write: false, note: expect.stringMatching('already') });
});

test('refuses a bad or out-of-date download', () => {
  const old = alp(NOW.getTime() - 37 * DAY);
  expect(planEe(old, null, new Uint8Array([60, 104, 116]), NOW).write).toBe(false);
  expect(planEe(old, null, alp(NOW.getTime() - 30 * DAY), NOW))
    .toMatchObject({ write: false, error: expect.stringMatching('out of date') });
});
