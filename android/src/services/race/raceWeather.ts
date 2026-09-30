// Twin of tools/race_weather.py: weather + daylight AT EACH CONTROL at its real arrival time (the
// timeline's ETAs, not a constant pace), plus the net prevailing head/tailwind. The forecast is the
// one online call (Open-Meteo, the app's openMeteoFetch); it is injectable so tests use a fixed
// forecast. Parity-tested.
import { NDT, parseIso, fields, pyRound } from './pyCompat';
import { cumulativeDistances, parseGpxPoints, RoutePt } from './geo';
import { events as astroEvents } from '../Astro';
import { windRelation, openMeteoFetch, WxSample } from '../WeatherRoute';

const pyMod = (a: number, n: number) => ((a % n) + n) % n;
const rad = (d: number) => d * Math.PI / 180;

/** weather_route._bearing, exactly (degrees(atan2) % 360). */
function bearing(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = rad(lat1), p2 = rad(lat2), dl = rad(lon2 - lon1);
  const y = Math.sin(dl) * Math.cos(p2);
  const x = Math.cos(p1) * Math.sin(p2) - Math.sin(p1) * Math.cos(p2) * Math.cos(dl);
  return pyMod(Math.atan2(y, x) * 180 / Math.PI, 360.0);
}

export type RaceFetch = (samples: Array<{ lat: number; lon: number; eta_dt: Date }>) => Promise<WxSample[] | null> | WxSample[] | null;

export async function raceWeather(controls: any[], gpx: string | null, points: RoutePt[] | null, tzOffsetH = 0.0,
                                  fetch: RaceFetch | null = null): Promise<any> {
  if ((points === null || points === undefined) && gpx) points = parseGpxPoints(gpx);
  if (!points || points.length < 2) return { ok: false, error: 'route needs >= 2 points' };
  if (!controls.length) return { ok: false, error: 'no controls' };
  const cumul = cumulativeDistances(points.map(p => [p.lat, p.lon] as [number, number]));
  const nearest = (km: number) => {
    let b = 0, bd = Infinity;
    for (let i = 0; i < cumul.length; i++) { const d = Math.abs(cumul[i] - km * 1000.0); if (d < bd) { bd = d; b = i; } }
    return b;
  };
  const samples: Array<{ lat: number; lon: number; eta_dt: Date }> = [];
  const meta: any[] = [];
  for (const c of controls) {
    const km = Number(c.distance_km);
    const i = nearest(km);
    const lat = points[i].lat, lon = points[i].lon;
    const j = Math.min(i + 1, points.length - 1);
    const heading = bearing(lat, lon, points[j].lat, points[j].lon);
    const arr: NDT | null = typeof c.arrival_dt === 'string' ? parseIso(c.arrival_dt) : null;
    if (arr === null) continue;
    // eta_dt = arrival - tz, as a UTC instant (the fetch reads it with UTC getters)
    samples.push({ lat, lon, eta_dt: new Date(Math.floor(arr / 1000) - tzOffsetH * 3600 * 1000) });
    meta.push({ label: c.label ?? null, lat, lon, heading, arr, km });
  }
  if (!samples.length) return { ok: false, error: 'no controls with an arrival time' };
  const wx = await (fetch || openMeteoFetch)(samples);
  if (!wx || wx.length !== samples.length) return { ok: false, error: 'forecast fetch failed' };

  const out: any[] = [], dark: string[] = [], head: number[] = [];
  meta.forEach((m, i) => {
    const w = wx[i];
    const rel = windRelation(w.wind_dir_deg, m.heading);
    head.push(w.wind_kmh * Math.cos(rad(w.wind_dir_deg - m.heading)));
    const f = fields(m.arr);
    const ev = astroEvents(f.year, f.month, f.day, m.lat, m.lon, tzOffsetH);
    const sr = ev.sun_min.sunrise ?? null, ss = ev.sun_min.sunset ?? null;
    const tod = f.hour * 60 + f.minute;
    const isDark = !!(sr !== null && ss !== null && (tod < sr || tod > ss));
    if (isDark && m.label) dark.push(m.label);
    out.push({ label: m.label, km: pyRound(m.km, 1), temp_c: pyRound(w.temp_c, 1), feels_c: pyRound(w.feels_c, 1),
               rain_mm: pyRound(w.rain_mm, 2), wind_kmh: pyRound(w.wind_kmh, 1), wind_rel: rel, is_dark: isDark,
               sunrise: ev.sun.sunrise ?? null, sunset: ev.sun.sunset ?? null });
  });
  const temps = out.map(c => c.temp_c);
  const netHead = head.length ? pyRound(head.reduce((a, b) => a + b, 0) / head.length, 1) : 0.0;
  const summary = {
    temp_min_c: Math.min(...temps), temp_max_c: Math.max(...temps),
    wind_max_kmh: pyRound(Math.max(...out.map(c => c.wind_kmh)), 1),
    rain_max_mm: pyRound(Math.max(...out.map(c => c.rain_mm)), 2),
    net_head_kmh: netHead, dark_controls: dark,
  };
  return { ok: true, controls: out, summary, verdict: dark.length ? 'In the dark at: ' + dark.join(', ') : 'All controls reached in daylight.' };
}

/** race_weather.py main() */
export async function weatherFromBody(body: any, fetch: RaceFetch | null = null): Promise<any> {
  try {
    return await raceWeather(body.controls || [], body.gpx ?? null, body.points ?? null, Number(body.tz || 0.0), fetch);
  } catch (e: any) {
    return { ok: false, error: String(e?.message ?? e) };
  }
}
