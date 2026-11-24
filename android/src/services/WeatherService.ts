import { useEffect, useRef, useState } from 'react';
import AsyncStorage from '@react-native-async-storage/async-storage';
import {
  BEACONDB_URL, LIVE_SECONDS, LOOKUP_AGENT, DevicePlace, onDevicePlace, placeIfAllowed, rememberedPlace,
} from './DeviceLocation';

// v3.0 UI port (2026-08-09, "replicate the desktop version feature wise") - ports
// desktop/src/services/weatherservice.cpp + qml/viewmodels/WeatherViewModel.qml byte-for-byte
// in intent: same Open-Meteo endpoint/params, same Nominatim reverse-geocode, same
// IP-based location fallback (same HTTPS services), same WMO weather-code -> icon/label mapping, same
// 10-minute auto-refresh, same "hide until hasFetchedOnce, then show a friendly offline
// message rather than an error" rule. No API key anywhere, matching docs/reference/sommet-spec.md's own
// "Open-Meteo (no API key)" choice - see weatherservice.h's own header comment.

export interface ForecastDay {
  date: string;
  high: number;
  low: number;
  code: number;
}

export interface WeatherData {
  currentTemperature: number;
  currentWeatherCode: number;
  windSpeed: number;
  todayHigh: number;
  todayLow: number;
  forecast: ForecastDay[];
}

const REFRESH_INTERVAL_MS = 10 * 60 * 1000;
// A failed fetch is tried again after a minute (desktop parity, 2026-11-24).
const RETRY_AFTER_FAILURE_MS = 60 * 1000;
// Same real "no location configured yet" placeholder as weatherservice.h's own
// m_latitude/m_longitude defaults - roughly central Europe, not 0,0 (the Gulf of Guinea).
const DEFAULT_LAT = 48.85;
const DEFAULT_LON = 2.35;

export async function fetchWeather(latitude: number, longitude: number): Promise<WeatherData | null> {
  // URLSearchParams + string concat, not `new URL()` - matches ApiStrava.ts's
  // own established pattern in this codebase (URL's own constructor isn't used anywhere
  // else here).
  const params = new URLSearchParams({
    latitude: String(latitude),
    longitude: String(longitude),
    current: 'temperature_2m,weather_code,wind_speed_10m',
    daily: 'temperature_2m_max,temperature_2m_min,weather_code',
    timezone: 'auto',
    forecast_days: '3',
  });
  // No network (tablet asleep overnight, Wi-Fi off) makes fetch() throw "Network request failed".
  // Return null like the location and place-name lookups: thrown, it went unhandled from the
  // 10-minute refresh and left the card stuck on loading (tablet, 2026-10-23).
  let body: any;
  try {
    const res = await fetch(`https://api.open-meteo.com/v1/forecast?${params.toString()}`);
    if (!res.ok) return null;
    body = await res.json();
  } catch {
    return null;
  }
  const current = body?.current;
  const daily = body?.daily;
  if (!current || !daily?.time?.length) return null;

  const forecast: ForecastDay[] = daily.time.map((date: string, i: number) => ({
    date,
    high: daily.temperature_2m_max?.[i] ?? 0,
    low: daily.temperature_2m_min?.[i] ?? 0,
    code: daily.weather_code?.[i] ?? -1,
  }));

  return {
    currentTemperature: current.temperature_2m ?? 0,
    currentWeatherCode: current.weather_code ?? -1,
    windSpeed: current.wind_speed_10m ?? 0,
    todayHigh: forecast[0]?.high ?? 0,
    todayLow: forecast[0]?.low ?? 0,
    forecast,
  };
}

export async function fetchPlaceName(latitude: number, longitude: number): Promise<string> {
  const params = new URLSearchParams({
    lat: String(latitude),
    lon: String(longitude),
    format: 'json',
    zoom: '10',
  });

  try {
    // Nominatim's usage policy requires a real identifying User-Agent - same real header
    // weatherservice.cpp's own fetchPlaceName() sends.
    const res = await fetch(`https://nominatim.openstreetmap.org/reverse?${params.toString()}`, {
      headers: { 'User-Agent': 'Sommet/2.0' },
    });
    if (!res.ok) return '';
    const body = await res.json();
    const address = body?.address ?? {};
    for (const key of ['city', 'town', 'village', 'municipality', 'county']) {
      if (address[key]) return address[key];
    }
    return '';
  } catch {
    return '';
  }
}

