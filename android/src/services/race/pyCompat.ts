// Python behaviours the race engine's numbers depend on, so the TypeScript twins of tools/race_*.py
// give the SAME answers as the desktop (Race Plan on Android, André 2026-09-29: "everything in one
// go"). Parity-tested by tools/test_race_parity.js.
//
//  - pyRound: Python's round(x, n) - rounds the exact binary value, ties to EVEN (JS toFixed ties up).
//  - Naive datetimes: the engine works in the event's local wall clock with no time zone (start_dt is
//    "local-naive"), to the microsecond like Python's datetime. Here a time is a number of
//    MICROSECONDS since 1970-01-01T00:00 of that wall clock (read with UTC getters, so no device time
//    zone or DST ever shifts it). Durations added the way timedelta(seconds=x) does: rounded to the
//    microsecond, ties to even.

export function pyRound(x: number, n = 0): number {
  if (!isFinite(x)) return x;
  const digits = Math.min(100, n + 60);
  const exact = Math.abs(x).toFixed(digits);          // exact decimal expansion of the binary value
  const dot = exact.indexOf('.');
  const tail = exact.slice(dot + 1 + n);
  const isTie = tail[0] === '5' && /^5?0*$/.test(tail);
  if (!isTie) {
    const r = Number(x.toFixed(n));
    return r === 0 ? 0 * Math.sign(x) || 0 : r;
  }
  // exact tie: keep the even neighbour
  const kept = Number(Math.abs(x).toFixed(digits).slice(0, dot + 1 + n).replace(/\.$/, ''));
  const lastDigit = n > 0 ? Number(exact[dot + n]) : Number(exact[dot - 1]);
  const step = Math.pow(10, -n);
  const mag = lastDigit % 2 === 0 ? kept : kept + step;
  return Number((Math.sign(x) * mag).toFixed(n));
}

/** Python round(x) with no digits -> int, ties to even. */
export function pyRoundInt(x: number): number {
  return pyRound(x, 0);
}

export function roundHalfEven(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

export function median(xs: number[]): number {
  const s = xs.slice().sort((a, b) => a - b);
  const n = s.length;
  if (!n) throw new Error('median of empty list');
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}

// ---- naive local datetimes (microseconds) -------------------------------------------------------

export type NDT = number;   // microseconds since 1970-01-01T00:00:00 (wall clock, no zone)

const US = 1e6;

/** datetime.fromisoformat for the shapes the engine sees: 'YYYY-MM-DD', 'YYYY-MM-DDTHH:MM',
 *  'YYYY-MM-DD HH:MM:SS(.ffffff)'. A trailing zone (Z / +02:00) is ignored - the engine is naive. */
export function parseIso(s: string): NDT {
  const m = /^(\d{4})-(\d{2})-(\d{2})(?:[T ](\d{2}):(\d{2})(?::(\d{2})(?:\.(\d{1,6}))?)?)?/.exec(s.trim());
  if (!m) throw new Error(`Invalid isoformat string: '${s}'`);
  const ms = Date.UTC(+m[1], +m[2] - 1, +m[3], +(m[4] || 0), +(m[5] || 0), +(m[6] || 0));
  const frac = m[7] ? Number((m[7] + '000000').slice(0, 6)) : 0;
  return ms * 1000 + frac;
}

const pad = (v: number, w = 2) => String(v).padStart(w, '0');

/** datetime.isoformat(): 'YYYY-MM-DDTHH:MM:SS', plus '.ffffff' only when there are microseconds. */
export function iso(t: NDT): string {
  const ms = Math.floor(t / 1000);
  const us = t - ms * 1000;
  const d = new Date(ms);
  const micro = (d.getUTCMilliseconds() * 1000 + us);
  const base = `${pad(d.getUTCFullYear(), 4)}-${pad(d.getUTCMonth() + 1)}-${pad(d.getUTCDate())}T` +
               `${pad(d.getUTCHours())}:${pad(d.getUTCMinutes())}:${pad(d.getUTCSeconds())}`;
  return micro ? `${base}.${pad(micro, 6)}` : base;
}

/** dt + timedelta(seconds=s) */
export function addSeconds(t: NDT, s: number): NDT {
  return t + roundHalfEven(s * US);
}

/** (a - b).total_seconds() */
export function diffSeconds(a: NDT, b: NDT): number {
  return (a - b) / US;
}

export function fields(t: NDT) {
  const ms = Math.floor(t / 1000);
  const d = new Date(ms);
  return { year: d.getUTCFullYear(), month: d.getUTCMonth() + 1, day: d.getUTCDate(),
           hour: d.getUTCHours(), minute: d.getUTCMinutes(), second: d.getUTCSeconds(),
           microsecond: d.getUTCMilliseconds() * 1000 + (t - ms * 1000),
           weekday: (d.getUTCDay() + 6) % 7 };           // Python: Monday = 0
}

/** dt.hour + dt.minute/60 + dt.second/3600 (the engine's clock-hour, microseconds ignored like Python). */
export function hourOf(t: NDT): number {
  const f = fields(t);
  return f.hour + f.minute / 60.0 + f.second / 3600.0;
}

/** dt.replace(hour=, minute=, second=0, microsecond=0) */
export function replaceHM(t: NDT, hour: number, minute: number): NDT {
  const f = fields(t);
  return Date.UTC(f.year, f.month - 1, f.day, hour, minute, 0) * 1000;
}

/** datetime(y, m, d, h, mi) */
export function ndt(y: number, mo: number, d: number, h = 0, mi = 0, s = 0): NDT {
  return Date.UTC(y, mo - 1, d, h, mi, s) * 1000;
}

/** dt.strftime('%H:%M') */
export function hhmm(t: NDT): string {
  const f = fields(t);
  return `${pad(f.hour)}:${pad(f.minute)}`;
}

/** Python '%.<n>f' % x (and f'{x:.<n>f}'): round-half-even on the exact value, and '-0' kept. */
export function pyFixed(x: number, n = 0): string {
  const r = pyRound(x, n);
  const s = Math.abs(r).toFixed(n);
  const neg = x < 0 || Object.is(r, -0);
  return (neg && (r !== 0 || x < 0) ? '-' : '') + s;
}

/** Python '%+.<n>f' */
export function pySignedFixed(x: number, n = 0): string {
  const s = pyFixed(x, n);
  return s.startsWith('-') ? s : '+' + s;
}

/** datetime.fromisoformat(str(s)[:19]) - the seconds-precision parse race_days uses. */
export function parseIso19(s: string): NDT {
  return parseIso(String(s).slice(0, 19));
}
