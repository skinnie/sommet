// The weather a move was done in - the Android twin of desktop/src/services/
// activityweatherservice.cpp (André, 2026-09-27, "2 super nice"; the idea from OpenAthlete's
// weather processor - idea and API choice only, their code is AGPL and was not copied).
//
// One Open-Meteo call per move at the track's first point, for the hours the move spans: the
// historical archive (ERA5) once the move is ~6 days old - cached for good in AsyncStorage, the
// past never changes - and the forecast API's recent past before that (not cached, re-asked
// once the archive has it). summarizeHourly() is pure and unit-tested against the same real
// hours the desktop was checked on.

import AsyncStorage from '@react-native-async-storage/async-storage';
import { windRelation } from './WeatherRoute';

export interface ActivityWeather {
  tempMin: number; tempMax: number; feels: number; rainMm: number;
  windKmh: number; gustKmh: number; windFromDeg: number; windCompass: string;
  code: number;
  headShare?: number; crossShare?: number; tailShare?: number;
  source?: 'archive' | 'forecast';
}

export interface Hourly {
  time: string[];
  temperature_2m: (number | null)[];
  apparent_temperature?: (number | null)[];
  precipitation?: (number | null)[];
  wind_speed_10m?: (number | null)[];
  wind_direction_10m?: (number | null)[];
  wind_gusts_10m?: (number | null)[];
  weather_code?: (number | null)[];
}

type LatLon = { lat: number; lon: number };

const rad = (d: number) => (d * Math.PI) / 180;

function haversineM(a: LatLon, b: LatLon): number {
  const dp = rad(b.lat - a.lat), dl = rad(b.lon - a.lon);
  const h = Math.sin(dp / 2) ** 2 + Math.cos(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.sin(dl / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.min(1, Math.sqrt(h)));
}

function bearingDeg(a: LatLon, b: LatLon): number {
  const dl = rad(b.lon - a.lon);
  const y = Math.sin(dl) * Math.cos(rad(b.lat));
  const x = Math.cos(rad(a.lat)) * Math.sin(rad(b.lat)) - Math.sin(rad(a.lat)) * Math.cos(rad(b.lat)) * Math.cos(dl);
  return ((Math.atan2(y, x) * 180) / Math.PI + 360) % 360;
}

export function compass(deg: number): string {
  return ['N', 'NE', 'E', 'SE', 'S', 'SW', 'W', 'NW'][Math.floor((((deg + 22.5) % 360) + 360) % 360 / 45) % 8];
}

/** Every hour the move touches (from the hour it started in to its end), UTC. */
export function summarizeHourly(h: Hourly, startMs: number, endMs: number, track: LatLon[]): ActivityWeather | null {
  const from = startMs - (startMs % 3600000);
  let tMin = Infinity, tMax = -Infinity, feels = 0, rain = 0, wind = 0, gust = 0, u = 0, v = 0, n = 0, code = -1;
  h.time.forEach((iso, i) => {
    const s = Date.parse(iso.endsWith('Z') ? iso : iso + 'Z');
    const tc = h.temperature_2m[i];
    if (s < from || s > endMs || tc == null) return;
    const ws = h.wind_speed_10m?.[i] ?? 0, wd = h.wind_direction_10m?.[i] ?? 0;
    tMin = Math.min(tMin, tc); tMax = Math.max(tMax, tc);
    feels += h.apparent_temperature?.[i] ?? tc;
    if (s > from) rain += h.precipitation?.[i] ?? 0;   // precipitation[i] = the preceding hour's total
    wind += ws;
    gust = Math.max(gust, h.wind_gusts_10m?.[i] ?? 0);
    u += ws * Math.sin(rad(wd)); v += ws * Math.cos(rad(wd));   // speed-weighted mean direction
    code = Math.max(code, h.weather_code?.[i] ?? -1);           // WMO codes rise with severity
    n++;
  });
  if (n === 0) return null;
  const windFromDeg = ((Math.atan2(u, v) * 180) / Math.PI + 360) % 360;
  const w: ActivityWeather = {
    tempMin: tMin, tempMax: tMax, feels: feels / n, rainMm: rain, windKmh: wind / n, gustKmh: gust,
    windFromDeg, windCompass: compass(windFromDeg), code,
  };
  // Share of the distance into / across / with the wind (calm air: skip, it's noise).
  if (wind / n >= 5) {
    const dist = { headwind: 0, crosswind: 0, tailwind: 0 };
    const pts = track.filter(p => p.lat !== 0 || p.lon !== 0);
    for (let i = 1; i < pts.length; i++) {
      const d = haversineM(pts[i - 1], pts[i]);
      if (d > 0.5) dist[windRelation(windFromDeg, bearingDeg(pts[i - 1], pts[i]))] += d;
    }
    const total = dist.headwind + dist.crosswind + dist.tailwind;
    if (total > 200) {
      w.headShare = dist.headwind / total; w.crossShare = dist.crosswind / total; w.tailShare = dist.tailwind / total;
    }
  }
  return w;
}

const CACHE_PREFIX = 'activityWeather.';

/** Cached answer, or one Open-Meteo call. null = no GPS, offline, or nothing for those hours. */
export async function fetchActivityWeather(
  key: string, startMs: number, durationS: number, track: LatLon[],
): Promise<ActivityWeather | null> {
  const first = track.find(p => p.lat !== 0 || p.lon !== 0);
  if (!key || !Number.isFinite(startMs) || !first) return null;
  const endMs = startMs + Math.max(0, durationS) * 1000;
  const archived = Date.now() - endMs > 6 * 86400000;
  if (archived) {
    try {
      const hit = await AsyncStorage.getItem(CACHE_PREFIX + key);
      if (hit) return JSON.parse(hit);
    } catch { /* re-ask */ }
  }
  const ymd = (ms: number) => new Date(ms).toISOString().slice(0, 10);
  const params = new URLSearchParams({
    latitude: first.lat.toFixed(4), longitude: first.lon.toFixed(4),
    hourly: 'temperature_2m,apparent_temperature,precipitation,wind_speed_10m,wind_direction_10m,wind_gusts_10m,weather_code',
    timezone: 'GMT', start_date: ymd(startMs), end_date: ymd(endMs),
  });
  const base = archived ? 'https://archive-api.open-meteo.com/v1/archive' : 'https://api.open-meteo.com/v1/forecast';
  try {
    const res = await fetch(`${base}?${params.toString()}`, { headers: { 'User-Agent': 'Sommet/1.0' } });
    if (!res.ok) return null;
    const body = await res.json();
    const w = body?.hourly ? summarizeHourly(body.hourly, startMs, endMs, track) : null;
    if (!w) return null;
    w.source = archived ? 'archive' : 'forecast';
    if (archived) AsyncStorage.setItem(CACHE_PREFIX + key, JSON.stringify(w)).catch(() => {});
    return w;
  } catch {
    return null;   // offline: the line just stays hidden
  }
}
