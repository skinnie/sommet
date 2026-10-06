import { parseRouteGpx, parseGpxWaypoints } from './RouteGpxParser';

// Direction pins for devices that draw a route as a bare line (Garmin eTrex 30, Suunto Ambit).
// TypeScript port of tools/route_pins.py - same rules, same constants, same output; keep the two
// in step (the jest tests pin the same cases as the Python selftest).
//
// A line on the screen is enough everywhere it simply bends. It stops being enough where the route
// meets itself: a crossing, the two ends of an out-and-back, the base of a lollipop loop. There
// you can take a wrong branch and still be "on the line". Those spots, and only those, get a pin.
// Rules agreed with André on real hardware (2026-09-30 .. 2026-10-05, eTrex 30 + Ambit3 Peak):
//   where      only where the route's own line has 3+ branches, plus "Turn back" at a turnaround
//   how many   one pin per visit, PIN_BEFORE_M before the junction on the path it arrives on
//   the word   which branch to take, lining the branches up as you ARRIVE (not the compass angle)
//   the name   "<word> <km>"
//   backup     "OK <km>" OK_AFTER_M after a decision unless the next pin is within OK_MIN_GAP_M
//   too close  pins closer than MERGE_M become one: "Back, left 1.2"

const STEP_M = 5;
const SAME_PATH_M = 12;
const MIN_SEP_M = 60;
const TIP_OVERLAP_M = 8;
const MIN_SPUR_M = 15;
const JUNCTION_MERGE_M = 35;
const PASS_RADIUS_M = 25;
const ARM_M = 25;
const BRANCH_MERGE_DEG = 35;
const STRAIGHT_DEG = 30;
const PIN_BEFORE_M = 20;
const PIN_CLEAR_M = 8;
const OK_AFTER_M = 100;
const OK_MIN_GAP_M = 150;
const MERGE_M = 30;
const AMBIT_NAME_BYTES = 15;
const AMBIT_MAX_PINS = 80; // the watch holds 100 route waypoints in total, shared by every route
const ABBREV: Record<string, string> = {
  Left: 'L', Right: 'R', Straight: 'S', Back: 'B', 'Slight left': 'SL', 'Slight right': 'SR', Start: 'Start',
};
const SYM: Record<string, string> = { decision: 'Flag, Blue', turn: 'Flag, Red', merged: 'Flag, Blue', ok: 'Pin, Green' };

type XY = [number, number];
export interface PinPoint { lat: number; lon: number; ele: number | null }
export type PinKind = 'start' | 'end' | 'decision' | 'turn' | 'merged' | 'ok';
export interface Pin { kind: PinKind; name: string; along: number; lat: number; lon: number }
interface PinEvent {
  kind: 'decision' | 'turn'; idx: number; km: number; word: string; jid: number;
}
export interface PinStats {
  target: 'etrex' | 'ambit'; km: number; pointsIn: number; junctions: number; decisions: number;
  turnarounds: number; pins: number; ignoredSpikes: number;
}
export interface PinResult { gpx: string; stats: PinStats; pins: Pin[] }

const bearing = (a: XY, b: XY) => ((Math.atan2(b[0] - a[0], b[1] - a[1]) * 180) / Math.PI + 360) % 360;
function turn(heading: number, brg: number): number {
  const d = ((((brg - heading + 180) % 360) + 360) % 360) - 180;
  return d === -180 ? 180 : d;
}
const dist = (a: XY, b: XY) => Math.hypot(a[0] - b[0], a[1] - b[1]);
const byteLen = (s: string) => unescape(encodeURIComponent(s)).length;

export interface PinAnalysis {
  pts: PinPoint[]; xy: XY[]; cum: number[]; res: XY[]; al: number[]; events: PinEvent[];
  spikes: number; ll: (p: XY) => [number, number];
}

