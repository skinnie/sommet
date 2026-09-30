// Twin of tools/race_pois.py: POIs from a PitStopper GPX export -> categories along the route, the
// resupply gaps (refill credits water + cemeteries + food/fuel), and the opening-hours re-read at the
// planned ETAs (published hours only - never guessed). Parity-tested.
import { NDT, parseIso19, fields, pyRound, pyFixed, diffSeconds, roundHalfEven } from './pyCompat';
import { cumulativeDistances, haversineM, parseGpxPoints, RoutePt } from './geo';
import { parseXml, iterAll, localName, XmlEl } from './miniXml';

const RESUPPLY = new Set(['water', 'food']);

function nearestKm(points: RoutePt[], cumul: number[], lat: number, lon: number): [number, number] {
  let bi = 0, bd = Infinity;
  points.forEach((p, i) => { const d = haversineM(lat, lon, p.lat, p.lon); if (d < bd) { bd = d; bi = i; } });
  return [cumul[bi] / 1000.0, bd];
}

export function gaps(kms: number[], totalKm: number) {
  const ks = kms.slice().sort((a, b) => a - b);
  if (!ks.length) return { count: 0, first_km: null as number | null, longest_gap_km: pyRound(totalKm, 1), longest_gap_after_km: 0.0 };
  const edges = [0.0, ...ks, totalKm];
  let longest = 0.0, after = 0.0;
  for (let i = 1; i < edges.length; i++) {
    const g = edges[i] - edges[i - 1];
    if (g > longest) { longest = g; after = edges[i - 1]; }
  }
  return { count: ks.length, first_km: pyRound(ks[0], 1), longest_gap_km: pyRound(longest, 1), longest_gap_after_km: pyRound(after, 1) };
}

const byKm = (pois: any[]) => pois.map((p, i) => [p, i] as [any, number]).sort((a, b) => (a[0].km - b[0].km) || (a[1] - b[1])).map(x => x[0]);

function buildOutput(perCat: Record<string, any[]>, cats: string[], totalKm: number, waterL: number, carryL: number) {
  const out: Record<string, any> = {};
  const summary: string[] = [];
  for (const cat of cats) {
    const pois = byKm(perCat[cat] || []);
    const info: any = { pois };
    if (RESUPPLY.has(cat)) {
      let gapKms = pois.map(p => p.km);
      if (cat === 'water') for (const extra of ['cemetery', 'food']) if (extra in perCat) gapKms = gapKms.concat(perCat[extra].map(p => p.km));
      const g = gaps(gapKms, totalKm);
      Object.assign(info, g);
      if (cat === 'water') {
        const need = pyRound(waterL * g.longest_gap_km / 100.0, 1);
        info.longest_gap_litres = need; info.carry_l = carryL; info.longest_gap_over_carry = need > carryL;
      }
      const label = ({ water: 'refill (water/café/shop)', food: 'food' } as any)[cat];
      const short = ({ water: 'refill', food: 'food' } as any)[cat];
      if (g.count === 0) summary.push(`No ${label} found on this route.`);
      else summary.push(`Longest stretch with no ${short}: ${pyFixed(g.longest_gap_km, 0)} km (after km ${pyFixed(g.longest_gap_after_km, 0)})`);
    } else if (cat === 'other') {
      info.count = pois.length;
      const kinds = new Map<string, number>();
      for (const p of pois) { const k = p.kind || p.subtype || 'Other'; kinds.set(k, (kinds.get(k) || 0) + 1); }
      info.kinds = Object.fromEntries(kinds);
      if (pois.length) {
        const top = Array.from(kinds.entries()).map((kv, i) => [kv, i] as [[string, number], number])
          .sort((a, b) => (b[0][1] - a[0][1]) || (a[1] - b[1])).map(x => x[0]);
        let txt = top.slice(0, 4).map(([k, c]) => `${k} ${c}`).join(' · ');
        if (top.length > 4) txt += ` · +${top.length - 4} more types`;
        summary.push('Also on the route: ' + txt);
      }
    } else {
      info.count = pois.length;
      if (pois.length) {
        const nice = ({ bike: 'Bike shop/repair', shelter: 'Accommodation', safety: 'Services',
                        cemetery: 'Cemeteries (likely water)', other: 'Other places' } as any)[cat] ?? cat;
        summary.push(`${nice}: ${pois.length}`);
      }
    }
    out[cat] = info;
  }
  return { ok: true, total_km: pyRound(totalKm, 1), categories: out, summary } as any;
}

