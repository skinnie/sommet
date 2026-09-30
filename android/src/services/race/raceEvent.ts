// Twin of tools/race_event.py: event/athlete/bike shapes, the smoothed route ascent and the
// curve-first speed model behind estimate_leg / estimate_route. Parity: tools/test_race_parity.js.
import { NDT, parseIso, iso, addSeconds, diffSeconds, hhmm, pyRound } from './pyCompat';
import { parseGpxPoints, RoutePt } from './geo';

export interface Cutoff { label: string; distance_km: number; cutoff_dt: NDT | null; open_dt: NDT | null }
export interface SpeedProfile { base_speed_kmh?: number; confidence?: string; model_source?: string; n_recent_rides?: number }
export interface AthleteInputs { weight_kg: number; ftp_w?: number | null; rmr_kcal_day?: number | null; speed_profile?: SpeedProfile | null }
export interface BikeInputs { bike_weight_kg: number; load_weight_kg?: number; bike_type?: string; aero_category?: string | null }
export interface RaceEvent {
  name: string; event_type: string; start_dt: NDT; gpx?: string | null; points: RoutePt[];
  target_finish_dt?: NDT | null; cutoffs: Cutoff[];
}

const opt = (v: any): NDT | null => (typeof v === 'string' ? parseIso(v) : (typeof v === 'number' ? v : null));

export function cutoffFromDict(d: any): Cutoff {
  return { label: d.label, distance_km: d.distance_km, cutoff_dt: opt(d.cutoff_dt), open_dt: opt(d.open_dt) };
}

export function eventFromDict(d: any): RaceEvent {
  if (d.name === undefined) throw new Error("'name'");
  if (d.event_type === undefined) throw new Error("'event_type'");
  return {
    name: d.name, event_type: d.event_type, start_dt: opt(d.start_dt) as NDT,
    gpx: d.gpx ?? null, points: d.points || [], target_finish_dt: opt(d.target_finish_dt),
    cutoffs: (d.cutoffs || []).map((c: any) => cutoffFromDict(c)),
  };
}

export const defaultAthlete = (): AthleteInputs => ({ weight_kg: 75.0, ftp_w: null, rmr_kcal_day: null, speed_profile: null });
export const defaultBike = (): BikeInputs => ({ bike_weight_kg: 10.0, load_weight_kg: 5.0, bike_type: 'tour', aero_category: null });

/** AthleteInputs(**d) / BikeInputs(**d): the dataclass defaults for the fields a caller left out. */
export function athleteFromDict(d: any): AthleteInputs {
  if (d.weight_kg === undefined) throw new Error("AthleteInputs.__init__() missing 1 required positional argument: 'weight_kg'");
  return { weight_kg: d.weight_kg, ftp_w: d.ftp_w ?? null, rmr_kcal_day: d.rmr_kcal_day ?? null, speed_profile: d.speed_profile ?? null };
}
export function bikeFromDict(d: any): BikeInputs {
  if (d.bike_weight_kg === undefined) throw new Error("BikeInputs.__init__() missing 1 required positional argument: 'bike_weight_kg'");
  return { bike_weight_kg: d.bike_weight_kg, load_weight_kg: d.load_weight_kg ?? 0.0, bike_type: d.bike_type ?? 'tour',
           aero_category: d.aero_category ?? null };
}

// ---- distances + smoothed ascent ---------------------------------------------------------------

function haversine(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371000;
  const r = (d: number) => d * Math.PI / 180;
  const phi1 = r(lat1), phi2 = r(lat2), dphi = r(lat2 - lat1), dlam = r(lon2 - lon1);
  const a = Math.sin(dphi / 2) ** 2 + Math.cos(phi1) * Math.cos(phi2) * Math.sin(dlam / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(a));
}

/** race_event._cumulative_distances -> [cumul_m, total_m] */
export function cumulativeDistances(points: RoutePt[]): [number[], number] {
  if (points.length < 2) return [[], 0.0];
  const cumul = [0.0];
  for (let i = 1; i < points.length; i++) {
    cumul.push(cumul[i - 1] + haversine(points[i - 1].lat, points[i - 1].lon, points[i].lat, points[i].lon));
  }
  return [cumul, cumul.length ? cumul[cumul.length - 1] : 0.0];
}

export const ASCENT_SMOOTH_M = 200.0;
export const ASCENT_HYSTERESIS_M = 3.0;

const _ascentCache = new Map<RoutePt[], Array<[number, number]>>();

