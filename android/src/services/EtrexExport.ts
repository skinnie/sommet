import { parseRouteGpx } from './RouteGpxParser';

// Garmin eTrex 30/30x/32x helper - TypeScript port of tools/etrex_export.py (same thresholds; keep the
// two in step). A track has no turn guidance on these units and a route holds only ~50 via points, so:
//   'track' -> the full geometry (thinned only past maxTrack points) + named waypoints at every turn
//              ("R 12.4") and wherever the track meets itself ("X STR 3.2");
//   'route' -> <= maxVia via points placed at start/end, both sides of crossings, the sharpest turns,
//              then Douglas-Peucker importance - the device's routable map calculates the guidance.

const STEP_M = 10;
const LOOK = 3;
const MIN_TURN_DEG = 40;
const TURN_GAP_M = 40;
const CROSS_M = 15;
const CROSS_MIN_ALONG_M = 150;
const RETRACE_MIN_M = 150; // a self-proximity run this long along the track is a genuine
// out-and-back (the same physical road ridden in opposite directions), not a point-like
// junction - "bear left/right" at its edges is the wrong vocabulary.
const RETRACE_ANTIPARALLEL_DEG = 120; // the sibling pass has to head close to the opposite
// way, not just "some other angle", or this is a Y-junction that stays close for a while
// before diverging, not a real retrace - no marker either way.
const CROSS_WPT_OFFSET_M = 18; // place a crossing's <wpt> this far past the junction, along
// THAT pass's own outgoing branch - not at the junction itself. Two passes through the same
// spot diverge afterwards, so offsetting each downstream separates the two pins on the map
// instead of leaving them stacked (real hardware finding, Andre, 2026-09-25).
const MAX_WAYPOINTS = 2000;

type XY = [number, number];
interface Pt { lat: number; lon: number; ele: number | null }
interface Mark {
  i: number; wptI?: number; km: number; delta: number; label: string; kind: 'turn' | 'crossing';
  name: string; desc: string; sym: string; event?: number; passNo?: number; otherKm?: number[];
  kindHint?: 'retrace'; edge?: 'starts' | 'ends';
}

// Distinct per-pass symbol - a cue that survives GPS drift and screen zoom, unlike relying on
// the offset position alone (untested assumption: whether the eTrex actually renders these
// Garmin symbol names as visually distinct icons - confirm on hardware). Cycles for a 3rd+ pass
// through the same spot (rare - only a real triple self-crossing would hit it).
const PASS_SYM = ['Flag, Green', 'Flag, Yellow', 'Flag, Red', 'Flag, Blue'];

function ordinalWord(n: number): string {
  const suffix = (n % 100 >= 10 && n % 100 <= 20) ? 'th' : (['th', 'st', 'nd', 'rd'][n % 10] ?? 'th');
  return `${n}${suffix}`;
}

export interface EtrexStats {
  mode: 'track' | 'route'; km: number; pointsIn: number; pointsOut: number;
  turns: number; crossings: number; waypoints: number; waypointsDropped: number;
}
export interface EtrexResult { gpx: string; stats: EtrexStats }

function project(pts: Pt[]): XY[] {
  const lat0 = pts.reduce((s, p) => s + p.lat, 0) / pts.length;
  const kx = Math.cos((lat0 * Math.PI) / 180) * 111320;
  return pts.map(p => [p.lon * kx, p.lat * 110540]);
}

function chordDist(xy: XY[], a: number, b: number, i: number): number {
  const [ax, ay] = xy[a]; const [bx, by] = xy[b]; const [px, py] = xy[i];
  const dx = bx - ax; const dy = by - ay; const sq = dx * dx + dy * dy;
  if (sq === 0) return Math.hypot(px - ax, py - ay);
  const t = ((px - ax) * dx + (py - ay) * dy) / sq;
  return Math.hypot(px - (ax + t * dx), py - (ay + t * dy));
}

function worst(xy: XY[], a: number, b: number): [number, number] {
  let best = -1; let at = -1;
  for (let i = a + 1; i < b; i++) {
    const d = chordDist(xy, a, b, i);
    if (d > best) { best = d; at = i; }
  }
  return [best, at];
}

