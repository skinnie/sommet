// Weather (+ sun/moon) along a planned route — the "what am I walking into?" layer.
// Faithful TypeScript port of the desktop's tools/weather_route.py: resample the track to even
// steps, stamp each with an ETA (start + distance/pace), fetch the forecast at each point/ETA
// (Open-Meteo), and emit temp-coloured polyline `segments`, a `profile` series, `wind_arrows`,
// an `astro` (sun/moon) summary and a plain-language `verdict`. The maths are offline/injectable
// (tests pass a synthetic fetch); only the default fetch touches the network (Open-Meteo).
// Keep in sync with weather_route.py. Uses Astro.ts (verified equal to astro.py).

import { events as astroEvents, AstroEvents } from './Astro';

export interface RoutePoint { lat: number; lon: number; ele?: number | null; }
export interface WxSample { temp_c: number; feels_c: number; rain_mm: number; wind_kmh: number; wind_dir_deg: number; }

export const TEMP_BUCKETS = [
  { key: 'freezing',  label: '< 0 °C',    upper: 0.0,      color: '#4575b4' },
  { key: 'cold',      label: '0–5 °C',    upper: 5.0,      color: '#74add1' },
  { key: 'cool',      label: '5–10 °C',   upper: 10.0,     color: '#abd9e9' },
  { key: 'mild',      label: '10–15 °C',  upper: 15.0,     color: '#fee090' },
  { key: 'warm',      label: '15–20 °C',  upper: 20.0,     color: '#fdae61' },
  { key: 'hot',       label: '20–25 °C',  upper: 25.0,     color: '#f46d43' },
  { key: 'scorching', label: '≥ 25 °C',   upper: Infinity, color: '#d73027' },
];

const WIND_REL: Record<string, string> = {
  headwind: '#d6453f', crosswind: '#e0912f', tailwind: '#2e9e6b',
};

function tempBucket(t: number) {
  for (const b of TEMP_BUCKETS) if (t < b.upper) return b;
  return TEMP_BUCKETS[TEMP_BUCKETS.length - 1];
}