// IP-based location over HTTPS (2026-10-22). It used ip-api.com, whose free tier is plain HTTP
// only - which Android blocks (network_security_config.xml), so the weather always fell back to
// the default. Same services as the desktop now, free and without a key: ipapi.co first,
// get.geojs.io if that fails or is rate-limited. City-level at best, not true GPS.
const IP_LOCATION_SERVICES = [
  'https://ipapi.co/json/',
  'https://get.geojs.io/v1/ip/geo.json',
];

export async function detectLocationFromIp(): Promise<{ latitude: number; longitude: number } | null> {
  // BeaconDB first, with nothing but the address it sees: it placed a Vodafone line in Leiria
  // near Leiria, where ipapi.co says Faro (2026-11-23). Its IP data is DB-IP's (CC BY 4.0).
  try {
    const res = await fetch(BEACONDB_URL, {
      method: 'POST', headers: { 'Content-Type': 'application/json', 'User-Agent': LOOKUP_AGENT },
      body: JSON.stringify({ wifiAccessPoints: [] }),
    });
    if (res.ok) {
      const body = await res.json();
      const latitude = Number(body?.location?.lat), longitude = Number(body?.location?.lng);
      if (Number.isFinite(latitude) && Number.isFinite(longitude)) return { latitude, longitude };
    }
  } catch { /* the next service */ }
  for (const url of IP_LOCATION_SERVICES) {
    try {
      const res = await fetch(url, { headers: { 'User-Agent': 'Sommet/2.0' } });
      if (!res.ok) continue;
      const body = await res.json();
      if (body?.error) continue;
      // ipapi.co sends the coordinates as numbers, get.geojs.io as strings.
      const latitude = Number(body?.latitude);
      const longitude = Number(body?.longitude);
      if (body?.latitude == null || body?.longitude == null
          || !Number.isFinite(latitude) || !Number.isFinite(longitude)) continue;
      return { latitude, longitude };
    } catch {
      // next service
    }
  }
  return null;
}

// Real WMO weather-interpretation codes (open-meteo.com/en/docs, "WMO Weather interpretation
// codes") - same ranges as WeatherViewModel.qml's own iconFor()/labelFor(), emoji instead of
// this app's own Material-Symbols-subset Icon.tsx (which has no weather glyphs at all - see
// assets/fonts/NOTICE.md on why that font is deliberately small/subsetted; adding 7 new
// glyphs to it isn't worth it for a widget emoji already render natively).
export function weatherEmoji(code: number): string {
  if (code === 0) return '☀️';
  if (code === 1 || code === 2) return '🌤️';
  if (code === 3) return '☁️';
  if (code === 45 || code === 48) return '🌫️';
  if (code >= 51 && code <= 67) return '🌧️';
  if (code >= 80 && code <= 82) return '🌧️';
  if (code === 71 || code === 73 || code === 75 || code === 77) return '❄️';
  if (code === 85 || code === 86) return '❄️';
  if (code === 95 || code === 96 || code === 99) return '⛈️';
  return '🌤️';
}

interface WeatherState {
  loading: boolean;
  available: boolean;
  hasFetchedOnce: boolean;
  placeName: string;
  data: WeatherData | null;
}

// Where the weather is for. Shared by Home's weather card and Settings -> Weather (desktop
// parity: one WeatherService for the whole app). A place set by hand in Settings is saved and
// wins. Otherwise: the device's position, else the last one it had, else the IP address, which
// can be a far-off city (Faro for a Vodafone line in Leiria, André 2026-11-23).
// The position is rounded to 0.01 degree (about 1 km) here: the weather and place-name services
// are outside ones, and a forecast needs no more than that.
export interface WeatherLocation {
  latitude: number;
  longitude: number;
  placeName: string;
  manual: boolean;
}
let currentLocation: WeatherLocation = {
  latitude: DEFAULT_LAT, longitude: DEFAULT_LON, placeName: '', manual: false,
};
let detectedOnce = false;
const locationListeners = new Set<(l: WeatherLocation) => void>();

function publishLocation(next: WeatherLocation) {
  currentLocation = next;
  locationListeners.forEach(f => f(next));
}

export function getWeatherLocation(): WeatherLocation {
  return currentLocation;
}

export function useWeatherLocation(): WeatherLocation {
  const [loc, setLoc] = useState(currentLocation);
  useEffect(() => {
    locationListeners.add(setLoc);
    setLoc(currentLocation);
    return () => { locationListeners.delete(setLoc); };
  }, []);
  return loc;
}