const PS_CATEGORY: Record<string, string> = {
  water: 'water', food: 'food', coffee: 'food', bar: 'food', gas: 'food', convenience_store: 'food',
  lodging: 'shelter', camping: 'shelter', bike_shop: 'bike',
  restroom: 'safety', shower: 'safety', hospital: 'safety', first_aid: 'safety', atm: 'safety',
};
const PS_GROUP: Record<string, string> = {
  water: 'Water', food: 'Food & Drink', coffee: 'Food & Drink', bar: 'Food & Drink', gas: 'Food & Drink',
  lodging: 'Accommodation', camping: 'Accommodation', transit: 'Transport', parking: 'Transport', caution: 'Transport',
  bike_parking: 'Cycling', bikeshare: 'Cycling', bike_shop: 'Cycling', hospital: 'Emergency', first_aid: 'Emergency',
  atm: 'Services', shopping: 'Shopping', convenience_store: 'Shopping', restroom: 'Amenities', shower: 'Amenities',
  rest_stop: 'Amenities', viewpoint: 'Recreation', park: 'Recreation', swimming: 'Recreation', generic: 'Other',
};
const CYCLIST_KEYS = new Set(['water', 'gas', 'coffee', 'restroom', 'bike_parking', 'bike_shop', 'bikeshare', 'convenience_store']);
const PS_SYM: Record<string, string> = { 'Drinking Water': 'water', Restaurant: 'food', 'Gas Station': 'food',
  'Convenience Store': 'food', Lodging: 'shelter', Campground: 'shelter', Restroom: 'safety', 'Car Repair': 'bike' };
const SUPERMARKET_WORDS = ['supermarket', 'supermarch', 'supermerc', 'supermarkt', 'supermärkt', 'grocer'];

function genericCategory(kind: string): string {
  const k = kind.toLowerCase();
  if (k.startsWith('alpine hut') || k.startsWith('wilderness hut')) return 'shelter';
  if (k.startsWith('motorway service')) return 'food';
  if (k.includes('repair station') || k.startsWith('compressed air')) return 'bike';
  if (['first aid', 'emergency phone', 'emergency ward', 'lifeguard', 'mountain rescue'].some(w => k.startsWith(w))) return 'safety';
  return 'other';
}

export function isPlaceholderName(name: string, kind: string): boolean {
  if (!name || !kind) return false;
  const n = name.trim();
  if (/^[a-z]+(?:_[a-z]+)+\d*$/.test(n)) return true;
  const norm = n.replace(/\d+$/, '').toLowerCase().split(' ').join('_');
  const snake = kind.trim().toLowerCase().split(' ').join('_');
  return !!norm && (norm === snake || norm === snake + 's' || snake.startsWith(norm));
}

function isCyclist(key: string, kind: string): boolean {
  const k = kind.toLowerCase();
  if (key === 'water') return !((k.includes('non') && k.includes('potable')) || k.includes('refill'));
  if (key === 'food') return k.startsWith('restaurant');
  return CYCLIST_KEYS.has(key);
}

export function cleanName(name: string): string {
  let n = name.trim().replace(/^\^+/, '').trim();
  n = n.replace(/\s+[LR]\d+\s*m?$/, '').trim();
  return n;
}

export const CUSTOM_TAGS: Record<string, [string, string]> = {
  cemetery: ['cemetery', 'Cemetery'], water: ['water', 'Water'], food: ['food', 'Food or drink'], other: ['other', 'Custom place'],
};

/** Python str.title() */
function pyTitle(s: string): string {
  let out = '', prevLetter = false;
  for (const ch of s) {
    const isLetter = ch.toLowerCase() !== ch.toUpperCase();
    out += isLetter ? (prevLetter ? ch.toLowerCase() : ch.toUpperCase()) : ch;
    prevLetter = isLetter;
  }
  return out;
}

/** Python str.strip(chars) */
const stripChars = (s: string, chars: string) => {
  let a = 0, b = s.length;
  while (a < b && chars.includes(s[a])) a++;
  while (b > a && chars.includes(s[b - 1])) b--;
  return s.slice(a, b);
};