export function analyzePins(pts: PinPoint[]): PinAnalysis {
  const lat0 = pts[0].lat; const lon0 = pts[0].lon;
  const kx = Math.cos((lat0 * Math.PI) / 180) * 111320; const ky = 110540;
  const xy: XY[] = pts.map(p => [(p.lon - lon0) * kx, (p.lat - lat0) * ky]);
  const cum = [0];
  for (let i = 1; i < xy.length; i++) cum.push(cum[i - 1] + dist(xy[i], xy[i - 1]));

  const res: XY[] = [xy[0]]; const al = [0];
  let trav = 0; let nxt = STEP_M;
  for (let i = 1; i < xy.length; i++) {
    const [x0, y0] = xy[i - 1]; const [x1, y1] = xy[i];
    const seg = Math.hypot(x1 - x0, y1 - y0);
    if (seg === 0) continue;
    while (nxt <= trav + seg) {
      const f = (nxt - trav) / seg;
      res.push([x0 + (x1 - x0) * f, y0 + (y1 - y0) * f]); al.push(nxt); nxt += STEP_M;
    }
    trav += seg;
  }
  if (al[al.length - 1] < trav - 0.5) { res.push(xy[xy.length - 1]); al.push(trav); }
  const n = res.length;

  // turnarounds: the route before and after a point stays together
  const tips: [number, number][] = [];
  for (let k = 1; k < n - 1; k++) {
    let m = 0;
    while (k - m - 1 >= 0 && k + m + 1 < n && dist(res[k - m - 1], res[k + m + 1]) <= TIP_OVERLAP_M) m++;
    if (m >= 2) tips.push([k, m]);
  }
  // stable sort by overlap, longest first (same order as Python's sorted)
  const sortedTips = tips.map((t, i) => ({ t, i })).sort((a, b) => b.t[1] - a.t[1] || a.i - b.i).map(o => o.t);
  let tipList: [number, number][] = [];
  for (const [k, m] of sortedTips) {
    if (tipList.every(([k2, m2]) => Math.abs(k - k2) > m2 + 2)) tipList.push([k, m]);
  }
  let spikes = 0;
  tipList = tipList.filter(([k, m]) => {
    let leg = 0;
    for (let q = 1; q <= m; q++) leg = Math.max(leg, dist(res[k], res[k - q]));
    if (leg < MIN_SPUR_M) { spikes++; return false; }
    return true;
  });

  // junction candidates: the base of each turnaround + both ends of every shared stretch
  const cand: XY[] = tipList.map(([k, m]) => [(res[k - m][0] + res[k + m][0]) / 2, (res[k - m][1] + res[k + m][1]) / 2]);
  const cell = SAME_PATH_M;
  const grid = new Map<string, number[]>();
  res.forEach((p, i) => {
    const key = `${Math.floor(p[0] / cell)},${Math.floor(p[1] / cell)}`;
    const l = grid.get(key); if (l) l.push(i); else grid.set(key, [i]);
  });
  const shared = new Array<boolean>(n).fill(false);
  for (let i = 0; i < n; i++) {
    const p = res[i]; const cx = Math.floor(p[0] / cell); const cy = Math.floor(p[1] / cell);
    search:
    for (let gx = cx - 1; gx <= cx + 1; gx++) for (let gy = cy - 1; gy <= cy + 1; gy++) {
      for (const j of grid.get(`${gx},${gy}`) ?? []) {
        if (Math.abs(al[j] - al[i]) >= MIN_SEP_M && dist(p, res[j]) <= SAME_PATH_M) { shared[i] = true; break search; }
      }
    }
  }
  for (let i = 0; i < n; i++) {
    if (shared[i] && (i === 0 || !shared[i - 1])) cand.push(res[i]);
    if (shared[i] && (i === n - 1 || !shared[i + 1])) cand.push(res[i]);
  }
  let clusters: XY[][] = [];
  for (const c of cand) {
    const hit = clusters.filter(cl => cl.some(o => dist(c, o) <= JUNCTION_MERGE_M));
    let merged: XY[] = [c];
    for (const cl of hit) merged = merged.concat(cl);
    clusters = clusters.filter(cl => !hit.includes(cl));
    clusters.push(merged);
  }
  const centers: XY[] = clusters.map(cl => [cl.reduce((s, p) => s + p[0], 0) / cl.length, cl.reduce((s, p) => s + p[1], 0) / cl.length]);

  const events: PinEvent[] = [];
  const armN = Math.floor(ARM_M / STEP_M);
  centers.forEach((jc, jid) => {
    const d = res.map(p => dist(p, jc));
    const passes: number[] = [];
    for (let i = 0; i < n; i++) {
      if (!(d[i] <= PASS_RADIUS_M && (i === 0 || d[i] <= d[i - 1]) && (i === n - 1 || d[i] < d[i + 1]))) continue;
      if (passes.length) {
        const prev = passes[passes.length - 1];
        let gapMax = 0;
        for (let q = prev; q <= i; q++) gapMax = Math.max(gapMax, d[q]);
        if (gapMax <= PASS_RADIUS_M && gapMax - Math.max(d[prev], d[i]) <= TIP_OVERLAP_M) {
          if (d[i] < d[prev]) passes[passes.length - 1] = i;
          continue;
        }
      }
      passes.push(i);
    }
    const arm = (c: number, direction: number): number | null => {
      let far = -1;
      if (direction < 0) { for (let r = c - 1; r > Math.max(-1, c - armN - 1); r--) if (far < 0 || d[r] > d[far]) far = r; }
      else { for (let r = c + 1; r < Math.min(n, c + armN + 1); r++) if (far < 0 || d[r] > d[far]) far = r; }
      if (far < 0 || d[far] < 6) return null;
      return bearing(jc, res[far]);
    };
    const arms = passes.map(c => [c, arm(c, -1), arm(c, +1)] as [number, number | null, number | null]);
    const branches: number[] = [];
    for (const [, a, b] of arms) for (const brg of [a, b]) {
      if (brg !== null && !branches.some(o => Math.abs(turn(o, brg)) <= BRANCH_MERGE_DEG)) branches.push(brg);
    }
    if (branches.length < 3) return;
    const brOf = (brg: number) => {
      let best = 0;
      for (let q = 1; q < branches.length; q++) if (Math.abs(turn(branches[q], brg)) < Math.abs(turn(branches[best], brg))) best = q;
      return best;
    };
    for (const [c, a, b] of arms) {
      if (a === null || b === null) continue; // the route starts or ends here: nothing to decide
      const arr = brOf(a); const dep = brOf(b);
      const heading = (a + 180) % 360;
      let word: string;
      if (arr === dep) word = 'Turn back';
      else {
        const opts = branches.map((brg, q) => ({ t: turn(heading, brg), q })).filter(o => o.q !== arr)
          .sort((x, y) => x.t - y.t || x.q - y.q);
        const rank = opts.findIndex(o => o.q === dep);
        const chosen = opts[rank].t;
        if (opts.length === 2) {
          const other = opts[1 - rank].t;
          word = Math.abs(chosen) <= STRAIGHT_DEG && STRAIGHT_DEG < Math.abs(other) ? 'Straight' : rank === 0 ? 'Left' : 'Right';
        } else if (opts.length === 3) word = ['Left', 'Straight', 'Right'][rank];
        else if (rank === 0) word = 'Left';
        else if (rank === opts.length - 1) word = 'Right';
        else word = Math.abs(chosen) <= STRAIGHT_DEG ? 'Straight' : chosen < 0 ? 'Slight left' : 'Slight right';
      }
      events.push({ kind: 'decision', idx: c, km: al[c] / 1000, word, jid });
    }
  });
  for (const [k] of tipList) events.push({ kind: 'turn', idx: k, km: al[k] / 1000, word: 'Turn back', jid: -1 });
  // stable sort by km (decisions were pushed before turnarounds, as in Python)
  const ordered = events.map((e, i) => ({ e, i })).sort((a, b) => a.e.km - b.e.km || a.i - b.i).map(o => o.e);
  return { pts, xy, cum, res, al, events: ordered, spikes, ll: p => [lat0 + p[1] / ky, lon0 + p[0] / kx] };
}

