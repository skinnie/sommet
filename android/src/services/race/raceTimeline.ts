// Twin of tools/race_timeline.py: segment the route at the controls, per-leg moving times from the
// speed model, stops / sleep / no-ride hours / fatigue walked along the clock, arrival + cutoff
// margins per control. Parity-tested against the Python on real events.
import { NDT, iso, addSeconds, diffSeconds, hourOf, replaceHM, pyRound } from './pyCompat';
import {
  RaceEvent, AthleteInputs, BikeInputs, Cutoff, estimateRoute, cumulativeDistances, ascentSteps, routePoints,
  defaultAthlete, defaultBike, RouteEstimate, eventFromDict, athleteFromDict, bikeFromDict,
} from './raceEvent';
import { RoutePt } from './geo';
import { Alertness } from './raceAlertness';
import { distributeStops, suggestSleepS } from './raceCalibration';

const DEFAULT_CONTROL_BASE_S = 600.0;
const FATIGUE_ONSET_H = 16.0;
const FATIGUE_RATE_PER_H = 0.010;
const FATIGUE_FLOOR = 0.80;
const WEAR_PER_NIGHT = 0.03;
const WEAR_FULL_NIGHT_S = 8 * 3600;
const WEAR_MIN_SLEEP_S = 2 * 3600;
const PLANNING_MARGIN = 1.04;
const SLEEP_RESET_K = 8.0;

const pyMod = (a: number, n: number) => ((a % n) + n) % n;

function fatigueFactor(awakeS: number, enabled: boolean): number {
  if (!enabled) return 1.0;
  const over = Math.max(0.0, awakeS / 3600.0 - FATIGUE_ONSET_H);
  return Math.max(FATIGUE_FLOOR, 1.0 - FATIGUE_RATE_PER_H * over);
}

export interface LegGeo { start_km: number; end_km: number; distance_km: number; ascent_m: number; descent_m: number }

export function segmentRoute(points: RoutePt[], cutKm: number[]): LegGeo[] {
  const [cumul, totalM] = cumulativeDistances(points);
  if (totalM <= 0 || points.length < 2) return [];
  const totalKm = totalM / 1000.0;
  const set = new Set<number>();
  for (const d of cutKm) if (d > 0.0 && d < totalKm) set.add(pyRound(d, 6));
  const cuts = Array.from(set).sort((a, b) => a - b);
  const bounds = [0.0, ...cuts, totalKm];
  const nSeg = bounds.length - 1;
  const legs: LegGeo[] = [];
  for (let i = 0; i < nSeg; i++) legs.push({ start_km: bounds[i], end_km: bounds[i + 1], distance_km: 0.0, ascent_m: 0.0, descent_m: 0.0 });
  const segOf = (km: number) => { for (let i = 0; i < nSeg; i++) if (km <= bounds[i + 1] + 1e-9) return i; return nSeg - 1; };
  const steps = ascentSteps(points, cumul);
  for (let i = 1; i < points.length; i++) {
    const stepM = cumul[i] - cumul[i - 1];
    if (stepM <= 0) continue;
    const s = segOf((cumul[i] + cumul[i - 1]) / 2000.0);
    legs[s].distance_km += stepM / 1000.0;
    legs[s].ascent_m += steps[i][0];
    legs[s].descent_m += steps[i][1];
  }
  for (const l of legs) {
    l.distance_km = pyRound(l.distance_km, 3); l.ascent_m = pyRound(l.ascent_m, 1); l.descent_m = pyRound(l.descent_m, 1);
  }
  return legs;
}

const fmt = (t: NDT | null | undefined): string | null => (typeof t === 'number' ? iso(t) : null);

function inNoRide(t: NDT, nr: [number, number]): boolean {
  const [s, e] = nr, h = hourOf(t);
  return s < e ? (s <= h && h < e) : (h >= s || h < e);
}

/** Next wall-clock `hour` strictly after t. */
function atHour(t: NDT, hour: number): NDT {
  const hh = pyMod(Math.trunc(hour), 24);
  const mm = pyRound((hour - Math.trunc(hour)) * 60);
  let cand = replaceHM(t, hh, pyMod(mm, 60));
  if (mm >= 60) cand = addSeconds(cand, 3600);
  if (cand <= t) cand = addSeconds(cand, 86400);
  return cand;
}

type Rest = [NDT, NDT, number];

