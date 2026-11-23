import { NativeModules, PermissionsAndroid, Platform } from 'react-native';

// Where this tablet or phone is, for the POIs map to open there (André, 2026-11-23). Android asks
// the owner once; without an answer or a fix the caller uses the weather place. The position
// stays on the device.
export interface DevicePlace { lat: number; lon: number; accuracy: number; ageSeconds: number }

let askedThisRun = false;

export async function currentPlace(timeoutMs = 20000): Promise<DevicePlace | null> {
  const native = NativeModules.DeviceLocation;
  if (Platform.OS !== 'android' || !native) return null;
  const permission = PermissionsAndroid.PERMISSIONS.ACCESS_FINE_LOCATION;
  let allowed = await PermissionsAndroid.check(permission);
  if (!allowed && !askedThisRun) {
    askedThisRun = true;
    allowed = (await PermissionsAndroid.request(permission)) === PermissionsAndroid.RESULTS.GRANTED;
  }
  if (!allowed) return null;
  return native.current(timeoutMs);
}