function shortName(words: string[], km: string | null, limit: number | null): string {
  const w = [words.length > 1 ? words[0].replace('Turn back', 'Back') : words[0],
    ...words.slice(1).map(x => x.replace('Turn back', 'back').toLowerCase())];
  const full = w.join(', ') + (km ? ` ${km}` : '');
  if (limit === null || byteLen(full) <= limit) return full;
  if (byteLen(w.join(', ')) <= limit) return w.join(', ');
  const short = words.map(x => ABBREV[x.replace('Turn back', 'Back')] ?? x.slice(0, 2)).join(',') + (km ? ` ${km}` : '');
  return short.slice(0, limit);
}

export function makePins(an: PinAnalysis, target: 'etrex' | 'ambit', okPins = true): Pin[] {
  const { res, al, events } = an;
  interface Raw { kind: PinKind; words: string[]; km: string | null; i: number }
  const raw: Raw[] = [];
  for (const e of events) if (e.kind === 'turn') raw.push({ kind: 'turn', words: ['Turn back'], km: e.km.toFixed(1), i: e.idx });
  for (const e of events) {
    if (e.kind !== 'decision') continue;
    let spot = e.idx;
    for (let back = Math.floor(PIN_BEFORE_M / STEP_M); back > 0; back--) {
      const q = Math.max(0, e.idx - back);
      if (raw.every(o => dist(res[q], res[o.i]) >= PIN_CLEAR_M)) { spot = q; break; }
    }
    raw.push({ kind: 'decision', words: [e.word], km: e.km.toFixed(1), i: spot });
  }
  if (okPins) {
    events.forEach((e, k) => {
      const nextKm = k + 1 < events.length ? events[k + 1].km : al[al.length - 1] / 1000;
      if ((nextKm - e.km) * 1000 < OK_MIN_GAP_M) return;
      const target_ = al[e.idx] + OK_AFTER_M;
      let q = 0;
      for (let r = 1; r < al.length; r++) if (Math.abs(al[r] - target_) < Math.abs(al[q] - target_)) q = r;
      raw.push({ kind: 'ok', words: ['OK'], km: (al[q] / 1000).toFixed(1), i: q });
    });
  }
  const isLoop = dist(res[0], res[res.length - 1]) < 1;
  if (target === 'ambit') {
    raw.push({ kind: 'start', words: ['Start'], km: null, i: 0 });
    if (!isLoop) raw.push({ kind: 'end', words: ['End'], km: null, i: res.length - 1 });
  }
  const order: Record<string, number> = { start: 0, turn: 1, decision: 1, ok: 1, end: 2 };
  const sorted = raw.map((p, k) => ({ p, k }))
    .sort((a, b) => al[a.p.i] - al[b.p.i] || order[a.p.kind] - order[b.p.kind] || a.k - b.k).map(o => o.p);

  const kept: Raw[] = [];
  for (const p of sorted) {
    const prev = kept[kept.length - 1];
    if (prev && al[p.i] - al[prev.i] < MERGE_M && p.kind !== 'end' && prev.kind !== 'end') {
      if (p.kind === 'ok') continue;
      if (prev.kind === 'ok') { kept[kept.length - 1] = { ...p }; continue; }
      prev.words = prev.words.concat(p.words);
      if (prev.kind !== 'start') prev.kind = 'merged';
      continue;
    }
    kept.push({ ...p });
  }
  const limit = target === 'ambit' ? AMBIT_NAME_BYTES : null;
  return kept.map(p => {
    const [lat, lon] = an.ll(res[p.i]);
    return { kind: p.kind, name: shortName(p.words, p.km, limit), along: al[p.i], lat, lon };
  });
}

