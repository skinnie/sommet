// SuuntoLink's Douglas-Peucker simplification, ported from sommet's
// tools/ambit_simplify.py (reconstructed there from route_simplifier.js and
// verified byte-for-byte against real captures: 1066->336 and 2911->852
// points, both reproducing the captured point body exactly).
//
// Kept in TypeScript rather than ported to C/JNI: the algorithm only needs
// the point array (already available in JS from GPX parsing) and produces
// an index list to keep, so there's no reason to cross the JNI boundary
// before this runs — only the already-simplified points cross it.

const START_TOLERANCE_M = 2.0;
const MAX_TOLERANCE_M = 131072.0;
const SIMPLIFY_RADIUS_M = 6378100.0; // indicative only, not discriminated by the fixtures

export interface LatLon {
  latitude: number;
  longitude: number;
}

function perpendicularDistance(
  ax: number, ay: number, bx: number, by: number, px: number, py: number
): number {
  const dx = bx - ax;
  const dy = by - ay;
  const squared = dx * dx + dy * dy;
  if (squared === 0) return Math.hypot(px - ax, py - ay);
  const t = ((px - ax) * dx + (py - ay) * dy) / squared;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function project(points: LatLon[], latRef: number, radius: number): [number, number][] {
  const cosRef = Math.cos((latRef * Math.PI) / 180);
  return points.map(p => [
    radius * cosRef * ((p.longitude * Math.PI) / 180),
    radius * ((p.latitude * Math.PI) / 180),
  ]);
}

/** Kept indices. Indices in `forced` (waypoints) are always kept and split the
 *  track into segments processed independently — same as the Python reference. */
export function douglasPeucker(
  points: LatLon[], tolerance: number, forced: number[] = [], latRef?: number,
  radius = SIMPLIFY_RADIUS_M
): number[] {
  const n = points.length;
  if (n <= 2) return Array.from({ length: n }, (_, i) => i);

  const lat0 = latRef ?? points.reduce((s, p) => s + p.latitude, 0) / n;
  const xy = project(points, lat0, radius);

  const keep = new Array<boolean>(n).fill(true);
  const forcedSorted = Array.from(new Set(forced.filter(i => i > 0 && i < n - 1))).sort((a, b) => a - b);
  const bounds = [0, ...forcedSorted, n - 1];
  const stack: [number, number][] = [];
  for (let i = 0; i < bounds.length - 1; i++) stack.push([bounds[i], bounds[i + 1]]);

  while (stack.length > 0) {
    const [first, last] = stack.pop()!;
    if (last <= first + 1) continue;
    const [ax, ay] = xy[first];
    const [bx, by] = xy[last];
    let worst = -1;
    let worstAt = -1;
    for (let i = first + 1; i < last; i++) {
      const [px, py] = xy[i];
      const gap = perpendicularDistance(ax, ay, bx, by, px, py);
      if (gap > worst) { worst = gap; worstAt = i; }
    }
    if (worst > tolerance) {
      stack.push([first, worstAt]);
      stack.push([worstAt, last]);
    } else {
      for (let i = first + 1; i < last; i++) keep[i] = false;
    }
  }
  return keep.reduce<number[]>((acc, k, i) => { if (k) acc.push(i); return acc; }, []);
}

/**
 * Indices kept after the doubling-tolerance loop. Returns null if the route
 * cannot be reduced under `maxPoints` even at the tolerance ceiling.
 */
export function simplifyRoute(
  points: LatLon[], maxPoints: number, forced: number[] = [], latRef?: number
): number[] | null {
  if (points.length <= maxPoints) return points.map((_, i) => i);
  let tolerance = START_TOLERANCE_M;
  while (true) {
    const kept = douglasPeucker(points, tolerance, forced, latRef);
    if (kept.length <= maxPoints) return kept;
    tolerance *= 2;
    if (tolerance > MAX_TOLERANCE_M) return null;
  }
}
