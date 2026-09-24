import { NativeModules } from 'react-native';
import { base64ToBytes, bytesToBase64 } from './Base64';
import {
  decodeBrytonWorkout, encodeBrytonWorkout, schemaToBryton,
  type BrytonWorkout, type Thresholds,
} from './BrytonFit';
import {
  readBrytonProfile, writeBrytonProfile,
  type BrytonProfile,
} from './BrytonProfile';

// High-level Bryton Aero 60 access over USB Mass Storage, bridging the native BrytonUsb module
// (libaums, moves raw bytes) to the pure-TS FIT/profile logic (BrytonFit.ts / BrytonProfile.ts).
// The native side is the same libaums transport GarminModule uses; here we just read/write the
// known paths and en/decode. The Aero 60 must be plugged into the tablet/phone over USB-OTG.

const Native = NativeModules.BrytonUsb as {
  connect(): Promise<{ found: boolean; name: string }>;
  readFile(path: string): Promise<string>;      // base64
  writeFile(path: string, base64: string): Promise<boolean>;
  listDir(path: string): Promise<string[]>;
  disconnect(): Promise<boolean>;
};

const PROFILE_PATH = 'System/Profile.bin';
const PLAN_DIR = 'System/Plan/Cycling';

export function isBrytonUsbAvailable(): boolean {
  return !!Native && typeof Native.connect === 'function';
}

export async function connectBryton(): Promise<{ found: boolean; name: string }> {
  return Native.connect();
}
export async function disconnectBryton(): Promise<void> {
  try { await Native.disconnect(); } catch { /* ignore */ }
}

export async function readProfile(): Promise<BrytonProfile> {
  const bytes = base64ToBytes(await Native.readFile(PROFILE_PATH));
  return readBrytonProfile(bytes);
}

// Read-modify-write: pull the current Profile.bin, patch only the given fields (all copies), and
// write it back byte-for-byte the same length. Never blind-writes the whole file.
export async function writeProfile(changes: Partial<BrytonProfile>): Promise<void> {
  const bytes = base64ToBytes(await Native.readFile(PROFILE_PATH));
  const patched = writeBrytonProfile(bytes, changes);
  await Native.writeFile(PROFILE_PATH, bytesToBase64(patched));
}

export async function listWorkouts(): Promise<string[]> {
  try {
    return (await Native.listDir(PLAN_DIR)).filter(n => n.toLowerCase().endsWith('.fit'));
  } catch { return []; }
}

export async function readWorkout(fileName: string): Promise<BrytonWorkout> {
  return decodeBrytonWorkout(base64ToBytes(await Native.readFile(`${PLAN_DIR}/${fileName}`)));
}

function safeName(name: string): string {
  const base = (name || 'Workout').replace(/[^A-Za-z0-9 _-]/g, '').trim().slice(0, 40) || 'Workout';
  return `${base}.fit`;
}

// A Bryton-native workout (targets already in %/rpm/km-h — what the builder produces).
export async function sendNativeWorkout(w: BrytonWorkout): Promise<string> {
  const file = safeName(w.name);
  await Native.writeFile(`${PLAN_DIR}/${file}`, bytesToBase64(encodeBrytonWorkout(w)));
  return file;
}

// A workout in the project schema (absolute W/bpm) — converted with the DEVICE'S OWN thresholds
// so the % the watch resolves matches the plan. Reads Profile.bin for FTP/MaxHR/LTHR.
export async function sendSchemaWorkout(workout: any): Promise<string> {
  const prof = await readProfile();
  const thr: Thresholds = { ftp: prof.ftp, maxHr: prof.maxHr, lthr: prof.lthr };
  const bryton = schemaToBryton(workout, thr);
  return sendNativeWorkout(bryton);
}
