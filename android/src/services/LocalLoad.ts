// Local training load - the Android twin of desktop/src/services/coachservice.cpp's
// localLoadByDay()/computeLocalReadiness(). Used ONLY when intervals.icu is not connected (or its
// fetch fails): André, 2026-09-27, "keep intervals.icu as the first, only when not connected".
//
// Per move: Banister's TRIMP (x 0.8) from the move's average HR when the watch recorded one and
// the HR profile (max/rest HR, sex - remembered from the watch's personal settings) is known;
// otherwise its duration x the sport's typical load per hour (loadPerHour below). The published 1991 formula - the same one
// OpenAthlete's training-load.service.ts applies (formula only; their code is AGPL, not copied).
// Pure functions, no I/O, so the maths is unit-tested (__tests__/LocalLoad.test.ts).

export interface HrProfile { maxHr: number; restHr: number; male: boolean }

export interface LocalMove {
  startMs: number;      // epoch ms
  durationS: number;
  avgHr?: number;       // 0/undefined = no HR recorded
  name?: string;        // sport / activity name ("Cycling", "Walking", ...) - picks the rate below
}

// Calibrated so the local estimate reads on intervals.icu's scale (André, 2026-09-28: the local
// numbers ran ~2x intervals'). From his 443 intervals.icu activities of the past year:
// icu_training_load = 0.80 x its own Banister TRIMP (median, n=292), and, per hour of moving
// time, rides 72, gravel 71, virtual rides 54, runs 38 (n=14), walks/hikes 9, gym-type 13-16.
// Swimming had no samples - a generic moderate 40. Same table as desktop coachservice.cpp.
export const TRIMP_TO_LOAD = 0.8;

export function loadPerHour(name?: string): number {
  const n = (name ?? '').toLowerCase();
  const has = (...keys: string[]) => keys.some(k => n.includes(k));
  if (has('walk', 'hik', 'trek', 'nordic')) return 9;
  if (has('indoor cycl', 'virtual', 'trainer', 'spin', 'zwift')) return 55;
  if (has('cycl', 'bike', 'ride', 'velo', 'gravel', 'mtb')) return 70;
  if (has('run', 'jog', 'treadmill', 'trail')) return 40;
  if (has('swim')) return 40;
  if (has('yoga', 'pilates', 'stretch', 'weight', 'strength', 'gym', 'climb', 'workout', 'training')) return 15;
  return 30;
}

export interface LocalSeries {
  days: string[];       // YYYY-MM-DD, local
  ctl: number[];
  atl: number[];
  freshness: number;    // CTL - ATL as of the START of today (same as the desktop)
  rampPerWeek: number;  // steepest 7-day CTL climb over the last 4 weeks
  hrMoves: number;      // moves in the last 120 days that carried HR load
  durationMoves: number; // ... and those counted by duration
}

const MAX_MOVE_S = 48 * 3600;
const WINDOW_DAYS = 120;

/** minutes x HRr x 0.64 e^(1.92 HRr) (men) / 0.86 e^(1.67 HRr) (women), HRr = (avg-rest)/(max-rest). */
export function banisterTrimp(minutes: number, avgHr: number, p: HrProfile): number {
  if (p.maxHr <= p.restHr || avgHr <= p.restHr) return 0;
  const hrr = Math.min(1, Math.max(0, (avgHr - p.restHr) / (p.maxHr - p.restHr)));
  return p.male ? minutes * hrr * 0.64 * Math.exp(1.92 * hrr)
                : minutes * hrr * 0.86 * Math.exp(1.67 * hrr);
}

