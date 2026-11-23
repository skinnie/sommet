import { NativeModules, PermissionsAndroid, Platform } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';

// Where this tablet or phone is, for the POIs map to open there (André, 2026-11-23). Android asks
// the owner once. The last real position is remembered, because indoors there may be no fix and
// the weather place comes from the IP address, which can be a far-off city (Faro for a Vodafone
// line in Leiria, 2026-11-23). The exact position stays on the device; the weather gets it rounded
// to about 1 km (WeatherService.ts).
export interface DevicePlace { lat: number; lon: number; accuracy: number; ageSeconds: number }

const LAST_KEY = 'device.lastPlace';
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