/** Per point: [gain, loss] after smoothing + hysteresis. Memoised per route array. */
export function ascentSteps(points: RoutePt[], cumul: number[]): Array<[number, number]> {
  const hit = _ascentCache.get(points);
  if (hit && hit.length === points.length) return hit;
  if (_ascentCache.size > 4) _ascentCache.clear();
  const v = _ascentSteps(points, cumul);
  _ascentCache.set(points, v);
  return v;
}

function bisectLeft(a: number[], x: number): number {
  let lo = 0, hi = a.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (a[mid] < x) lo = mid + 1; else hi = mid; }
  return lo;
}
function bisectRight(a: number[], x: number): number {
  let lo = 0, hi = a.length;
  while (lo < hi) { const mid = (lo + hi) >> 1; if (x < a[mid]) hi = mid; else lo = mid + 1; }
  return lo;
}

function _ascentSteps(points: RoutePt[], cumul: number[]): Array<[number, number]> {
  const n = points.length;
  const ele: Array<number | null> = [];
  let last: number | null = null;
  for (const p of points) {
    const e = p.ele;
    if (e === null || e === undefined || (e as any) === '') ele.push(last);
    else { last = Number(e); ele.push(last); }
  }
  const first = ele.find(e => e !== null);
  if (first === undefined || first === null) return Array.from({ length: n }, () => [0.0, 0.0] as [number, number]);
  const el = ele.map(e => (e === null ? first : e)) as number[];
  const pre = [0.0];
  for (const e of el) pre.push(pre[pre.length - 1] + e);
  const half = ASCENT_SMOOTH_M / 2.0;
  const out: Array<[number, number]> = [];
  let ref: number | null = null;
  for (let i = 0; i < n; i++) {
    const lo = bisectLeft(cumul, cumul[i] - half);
    const hi = bisectRight(cumul, cumul[i] + half);
    const sm = (pre[hi] - pre[lo]) / Math.max(1, hi - lo);
    let gain = 0.0, loss = 0.0;
    if (ref === null) ref = sm;
    else if (sm - ref > ASCENT_HYSTERESIS_M) { gain = sm - ref; ref = sm; }
    else if (ref - sm > ASCENT_HYSTERESIS_M) { loss = ref - sm; ref = sm; }
    out.push([gain, loss]);
  }
  return out;
}

export function ascentDescentM(points: RoutePt[]): [number, number] {
  if (points.length < 2) return [0.0, 0.0];
  const [cumul] = cumulativeDistances(points);
  const steps = ascentSteps(points, cumul);
  let g = 0, l = 0;
  for (const [a, b] of steps) { g += a; l += b; }
  return [g, l];
}

// ---- curve-first speed model --------------------------------------------------------------------

export const CURVE_DIVISOR = 300.0;
export const CURVE_EXPONENT = 1.2;
export const CURVE_FLOOR_KMH = 4.0;

export interface LegEstimate { moving_time_s: number; avg_speed_kmh: number; confidence: string; model_source: string }

export function climbDensity(distanceKm: number, ascentM: number): number {
  if (distanceKm <= 0) return 0.0;
  return (ascentM || 0.0) / distanceKm * 100.0;
}

export function curveSpeedKmh(base: number, climbDensityV: number): number {
  return Math.max(base - Math.pow(climbDensityV / CURVE_DIVISOR, CURVE_EXPONENT), CURVE_FLOOR_KMH);
}

export function estimateLeg(distanceKm: number, ascentM: number, _descentM: number,
                            athlete: AthleteInputs | null, _bike: BikeInputs | null): LegEstimate {
  const profile = athlete ? athlete.speed_profile : null;
  let speed: number, confidence: string, source: string;
  if (profile && profile.base_speed_kmh) {
    speed = curveSpeedKmh(Number(profile.base_speed_kmh), climbDensity(distanceKm, ascentM));
    confidence = profile.confidence ?? 'medium';
    source = profile.model_source ?? 'personal';
  } else {
    speed = 15.0; confidence = 'low'; source = 'placeholder';
  }
  const moving = speed > 0 ? (distanceKm / speed) * 3600.0 : 0.0;
  return { moving_time_s: pyRound(moving, 1), avg_speed_kmh: pyRound(speed, 1), confidence, model_source: source };
}

export interface RouteEstimate {
  moving_time_s: number; distance_km: number; avg_speed_kmh: number; confidence: string; model_source: string;
  legs: LegEstimate[];
}

