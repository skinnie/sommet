import { analyzePins, makePins, etrexPinsGpx, PinPoint } from './RoutePins';
import { parseRouteGpx, parseGpxWaypoints } from './RouteGpxParser';

// Garmin eTrex 30/30x/32x helper - TypeScript port of tools/etrex_export.py (keep the two in step).
// The eTrex draws a track as a bare line, so:
//   'track' -> the original track, point for point, + direction pins as waypoints, only where the
//              route meets itself (rules and hardware history in RoutePins.ts / tools/route_pins.py);
//   'route' -> <= maxVia via points: start/end, every decision spot, then Douglas-Peucker importance.

const MAX_WAYPOINTS = 2000; // eTrex 30 stores 2000 waypoints

type XY = [number, number];

export interface EtrexStats {
  mode: 'track' | 'route'; km: number; pointsIn: number; pointsOut: number;
  junctions: number; decisions: number; turnarounds: number; pins: number;
  ignoredSpikes: number; waypointsDropped: number;
}
export interface EtrexResult { gpx: string; stats: EtrexStats }

function worst(xy: XY[], a: number, b: number): [number, number] {
  const [ax, ay] = xy[a]; const [bx, by] = xy[b];
  const dx = bx - ax; const dy = by - ay; const sq = dx * dx + dy * dy;
  let best = -1; let at = -1;
  for (let i = a + 1; i < b; i++) {
    const [px, py] = xy[i];
    let d: number;
    if (sq === 0) d = Math.hypot(px - ax, py - ay);
    else { const t = ((px - ax) * dx + (py - ay) * dy) / sq; d = Math.hypot(px - (ax + t * dx), py - (ay + t * dy)); }
    if (d > best) { best = d; at = i; }
  }
  return [best, at];
}

/** Up to `budget` extra indices beyond `forced`, always the worst-fitting point next. */
function dpRank(xy: XY[], forced: number[], budget: number): number[] {
  const n = xy.length;
  const keep = new Set<number>([0, n - 1, ...forced.filter(i => i >= 0 && i < n)]);
  const bounds = [...keep].sort((a, b) => a - b);
  const heap: { d: number; a: number; b: number; at: number }[] = [];
  const push = (e: { d: number; a: number; b: number; at: number }) => {
    heap.push(e);
    let c = heap.length - 1;
    while (c > 0) {
      const p = (c - 1) >> 1;
      if (heap[p].d >= heap[c].d) break;
      [heap[p], heap[c]] = [heap[c], heap[p]]; c = p;
    }
  };
  const pop = () => {
    const top = heap[0]; const last = heap.pop()!;
    if (heap.length) {
      heap[0] = last;
      let c = 0;
      for (;;) {
        const l = 2 * c + 1; const r = l + 1; let m = c;
        if (l < heap.length && heap[l].d > heap[m].d) m = l;
        if (r < heap.length && heap[r].d > heap[m].d) m = r;
        if (m === c) break;
        [heap[m], heap[c]] = [heap[c], heap[m]]; c = m;
      }
    }
    return top;
  };
  for (let k = 0; k + 1 < bounds.length; k++) {
    const a = bounds[k]; const b = bounds[k + 1];
    if (b > a + 1) { const [d, at] = worst(xy, a, b); push({ d, a, b, at }); }
  }
  let extra = 0;
  while (heap.length && extra < budget) {
    const { a, b, at } = pop();
    keep.add(at); extra++;
    for (const [lo, hi] of [[a, at], [at, b]] as [number, number][]) {
      if (hi > lo + 1) { const [d, i] = worst(xy, lo, hi); push({ d, a: lo, b: hi, at: i }); }
    }
  }
  return [...keep].sort((a, b) => a - b);
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

export interface EtrexOptions {
  mode: 'track' | 'route'; name?: string; maxTrack?: number; maxVia?: number; reverse?: boolean;
}

/** Throws on unreadable / point-less GPX (callers surface the message). */
export function buildEtrexGpx(gpxXml: string, opts: EtrexOptions): EtrexResult {
  const { mode, maxTrack = 10000, maxVia = 50 } = opts;
  const parsed = parseRouteGpx(gpxXml, opts.name ?? 'eTrex route');
  let pts: PinPoint[] = parsed.points.map(p => ({ lat: p.latitude, lon: p.longitude, ele: p.elevation }));
  if (pts.length < 2) throw new Error('No track points found in this GPX');
  if (opts.reverse) pts = [...pts].reverse();
  const label = opts.name ?? parsed.name;
  const an = analyzePins(pts);
  let pins = makePins(an, 'etrex');
  const dropped = Math.max(0, pins.length - MAX_WAYPOINTS);
  if (dropped) pins = pins.filter(p => p.kind !== 'ok').slice(0, MAX_WAYPOINTS); // the OK backups go first
  const decisions = an.events.filter(e => e.kind === 'decision');
  const base = {
    mode, km: Math.round(an.al[an.al.length - 1] / 10) / 100, pointsIn: pts.length,
    junctions: new Set(decisions.map(e => e.jid)).size, decisions: decisions.length,
    turnarounds: an.events.length - decisions.length, ignoredSpikes: an.spikes, waypointsDropped: dropped,
  };

  if (mode === 'track') {
    const out = pts.length > maxTrack ? dpRank(an.xy, [], maxTrack - 2).map(i => pts[i]) : pts; // thinned only then
    let extra: { lat: number; lon: number; name: string }[] = [];
    if (!opts.reverse) {
      try { extra = parseGpxWaypoints(gpxXml).map(w => ({ lat: w.latitude, lon: w.longitude, name: w.name })); } catch { extra = []; }
    }
    return { gpx: etrexPinsGpx(out, pins, label, extra), stats: { ...base, pointsOut: out.length, pins: pins.length } };
  }

  const named = new Map<number, string>();
  for (const p of pins) {
    if (p.kind === 'ok') continue;
    let q = 0;
    for (let r = 1; r < an.al.length; r++) if (Math.abs(an.al[r] - p.along) < Math.abs(an.al[q] - p.along)) q = r;
    named.set(q, p.name);
  }
  const forced = [...named.keys()].sort((a, b) => a - b).slice(0, Math.max(0, maxVia - 2));
  const idx = dpRank(an.res, forced, Math.max(0, maxVia - 2 - forced.length));
  const rows = idx.map(i => {
    const [lat, lon] = an.ll(an.res[i]);
    const nm = forced.includes(i) ? `<name>${esc(named.get(i)!)}</name>` : '';
    return `    <rtept lat="${lat.toFixed(6)}" lon="${lon.toFixed(6)}">${nm}</rtept>`;
  });
  return {
    gpx: '<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n'
      + `  <rte><name>${esc(label)}</name>\n${rows.join('\n')}\n  </rte>\n</gpx>\n`,
    stats: { ...base, pointsOut: idx.length, pins: 0 },
  };
}