function ymdLocal(ms: number): string {
  const d = new Date(ms);
  const p = (n: number) => String(n).padStart(2, '0');
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`;
}

interface Scored { startMs: number; durationS: number; load: number; hr: boolean }

/** One move, several rows: the watch's own copy (UTC) beside its intervals.icu import (local
 *  time), or two identical imports. Same move = durations within 1% (5 s floor) and starts a
 *  whole number of hours apart (±3 min, up to 14 h). Keeps the richer load (HR wins). */
export function dedupeMoves<T extends Scored>(moves: T[]): T[] {
  const sorted = [...moves].sort((a, b) => a.startMs - b.startMs);
  const kept: T[] = [];
  for (const m of sorted) {
    let dup = false;
    for (let k = kept.length - 1; k >= 0 && m.startMs - kept[k].startMs <= (14 * 3600 + 180) * 1000; k--) {
      const o = kept[k];
      const dt = (m.startMs - o.startMs) / 1000;
      const offHour = dt - Math.round(dt / 3600) * 3600;
      if (Math.abs(m.durationS - o.durationS) <= Math.max(5, 0.01 * o.durationS) && Math.abs(offHour) <= 180) {
        if ((m.hr && !o.hr) || (m.hr === o.hr && m.load > o.load)) kept[k] = m;
        dup = true;
        break;
      }
    }
    if (!dup) kept.push(m);
  }
  return kept;
}

export function computeLocalSeries(moves: LocalMove[], profile: HrProfile | null, now = Date.now()): LocalSeries {
  const windowStart = now - WINDOW_DAYS * 86400 * 1000;
  const scored: Scored[] = [];
  for (const m of moves) {
    // Longer than two days = a watch left recording / corrupt header, not training (real,
    // 2026-09-27: two 59841-min "Running" rows put the desktop's Fitness at 356).
    if (!(m.durationS > 0) || m.durationS > MAX_MOVE_S || !Number.isFinite(m.startMs)) continue;
    const minutes = m.durationS / 60;
    const trimp = profile && m.avgHr && m.avgHr > 0 ? banisterTrimp(minutes, m.avgHr, profile) : 0;
    // With HR: TRIMP on intervals' scale; without: the sport's typical load per hour.
    const load = trimp > 0 ? TRIMP_TO_LOAD * trimp : (m.durationS / 3600) * loadPerHour(m.name);
    scored.push({ startMs: m.startMs, durationS: m.durationS, load, hr: trimp > 0 });
  }
  const kept = dedupeMoves(scored);

  const loadByDay = new Map<string, number>();
  let hrMoves = 0, durationMoves = 0, earliest = Infinity;
  for (const m of kept) {
    const day = ymdLocal(m.startMs);
    loadByDay.set(day, (loadByDay.get(day) ?? 0) + m.load);
    earliest = Math.min(earliest, m.startMs);
    if (m.startMs >= windowStart) { if (m.hr) hrMoves++; else durationMoves++; }
  }

  // Walk day by day from 119 days back (or the first move, if later) to today.
  const today = new Date(now); today.setHours(12, 0, 0, 0);
  const start = new Date(today); start.setDate(start.getDate() - (WINDOW_DAYS - 1));
  if (!loadByDay.size) start.setTime(today.getTime());
  else if (earliest > start.getTime()) { start.setTime(earliest); start.setHours(12, 0, 0, 0); }

  const aCtl = 1 - Math.exp(-1 / 42), aAtl = 1 - Math.exp(-1 / 7);
  const days: string[] = [], ctlArr: number[] = [], atlArr: number[] = [];
  let ctl = 0, atl = 0, freshness = 0;
  for (const d = new Date(start); d.getTime() <= today.getTime(); d.setDate(d.getDate() + 1)) {
    const key = ymdLocal(d.getTime());
    freshness = ctl - atl;
    const load = loadByDay.get(key) ?? 0;
    ctl = ctl * (1 - aCtl) + load * aCtl;
    atl = atl * (1 - aAtl) + load * aAtl;
    days.push(key); ctlArr.push(ctl); atlArr.push(atl);
  }
  const n = days.length;
  let rampPerWeek = 0;
  for (let d = n - 1; d >= Math.max(7, n - 28); d--) rampPerWeek = Math.max(rampPerWeek, ctlArr[d] - ctlArr[d - 7]);
  return { days, ctl: ctlArr, atl: atlArr, freshness, rampPerWeek, hrMoves, durationMoves };
}