export function estimateRoute(legs: Array<[number, number, number]>, athlete: AthleteInputs | null,
                              bike: BikeInputs | null): RouteEstimate {
  const ests: LegEstimate[] = [];
  let totalT = 0.0, totalKm = 0.0;
  for (const [d, a, de] of legs) {
    const e = estimateLeg(d, a, de, athlete, bike);
    ests.push(e); totalT += e.moving_time_s; totalKm += d;
  }
  const rank: Record<string, number> = { low: 0, medium: 1, high: 2 };
  let confidence = 'low';
  if (ests.length) {
    confidence = ests[0].confidence;
    for (const e of ests) if ((rank[e.confidence] ?? 0) < (rank[confidence] ?? 0)) confidence = e.confidence;
  }
  return {
    moving_time_s: pyRound(totalT, 1), distance_km: pyRound(totalKm, 2),
    avg_speed_kmh: totalT > 0 ? pyRound(totalKm / (totalT / 3600.0), 1) : 0.0,
    confidence, model_source: ests.length ? ests[0].model_source : 'placeholder',
    legs: ests.map(e => ({ ...e })),
  };
}

// ---- baseline plan (/api/race/plan/create) -----------------------------------------------------

export function routePoints(event: RaceEvent): RoutePt[] {
  let pts = event.points;
  if ((!pts || !pts.length) && event.gpx) {
    try { pts = parseGpxPoints(event.gpx); } catch { pts = []; }
  }
  return pts || [];
}

export function baselinePlan(event: RaceEvent, athlete?: AthleteInputs | null, bike?: BikeInputs | null) {
  athlete = athlete || defaultAthlete();
  bike = bike || defaultBike();
  const points = routePoints(event);
  const distM = points.length ? cumulativeDistances(points)[1] : 0.0;
  const eventDict = eventToDict(event);
  if (distM === 0.0) {
    return { event: eventDict, athlete, bike, provisional: true, distance_m: 0.0, finish_eta_dt: null,
             moving_time_s: 0.0, required_avg_speed_kmh: 0.0, summary: { error: 'route has no distance (no GPX or points)' } };
  }
  const km = distM / 1000.0;
  const [asc, desc] = ascentDescentM(points);
  const est = estimateLeg(km, asc, desc, athlete, bike);
  const finish = addSeconds(event.start_dt, est.moving_time_s);
  let required: number | null = null;
  let deadline = event.target_finish_dt ?? null;
  if (deadline === null && event.cutoffs.length) {
    const cs = event.cutoffs.map(c => c.cutoff_dt).filter((x): x is NDT => x !== null);
    deadline = cs.length ? Math.min(...cs) : null;
  }
  if (deadline !== null) {
    const hours = diffSeconds(deadline, event.start_dt) / 3600.0;
    if (hours > 0) required = pyRound(km / hours, 1);
  }
  return {
    event: eventDict, athlete, bike, provisional: true, distance_m: distM, finish_eta_dt: iso(finish),
    moving_time_s: est.moving_time_s, required_avg_speed_kmh: required || 0.0,
    summary: {
      distance_km: pyRound(km, 2), ascent_m: pyRound(asc), predicted_avg_speed_kmh: est.avg_speed_kmh,
      moving_time_hours: pyRound(est.moving_time_s / 3600.0, 1), finish_time: hhmm(finish),
      confidence: est.confidence, model_source: est.model_source, required_avg_speed_kmh: required,
    },
  };
}

export function eventToDict(e: RaceEvent) {
  const f = (t: NDT | null | undefined) => (typeof t === 'number' ? iso(t) : t ?? null);
  return {
    name: e.name, event_type: e.event_type, start_dt: f(e.start_dt), gpx: e.gpx ?? null, points: e.points,
    target_finish_dt: f(e.target_finish_dt),
    cutoffs: e.cutoffs.map(c => ({ label: c.label, distance_km: c.distance_km, cutoff_dt: f(c.cutoff_dt), open_dt: f(c.open_dt) })),
  };
}

/** race_event.py main(): {event, athlete?, bike?} -> {ok, plan} | {ok:false, error}. */
export function planFromBody(body: any): any {
  try {
    const event = eventFromDict(body.event || {});
    const athlete = 'athlete' in body ? athleteFromDict(body.athlete || {}) : null;
    const bike = 'bike' in body ? bikeFromDict(body.bike || {}) : null;
    return { ok: true, plan: baselinePlan(event, athlete, bike) };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}