// Cumulative great-circle (haversine) distances in metres, matching geo_util.cumulative_distances.
function cumulativeDistances(pts: Array<[number, number]>): number[] {
  const R = 6371000.0;
  const out = [0.0];
  for (let i = 1; i < pts.length; i++) {
    const [la1, lo1] = pts[i - 1], [la2, lo2] = pts[i];
    const p1 = (la1 * Math.PI) / 180, p2 = (la2 * Math.PI) / 180;
    const dp = ((la2 - la1) * Math.PI) / 180, dl = ((lo2 - lo1) * Math.PI) / 180;
    const a = Math.sin(dp / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
    out.push(out[i - 1] + 2 * R * Math.asin(Math.min(1, Math.sqrt(a))));
  }
  return out;
}

function bearing(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = (lat1 * Math.PI) / 180, p2 = (lat2 * Math.PI) / 180;
  const dl = ((lon2 - lon1) * Math.PI) / 180;
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

export function windRelation(windFromDeg: number, headingDeg: number): 'headwind' | 'crosswind' | 'tailwind' {
  const diff = Math.abs((((windFromDeg - headingDeg + 180) % 360) + 360) % 360 - 180);
  if (diff < 60) return 'headwind';
  if (diff > 120) return 'tailwind';
  return 'crosswind';
}

function resample(points: RoutePoint[], dists: number[], stepM: number) {
  const total = dists[dists.length - 1];
  const lats: number[] = [], lons: number[] = [], eles: Array<number | null> = [], ds: number[] = [];
  let j = 0, d = 0.0;
  while (d <= total + 1e-6) {
    while (j < dists.length - 2 && dists[j + 1] < d) j++;
    const seg = dists[j + 1] - dists[j];
    const t = seg <= 0 ? 0.0 : Math.max(0.0, Math.min(1.0, (d - dists[j]) / seg));
    const a = points[j], b = points[j + 1];
    lats.push(a.lat + (b.lat - a.lat) * t);
    lons.push(a.lon + (b.lon - a.lon) * t);
    const ae = a.ele, be = b.ele;
    eles.push(ae == null || be == null ? null : ae + (be - ae) * t);
    ds.push(d);
    d += stepM;
  }
  const last = points[points.length - 1];
  if (ds.length && total - ds[ds.length - 1] > stepM * 0.25) {
    lats.push(last.lat); lons.push(last.lon); eles.push(last.ele ?? null); ds.push(total);
  }
  return { lats, lons, eles, ds };
}

const hhmm = (mins: number): string => {
  const m = ((Math.round(mins) % 1440) + 1440) % 1440;
  return `${String(Math.floor(m / 60)).padStart(2, '0')}:${String(m % 60).padStart(2, '0')}`;
};

export interface WeatherRoutePlan {
  ok: boolean; error?: string;
  segments?: Array<{ bucket: string; color: string; coords: Array<[number, number]> }>;
  profile?: Array<any>;
  wind_arrows?: Array<any>;
  astro?: AstroEvents;
  verdict?: { state: string; headline: string; detail: string; dark_km: number | null };
  summary?: any;
  legend?: Array<{ key: string; label: string; color: string }>;
}

export type FetchFn = (samples: Array<{ lat: number; lon: number; eta_dt: Date }>) => Promise<WxSample[] | null>;

/** Plan weather along a track. `date` is {y,mo,d} local; `start` "HH:MM"; `pace_kmh`; `tzOffsetH`. */
export async function planWeatherRoute(
  points: RoutePoint[],
  opts: { start?: string; date?: { y: number; mo: number; d: number }; pace_kmh?: number; tzOffsetH?: number; stepM?: number; fetch?: FetchFn } = {},
): Promise<WeatherRoutePlan> {
  const start = opts.start ?? '09:00';
  const pace = opts.pace_kmh ?? 4.5;
  const tz = opts.tzOffsetH ?? 0.0;
  const pts: RoutePoint[] = points.map(p => ({ lat: +p.lat, lon: +p.lon, ele: p.ele == null || (p.ele as any) === '' ? null : +p.ele }));
  if (pts.length < 2) return { ok: false, error: 'a route needs at least 2 points' };

  const dists = cumulativeDistances(pts.map(p => [p.lat, p.lon] as [number, number]));
  const total = dists[dists.length - 1];
  if (total <= 0) return { ok: false, error: 'route has zero length' };

  const stepM = opts.stepM ?? Math.min(2000.0, Math.max(200.0, pace * 1000.0 * 0.5));
  const { lats, lons, eles, ds } = resample(pts, dists, stepM);
  const n = ds.length;

  const now = new Date();
  const d0 = opts.date ?? { y: now.getUTCFullYear(), mo: now.getUTCMonth() + 1, d: now.getUTCDate() };
  const [sh, sm] = start.split(':').map(x => parseInt(x, 10));
  const startMin = sh * 60 + sm;
  const midnightUtcMs = Date.UTC(d0.y, d0.mo - 1, d0.d) - tz * 3600_000;

  const etas = ds.map(x => startMin + (x / 1000.0 / pace) * 60.0);
  const samples = etas.map((e, i) => ({ lat: lats[i], lon: lons[i], eta_dt: new Date(midnightUtcMs + e * 60_000) }));

  const wx = await (opts.fetch ?? openMeteoFetch)(samples);
  if (wx == null || wx.length !== n) return { ok: false, error: 'forecast fetch failed or returned wrong length' };

  const headings: number[] = [];
  for (let i = 0; i < n; i++) {
    const [a, b] = i < n - 1 ? [i, i + 1] : [i - 1, i];
    headings.push(bearing(lats[a], lons[a], lats[b], lons[b]));
  }

  const profile: any[] = [];
  const segments: WeatherRoutePlan['segments'] = [];
  let cur: { key: string; color: string; coords: Array<[number, number]> } | null = null;
  const r1 = (v: number) => Math.round(v * 10) / 10;
  const r2 = (v: number) => Math.round(v * 100) / 100;
  const r6 = (v: number) => Math.round(v * 1e6) / 1e6;
  for (let i = 0; i < n; i++) {
    const w = wx[i];
    const rel = windRelation(w.wind_dir_deg, headings[i]);
    const bucket = tempBucket(w.temp_c);
    profile.push({
      dist_m: r1(ds[i]), eta_min: r1(etas[i]), eta: hhmm(etas[i]),
      ele_m: eles[i] == null ? null : r1(eles[i]!),
      temp_c: r1(w.temp_c), feels_c: r1(w.feels_c), rain_mm: r2(w.rain_mm),
      wind_kmh: r1(w.wind_kmh), wind_rel: rel, color: bucket.color,
    });
    if (cur == null || cur.key !== bucket.key) {
      if (cur != null) segments!.push({ bucket: cur.key, color: cur.color, coords: cur.coords });
      cur = { key: bucket.key, color: bucket.color, coords: [] };
    }
    cur.coords.push([r6(lats[i]), r6(lons[i])]);
  }
  if (cur != null) segments!.push({ bucket: cur.key, color: cur.color, coords: cur.coords });

  const wind_arrows = wx.map((w, i) => ({
    lat: r6(lats[i]), lon: r6(lons[i]), wind_kmh: r1(w.wind_kmh), wind_dir_deg: r1(w.wind_dir_deg),
    heading_deg: r1(headings[i]), rel: profile[i].wind_rel, color: WIND_REL[profile[i].wind_rel],
  }));

  const mid = Math.floor(n / 2);
  const ev = astroEvents(d0.y, d0.mo, d0.d, lats[mid], lons[mid], tz);
  const verdict = makeVerdict(etas, ds, pace, startMin, ev);

  const temps = wx.map(w => w.temp_c), rains = wx.map(w => w.rain_mm), winds = wx.map(w => w.wind_kmh);
  const summary = {
    distance_m: r1(total), start, finish: hhmm(etas[n - 1]), finish_min: r1(etas[n - 1]), pace_kmh: pace,
    temp_min_c: r1(Math.min(...temps)), temp_max_c: r1(Math.max(...temps)),
    rain_max_mm: r2(Math.max(...rains)), wind_max_kmh: r1(Math.max(...winds)),
  };
  return {
    ok: true, segments, profile, wind_arrows, astro: ev, verdict, summary,
    legend: TEMP_BUCKETS.map(b => ({ key: b.key, label: b.label, color: b.color })),
  };
}

function makeVerdict(etas: number[], ds: number[], pace: number, startMin: number, ev: AstroEvents) {
  const finish = etas[etas.length - 1];
  const sunset = ev.sun_min.sunset;
  const dusk = ev.sun_min.civil_dusk;
  let moon = '';
  if (ev.moon_illumination != null) {
    const pct = Math.round(ev.moon_illumination * 100);
    moon = ` A ${pct}% ${ev.moon_phase} rises ${ev.moon.moonrise || '--'}.`;
  }
  if (sunset == null) {
    return { state: 'ok', headline: `Finish ${hhmm(finish)}.`, detail: 'No sunset today at this latitude.', dark_km: null };
  }
  if (finish > sunset) {
    const after = Math.round(finish - sunset);
    let darkKm: number | null = ((sunset - startMin) / 60.0) * pace;
    let detail = 'Daylight runs out';
    if (darkKm > 0 && darkKm < ds[ds.length - 1] / 1000.0) {
      detail += ` around km ${darkKm.toFixed(1)}.`;
    } else {
      detail = `Civil dusk ${ev.sun.civil_dusk || '--'}.`;
      darkKm = null;
    }
    return {
      state: dusk != null && finish > dusk ? 'critical' : 'warn',
      headline: `You'll finish ~${after} min after dark (${hhmm(finish)}) — bring a headlamp.`,
      detail: detail + moon, dark_km: darkKm == null ? null : Math.round(darkKm * 10) / 10,
    };
  }
  const spare = Math.round(sunset - finish);
  return {
    state: 'ok',
    headline: `You'll finish ${hhmm(finish)} — about ${spare} min of daylight to spare.`,
    detail: `Sunset ${ev.sun.sunset || '--'}.${moon}`, dark_km: null,
  };
}

/** Default fetch: one Open-Meteo call for all sampled points, each at the hour nearest its ETA. */
export const openMeteoFetch: FetchFn = async (samples) => {
  if (!samples.length) return [];
  const lats = samples.map(s => s.lat.toFixed(4)).join(',');
  const lons = samples.map(s => s.lon.toFixed(4)).join(',');
  const iso = (d: Date) => `${d.getUTCFullYear()}-${String(d.getUTCMonth() + 1).padStart(2, '0')}-${String(d.getUTCDate()).padStart(2, '0')}`;
  const startD = iso(new Date(Math.min(...samples.map(s => s.eta_dt.getTime()))));
  const endD = iso(new Date(Math.max(...samples.map(s => s.eta_dt.getTime()))));
  const q = new URLSearchParams({
    latitude: lats, longitude: lons,
    hourly: 'temperature_2m,apparent_temperature,precipitation,wind_speed_10m,wind_direction_10m,weather_code',
    timezone: 'UTC', start_date: startD, end_date: endD,
  });
  const resp = await fetch('https://api.open-meteo.com/v1/forecast?' + q.toString());
  if (!resp.ok) return null;
  const doc = await resp.json();
  const locs: any[] = Array.isArray(doc) ? doc : [doc];
  return samples.map((s, i) => {
    const loc = i < locs.length ? locs[i] : locs[locs.length - 1];
    const h = loc.hourly;
    const times: number[] = h.time.map((t: string) => Date.parse(t + 'Z'));
    const eta = s.eta_dt.getTime();
    let k = 0;
    for (let j = 1; j < times.length; j++) if (Math.abs(times[j] - eta) < Math.abs(times[k] - eta)) k = j;
    return {
      temp_c: h.temperature_2m[k], feels_c: h.apparent_temperature[k],
      rain_mm: h.precipitation[k] || 0.0, wind_kmh: h.wind_speed_10m[k], wind_dir_deg: h.wind_direction_10m[k],
    };
  });
};