/** Kept indices within a metre tolerance (iterative Douglas-Peucker). */
function toleranceFilter(xy: XY[], tol: number): number[] {
  const n = xy.length;
  const keep = new Array<boolean>(n).fill(false);
  keep[0] = keep[n - 1] = true;
  const stack: [number, number][] = [[0, n - 1]];
  while (stack.length) {
    const [a, b] = stack.pop()!;
    if (b <= a + 1) continue;
    const [d, at] = worst(xy, a, b);
    if (d > tol) { keep[at] = true; stack.push([a, at], [at, b]); }
  }
  return keep.reduce<number[]>((o, k, i) => { if (k) o.push(i); return o; }, []);
}

/** Up to `budget` extra indices beyond `forced`, always the worst-fitting point next. */
function dpRank(xy: XY[], forced: number[], budget: number): number[] {
  const n = xy.length;
  const keep = new Set<number>([0, n - 1, ...forced.filter(i => i >= 0 && i < n)]);
  const bounds = [...keep].sort((a, b) => a - b);
  // tiny binary max-heap on deviation
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

function resample(xy: XY[], pts: Pt[], step: number): { xy: XY[]; pts: Pt[]; along: number[] } {
  const oxy: XY[] = [xy[0]]; const opt: Pt[] = [{ ...pts[0] }]; const od = [0];
  let travelled = 0; let nextAt = step;
  for (let i = 1; i < xy.length; i++) {
    const [x0, y0] = xy[i - 1]; const [x1, y1] = xy[i];
    const seg = Math.hypot(x1 - x0, y1 - y0);
    if (seg === 0) continue;
    while (nextAt <= travelled + seg) {
      const f = (nextAt - travelled) / seg;
      oxy.push([x0 + (x1 - x0) * f, y0 + (y1 - y0) * f]);
      const a = pts[i - 1]; const b = pts[i];
      opt.push({
        lat: a.lat + (b.lat - a.lat) * f, lon: a.lon + (b.lon - a.lon) * f,
        ele: a.ele === null || b.ele === null ? null : a.ele + (b.ele - a.ele) * f,
      });
      od.push(nextAt); nextAt += step;
    }
    travelled += seg;
  }
  if (od[od.length - 1] < travelled - 1) { oxy.push(xy[xy.length - 1]); opt.push({ ...pts[pts.length - 1] }); od.push(travelled); }
  return { xy: oxy, pts: opt, along: od };
}

const bearing = (a: XY, b: XY) => (Math.atan2(b[0] - a[0], b[1] - a[1]) * 180) / Math.PI;
function delta(bin: number, bout: number): number {
  const d = ((((bout - bin + 180) % 360) + 360) % 360) - 180;
  return d === -180 ? 180 : d;
}
function headingChange(xy: XY[], a: number, b: number): number {
  const i0 = Math.max(0, a - LOOK); const i1 = Math.min(xy.length - 1, b + LOOK);
  if (i0 === a || i1 === b) return 0;
  return delta(bearing(xy[i0], xy[a]), bearing(xy[b], xy[i1]));
}
function label(d: number): string {
  const a = Math.abs(d); const side = d > 0 ? 'R' : 'L';
  if (a >= 165) return 'U';
  if (a >= 120) return `SH${side}`;
  if (a >= 60) return side;
  if (a >= 20) return `S${side}`;
  return 'STR';
}
const WORDING: Record<string, string> = {
  L: 'Turn left', R: 'Turn right', SL: 'Bear left', SR: 'Bear right',
  SHL: 'Sharp left', SHR: 'Sharp right', U: 'U-turn', STR: 'Go straight',
};
// Spelled out, not the raw L/R/SL/SR code - a rider glancing at the map has no legend and the
// fuller desc text isn't reliably shown by the eTrex UI (confirmed on hardware, 2026-09-25: only
// the <name> is visible while navigating), so the visible name has to be self-explanatory alone.
const NAME_WORD: Record<string, string> = {
  L: 'Left', R: 'Right', SL: 'Bear left', SR: 'Bear right',
  SHL: 'Sharp left', SHR: 'Sharp right', U: 'U-turn', STR: 'Straight',
};

function findTurns(xy: XY[], along: number[], minDeg: number) {
  const n = xy.length;
  if (n < 2 * LOOK + 1) return [];
  const dl = new Array<number>(n).fill(0);
  for (let i = LOOK; i < n - LOOK; i++) dl[i] = delta(bearing(xy[i - LOOK], xy[i]), bearing(xy[i], xy[i + LOOK]));
  const cands: number[] = [];
  for (let i = LOOK; i < n - LOOK; i++) {
    if (Math.abs(dl[i]) >= minDeg && Math.abs(dl[i]) >= Math.abs(dl[i - 1]) && Math.abs(dl[i]) > Math.abs(dl[i + 1]) - 1e-9) cands.push(i);
  }
  cands.sort((a, b) => Math.abs(dl[b]) - Math.abs(dl[a]));
  const taken: number[] = [];
  for (const i of cands) if (taken.every(j => Math.abs(along[i] - along[j]) >= TURN_GAP_M)) taken.push(i);
  taken.sort((a, b) => a - b);
  return taken.map(i => ({ i, km: along[i] / 1000, delta: dl[i], label: label(dl[i]) }));
}

/** The first index at or past `along[i] + offsetM` - i.e. `i` moved forward along THIS pass's
 *  own direction of travel (indices only increase in time/along-track order, so this can't
 *  accidentally jump onto a different pass through the same spot). Clipped to bounds. */
function offsetForward(along: number[], i: number, offsetM: number): number {
  const target = along[i] + offsetM;
  let j = i;
  while (j < along.length - 1 && along[j] < target) j++;
  return j;
}

function findCrossings(xy: XY[], along: number[], crossM: number, wptOffsetM: number = CROSS_WPT_OFFSET_M) {
  const cell = Math.max(crossM, 1);
  const grid = new Map<string, number[]>();
  xy.forEach(([x, y], i) => {
    const k = `${Math.floor(x / cell)},${Math.floor(y / cell)}`;
    const l = grid.get(k); if (l) l.push(i); else grid.set(k, [i]);
  });
  const near = new Set<number>();
  const links: [number, number][] = [];
  xy.forEach(([x, y], i) => {
    const cx = Math.floor(x / cell); const cy = Math.floor(y / cell);
    for (let gx = cx - 1; gx <= cx + 1; gx++) for (let gy = cy - 1; gy <= cy + 1; gy++) {
      for (const j of grid.get(`${gx},${gy}`) ?? []) {
        if (j <= i || along[j] - along[i] < CROSS_MIN_ALONG_M) continue;
        if (Math.hypot(xy[j][0] - x, xy[j][1] - y) <= crossM) { near.add(i); near.add(j); links.push([i, j]); }
      }
    }
  });
  if (!near.size) return [];
  const runs: number[][] = [];
  for (const i of [...near].sort((a, b) => a - b)) {
    const last = runs[runs.length - 1];
    if (last && i - last[last.length - 1] <= 2) last.push(i); else runs.push([i]);
  }
  const runOf = new Map<number, number>();
  runs.forEach((r, k) => r.forEach(i => runOf.set(i, k)));
  const parent = runs.map((_, k) => k);
  const find = (a: number): number => { while (parent[a] !== a) { parent[a] = parent[parent[a]]; a = parent[a]; } return a; };
  for (const [i, j] of links) parent[find(runOf.get(i)!)] = find(runOf.get(j)!);
  const events = new Map<number, number[]>();
  runs.forEach((_, r) => { const f = find(r); const l = events.get(f); if (l) l.push(r); else events.set(f, [r]); });
  const ordered = [...events.values()].sort((a, b) => runs[a[0]][0] - runs[b[0]][0]);
  const out: {
    i: number; wptI: number; km: number; delta: number; label: string; event: number; passNo: number;
    otherKm: number[]; kindHint?: 'retrace'; edge?: 'starts' | 'ends'; sym?: string;
  }[] = [];
  ordered.forEach((rs, evNo) => {
    const kms = rs.map(r => along[runs[r][0]] / 1000);
    // Passes in ride order - lets the 1st/2nd/3rd pass through this same spot get a visibly
    // different marker (see PASS_SYM below), a cue that survives GPS drift and screen zoom,
    // unlike relying on the offset position alone.
    const rsInOrder = [...rs].sort((a, b) => runs[a][0] - runs[b][0]);
    const runLength = new Map(rs.map(r => [r, along[runs[r][runs[r].length - 1]] - along[runs[r][0]]]));

    if (rs.some(r => (runLength.get(r) ?? 0) >= RETRACE_MIN_M)) {
      // A genuine out-and-back: this isn't "which fork do I take", it's "I'm about to ride a
      // stretch I've already ridden, the other way". Only flag it where a sibling run really is
      // heading close to the opposite way - a Y-junction that merely stays close for a while
      // before diverging isn't a retrace, and gets no marker at all here.
      const runBearing = new Map(rs.map(r => [r, bearing(xy[runs[r][0]], xy[runs[r][runs[r].length - 1]])]));
      rsInOrder.forEach((r, idx) => {
        const passNo = idx + 1;
        const a = runs[r][0]; const b = runs[r][runs[r].length - 1];
        const opposite = rs.some(r2 => r2 !== r && Math.abs(delta(runBearing.get(r)!, runBearing.get(r2)!)) >= RETRACE_ANTIPARALLEL_DEG);
        if (!opposite) return;
        const sym = PASS_SYM[(passNo - 1) % PASS_SYM.length];
        (['starts', 'ends'] as const).forEach(edge => {
          const i = edge === 'starts' ? a : b;
          out.push({
            i, wptI: i, km: along[i] / 1000, delta: 0, label: 'RETRACE', kindHint: 'retrace', edge, sym,
            event: evNo + 1, passNo,
            otherKm: kms.filter(k => Math.abs(k - along[a] / 1000) > 0.05).map(k => Math.round(k * 10) / 10),
          });
        });
      });
      return;
    }

    rsInOrder.forEach((r, idx) => {
      const passNo = idx + 1;
      const a = runs[r][0]; const b = runs[r][runs[r].length - 1];
      const spots = along[b] - along[a] <= 60 ? [Math.floor((a + b) / 2)] : [a, b];
      for (const i of spots) {
        const d = spots.length === 1 ? headingChange(xy, a, b) : headingChange(xy, i, i);
        out.push({
          i, wptI: offsetForward(along, i, wptOffsetM), km: along[i] / 1000, delta: d, label: label(d),
          event: evNo + 1, passNo,
          otherKm: kms.filter(k => Math.abs(k - along[a] / 1000) > 0.05).map(k => Math.round(k * 10) / 10),
        });
      }
    });
  });
  return out.sort((a, b) => a.i - b.i);
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const ele = (p: Pt) => (p.ele === null ? '' : `<ele>${p.ele.toFixed(1)}</ele>`);
const pt = (tag: string, p: Pt, inner = '') => `<${tag} lat="${p.lat.toFixed(6)}" lon="${p.lon.toFixed(6)}">${ele(p)}${inner}</${tag}>`;

export interface EtrexOptions {
  mode: 'track' | 'route'; name?: string; maxTrack?: number; maxVia?: number;
  minTurnDeg?: number; crossM?: number; wptOffsetM?: number;
}

/** Throws on unreadable / point-less GPX (callers surface the message). */
export function buildEtrexGpx(gpxXml: string, opts: EtrexOptions): EtrexResult {
  const { mode, maxTrack = 10000, maxVia = 50, minTurnDeg = MIN_TURN_DEG, crossM = CROSS_M,
          wptOffsetM = CROSS_WPT_OFFSET_M } = opts;
  const parsed = parseRouteGpx(gpxXml, opts.name ?? 'eTrex route');
  const pts: Pt[] = parsed.points.map(p => ({ lat: p.latitude, lon: p.longitude, ele: p.elevation }));
  if (pts.length < 2) throw new Error('No track points found in this GPX');
  const label0 = opts.name ?? parsed.name;

  const raw = project(pts);
  const quiet = toleranceFilter(raw, 2);
  const s = resample(quiet.map(i => raw[i]), quiet.map(i => pts[i]), STEP_M);

  const crossings = findCrossings(s.xy, s.along, crossM, wptOffsetM);
  const turns = findTurns(s.xy, s.along, minTurnDeg)
    .filter(t => crossings.every(c => Math.abs(s.along[t.i] - s.along[c.i]) > TURN_GAP_M));
  const totalKm = s.along[s.along.length - 1] / 1000;

  let marks: Mark[] = [];
  for (const t of turns) {
    const next = turns.find(u => u.km > t.km);
    marks.push({
      ...t, kind: 'turn', name: `${NAME_WORD[t.label]} ${t.km.toFixed(1)}`, sym: 'Flag, Blue',
      desc: `${WORDING[t.label]} at ${t.km.toFixed(1)} km${next ? `; next turn in ${(next.km - t.km).toFixed(1)} km` : `; then to the end (${totalKm.toFixed(1)} km)`}`,
    });
  }
  for (const c of crossings) {
    const ordinal = ordinalWord(c.passNo);
    if (c.kindHint === 'retrace') {
      // A genuine out-and-back, not a fork - "bear left/right" would be the wrong vocabulary
      // here, so name it for what it actually is.
      marks.push({
        ...c, kind: 'crossing', name: `${ordinal} Retrace ${c.edge} ${c.km.toFixed(1)}`, sym: c.sym!,
        desc: `You ride this same stretch again the other way (also at km ${c.otherKm.join(', ') || '-'}) - ${ordinal} time it ${c.edge} at ${c.km.toFixed(1)} km`,
      });
      continue;
    }
    marks.push({
      ...c, kind: 'crossing', name: `${ordinal} ${NAME_WORD[c.label]} ${c.km.toFixed(1)}`,
      sym: PASS_SYM[(c.passNo - 1) % PASS_SYM.length],
      desc: `Track crosses itself here (also at km ${c.otherKm.join(', ') || '-'}) - this is the ${ordinal} time: ${WORDING[c.label].toLowerCase()} at ${c.km.toFixed(1)} km`,
    });
  }
  marks.sort((a, b) => a.i - b.i);
  const dropped = Math.max(0, marks.length - MAX_WAYPOINTS);
  if (dropped) {
    marks = [...marks]
      .sort((a, b) => (a.kind === 'crossing' ? 0 : 1) - (b.kind === 'crossing' ? 0 : 1) || Math.abs(b.delta) - Math.abs(a.delta))
      .slice(0, MAX_WAYPOINTS).sort((a, b) => a.i - b.i);
  }

  const head = '<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n';
  let body: string; let pointsOut: number;
  if (mode === 'track') {
    const idx = pts.length > maxTrack ? dpRank(raw, [], maxTrack - 2) : pts.map((_, i) => i);
    // crossings carry wptI (offset past the junction, onto that pass's own branch); turns don't
    // need it, they're never co-located with another mark, so fall back to i.
    const wpts = marks.map(m => `  ${pt('wpt', s.pts[m.wptI ?? m.i], `<name>${esc(m.name)}</name><desc>${esc(m.desc)}</desc><sym>${esc(m.sym)}</sym>`)}`).join('\n');
    body = `${wpts}\n  <trk><name>${esc(label0)}</name><trkseg>\n${idx.map(i => `    ${pt('trkpt', pts[i])}`).join('\n')}\n  </trkseg></trk>`;
    pointsOut = idx.length;
  } else {
    const ranked = [...marks].sort((a, b) => (a.kind === 'crossing' ? 0 : 1) - (b.kind === 'crossing' ? 0 : 1) || Math.abs(b.delta) - Math.abs(a.delta));
    const forced = [...new Set(ranked.slice(0, Math.max(0, maxVia - 2)).map(m => m.i))].sort((a, b) => a - b);
    const idx = dpRank(s.xy, forced, Math.max(0, maxVia - 2 - forced.length));
    const byI = new Map(marks.map(m => [m.i, m]));
    body = `  <rte><name>${esc(label0)}</name>\n${idx.map(i => `    ${pt('rtept', s.pts[i], byI.has(i) ? `<name>${esc(byI.get(i)!.name)}</name>` : '')}`).join('\n')}\n  </rte>`;
    pointsOut = idx.length;
  }
  return {
    gpx: `${head}${body}\n</gpx>\n`,
    stats: {
      mode, km: Math.round(totalKm * 100) / 100, pointsIn: pts.length, pointsOut,
      turns: marks.filter(m => m.kind === 'turn').length,
      crossings: new Set(marks.filter(m => m.kind === 'crossing').map(m => m.event)).size,
      waypoints: mode === 'track' ? marks.length : 0, waypointsDropped: mode === 'track' ? dropped : 0,
    },
  };
}
