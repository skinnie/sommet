// Twin of tools/race_calibration.py: fitting the rider's base speed from rides (history, one ride,
// self-rating, weight prior), the rolling stop ratio, how stops are spread over controls, and the
// sleep-suggestion tiers. Parity-tested against the Python.
import { climbDensity, CURVE_DIVISOR, CURVE_EXPONENT } from './raceEvent';
import { median, pyRound, parseIso, NDT } from './pyCompat';

export const MIN_CAL_DISTANCE_KM = 60.0;
export const MIN_PLAUSIBLE_KMH = 8.0;
export const MAX_PLAUSIBLE_KMH = 45.0;
export const MAX_CLIMB_DENSITY = 4000.0;
export const MAX_STOP_RATIO = 0.45;
export const DEFAULT_WINDOW_DAYS = 120;
export const MIN_WINDOW_RIDES = 3;
export const TARGET_WINDOW_RIDES = 8;
const OFFROAD = new Set(['mtb', 'gravel', 'mountain_biking', 'mountain biking', 'cyclocross', 'offroad']);
export const SELF_RATING_BASE: Record<string, number> = { casual: 22.0, steady: 25.0, strong: 28.0, racer: 31.0 };
const PRIOR_W_PER_KG = 1.6, PRIOR_CDA = 0.42, PRIOR_CRR = 0.005;
const G = 9.81, RHO = 1.2, ETA = 0.97, CV = 0.1;

export interface Ride {
  date?: string | null; start_time?: string | null; distance_km?: number; ascent_m?: number;
  moving_time_s?: number; elapsed_time_s?: number; sport?: string | null; is_race?: boolean; is_group?: boolean;
}

function movingSpeed(r: Ride): number | null {
  const t = r.moving_time_s || 0, d = r.distance_km || 0;
  return t > 0 && d > 0 ? d / (t / 3600.0) : null;
}

export function isCalibrationRide(r: Ride): boolean {
  const dist = r.distance_km || 0;
  if (dist < MIN_CAL_DISTANCE_KM) return false;
  const sp = movingSpeed(r);
  if (sp === null || !(MIN_PLAUSIBLE_KMH <= sp && sp <= MAX_PLAUSIBLE_KMH)) return false;
  if (climbDensity(dist, r.ascent_m || 0) > MAX_CLIMB_DENSITY) return false;
  if (typeof r.sport === 'string' && OFFROAD.has(r.sport.trim().toLowerCase())) return false;
  const el = r.elapsed_time_s, mv = r.moving_time_s;
  if (el && mv && el > 0 && (1.0 - mv / el) > MAX_STOP_RATIO) return false;
  if (r.is_race || r.is_group) return false;
  return true;
}

function impliedBase(r: Ride): number | null {
  const sp = movingSpeed(r);
  if (sp === null) return null;
  return sp + Math.pow(climbDensity(r.distance_km || 0, r.ascent_m || 0) / CURVE_DIVISOR, CURVE_EXPONENT);
}

function rideDate(r: Ride): NDT | null {
  const dt = r.date || r.start_time;
  if (typeof dt !== 'string') return null;
  try { return parseIso(dt); } catch { return null; }
}

const DAY_US = 86400 * 1e6;

function windowRides(clean: Ride[], now: NDT | null, windowDays: number): Ride[] {
  const dated = clean.map(r => [rideDate(r), r] as [NDT | null, Ride]);
  let ref: NDT | null = now;
  if (ref === null) {
    for (const [d] of dated) if (d !== null && (ref === null || d > ref)) ref = d;
  }
  if (ref === null) return clean;
  let days = windowDays;
  const pick = () => dated.filter(([d]) => d !== null && d >= (ref as number) - days * DAY_US).map(([, r]) => r);
  let w = pick();
  while (w.length < MIN_WINDOW_RIDES && days < 4000) { days *= 2; w = pick(); }
  return w.length < MIN_WINDOW_RIDES ? clean : w;
}

export function calibrateFromHistory(rides: Ride[], now: NDT | null = null, windowDays = DEFAULT_WINDOW_DAYS) {
  const clean = rides.filter(isCalibrationRide);
  if (!clean.length) return null;
  const w = windowRides(clean, now, windowDays);
  const bases = w.map(impliedBase).filter((b): b is number => b !== null);
  if (!bases.length) return null;
  const n = bases.length;
  return { base_speed_kmh: pyRound(median(bases), 1),
           confidence: n >= TARGET_WINDOW_RIDES ? 'high' : (n >= MIN_WINDOW_RIDES ? 'medium' : 'low'),
           model_source: 'personal', n_recent_rides: n };
}

export function calibrateFromSingleRide(r: Ride) {
  const b = impliedBase(r);
  if (b === null) return null;
  return { base_speed_kmh: pyRound(b, 1), confidence: 'low', model_source: 'personal', n_recent_rides: 1 };
}

export function calibrateFromSelfRating(rating: string | null | undefined) {
  const b = SELF_RATING_BASE[(rating || '').trim().toLowerCase()];
  if (b === undefined) return null;
  return { base_speed_kmh: b, confidence: 'low', model_source: 'generic', n_recent_rides: 0 };
}