const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
const r7 = (v: number) => Math.round(v * 1e7) / 1e7;

export function etrexPinsGpx(pts: PinPoint[], pins: Pin[], name: string, extra: { lat: number; lon: number; name: string }[] = []): string {
  const rows = pins.map(p => `  <wpt lat="${p.lat.toFixed(7)}" lon="${p.lon.toFixed(7)}"><name>${esc(p.name)}</name><sym>${SYM[p.kind] ?? 'Flag, Blue'}</sym></wpt>\n`);
  for (const w of extra) rows.push(`  <wpt lat="${w.lat.toFixed(7)}" lon="${w.lon.toFixed(7)}"><name>${esc(w.name.slice(0, 30))}</name><sym>Scenic Area</sym></wpt>\n`);
  const trk = pts.map(p => `    <trkpt lat="${p.lat.toFixed(7)}" lon="${p.lon.toFixed(7)}">${p.ele === null ? '' : `<ele>${p.ele.toFixed(1)}</ele>`}</trkpt>`);
  return '<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n'
    + rows.join('') + `  <trk><name>${esc(name)}</name><trkseg>\n${trk.join('\n')}\n  </trkseg></trk>\n</gpx>\n`;
}

/** A route whose pins are waypoints sitting exactly on route points, in riding order. */
export function ambitPinsGpx(an: PinAnalysis, pins: Pin[], name: string): string {
  interface Row { a: number; lat: number; lon: number; ele: number | null; nm: string | null }
  const seq: Row[] = an.pts.map((p, i) => ({ a: an.cum[i], lat: r7(p.lat), lon: r7(p.lon), ele: p.ele, nm: null }));
  const same = (x: number, y: number) => Math.abs(x - y) < 1e-7;
  const wpts: { lat: number; lon: number; nm: string }[] = [];
  for (const p of pins) {
    if (p.kind === 'start') { wpts.push({ lat: seq[0].lat, lon: seq[0].lon, nm: p.name }); continue; }
    if (p.kind === 'end') continue;
    let lat = r7(p.lat); const lon = r7(p.lon);
    while (seq.some(s => same(s.lat, lat) && same(s.lon, lon))) lat = r7(lat + 2e-7);
    seq.push({ a: p.along, lat, lon, ele: null, nm: p.name });
    wpts.push({ lat, lon, nm: p.name });
  }
  const line = seq.map((s, k) => ({ s, k })).sort((x, y) => x.s.a - y.s.a || Number(x.s.nm !== null) - Number(y.s.nm !== null) || x.k - y.k).map(o => o.s);
  let last = line.find(s => s.ele !== null)?.ele ?? 0;
  for (const s of line) { if (s.ele === null) s.ele = last; last = s.ele; }
  const end = pins.find(p => p.kind === 'end');
  if (end) {
    const tail = line[line.length - 1];
    if (wpts.some(w => same(w.lat, tail.lat) && same(w.lon, tail.lon))
      || line.filter(q => same(q.lat, tail.lat) && same(q.lon, tail.lon)).length > 1) tail.lat = r7(tail.lat + 2e-7);
    wpts.push({ lat: tail.lat, lon: tail.lon, nm: end.name });
  }
  return '<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n'
    + `  <metadata><name>${esc(name)}</name></metadata>\n`
    + wpts.map(w => `  <wpt lat="${w.lat.toFixed(7)}" lon="${w.lon.toFixed(7)}"><name>${esc(w.nm)}</name><type>Waypoint</type></wpt>\n`).join('')
    + `  <rte><name>${esc(name)}</name>\n`
    + line.map(s => `    <rtept lat="${s.lat.toFixed(7)}" lon="${s.lon.toFixed(7)}"><ele>${(s.ele ?? 0).toFixed(1)}</ele></rtept>\n`).join('')
    + '  </rte>\n</gpx>\n';
}