export function parsePitstopperGpx(gpxText: string, customTag = 'cemetery'): any[] {
  let root: XmlEl;
  try { root = parseXml(gpxText); } catch { return []; }
  const out: any[] = [];
  for (const w of iterAll(root)) {
    if (localName(w.tag) !== 'wpt') continue;
    const f = (tag: string) => { for (const c of w.children) if (localName(c.tag) === tag) return (c.text || '').trim(); return ''; };
    const cmt = f('cmt'), sym = f('sym'), desc = f('desc');
    let name = cleanName(f('name'));
    const key = cmt.split('.')[0].trim().toLowerCase();
    const core = stripChars(desc.split(/\.\s*(?:Hours|Website|Phone):/)[0].trim(), '.');
    let full = '', kind = core;
    if (core.startsWith('Full name:')) {
      const rest = core.slice('Full name:'.length).trim();
      const k = rest.lastIndexOf('. ');
      if (k >= 0) { full = rest.slice(0, k); kind = rest.slice(k + 2); } else { full = rest; kind = ''; }
    }
    full = full.trim(); kind = kind.trim();
    let isCustom = false;
    let cat: string;
    if (key === 'shopping') {
      cat = SUPERMARKET_WORDS.some(wd => kind.toLowerCase().startsWith(wd)) ? 'food' : 'other';
    } else if (key === 'generic') {
      if (kind === 'POI') { [cat, kind] = CUSTOM_TAGS[customTag] ?? CUSTOM_TAGS.cemetery; isCustom = true; }
      else cat = genericCategory(kind);
    } else {
      cat = PS_CATEGORY[key] || PS_SYM[sym] || 'other';
      if (key === 'water' && kind.toLowerCase().includes('non') && kind.toLowerCase().includes('potable')) cat = 'other';
    }
    if (full) name = full;
    else if (isCustom && (!name || /^POI\d*$/.test(name))) name = (CUSTOM_TAGS[customTag] ?? CUSTOM_TAGS.cemetery)[1];
    const useKind = !full && key !== 'generic' && isPlaceholderName(name, kind);
    kind = kind.split(/\s+-\s+(?:Outbound|Return|Pass)\b/)[0].trim();
    if (kind.endsWith('s') && !kind.endsWith('ss') && kind.length > 3) kind = kind.slice(0, -1);
    if (useKind) name = kind;
    const kms = Array.from(cmt.matchAll(/at\s+([0-9]+(?:\.[0-9]+)?)\s*km/g), m => parseFloat(m[1]));
    const hm = /Hours:\s*(.+?)(?:\.\s+(?:Website|Phone)|$)/.exec(desc);
    out.push({ lat: parseFloat(w.attrs.lat), lon: parseFloat(w.attrs.lon), name: name || pyTitle(key), cat, kms, sub: key,
               kind, group: isCustom ? 'Custom tag' : (PS_GROUP[key] ?? 'Other'), cyclist: isCyclist(key, kind),
               hours: hm ? hm[1].trim() : '' });
  }
  return out;
}

export function analyzeWaypoints(points: RoutePt[], wpts: any[], waterL = 2.0, carryL = 1.5, mirrorKms = false): any {
  if (points.length < 2) return { ok: false, error: 'route needs >= 2 points' };
  const cumul = cumulativeDistances(points.map(p => [p.lat, p.lon] as [number, number]));
  const totalKm = cumul[cumul.length - 1] / 1000.0;
  const cats = ['water', 'food', 'cemetery', 'bike', 'shelter', 'safety', 'other'];
  const perCat: Record<string, any[]> = {};
  for (const c of cats) perCat[c] = [];
  const seen = new Set<string>();
  const groups: Record<string, number> = {};
  for (const w of wpts) { const g = w.group ?? 'Other'; groups[g] = (groups[g] || 0) + 1; }
  const CELL = 0.01;
  const grid = new Map<string, number[]>();
  points.forEach((p, i) => {
    const k = `${Math.trunc(p.lat / CELL)},${Math.trunc(p.lon / CELL)}`;
    const l = grid.get(k); if (l) l.push(i); else grid.set(k, [i]);
  });
  const nearestFast = (lat: number, lon: number) => {
    const cx = Math.trunc(lat / CELL), cy = Math.trunc(lon / CELL);
    let bi = -1, bd = Infinity;
    for (const dx of [-1, 0, 1]) for (const dy of [-1, 0, 1]) {
      for (const i of grid.get(`${cx + dx},${cy + dy}`) || []) {
        const d = haversineM(lat, lon, points[i].lat, points[i].lon);
        if (d < bd) { bd = d; bi = i; }
      }
    }
    return bi < 0 ? nearestKm(points, cumul, lat, lon)[0] : cumul[bi] / 1000.0;
  };
  for (const w of wpts) {
    const given: number[] = mirrorKms ? w.kms.map((k: number) => totalKm - k) : w.kms;
    const kms = given.length ? given : [pyRound(nearestFast(w.lat, w.lon), 1)];
    for (const km of kms) {
      const key = `${w.cat}|${pyRound(w.lat, 5)}|${pyRound(w.lon, 5)}|${pyRound(km, 1)}`;
      if (seen.has(key)) continue;
      seen.add(key);
      const poi: any = { name: w.name, km: pyRound(km, 1), lat: w.lat, lon: w.lon, subtype: w.sub, kind: w.kind ?? '',
                         group: w.group ?? 'Other', cyclist: !!w.cyclist };
      if (w.hours) poi.hours = w.hours;
      perCat[w.cat].push(poi);
    }
  }
  const out = buildOutput(perCat, cats.filter(c => perCat[c].length || RESUPPLY.has(c)), totalKm, waterL, carryL);
  out.groups = groups;
  return out;
}