export function physicsPriorBase(weightKg: number, bikeKg = 10.0, loadKg = 0.0) {
  const power = PRIOR_W_PER_KG * weightKg, mass = weightKg + bikeKg + loadKg;
  const net = (v: number) => power * ETA - v * (G * mass * PRIOR_CRR + CV * v + 0.5 * RHO * PRIOR_CDA * v * v);
  let lo = 0.5, hi = 25.0;
  for (let i = 0; i < 60; i++) { const mid = (lo + hi) / 2; if (net(mid) > 0) lo = mid; else hi = mid; }
  return { base_speed_kmh: pyRound((lo + hi) / 2 * 3.6, 1), confidence: 'low', model_source: 'physics', n_recent_rides: 0 };
}

export function buildSpeedProfile(o: { rides?: Ride[]; single_ride?: Ride; self_rating?: string; weight_kg?: number;
                                       bike_weight_kg?: number; load_weight_kg?: number; now?: NDT | null }) {
  if (o.rides && o.rides.length) { const p = calibrateFromHistory(o.rides, o.now ?? null); if (p) return p; }
  if (o.single_ride) { const p = calibrateFromSingleRide(o.single_ride); if (p) return p; }
  if (o.self_rating) { const p = calibrateFromSelfRating(o.self_rating); if (p) return p; }
  if (o.weight_kg) return physicsPriorBase(o.weight_kg, o.bike_weight_kg ?? 10.0, o.load_weight_kg ?? 0.0);
  return null;
}

// ---- rolling stops -----------------------------------------------------------------------------

export const DEFAULT_STOP_RATIO = 0.18;
export const STOP_RATIO_MAX = 0.60;
export const DEFAULT_CONTROL_BASE_S = 600.0;

function stopRatioOf(r: Ride): number | null {
  const el = r.elapsed_time_s, mv = r.moving_time_s;
  if (!el || !mv || el <= 0 || mv <= 0 || el < mv) return null;
  const x = (el - mv) / mv;
  return x >= 0.0 && x <= STOP_RATIO_MAX ? x : null;
}

export function stopRatioFromRides(rides: Ride[], now: NDT | null = null, windowDays = DEFAULT_WINDOW_DAYS) {
  const clean = rides.filter(isCalibrationRide);
  const w = windowRides(clean, now, windowDays);
  const ratios = w.map(stopRatioOf).filter((x): x is number => x !== null);
  if (!ratios.length) return { ratio: DEFAULT_STOP_RATIO, source: 'default', confidence: 'low', n_recent_rides: 0 };
  const n = ratios.length;
  return { ratio: pyRound(median(ratios), 3), source: 'personal',
           confidence: n >= TARGET_WINDOW_RIDES ? 'high' : (n >= MIN_WINDOW_RIDES ? 'medium' : 'low'), n_recent_rides: n };
}

/** {control leg index: stop seconds}; the budget (ratio x total moving) is preserved, overrides win. */
export function distributeStops(controlIdxs: number[], legMovingS: number[], stopRatio = DEFAULT_STOP_RATIO,
                                perControlBaseS = DEFAULT_CONTROL_BASE_S,
                                overrides: Record<number, number> | null = null): Map<number, number> {
  const ctrl = controlIdxs.filter(i => i >= 0 && i < legMovingS.length);
  const ov = new Map<number, number>();
  for (const [k, v] of Object.entries(overrides || {})) {
    const i = parseInt(k, 10);
    if (ctrl.includes(i)) ov.set(i, Math.max(0.0, Number(v)));
  }
  const out = new Map<number, number>(ov);
  const eligible = ctrl.filter(i => !ov.has(i));
  const budget = Math.max(0.0, stopRatio) * legMovingS.reduce((a, b) => a + b, 0);
  let ovSum = 0; ov.forEach(v => { ovSum += v; });
  const remaining = Math.max(0.0, budget - ovSum);
  if (!eligible.length) return out;
  const baseTotal = perControlBaseS * eligible.length;
  if (baseTotal >= remaining) {
    const share = remaining / eligible.length;
    for (const i of eligible) out.set(i, share);
    return out;
  }
  const leftover = remaining - baseTotal;
  const movE = eligible.reduce((a, i) => a + legMovingS[i], 0) || 1.0;
  for (const i of eligible) out.set(i, perControlBaseS + leftover * (legMovingS[i] / movE));
  return out;
}

export function suggestSleepS(elapsedS: number): number {
  const h = elapsedS / 3600.0;
  if (h < 20) return 0;
  if (h < 30) return 3600;
  if (h < 40) return 3 * 3600;
  return 5 * 3600;
}

/** race_calibrate.py, from the app's FIT decoder (shared/activity_streams summary) instead of
 *  tools/fit_decode: same session fields, times truncated to whole seconds like fit_decode's int(). */
export function calibrateFitSummary(summary: { dist_m?: number | null; timer_s?: number | null; elapsed_s?: number | null;
                                               ascent_m?: number | null }): any {
  const distKm = (summary.dist_m || 0) / 1000.0;
  if (distKm <= 0) return { ok: false, error: 'could not read distance/moving-time from that FIT' };
  const moving = Math.trunc(summary.timer_s || 0) || Math.trunc(summary.elapsed_s || 0) || 0;
  const ride: Ride = { distance_km: distKm, ascent_m: summary.ascent_m || 0.0, moving_time_s: moving,
                       elapsed_time_s: Math.trunc(summary.elapsed_s || 0) };
  const prof = calibrateFromSingleRide(ride);
  if (!prof) return { ok: false, error: 'ride too short/steep to place you on the curve' };
  return { ok: true, profile: prof, ride: { distance_km: pyRound(distKm, 1), ascent_m: pyRound(ride.ascent_m || 0),
                                           moving_time_s: pyRound(moving) } };
}