export interface PinOptions { target: 'etrex' | 'ambit'; name?: string; reverse?: boolean; okPins?: boolean }

/** Throws on unreadable / point-less GPX, or (Ambit) when the route needs more pins than fit. */
export function buildRoutePins(gpxXml: string, opts: PinOptions): PinResult {
  const parsed = parseRouteGpx(gpxXml, opts.name ?? 'Route');
  let pts: PinPoint[] = parsed.points.map(p => ({ lat: p.latitude, lon: p.longitude, ele: p.elevation }));
  if (pts.length < 2) throw new Error('No track points found in this GPX');
  if (opts.reverse) pts = [...pts].reverse();
  const an = analyzePins(pts);
  let pins = makePins(an, opts.target, opts.okPins ?? true);
  if (opts.target === 'ambit' && pins.length > AMBIT_MAX_PINS) {
    pins = makePins(an, opts.target, false); // the OK backups go first
    if (pins.length > AMBIT_MAX_PINS) {
      throw new Error(`This route needs ${pins.length} pins and the watch only has room for about ${AMBIT_MAX_PINS} - split it into shorter routes (for example one per day)`);
    }
  }
  const label = opts.name ?? parsed.name;
  let extra: { lat: number; lon: number; name: string }[] = [];
  if (opts.target === 'etrex' && !opts.reverse) {
    try { extra = parseGpxWaypoints(gpxXml).map(w => ({ lat: w.latitude, lon: w.longitude, name: w.name })); } catch { extra = []; }
  }
  const gpx = opts.target === 'ambit' ? ambitPinsGpx(an, pins, label) : etrexPinsGpx(pts, pins, label, extra);
  const decisions = an.events.filter(e => e.kind === 'decision');
  return {
    gpx, pins,
    stats: {
      target: opts.target, km: Math.round(an.al[an.al.length - 1] / 10) / 100, pointsIn: pts.length,
      junctions: new Set(decisions.map(e => e.jid)).size, decisions: decisions.length,
      turnarounds: an.events.length - decisions.length,
      pins: pins.filter(p => p.kind !== 'start' && p.kind !== 'end').length, ignoredSpikes: an.spikes,
    },
  };
}