function walkRiding(clock: NDT, moveS: number, nr: [number, number] | null, restAtStart: boolean,
                    stops: Array<[number, number]>, alert: Alertness | null): [NDT, Rest[], number] {
  const todo = stops.slice().sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  let si = 0;
  const rests: Rest[] = [];
  let done = 0.0, first = true, ridingS = 0.0;
  for (;;) {
    if (nr && (restAtStart || !first) && inNoRide(clock, nr)) {
      const end = atHour(clock, nr[1]);
      rests.push([clock, end, done]);
      if (alert) alert.sleep(diffSeconds(end, clock));
      clock = end;
    }
    first = false;
    if (si < todo.length && todo[si][0] <= done + 1e-6) {
      if (alert) alert.awake(todo[si][1]);
      clock = addSeconds(clock, todo[si][1]);
      si++;
      continue;
    }
    const remaining = moveS - done;
    if (remaining <= 1e-6 && si >= todo.length) break;
    const f = alert ? alert.speedFactor(clock) : 1.0;
    const untilStop = si < todo.length ? (todo[si][0] - done) / f : Infinity;
    const untilWin = nr ? diffSeconds(atHour(clock, nr[0]), clock) : Infinity;
    const step = Math.min(Math.max(remaining, 0.0) / f, untilStop, untilWin, alert ? 600.0 : Infinity);
    if (alert) { alert.record(clock); alert.awake(step); }
    clock = addSeconds(clock, step);
    done += step * f;
    ridingS += step;
    if (done >= moveS - 1e-6 && si >= todo.length) break;
  }
  return [clock, rests, ridingS];
}

export interface TimelineOptions {
  stops_s?: number[] | null; stop_total_s?: number | null; stop_profile?: any; sleep?: any;
  sleep_windows?: any[] | null; stop_events?: any[] | null; wind_speed_delta_kmh?: number;
  control_overrides?: Record<number, number> | null; fatigue?: any; no_ride?: any;
  /** tests only (the Python's injectable estimator switches the planning margin off) */
  route_estimator?: ((legs: Array<[number, number, number]>, a: AthleteInputs, b: BikeInputs) => RouteEstimate) | null;
}

