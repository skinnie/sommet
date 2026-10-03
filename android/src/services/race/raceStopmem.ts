// Twin of tools/race_stopmem.py (the pure part): learned per-distance off-bike hours. The store
// {points:[{km, hours}]} is passed in and handed back, so the logic is testable against the Python;
// the app keeps it in AsyncStorage (raceStorage.ts), the desktop in ~/.sommet/race_stops.json.
import { pyRound } from './pyCompat';

export interface StopStore { points: Array<{ km: number; hours: number }> }

const DEFAULT: Array<[number, number]> = [[120, 0.5], [200, 1.0], [300, 1.75], [400, 3.0], [600, 7.0], [1000, 14.0], [1200, 18.0]];

function interp(points: Array<[number, number]>, km: number): number {
  const pts = points.slice().sort((a, b) => (a[0] - b[0]) || (a[1] - b[1]));
  if (km <= pts[0][0]) return pts[0][1];
  if (km >= pts[pts.length - 1][0]) {
    const [x0, y0] = pts[pts.length - 2], [x1, y1] = pts[pts.length - 1];
    const slope = x1 !== x0 ? (y1 - y0) / (x1 - x0) : 0.0;
    return Math.max(y1, y1 + slope * (km - x1));
  }
  for (let i = 1; i < pts.length; i++) {
    if (km <= pts[i][0]) {
      const [x0, y0] = pts[i - 1], [x1, y1] = pts[i];
      const f = x1 !== x0 ? (km - x0) / (x1 - x0) : 0.0;
      return y0 + f * (y1 - y0);
    }
  }
  return pts[pts.length - 1][1];
}

export function suggest(km: number, store: StopStore) {
  const user = (store.points || []).filter(p => p.hours !== null && p.hours !== undefined)
    .map(p => [Number(p.km), Number(p.hours)] as [number, number]);
  if (user.length >= 2) return { ok: true, hours: pyRound(interp(user, km), 2), source: 'learned', n_points: user.length };
  if (user.length === 1) {
    const [ukm, uh] = user[0];
    const d = interp(DEFAULT, ukm);
    const scale = d > 0 ? uh / d : 1.0;
    return { ok: true, hours: pyRound(interp(DEFAULT, km) * scale, 2), source: 'learned', n_points: 1 };
  }
  return { ok: true, hours: pyRound(interp(DEFAULT, km), 2), source: 'default', n_points: 0 };
}

/** Returns [result, new store]. One point per ~25 km band, latest wins. */
export function record(km: number, hours: number, store: StopStore): [any, StopStore] {
  const band = pyRound(km / 25.0) * 25.0;
  const pts = (store.points || []).filter(p => pyRound(Number(p.km) / 25.0) * 25.0 !== band);
  pts.push({ km: pyRound(km, 1), hours: pyRound(Number(hours), 2) });
  const sorted = pts.map((p, i) => [p, i] as [typeof p, number]).sort((a, b) => (a[0].km - b[0].km) || (a[1] - b[1])).map(x => x[0]);
  return [{ ok: true, n_points: sorted.length }, { ...store, points: sorted }];
}
