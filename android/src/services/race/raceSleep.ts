// Twin of tools/race_sleep.py: WHERE and WHEN to sleep - one window per night, on that night's
// "riding difficulty" peak (body clock + darkness eased by moonlight + cold), clamped to the dark and
// kept only when enough riding follows and the later cutoffs can absorb it. Uses the app's Astro.ts
// (the verified twin of tools/astro.py). Parity-tested.
import { NDT, parseIso, iso, addSeconds, diffSeconds, fields, hhmm, pyRound } from './pyCompat';
import { cumulativeDistances, parseGpxPoints, RoutePt } from './geo';
import { events as astroEvents, AstroEvents } from '../Astro';

const W_CIRCADIAN = 0.5, W_DARK = 0.5, W_COLD = 0.2, MOON_RELIEF = 0.6, CIRCADIAN_NADIR_H = 4.5, SAMPLE_MIN = 20;

const circadian = (h: number) => 0.5 * (1.0 + Math.cos(2.0 * Math.PI * (h - CIRCADIAN_NADIR_H) / 24.0));
const minutesOfDay = (t: NDT) => { const f = fields(t); return f.hour * 60 + f.minute + f.second / 60.0; };

function knotsOf(start: NDT, controls: any[]): Array<[number, NDT]> {
  const ks: Array<[number, NDT]> = [[0.0, start]];
  for (const c of controls) {
    const dt = c.arrival_dt;
    if (typeof dt === 'string') ks.push([Number(c.distance_km), parseIso(dt)]);
  }
  return ks.map((k, i) => [k, i] as [[number, NDT], number]).sort((a, b) => (a[0][1] - b[0][1]) || (a[1] - b[1])).map(x => x[0]);
}

function kmAtTime(knots: Array<[number, NDT]>, t: NDT): number {
  if (t <= knots[0][1]) return knots[0][0];
  if (t >= knots[knots.length - 1][1]) return knots[knots.length - 1][0];
  for (let i = 1; i < knots.length; i++) {
    const [k0, t0] = knots[i - 1], [k1, t1] = knots[i];
    if (t0 <= t && t <= t1) {
      const span = diffSeconds(t1, t0);
      const f = span <= 0 ? 0.0 : diffSeconds(t, t0) / span;
      return k0 + f * (k1 - k0);
    }
  }
  return knots[knots.length - 1][0];
}

function pointAtKm(points: RoutePt[], cumul: number[], km: number): [number, number] {
  let best = 0, bd = Infinity;
  for (let j = 0; j < cumul.length; j++) { const d = Math.abs(cumul[j] - km * 1000.0); if (d < bd) { bd = d; best = j; } }
  return [points[best].lat, points[best].lon];
}

class AstroCache {
  private c = new Map<string, AstroEvents>();
  constructor(private tz: number) {}
  get(t: NDT, lat: number, lon: number): AstroEvents {
    const f = fields(t);
    const key = `${f.year}-${f.month}-${f.day}|${pyRound(lat * 2) / 2}|${pyRound(lon * 2) / 2}`;
    let ev = this.c.get(key);
    if (!ev) { ev = astroEvents(f.year, f.month, f.day, lat, lon, this.tz); this.c.set(key, ev); }
    return ev;
  }
}

function isDark(ev: AstroEvents, mod: number): boolean {
  const sr = ev.sun_min.sunrise, ss = ev.sun_min.sunset;
  if (sr === null || sr === undefined || ss === null || ss === undefined) return false;
  return mod < sr || mod > ss;
}

function moonUp(ev: AstroEvents, mod: number): boolean {
  const mr = ev.moon_min.moonrise ?? null, ms = ev.moon_min.moonset ?? null;
  if (mr === null && ms === null) return (ev.moon_min.transit ?? null) !== null;
  if (mr !== null && ms !== null) return mr <= ms ? (mr <= mod && mod <= ms) : (mod >= mr || mod <= ms);
  if (mr !== null) return mod >= mr;
  return mod <= (ms as number);
}

function tempAt(weather: any[] | null | undefined, km: number): number | null {
  if (!weather || !weather.length) return null;
  let best = weather[0], bd = Infinity;
  for (const w of weather) { const d = Math.abs(Number(w.km ?? 0) - km); if (d < bd) { bd = d; best = w; } }
  return best.temp_c ?? null;
}

