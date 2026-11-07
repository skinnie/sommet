// "Is this the same move?" - the phone's twin of desktop/src/services/samemove.h (and
// tools/same_move.py): ONE rule for the training load, bike-computer imports and the intervals.icu
// import. Same reasons as there: starts compared as UTC instants, never through the phone's time
// zone (intervals.icu's start_date, not start_date_local), plus a copy EXACTLY whole hours away
// (to 5 s) with a closely matching distance/duration - a clock stored in the wrong zone (pool
// swims without GPS, Tacx uploads). Durations disagree (pauses), distance agrees best. Two different watches on one ride are two recordings. Shared cases:
// __tests__/sameMove.cases.json. Change all three together.

export interface SameMoveRow {
  start: number;        // epoch seconds, UTC; < 0 or NaN = unknown
  durationS: number;    // 0 = unknown
  distanceM: number;    // 0 = unknown / indoor
  watch?: boolean;      // read straight off a watch
  device?: string;      // which watch, for watch rows
}

export function sameMove(a: SameMoveRow, b: SameMoveRow): boolean {
  if (!(a.start >= 0) || !(b.start >= 0)) return false;
  const gap = Math.abs(a.start - b.start);
  let shifted = false;
  if (gap > 180) {
    const h = Math.round(gap / 3600);
    if (h < 1 || h > 14 || Math.abs(gap - h * 3600) > 5) return false;  // not the same start
    shifted = true;                                                    // a clock in the wrong zone
  }
  if (a.watch && b.watch && a.device && b.device && a.device !== b.device) return false;  // two watches

  if (a.distanceM > 100 && b.distanceM > 100) {
    const diff = Math.abs(a.distanceM - b.distanceM);
    return diff <= 50 || diff / Math.max(a.distanceM, b.distanceM) <= (shifted ? 0.02 : 0.05);
  }
  if (shifted && (a.distanceM > 100 || b.distanceM > 100)) return false;  // distance on one side only
  if (a.durationS > 0 && b.durationS > 0) {
    const r = a.durationS / b.durationS;
    return shifted ? r >= 0.97 && r <= 1.03 : r >= 0.66 && r <= 1.5;
  }
  return !shifted;                                                     // only the start is known
}

/** Epoch seconds (UTC) of an ISO time that carries its zone ("...Z" / "+01:00"); NaN otherwise.
 *  A time without a zone is local to wherever the move was recorded, so it can't be compared. */
export function utcSeconds(iso: string | undefined | null): number {
  const s = String(iso ?? '');
  if (!/(Z|[+-]\d\d:?\d\d)$/.test(s)) return NaN;
  const t = Date.parse(s);
  return isNaN(t) ? NaN : Math.floor(t / 1000);
}

/** A stored activity's UTC start: start_utc when the import kept it (intervals.icu rows, whose
 *  date is a local time without a zone), else the date itself when it carries a zone (watch GPX,
 *  FIT). A zone-less date with no start_utc yet (an intervals.icu row from before 2026-11-07,
 *  until the next full import fills it) is read as this device's local time - the old behaviour. */
export function startUtcOf(a: { date: string; start_utc?: number }): number {
  if (a.start_utc && a.start_utc > 0) return a.start_utc;
  const z = utcSeconds(a.date);
  if (!isNaN(z)) return z;
  const t = Date.parse(a.date);
  return isNaN(t) ? NaN : Math.floor(t / 1000);
}