// ---- opening hours ---------------------------------------------------------------------------------

const DAYS = ['mo', 'tu', 'we', 'th', 'fr', 'sa', 'su'];
const pyMod = (a: number, n: number) => ((a % n) + n) % n;

export function parseHours(text: string | null | undefined): ((wd: number, minute: number) => boolean) | null {
  const t = (text || '').trim();
  if (!t) return null;
  if (t.toLowerCase() === '24/7' || t.toLowerCase() === '24h') return () => true;
  const week: Array<Array<[number, number]> | null> = [null, null, null, null, null, null, null];
  for (let rule of t.split(/\s*;\s*/)) {
    rule = rule.trim();
    if (!rule) continue;
    const d = '(?:Mo|Tu|We|Th|Fr|Sa|Su)';
    const mm = new RegExp(`^((?:${d}(?:\\s*-\\s*${d})?\\s*,?\\s*)*)(.*)$`, 'i').exec(rule)!;
    const dayspec = mm[1].trim(), rest = mm[2].trim();
    let days: number[] = [];
    if (dayspec) {
      for (const part of stripChars(dayspec.trim(), ', ').split(/\s*,\s*/)) {
        const ab = part.split('-').map(x => x.trim().toLowerCase().slice(0, 2));
        if (!ab.every(x => DAYS.includes(x))) return null;
        const a = DAYS.indexOf(ab[0]), b = DAYS.indexOf(ab[ab.length - 1]);
        if (a <= b) for (let k = a; k <= b; k++) days.push(k);
        else { for (let k = a; k < 7; k++) days.push(k); for (let k = 0; k <= b; k++) days.push(k); }
      }
    } else days = [0, 1, 2, 3, 4, 5, 6];
    let spans: Array<[number, number]>;
    if (/^(?:off|closed)$/i.test(rest)) spans = [];
    else {
      spans = [];
      for (const m of rest.matchAll(/(\d{1,2}:\d{2})\s*-\s*(\d{1,2}:\d{2})/g)) {
        const [h1, m1] = m[1].split(':'), [h2, m2] = m[2].split(':');
        spans.push([parseInt(h1, 10) * 60 + parseInt(m1, 10), parseInt(h2, 10) * 60 + parseInt(m2, 10)]);
      }
      if (!spans.length) return null;
    }
    for (const dd of days) week[dd] = spans;
  }
  return (wd: number, minute: number) => {
    for (const [back, day] of [[0, wd], [1, pyMod(wd - 1, 7)]] as Array<[number, number]>) {
      for (const [a, b] of week[day] || []) {
        if (b > a) { if (back === 0 && a <= minute && minute < b) return true; }
        else {
          if (back === 0 && minute >= a) return true;
          if (back === 1 && minute < b) return true;
        }
      }
    }
    return false;
  };
}

function etaAt(km: number, anchors: Array<[number, NDT]>): NDT {
  if (km <= anchors[0][0]) return anchors[0][1];
  for (let i = 1; i < anchors.length; i++) {
    const [k0, t0] = anchors[i - 1], [k1, t1] = anchors[i];
    if (km <= k1) {
      const f = k1 === k0 ? 0.0 : (km - k0) / (k1 - k0);
      return t0 + roundHalfEven((t1 - t0) * f);
    }
  }
  return anchors[anchors.length - 1][1];
}