export function planSleep(controls: any[], points: RoutePt[], start: NDT, tzOffsetH = 0.0, suggestedTotalS = 0.0,
                          weather: any[] | null = null, minWindowS = 1800.0, minRideBeforeS = 3 * 3600.0,
                          minRideAfterS = 2.5 * 3600.0): any {
  if (controls.length < 1 || points.length < 2) return { ok: false, error: 'need a route and at least a finish control' };
  const knots = knotsOf(start, controls);
  const finish = knots[knots.length - 1][1];
  if (finish <= start) return { ok: false, error: 'finish is not after start' };
  const cumul = cumulativeDistances(points.map(p => [p.lat, p.lon] as [number, number]));
  const cache = new AstroCache(tzOffsetH);

  const samples: any[] = [];
  for (let t = start; t <= finish; t = addSeconds(t, SAMPLE_MIN * 60)) {
    const km = kmAtTime(knots, t);
    const [lat, lon] = pointAtKm(points, cumul, km);
    const ev = cache.get(t, lat, lon);
    const mod = minutesOfDay(t);
    const dark = isDark(ev, mod);
    const moonlit = moonUp(ev, mod) ? (ev.moon_illumination ?? 0.0) : 0.0;
    const f = fields(t);
    const circ = circadian(f.hour + f.minute / 60.0);
    const temp = tempAt(weather, km);
    const cold = temp === null ? 0.0 : Math.max(0.0, Math.min(1.0, (10.0 - Number(temp)) / 15.0));
    let diff = W_CIRCADIAN * circ + W_DARK * (dark ? 1.0 : 0.0) * (1.0 - MOON_RELIEF * moonlit);
    diff = Math.min(1.0, diff + W_COLD * cold);
    samples.push({ t, km, dark, diff, temp, moon: pyRound(ev.moon_illumination ?? 0.0, 2) });
  }

  let nights: any[][] = [];
  let cur: any[] = [];
  for (const s of samples) {
    if (s.dark) cur.push(s);
    else if (cur.length) { nights.push(cur); cur = []; }
  }
  if (cur.length) nights.push(cur);
  const rideFloor = addSeconds(start, minRideBeforeS);
  nights = nights.filter(n => diffSeconds(n[n.length - 1].t, n[0].t) >= minWindowS && n[n.length - 1].t >= rideFloor);
  if (!nights.length) return { ok: true, windows: [], note: 'No usable night on this ride - it finishes before real darkness.' };

  const perNight = suggestedTotalS > 0 ? Math.max(minWindowS, suggestedTotalS / nights.length) : minWindowS;
  const windows: any[] = [];
  nights.forEach((night, idx) => {
    let peak = night[0];
    for (const s of night) if (s.diff > peak.diff) peak = s;
    // half = timedelta(seconds=per/2): rounded to the microsecond once, then subtracted / added
    const half = addSeconds(0, perNight / 2.0);
    let wStart = peak.t - half, wEnd = peak.t + half;
    if (wStart < night[0].t) { wStart = night[0].t; wEnd = addSeconds(night[0].t, perNight); }
    if (wEnd > night[night.length - 1].t) { wEnd = night[night.length - 1].t; wStart = night[night.length - 1].t - addSeconds(0, perNight); }
    if (diffSeconds(finish, wEnd) < minRideAfterS) return;
    const km = kmAtTime(knots, wStart);
    const after = controls.filter(c => Number(c.distance_km) >= km && c.margin_s !== null && c.margin_s !== undefined);
    const tightest = after.length ? Math.min(...after.map(c => Number(c.margin_s))) : null;
    const cutoffOk = tightest === null || tightest > perNight;
    const finishKm = controls.length ? Math.max(...controls.map(c => Number(c.distance_km))) : km;
    let near: string | null = null;
    let cand = controls[0], cd = Infinity;
    for (const c of controls) { const d = Math.abs(Number(c.distance_km) - km); if (d < cd) { cd = d; cand = c; } }
    if (Math.abs(Number(cand.distance_km) - km) <= 20.0 && Number(cand.distance_km) < finishKm - 1.0) near = cand.label ?? null;
    const temp = tempAt(weather, km);
    const reasons = ['circadian low'];
    if (peak.dark) reasons.push('dark');
    if (peak.moon >= 0.5) reasons.push(peak.moon >= 0.8 ? 'bright moon' : 'half moon');
    if (temp !== null && temp <= 8) reasons.push(`cold (${pyRound(temp)}°C)`);
    windows.push({
      night: idx + 1, start_local: hhmm(wStart), end_local: hhmm(wEnd), start_dt: iso(wStart),
      duration_s: pyRound(diffSeconds(wEnd, wStart)), km: pyRound(km, 1), near_control: near,
      temp_c: temp === null ? null : pyRound(Number(temp), 1), moon_illumination: peak.moon,
      reason: reasons.join(', '), cutoff_ok: cutoffOk,
      tightest_margin_after_s: tightest === null ? null : pyRound(tightest),
    });
  });
  const total = windows.reduce((a, w) => a + w.duration_s, 0);
  return { ok: true, windows, n_nights: nights.length, total_sleep_s: total };
}

/** race_sleep.py main() */
export function sleepFromBody(body: any): any {
  try {
    let points: RoutePt[] = body.points;
    if ((!points || !points.length) && body.gpx) points = parseGpxPoints(body.gpx);
    const start = typeof body.start_dt === 'string' ? parseIso(body.start_dt) : body.start_dt;
    return planSleep(body.controls || [], points || [], start, Number(body.tz || 0.0),
                     Number(body.suggested_total_s || 0.0), body.weather ?? null);
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}
