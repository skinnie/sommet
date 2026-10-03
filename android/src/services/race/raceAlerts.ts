// Twin of tools/race_alerts.py: the one ranked "critical points" list - major climbs from the route,
// cutoff risk, too-early arrivals, darkness, water / food gaps - by severity then km. Parity-tested.
import { cumulativeDistances, parseGpxPoints, RoutePt } from './geo';
import { pyRound, pyFixed } from './pyCompat';

const SEV_RANK: Record<string, number> = { critical: 0, warn: 1, info: 2 };
const empty = (e: any) => e === null || e === undefined || e === '';

function resampleEle(points: RoutePt[], cumul: number[], stepM = 100.0): Array<[number, number]> {
  let eles: any[] = points.map(p => p.ele);
  if (eles.some(empty)) {
    const firstKnown = eles.find(e => !empty(e));
    if (firstKnown === undefined) return [];
    const last = Number(firstKnown);
    eles = eles.map(e => (!empty(e) ? Number(e) : last));
  } else eles = eles.map(Number);
  const total = cumul[cumul.length - 1];
  const out: Array<[number, number]> = [];
  let d = 0.0, j = 0;
  while (d <= total) {
    while (j < cumul.length - 1 && cumul[j + 1] < d) j++;
    if (j >= cumul.length - 1) { out.push([d, eles[eles.length - 1]]); break; }
    const span = cumul[j + 1] - cumul[j];
    const f = span <= 0 ? 0.0 : (d - cumul[j]) / span;
    out.push([d, eles[j] + f * (eles[j + 1] - eles[j])]);
    d += stepM;
  }
  return out;
}

export function findClimbs(points: RoutePt[], cumul: number[]) {
  const s = resampleEle(points, cumul);
  if (s.length < 3) return [];
  const DROP_RESET = 20.0, CLIMB_START = 10.0;
  const climbs: any[] = [];
  const close = (lo: number, hi: number) => {
    const gain = s[hi][1] - s[lo][1], len = s[hi][0] - s[lo][0];
    if (gain >= 120 && len > 0) climbs.push({ start_km: s[lo][0] / 1000.0, top_km: s[hi][0] / 1000.0,
                                              gain_m: pyRound(gain), avg_grade: pyRound(100.0 * gain / len, 1) });
  };
  let low = 0, hi = 0, climbing = false;
  for (let i = 1; i < s.length; i++) {
    if (!climbing) {
      if (s[i][1] <= s[low][1]) { low = hi = i; }
      else if (s[i][1] - s[low][1] >= CLIMB_START) { climbing = true; hi = i; }
      else hi = i;
    } else {
      if (s[i][1] >= s[hi][1]) hi = i;
      else if (s[hi][1] - s[i][1] >= DROP_RESET) { close(low, hi); low = hi = i; climbing = false; }
    }
  }
  if (climbing) close(low, hi);
  return climbs;
}

function hm(seconds: number): string {
  const s = Math.trunc(Math.abs(seconds));
  return `${Math.floor(s / 3600)}h${String(Math.floor((s % 3600) / 60)).padStart(2, '0')}`;
}

export function buildAlerts(points: RoutePt[], timeline: any = null, weather: any = null, pois: any = null): any {
  if (points.length < 2) return { ok: false, error: 'route needs >= 2 points' };
  const cumul = cumulativeDistances(points.map(p => [p.lat, p.lon] as [number, number]));
  const alerts: any[] = [];
  for (const c of findClimbs(points, cumul)) {
    const steep = c.avg_grade >= 6.0, warn = c.avg_grade >= 5.0 || c.gain_m >= 600;
    const label = steep ? 'Steep climb' : (c.gain_m >= 400 ? 'Big climb' : 'Climb');
    alerts.push({ km: pyRound(c.start_km, 1), kind: 'climb', severity: warn ? 'warn' : 'info',
                  text: `${label} from km ${pyFixed(c.start_km, 0)}: +${Math.trunc(c.gain_m)} m at ${pyFixed(c.avg_grade, 1)}%` });
  }
  for (const r of (timeline || {}).controls || []) {
    const m = r.margin_s;
    if (m === null || m === undefined) continue;
    if (m < 0) alerts.push({ km: r.distance_km, kind: 'cutoff', severity: 'critical', text: `${r.label ?? 'None'}: OVER cutoff by ${hm(-m)}` });
    else if (m < 3600) alerts.push({ km: r.distance_km, kind: 'cutoff', severity: 'warn', text: `${r.label ?? 'None'}: tight cutoff — only ${hm(m)} margin` });
  }
  for (const r of (timeline || {}).controls || []) {
    const e = r.early_s;
    if (e === null || e === undefined || e <= 600) continue;
    alerts.push({ km: r.distance_km, kind: 'early', severity: e >= 3600 ? 'warn' : 'info',
                  text: `${r.label ?? 'None'}: arrive ${hm(e)} before it opens — you'd wait` });
  }
  for (const c of (weather || {}).controls || []) {
    if (c.is_dark) alerts.push({ km: c.km ?? 0, kind: 'dark', severity: 'info',
                                 text: `${c.label || `km ${pyFixed(c.km ?? 0, 0)}`}: reached after dark` });
  }
  const cats = (pois || {}).categories || {};
  const w = cats.water;
  if (w && (w.count ?? 0) >= 0) {
    if ((w.count ?? 0) === 0) {
      alerts.push({ km: 0, kind: 'water', severity: 'critical', text: 'No drinking water found on route — carry/plan resupply' });
    } else if ((w.longest_gap_km ?? 0) >= 40) {
      const sev = w.longest_gap_over_carry ? 'critical' : 'warn';
      const extra = w.longest_gap_over_carry ? ` — needs ~${pyFixed(w.longest_gap_litres ?? 0, 1)} L (> ${pyFixed(w.carry_l ?? 0, 1)} carried)` : '';
      alerts.push({ km: w.longest_gap_after_km ?? 0, kind: 'water', severity: sev,
                    text: `${pyFixed(w.longest_gap_km, 0)} km without water after km ${pyFixed(w.longest_gap_after_km ?? 0, 0)}${extra}` });
    }
  }
  const f = cats.food;
  if (f && (f.longest_gap_km ?? 0) >= 80) {
    alerts.push({ km: f.longest_gap_after_km ?? 0, kind: 'food', severity: 'warn',
                  text: `No food for ${pyFixed(f.longest_gap_km, 0)} km after km ${pyFixed(f.longest_gap_after_km ?? 0, 0)}` });
  }
  const sorted = alerts.map((a, i) => [a, i] as [any, number])
    .sort((x, y) => ((SEV_RANK[x[0].severity] ?? 3) - (SEV_RANK[y[0].severity] ?? 3)) || (x[0].km - y[0].km) || (x[1] - y[1]))
    .map(x => x[0]);
  const counts: Record<string, number> = {};
  for (const s of ['critical', 'warn', 'info']) counts[s] = sorted.filter(a => a.severity === s).length;
  return { ok: true, alerts: sorted, counts };
}

/** race_alerts.py main() */
export function alertsFromBody(body: any): any {
  try {
    let points: RoutePt[] = body.points;
    if ((!points || !points.length) && body.gpx) points = parseGpxPoints(body.gpx);
    return buildAlerts(points || [], body.timeline ?? null, body.weather ?? null, body.pois ?? null);
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}