export function refillOpenStatus(poi: any, cat: string, when: NDT): 'open' | 'closed' | 'unknown' {
  const fn = parseHours(poi.hours ?? '');
  if (fn !== null) { const f = fields(when); return fn(f.weekday, f.hour * 60 + f.minute) ? 'open' : 'closed'; }
  return cat === 'water' ? 'open' : 'unknown';
}

const WEEKDAY = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'];

export function openRefillAnalysis(result: any, eta: any[]): any {
  const anchors = eta.filter(a => a.dt).map((a, i) => [[Number(a.km), parseIso19(String(a.dt))], i] as [[number, NDT], number])
    .sort((x, y) => (x[0][0] - y[0][0]) || (x[1] - y[1])).map(x => x[0]);
  if (anchors.length < 2) return { ok: false, error: 'need at least two ETA points (start + a control)' };
  const totalKm = Number(result.total_km || anchors[anchors.length - 1][0]);
  const cats = result.categories || {};
  const collect = (names: string[]) => {
    const confirmed: number[] = [], maybe: number[] = [];
    const n: Record<string, number> = { open: 0, closed: 0, unknown: 0 };
    for (const c of names) for (const p of (cats[c] || {}).pois || []) {
      const st = refillOpenStatus(p, c, etaAt(p.km, anchors));
      n[st]++;
      if (st === 'open') confirmed.push(p.km);
      if (st !== 'closed') maybe.push(p.km);
    }
    return [confirmed, maybe, n] as [number[], number[], Record<string, number>];
  };
  const [cRef, mRef, nRef] = collect(['water', 'cemetery', 'food']);
  const [cFood, mFood, nFood] = collect(['food']);
  const gRef = gaps(cRef, totalKm), gmRef = gaps(mRef, totalKm), gFood = gaps(cFood, totalKm), gmFood = gaps(mFood, totalKm);
  const at = (km: number) => { const t = etaAt(km, anchors), f = fields(t); return `${WEEKDAY[f.weekday]} ${String(f.hour).padStart(2, '0')}:${String(f.minute).padStart(2, '0')}`; };
  const lines: string[] = [];
  const tot = nRef.open + nRef.closed + nRef.unknown;
  if (tot) {
    lines.push(`At your planned times: longest stretch with no CONFIRMED-open refill ${pyFixed(gRef.longest_gap_km, 0)} km (after km ${pyFixed(gRef.longest_gap_after_km, 0)}, ~${at(gRef.longest_gap_after_km)}); ` +
               `if places with unknown hours are open, ${pyFixed(gmRef.longest_gap_km, 0)} km. Of ${tot} refill points: ${nRef.open} open, ${nRef.closed} closed, ${nRef.unknown} unknown hours.`);
  }
  if (nFood.open + nFood.closed + nFood.unknown) {
    lines.push(`Food: ${pyFixed(gFood.longest_gap_km, 0)} km with none confirmed open (after km ${pyFixed(gFood.longest_gap_after_km, 0)}, ~${at(gFood.longest_gap_after_km)}); ${pyFixed(gmFood.longest_gap_km, 0)} km if unknown-hours places are open.`);
  }
  return { ok: true, lines, refill: gRef, refill_if_unknown_open: gmRef, food: gFood, food_if_unknown_open: gmFood, counts: nRef,
           note: "Opening hours are OpenStreetMap's, where published (none are guessed); public holidays are not considered." };
}

/** race_pois.py main() */
export function poisFromBody(body: any): any {
  try {
    if (body.pois && body.eta) return openRefillAnalysis(body.pois, body.eta);
    let points: RoutePt[] = body.points;
    if ((!points || !points.length) && body.gpx) points = parseGpxPoints(body.gpx);
    if (!body.poi_gpx) return { ok: false, error: 'poi_gpx (a PitStopper GPX export) is required' };
    const wpts = parsePitstopperGpx(body.poi_gpx, String(body.custom_tag || 'cemetery'));
    const rev = !!body.reverse;
    if (rev) points = (points || []).slice().reverse();
    const r = analyzeWaypoints(points || [], wpts, Number(body.water_l_per_100km || 2.0), Number(body.carry_l || 1.5), rev);
    if (r.ok) r.imported = wpts.length;
    return r;
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}

void diffSeconds;