const MANUAL_KEY = 'weather.manualPlace';
const roughly = (v: number) => Math.round(v * 100) / 100;

function publishDevicePlace(p: DevicePlace) {
  if (currentLocation.manual) return;
  publishLocation({ latitude: roughly(p.lat), longitude: roughly(p.lon),
                    placeName: currentLocation.placeName, manual: false });
}
// A position read by any screen (the POIs map) moves the weather too.
onDevicePlace(p => { if (p.ageSeconds < LIVE_SECONDS) publishDevicePlace(p); });

async function locateFromDevice(): Promise<void> {
  const last = await rememberedPlace();
  if (last) {
    publishDevicePlace(last);
  } else {
    const loc = await detectLocationFromIp();
    if (loc && !currentLocation.manual) {
      publishLocation({ ...loc, placeName: currentLocation.placeName, manual: false });
    }
  }
  placeIfAllowed().catch(() => {});              // a live fix, if there is one, moves it again
}

/** At start: the saved place if one was set by hand, else where the device is. */
async function startLocating(): Promise<void> {
  detectedOnce = true;
  try {
    const raw = await AsyncStorage.getItem(MANUAL_KEY);
    const saved = raw ? JSON.parse(raw) : null;
    if (saved && Number.isFinite(saved.latitude) && Number.isFinite(saved.longitude)) {
      publishLocation({ latitude: saved.latitude, longitude: saved.longitude, placeName: '', manual: true });
      return;
    }
  } catch { /* no saved place */ }
  await locateFromDevice();
}

/** Settings -> Weather -> "This device": forget the place set by hand and locate again. */
export async function locateAutomatically(): Promise<void> {
  detectedOnce = true;
  currentLocation = { ...currentLocation, manual: false };
  await AsyncStorage.removeItem(MANUAL_KEY).catch(() => {});
  publishLocation(currentLocation);
  await locateFromDevice();
}

/** Settings -> Weather -> Manual: use these coordinates, now and after a restart. */
export function setManualLocation(latitude: number, longitude: number): void {
  detectedOnce = true;
  AsyncStorage.setItem(MANUAL_KEY, JSON.stringify({ latitude, longitude })).catch(() => {});
  publishLocation({ latitude, longitude, placeName: '', manual: true });
}

// Home's weather card. Follows the shared location above: a change made in Settings refreshes
// it (Home stays mounted under Settings).
export function useWeather() {
  const [state, setState] = useState<WeatherState>({
    loading: false, available: false, hasFetchedOnce: false, placeName: '', data: null,
  });

  const retry = useRef<ReturnType<typeof setTimeout> | null>(null);
  async function refresh() {
    if (retry.current) { clearTimeout(retry.current); retry.current = null; }
    setState(s => ({ ...s, loading: true }));
    const { latitude, longitude } = currentLocation;
    const [data, placeName] = await Promise.all([
      fetchWeather(latitude, longitude),
      fetchPlaceName(latitude, longitude),
    ]);
    setState({
      loading: false,
      available: data !== null,
      hasFetchedOnce: true,
      placeName,
      data,
    });
    if (data === null) retry.current = setTimeout(refresh, RETRY_AFTER_FAILURE_MS);
    // Share the place name too (Settings -> Weather shows it), if the location hasn't moved since.
    if (currentLocation.latitude === latitude && currentLocation.longitude === longitude
        && currentLocation.placeName !== placeName) {
      publishLocation({ ...currentLocation, placeName });
    }
  }

  useEffect(() => {
    // A location change refreshes; a place-name-only update (published by refresh) does not.
    let last = { latitude: currentLocation.latitude, longitude: currentLocation.longitude };
    let fetched = false;
    const onLocation = (l: WeatherLocation) => {
      if (l.latitude === last.latitude && l.longitude === last.longitude) return;
      last = { latitude: l.latitude, longitude: l.longitude };
      fetched = true;
      refresh();
    };
    locationListeners.add(onLocation);
    if (!detectedOnce) startLocating().then(() => { if (!fetched) refresh(); });
    else refresh();
    const iv = setInterval(refresh, REFRESH_INTERVAL_MS);
    return () => {
      clearInterval(iv); locationListeners.delete(onLocation);
      if (retry.current) clearTimeout(retry.current);
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return { ...state, refresh };
}
