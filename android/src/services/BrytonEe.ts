// Bryton Aero 60 GPS prediction data (System/AssitOff.alp + System/date.txt) - pure TS twin of the
// desktop tools/bryton_ee.py (same rules, same tests' cases). The device holds u-blox's 14-day
// AlmanacPlus file (https://alp.u-blox.com/current_14d.alp, no account) and date.txt = when it
// stops being good (4 bytes, big-endian Unix time). Bryton's own USB tool writes date.txt as
// midnight (local) 14 days after today; Sommet does the same, never past the file's own end, and
// only over a file of the same kind: anything else is left alone.

export const EE_URL = 'https://alp.u-blox.com/current_14d.alp';
export const EE_PATH = 'System/AssitOff.alp';
export const EE_DATE_PATH = 'System/date.txt';
const DAYS = 14;
const ALP_MAGIC = 0x015062b5;
const GPS_EPOCH_MS = Date.UTC(1980, 0, 6);
const LEAP_SECONDS = 18;

export interface EeInfo {
  ok: boolean; error?: string; bytes?: number; start?: string; end?: string; days?: number;
  validUntil?: string; last?: string; daysLeft?: number; expired?: boolean; dateFile?: string | null;
}

const isoDay = (d: Date) =>
  `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
const isoUtc = (ms: number) => new Date(ms).toISOString().replace('.000Z', '+00:00');

/** Checks a u-blox ALP file: magic, the length its header states, and the span it covers. */
export function decodeAlp(data: Uint8Array): EeInfo {
  if (data.length < 0x54) return { ok: false, error: 'too short to be u-blox GPS data' };
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  if (dv.getUint32(0, true) !== ALP_MAGIC) return { ok: false, error: 'not u-blox AlmanacPlus data' };
  const words = dv.getUint16(0x44, true);
  if (words * 4 !== data.length) {
    return { ok: false, error: `file is ${data.length} bytes but its header says ${words * 4}` };
  }
  const tow = dv.getUint32(0x4c, true), week = dv.getUint16(0x50, true), span = dv.getUint16(0x52, true);
  if (tow >= 7 * 86400 || span <= 0 || span > 6 * 1008 || week < 1000) {
    return { ok: false, error: 'header times make no sense' };
  }
  const start = GPS_EPOCH_MS + (week * 7 * 86400 + tow - LEAP_SECONDS) * 1000;
  const end = start + span * 600 * 1000;
  return { ok: true, bytes: data.length, start: isoUtc(start), end: isoUtc(end),
    days: Math.round(span / 14.4) / 10 };
}

/** date.txt -> ms since epoch, or null when it isn't the 4-byte form. */
export function readDate(data: Uint8Array | null): number | null {
  if (!data || data.length !== 4) return null;
  return new DataView(data.buffer, data.byteOffset, 4).getUint32(0, false) * 1000;
}

export function encodeDate(ms: number): Uint8Array {
  const out = new Uint8Array(4);
  new DataView(out.buffer).setUint32(0, Math.floor(ms / 1000), false);
  return out;
}

/** validUntil / last (last whole local day) / daysLeft / expired. date.txt wins when present. */
export function goodUntil(info: EeInfo, dateMs: number | null, now = new Date()): EeInfo {
  if (!info.ok) return info;
  const until = dateMs ?? (info.end ? Date.parse(info.end) : NaN);
  if (Number.isNaN(until)) return info;
  const last = new Date(until - 1000);
  const lastDay = new Date(last.getFullYear(), last.getMonth(), last.getDate());
  const today = new Date(now.getFullYear(), now.getMonth(), now.getDate());
  const left = Math.round((lastDay.getTime() - today.getTime()) / 86400000);
  return { ...info, validUntil: isoUtc(until), last: isoDay(last), daysLeft: Math.max(left, 0),
    expired: now.getTime() >= until, dateFile: dateMs == null ? null : isoUtc(dateMs) };
}

/** Bryton's rule: midnight (local) DAYS days after today, capped at the file's own end. */
export function newDate(info: EeInfo, now = new Date()): number {
  const midnight = new Date(now.getFullYear(), now.getMonth(), now.getDate() + DAYS).getTime();
  return Math.min(midnight, Date.parse(info.end!));
}

export type EePlan =
  | { write: true; old: EeInfo; next: EeInfo; date: Uint8Array }
  | { write: false; old?: EeInfo; next?: EeInfo; note?: string; error?: string };

/** Whether to replace the device's files (`old`/`oldDate`, null when absent) with `next`. */
export function planEe(old: Uint8Array | null, oldDate: Uint8Array | null, next: Uint8Array,
                       now = new Date()): EePlan {
  const n = decodeAlp(next);
  if (!n.ok) return { write: false, error: `the downloaded file is not usable: ${n.error}` };
  if (Date.parse(n.end!) <= now.getTime()) {
    return { write: false, next: n, error: 'the downloaded file is already out of date' };
  }
  if (!old) return { write: false, next: n, error: 'the device has no GPS file yet, so Sommet doesn\'t add one' };
  const o0 = decodeAlp(old);
  if (!o0.ok) return { write: false, old: o0, next: n, error: 'the device\'s GPS file is in a format Sommet doesn\'t know, so it was left alone' };
  const oldUntil = readDate(oldDate);
  const o = goodUntil(o0, oldUntil, now);
  const until = newDate(n, now);
  const nx = goodUntil(n, until, now);
  if (oldUntil != null && oldUntil >= until) {
    return { write: false, old: o, next: nx, note: 'the device already has today\'s GPS data' };
  }
  return { write: true, old: o, next: nx, date: encodeDate(until) };
}