export function buildTimeline(event: RaceEvent, athleteIn: AthleteInputs | null, bikeIn: BikeInputs | null,
                              o: TimelineOptions = {}): any {
  const athlete = athleteIn || defaultAthlete();
  const bike = bikeIn || defaultBike();
  const injected = !!o.route_estimator;
  const estimator = o.route_estimator || estimateRoute;

  const points = routePoints(event);
  const totalM = points.length ? cumulativeDistances(points)[1] : 0.0;
  if (totalM <= 0) return { ok: false, error: 'route has no distance (no GPX or points)' };
  const totalKm = totalM / 1000.0;

  const sorted = event.cutoffs.map((c, i) => [c, i] as [Cutoff, number])
    .sort((a, b) => (a[0].distance_km - b[0].distance_km) || (a[1] - b[1])).map(x => x[0]);
  const tol = Math.max(0.2, 0.01 * totalKm);
  const interior = sorted.filter(c => c.distance_km > 0.0 && c.distance_km < totalKm - tol);
  let finishControl: Cutoff | null = null;
  for (let i = sorted.length - 1; i >= 0; i--) if (Math.abs(sorted[i].distance_km - totalKm) <= tol) { finishControl = sorted[i]; break; }

  const legsGeo = segmentRoute(points, interior.map(c => c.distance_km));
  if (!legsGeo.length) return { ok: false, error: 'could not segment route' };
  const endControls: Array<Cutoff | null> = [...interior, finishControl];

  const routeEst = estimator(legsGeo.map(l => [l.distance_km, l.ascent_m, l.descent_m] as [number, number, number]), athlete, bike);
  const legEst: any[] = routeEst.legs || [];

  const wind = Number(o.wind_speed_delta_kmh || 0.0);
  if (wind && Math.abs(wind) > 0.05) {
    for (let i = 0; i < Math.min(legEst.length, legsGeo.length); i++) {
      const e = legEst[i], sp = Number(e.avg_speed_kmh || 0.0), dkm = Number(legsGeo[i].distance_km);
      if (sp > 0 && dkm > 0) {
        const nsp = Math.max(4.0, Math.min(sp + wind, sp * 1.5));
        e.avg_speed_kmh = pyRound(nsp, 1);
        e.moving_time_s = pyRound(dkm / nsp * 3600.0, 1);
      }
    }
  }

  const n = legsGeo.length;
  const legMoving = Array.from({ length: n }, (_, i) => (i < legEst.length ? Number(legEst[i].moving_time_s ?? 0.0) : 0.0));
  const totalMoving = legMoving.reduce((a, b) => a + b, 0);
  const genuine = interior.map((_, i) => i);

  const legStops = new Array(n).fill(0.0);
  let aggregateStop = 0.0;
  if (o.stops_s !== null && o.stops_s !== undefined) {
    for (let i = 0; i < Math.min(n, o.stops_s.length); i++) legStops[i] = Number(o.stops_s[i]);
  } else if (o.stop_total_s && o.stop_total_s > 0) {
    if (genuine.length && totalMoving > 0) {
      const dist = distributeStops(genuine, legMoving, Number(o.stop_total_s) / totalMoving, DEFAULT_CONTROL_BASE_S, o.control_overrides || {});
      dist.forEach((secs, idx) => { if (idx >= 0 && idx < n) legStops[idx] = Number(secs); });
    } else aggregateStop = Number(o.stop_total_s);
  } else if (o.stop_profile && Number(o.stop_profile.ratio || 0.0) > 0.0) {
    const ratio = Number(o.stop_profile.ratio);
    const base = Number(o.stop_profile.per_control_base_s ?? DEFAULT_CONTROL_BASE_S);
    if (genuine.length) {
      const dist = distributeStops(genuine, legMoving, ratio, base, o.control_overrides || {});
      dist.forEach((secs, idx) => { if (idx >= 0 && idx < n) legStops[idx] = Number(secs); });
    } else aggregateStop = ratio * totalMoving;
  }

  const assignToLeg = (km: number) => {
    let idx = n - 1;
    for (let i = 0; i < n; i++) if (legsGeo[i].end_km >= km - 0.01) { idx = i; break; }
    return idx >= n - 1 ? Math.max(0, n - 2) : idx;
  };
  const legSleep = new Array(n).fill(0.0);
  const legNap = new Array(n).fill(false);
  for (const w of o.sleep_windows || []) {
    const dur = Number(w.duration_s ?? 0.0);
    if (dur <= 0) continue;
    const idx = assignToLeg(Number(w.km ?? 0.0));
    legSleep[idx] += dur;
    if (w.nap) legNap[idx] = true;
  }
  const legEventStop = new Array(n).fill(0.0);
  for (const e of o.stop_events || []) {
    const dur = Number(e.duration_s ?? 0.0);
    if (dur <= 0) continue;
    legEventStop[assignToLeg(Number(e.km ?? 0.0))] += dur;
  }

  const fatigue = o.fatigue || null;
  const fatigueOn = !!(fatigue && fatigue.enabled);
  // DESKTOP BUG, copied on purpose for parity (found 2026-09-29, raised with André): the Python sets
  // route_estimator = route_estimator or estimate_route BEFORE testing `route_estimator is None`, so
  // the +4 % planning margin is never applied. Fix both sides together once he decides.
  void injected; void PLANNING_MARGIN;
  const margin = 1.0;
  let awakeS = Number((fatigue || {}).awake_at_start_s ?? 0.0);
  let wear = 0.0;
  let nr: [number, number] | null = null;
  const noRide = o.no_ride;
  if (noRide && noRide.start_h !== null && noRide.start_h !== undefined && noRide.end_h !== null && noRide.end_h !== undefined) {
    const a = pyMod(Number(noRide.start_h), 24.0), b = pyMod(Number(noRide.end_h), 24.0);
    if (a !== b) nr = [a, b];
  }
  let forcedRestTotal = 0.0;
  let alert: Alertness | null = null;
  if (fatigueOn && fatigue.model === 'twoprocess') {
    alert = new Alertness(Number(fatigue.bed_h ?? 22.0), Number(fatigue.wake_h ?? 6.0),
                          fatigue.awake_h_at_start ?? null, event.start_dt);
  }

  const rows: any[] = [];
  let clock = event.start_dt;
  let runningMoving = 0.0, totalStop = 0.0, plannedSleep = 0.0;
  let worstMargin: number | null = null, worstLabel: string | null = null;
  const perLegProvisional = legsGeo.length > 1;

  for (let i = 0; i < n; i++) {
    const geo = legsGeo[i];
    const lt = i < legEst.length ? legEst[i] : { moving_time_s: 0.0, avg_speed_kmh: 0.0, confidence: 'low', model_source: 'placeholder' };
    const ctrl = i < endControls.length ? endControls[i] : null;
    const baseMove = Number(lt.moving_time_s ?? 0.0) * margin;
    let fac = fatigueFactor(awakeS + 0.5 * baseMove, fatigueOn);
    if (alert) fac = 1.0;
    else if (fatigueOn) fac = Math.max(FATIGUE_FLOOR, fac * (1.0 - wear));
    const moveS = fac > 0 ? baseMove / fac : baseMove;
    let inlineStop = 0.0;
    const legRests: Array<{ start: NDT; end: NDT; km: number }> = [];
    let inline: Array<[number, number]> = [];
    if (n === 1 && aggregateStop > 0) {
      const k = Math.max(1, Math.trunc(pyRound(aggregateStop / 1500.0)));
      inline = Array.from({ length: k }, (_, j) => [(j + 1) * moveS / (k + 1), aggregateStop / k] as [number, number]);
    }
    let ridingS = moveS;
    let arrival: NDT;
    if (nr || inline.length || alert) {
      const [arr, raw, rs] = walkRiding(clock, moveS, nr, i > 0, inline, alert);
      arrival = arr; ridingS = rs;
      for (const [r0, r1, done] of raw) {
        legRests.push({ start: r0, end: r1, km: geo.start_km + geo.distance_km * (moveS > 0 ? done / moveS : 0.0) });
      }
      if (inline.length) { totalStop += aggregateStop; inlineStop = aggregateStop; }
    } else {
      arrival = addSeconds(clock, moveS);
    }
    runningMoving += alert ? ridingS : moveS;

    const isFinish = i === n - 1;
    let stopS = isFinish ? 0.0 : legStops[i] + legEventStop[i];
    const slp = legSleep[i];
    let depart = addSeconds(arrival, stopS + slp);
    if (alert) { alert.awake(stopS); alert.sleep(slp); }
    if (nr && !isFinish && inNoRide(depart, nr)) {
      const rEnd = atHour(depart, nr[1]);
      legRests.push({ start: depart, end: rEnd, km: geo.end_km });
      if (alert) alert.sleep(diffSeconds(rEnd, depart));
      depart = rEnd;
    }
    const legRestS = legRests.reduce((a, r) => a + diffSeconds(r.end, r.start), 0);
    forcedRestTotal += legRestS;
    totalStop += stopS;
    plannedSleep += slp;

    awakeS += moveS + stopS + inlineStop;
    if (slp > 0) {
      awakeS = Math.max(0.0, awakeS - slp * SLEEP_RESET_K);
      if (slp >= WEAR_MIN_SLEEP_S) wear += WEAR_PER_NIGHT * Math.max(0.0, 1.0 - slp / WEAR_FULL_NIGHT_S);
    }
    for (const r of legRests) {
      const d = diffSeconds(r.end, r.start);
      awakeS = Math.max(0.0, awakeS - d * SLEEP_RESET_K);
      if (d >= WEAR_MIN_SLEEP_S) wear += WEAR_PER_NIGHT * Math.max(0.0, 1.0 - d / WEAR_FULL_NIGHT_S);
    }

    const cutoff = ctrl ? ctrl.cutoff_dt : null;
    let marginS: number | null = null;
    if (typeof cutoff === 'number') {
      marginS = diffSeconds(cutoff, arrival);
      if (worstMargin === null || marginS < worstMargin) { worstMargin = marginS; worstLabel = ctrl ? ctrl.label : null; }
    }
    const open = ctrl ? ctrl.open_dt : null;
    const earlyS = typeof open === 'number' ? diffSeconds(open, arrival) : null;

    // With the two-process model the slowdown happens inside the walk (ridingS): report the leg as
    // ridden, so the rows add up to the total moving time.
    let legMoveS = moveS, legFac = fac;
    if (alert && ridingS > 0) { legMoveS = ridingS; legFac = fac * moveS / ridingS; }
    rows.push({
      index: i,
      label: ctrl ? ctrl.label : (isFinish ? 'Finish' : `Control ${i + 1}`),
      distance_km: pyRound(geo.end_km, 2),
      leg_distance_km: geo.distance_km,
      leg_ascent_m: geo.ascent_m,
      moving_time_s: pyRound(legMoveS, 1),
      avg_speed_kmh: pyRound(Number(lt.avg_speed_kmh || 0.0) * legFac, 1),
      fatigue_factor: pyRound(legFac, 3),
      arrival_dt: fmt(arrival),
      stop_s: pyRound(stopS, 1),
      sleep_s: pyRound(slp, 1),
      rests: legRests.map(r => ({ start: fmt(r.start), end: fmt(r.end), km: pyRound(r.km, 1) })),
      stop_overridden: !!(o.control_overrides && Object.prototype.hasOwnProperty.call(o.control_overrides, i) && !isFinish),
      depart_dt: fmt(depart),
      cutoff_dt: fmt(cutoff),
      margin_s: marginS === null ? null : pyRound(marginS, 1),
      opens_dt: fmt(open),
      early_s: earlyS === null ? null : pyRound(earlyS, 1),
      confidence: lt.confidence ?? null,
    });
    clock = depart;
  }

  if (aggregateStop > 0 && n !== 1) { totalStop += aggregateStop; clock = addSeconds(clock, aggregateStop); }

  const elapsedNoSleep = diffSeconds(clock, event.start_dt) - plannedSleep - forcedRestTotal;
  const sleepSuggested = suggestSleepS(elapsedNoSleep);
  let lumpSleep = 0.0;
  if (o.sleep && o.sleep.enabled && Number(o.sleep.duration_s ?? 0.0) > 0) {
    lumpSleep = Number(o.sleep.duration_s);
    clock = addSeconds(clock, lumpSleep);
  }
  const finish = clock;
  const elapsed = diffSeconds(finish, event.start_dt);
  const sleepTime = plannedSleep + lumpSleep + forcedRestTotal;
  const totalAscent = pyRound(legsGeo.reduce((a, l) => a + l.ascent_m, 0), 1);
  const sp = o.stop_profile;
  const hasStops = o.stops_s !== null && o.stops_s !== undefined;

  return {
    ok: true,
    distance_km: pyRound(totalKm, 2),
    total_ascent_m: totalAscent,
    start_dt: fmt(event.start_dt),
    finish_eta_dt: fmt(finish),
    moving_time_s: pyRound(runningMoving, 1),
    stop_time_s: pyRound(totalStop, 1),
    sleep_time_s: pyRound(sleepTime, 1),
    alertness: !alert ? null : { model: 'twoprocess', min: pyRound(alert.min_alertness, 1), min_at: alert.min_at !== null ? fmt(alert.min_at) : null },
    no_ride: !nr ? null : { start_h: nr[0], end_h: nr[1], rest_s: pyRound(forcedRestTotal, 1) },
    elapsed_time_s: pyRound(elapsed, 1),
    confidence: routeEst.confidence ?? 'low',
    model_source: routeEst.model_source ?? 'placeholder',
    stop_source: (sp && !hasStops) ? (sp.source ?? null) : (hasStops ? 'manual' : null),
    sleep_suggested_s: sleepSuggested,
    worst_margin_s: worstMargin === null ? null : pyRound(worstMargin, 1),
    worst_margin_control: worstLabel,
    per_control_provisional: perLegProvisional,
    controls: rows,
  };
}

/** race_timeline.py main(): the /api/race/timeline body -> {ok, timeline} | {ok:false, error}. */
export function timelineFromBody(body: any): any {
  try {
    const event = eventFromDict(body.event || {});
    const athlete = body.athlete ? athleteFromDict(body.athlete) : null;
    const bike = body.bike ? bikeFromDict(body.bike) : null;
    const ov: Record<number, number> = {};
    for (const [k, v] of Object.entries(body.control_overrides || {})) ov[parseInt(k, 10)] = Number(v);
    const tl = buildTimeline(event, athlete, bike, {
      stops_s: body.stops_s ?? null, stop_total_s: body.stop_total_s ?? null, stop_profile: body.stop_profile ?? null,
      sleep: body.sleep ?? null, sleep_windows: body.sleep_windows ?? null, stop_events: body.stop_events ?? null,
      wind_speed_delta_kmh: Number(body.wind_speed_delta_kmh || 0.0),
      control_overrides: Object.keys(ov).length ? ov : null, fatigue: body.fatigue ?? null, no_ride: body.no_ride ?? null,
    });
    return { ok: tl.ok ?? false, timeline: tl };
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}
