import { NativeModules, PermissionsAndroid, Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

// Where this tablet or phone is, for the POIs map to open there (André, 2026-11-23). Android asks
// the owner once. The last real position is remembered, because indoors there may be no fix and
// the weather place comes from the IP address, which can be a far-off city (Faro for a Vodafone
// line in Leiria, 2026-11-23). The exact position stays on the device; the weather gets it rounded
// to about 1 km (WeatherService.ts).
//
// By Wi-Fi, when the owner switched it on (Settings > Weather; André, 2026-11-23: "can't we also
// know aprox location by wifi?"): the addresses of the Wi-Fi networks around go to BeaconDB, an
// open database, which answers with a position. It runs next to the GPS, which wins when it has a
// fix. Left out, as is the custom for these services: networks whose name ends in "_nomap", and
// addresses a device made up (locally administered). Fewer than two networks: nothing is sent.
export interface DevicePlace { lat: number; lon: number; accuracy: number; ageSeconds: number }

const LAST_KEY = 'device.lastPlace';
const WIFI_KEY = 'location.wifi';
export const BEACONDB_URL = 'https://api.beacondb.net/v1/geolocate';
export const LOOKUP_AGENT = 'Sommet/2.0 (open-source watch app)';
const MAX_NETWORKS = 20;

export async function wifiLookupOn(): Promise<boolean> {
  try { return (await AsyncStorage.getItem(WIFI_KEY)) === '1'; } catch { return false; }
}
export async function setWifiLookup(on: boolean): Promise<void> {
  try { await AsyncStorage.setItem(WIFI_KEY, on ? '1' : '0'); } catch { /* stays as it was */ }
}

/** What of the networks heard may be sent: the strongest real ones, at most MAX_NETWORKS. */
export function usableNetworks(heard: { bssid: string; level: number; ssid?: string }[]):
    { macAddress: string; signalStrength: number }[] {
  return heard
    .filter(n => /^([0-9a-f]{2}:){5}[0-9a-f]{2}$/i.test(n.bssid))
    .filter(n => !(n.ssid ?? '').toLowerCase().endsWith('_nomap'))
    .filter(n => (parseInt(n.bssid.slice(0, 2), 16) & 0x02) === 0)      // made-up addresses
    .sort((a, b) => b.level - a.level)
    .slice(0, MAX_NETWORKS)
    .map(n => ({ macAddress: n.bssid.toLowerCase(), signalStrength: Math.round(n.level) }));
}

/** BeaconDB's answer as a place; one that fell back to the IP address is not one. */
export function placeFromLookup(body: any): DevicePlace | null {
  const lat = Number(body?.location?.lat), lon = Number(body?.location?.lng);
  if (body?.fallback || !Number.isFinite(lat) || !Number.isFinite(lon)) return null;
  return { lat, lon, accuracy: Number(body?.accuracy) || 1000, ageSeconds: 0 };
}

async function wifiPlace(): Promise<DevicePlace | null> {
  if (!(await wifiLookupOn())) return null;
  const networks = usableNetworks(await NativeModules.DeviceLocation.wifiNearby());
  if (networks.length < 2) return null;
  const res = await fetch(BEACONDB_URL, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'User-Agent': LOOKUP_AGENT },
    body: JSON.stringify({ considerIp: false, wifiAccessPoints: networks }),
  });
  if (!res.ok) return null;
  const place = placeFromLookup(await res.json());
  if (place) {
    AsyncStorage.setItem(LAST_KEY, JSON.stringify(place)).catch(() => {});
    listeners.forEach(f => f(place));
  }
  return place;
}
/** A position older than this is where the device was, not where it is. */
export const LIVE_SECONDS = 600;

let askedThisRun = false;
const listeners = new Set<(p: DevicePlace) => void>();

/** Every position read, from whichever screen asked for it. */
export function onDevicePlace(f: (p: DevicePlace) => void): () => void {
  listeners.add(f);
  return () => { listeners.delete(f); };
}

async function read(timeoutMs: number): Promise<DevicePlace | null> {
  wifiPlace().catch(() => null);                 // quick and rough, next to the GPS
  const place: DevicePlace | null = await NativeModules.DeviceLocation.current(timeoutMs);
  if (place) {
    AsyncStorage.setItem(LAST_KEY, JSON.stringify(place)).catch(() => {});
    listeners.forEach(f => f(place));
  }
  return place;
}

/** Like currentPlace, but never shows Android's question (for the weather at start). */
export async function placeIfAllowed(timeoutMs = 30000): Promise<DevicePlace | null> {
  if (Platform.OS !== 'android' || !NativeModules.DeviceLocation) return null;
  if (!(await PermissionsAndroid.check(PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION))) return null;
  return read(timeoutMs);
}

/** The last real position this device had, or null. */
export async function rememberedPlace(): Promise<DevicePlace | null> {
  try {
    const raw = await AsyncStorage.getItem(LAST_KEY);
    const p = raw ? JSON.parse(raw) : null;
    return p && Number.isFinite(p.lat) && Number.isFinite(p.lon) ? p : null;
  } catch {
    return null;
  }
}

export async function currentPlace(timeoutMs = 60000): Promise<DevicePlace | null> {
  const native = NativeModules.DeviceLocation;
  if (Platform.OS !== 'android' || !native) return null;
  const permission = PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION;
  let allowed = await PermissionsAndroid.check(permission);
  if (!allowed && !askedThisRun) {
    askedThisRun = true;
    allowed = (await PermissionsAndroid.request(permission)) === PermissionsAndroid.RESULTS.GRANTED;
  }
  if (!allowed) return null;
  return read(timeoutMs);
}
