// Sleep score - a port of Train Libre's Sleep Health Score engine (SHS v3.5,
// lib/features/sleep/domain/scoring/sleep_scoring_engine.dart, GPL-3.0 like this app,
// https://github.com/rfivesix/train-libre). André, 2026-09-27: "5 yes why not".
// Twin of desktop/src/services/sleepscore.cpp - keep the two in step.
//
// Five domains on [0,1], weighted 30/20/25/15/10 - duration, continuity (efficiency + wake after
// sleep onset), architecture (deep/REM minutes, light-sleep share), circadian timing (mid-sleep
// near 03:30) and regularity (spread of mid-sleep over the last nights) - renormalised over the
// ones the source actually provides, then multiplied by the single worst bottleneck (short REM,
// short deep, short night, late mid-sleep). A fitness heuristic, not a clinical measure.
//
// What each source gives: Garmin - stages, awake time, bed/wake times (all five domains);
// intervals.icu - duration only (so duration + Train Libre's continuity fallback).

export interface SleepNight {
  durationMin?: number;        // total sleep time
  efficiencyPct?: number;      // asleep / in bed
  wasoMin?: number;            // awake after sleep onset
  lightPct?: number; deepPct?: number; remPct?: number;   // of total sleep time
  onsetHourLocal?: number;     // 0..24 local clock of sleep onset
  midSleepSdHours?: number;    // spread of mid-sleep over the previous nights
  regularityDays?: number;     // how many nights that spread is over
}

export interface SleepScore {
  score: number;               // 0..100
  state: 'good' | 'average' | 'poor';
  completeness: number;        // share of the domain weights the source could feed (0..1)
  bottleneck?: 'rem' | 'n3' | 'tst' | 'timing';
}

const gauss = (x: number, mu: number, sigma: number) => Math.exp(-((x - mu) ** 2) / (2 * sigma * sigma));

function linear(v: number, xMin: number, xMax: number, yMin: number, yMax: number): number {
  if (xMin === xMax) return yMax;
  const c = xMin < xMax ? Math.min(Math.max(v, xMin), xMax) : Math.min(Math.max(v, xMax), xMin);
  return yMin + ((c - xMin) / (xMax - xMin)) * (yMax - yMin);
}

export function scoreDuration(min: number): number {
  const h = min / 60;
  if (h > 10.5) return 0;
  if (h >= 7 && h <= 9) return 1;
  return gauss(h, h < 7 ? 7 : 9, 1);
}
export const scoreEfficiency = (pct: number) => 1 / (1 + Math.exp(-50 * (pct / 100 - 0.9)));
export const scoreWaso = (min: number) => 1 / (1 + (Math.max(min - 20, 0) / 30) ** 2);
const lightPenalty = (pct: number) => (pct > 65 ? gauss(pct, 65, 7) : 1);

export function scoreArchitecture(durationMin: number, deepPct: number, remPct: number, lightPct = 0): number {
  const n3 = (deepPct / 100) * durationMin, rem = (remPct / 100) * durationMin;
  const aN3 = Math.min(1, n3 / 90) * gauss(n3, 90, 40);
  const aRem = Math.min(1, rem / 100) * gauss(rem, 100, 40);
  return Math.min(1, Math.max(0, (0.45 * aN3 + 0.45 * aRem) * lightPenalty(lightPct) + 0.1));
}

/** Mid-sleep clock hour, continuous across midnight (evening onsets are taken as negative). */
export function midSleepHour(onsetHourLocal: number, durationMin: number): number {
  let ms = (onsetHourLocal > 12 ? onsetHourLocal - 24 : onsetHourLocal) + durationMin / 120;
  while (ms < 0) ms += 24;
  while (ms >= 24) ms -= 24;
  return ms;
}

export function scoreTiming(onsetHourLocal: number, durationMin: number): number {
  const ms = midSleepHour(onsetHourLocal, durationMin);
  let s = gauss(ms, 3.5, 1);
  if (ms > 5.5) s *= gauss(ms, 5.5, 0.5);
  return Math.min(1, Math.max(0, s));
}
export const scoreRegularity = (sdHours: number) => 1 / (1 + sdHours ** 2);

export function computeSleepScore(n: SleepNight): SleepScore | null {
  const D = n.durationMin != null ? scoreDuration(n.durationMin) : null;
  const se = n.efficiencyPct != null ? scoreEfficiency(n.efficiencyPct) : null;
  const waso = n.wasoMin != null ? scoreWaso(n.wasoMin) : null;
  // No efficiency/WASO: Train Libre's fallback - light-sleep share as a fragmentation proxy.
  const C = se != null && waso != null ? 0.5 * se + 0.5 * waso
    : se ?? waso ?? 0.9 * lightPenalty(n.lightPct ?? 0) + 0.1 * (D ?? 0);
  const A = n.durationMin != null && n.deepPct != null && n.remPct != null
    ? scoreArchitecture(n.durationMin, n.deepPct, n.remPct, n.lightPct) : null;
  const T = n.onsetHourLocal != null && n.durationMin != null ? scoreTiming(n.onsetHourLocal, n.durationMin) : null;
  const R = n.midSleepSdHours != null && (n.regularityDays ?? 0) >= 5 ? scoreRegularity(n.midSleepSdHours) : null;

  const parts: [number, number | null][] = [[0.30, D], [0.20, C], [0.25, A], [0.15, T], [0.10, R]];
  let w = 0, sum = 0;
  for (const [wi, s] of parts) if (s != null) { w += wi; sum += wi * s; }
  if (w <= 0 || D == null) return null;   // no duration: nothing honest to score

  let mult = 1;
  let bottleneck: SleepScore['bottleneck'];
  const take = (m: number, why: NonNullable<SleepScore['bottleneck']>) => { if (m < mult) { mult = m; bottleneck = why; } };
  if (n.remPct != null && n.durationMin != null) take(linear((n.remPct / 100) * n.durationMin, 40, 60, 0.65, 1), 'rem');
  if (n.deepPct != null && n.durationMin != null) take(linear((n.deepPct / 100) * n.durationMin, 40, 70, 0.60, 1), 'n3');
  if (n.durationMin != null) take(linear(n.durationMin / 60, 5, 6.5, 0.5, 1), 'tst');
  if (n.onsetHourLocal != null && n.durationMin != null) take(linear(midSleepHour(n.onsetHourLocal, n.durationMin), 7.5, 5.5, 0.55, 1), 'timing');

  const score = Math.min(100, Math.max(0, (sum / w) * 100 * mult));
  return { score, state: score >= 80 ? 'good' : score >= 60 ? 'average' : 'poor', completeness: Math.min(1, w), bottleneck };
}

/** Standard deviation (hours) of mid-sleep clock times, handling the midnight wrap. */
export function midSleepSd(midSleepHours: number[]): number | null {
  if (midSleepHours.length < 2) return null;
  // Unwrap around the first night so 23:50 and 00:10 are 20 min apart, not 23.7 h.
  const ref = midSleepHours[0];
  const xs = midSleepHours.map(h => { let d = h - ref; if (d > 12) d -= 24; if (d < -12) d += 24; return ref + d; });
  const mean = xs.reduce((a, b) => a + b, 0) / xs.length;
  return Math.sqrt(xs.reduce((a, b) => a + (b - mean) ** 2, 0) / xs.length);
}
