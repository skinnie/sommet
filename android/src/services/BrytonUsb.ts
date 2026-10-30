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
import { EE_URL, EE_PATH, EE_DATE_PATH, decodeAlp, readDate, goodUntil, planEe, type EeInfo } from './BrytonEe';
import { saveWatchBackup } from './WatchBackup';

// High-level Bryton Aero 60 access over USB Mass Storage, bridging the native BrytonUsb module
// (libaums, moves raw bytes) to the pure-TS FIT/profile logic (BrytonFit.ts / BrytonProfile.ts).
// The native side is the same libaums transport GarminModule uses; here we just read/write the
// known paths and en/decode. The Aero 60 must be plugged into the tablet/phone over USB-OTG.

const Native = NativeModules.BrytonUsb as {
  detect(): Promise<{ plugged: boolean; granted: boolean }>;
  connect(): Promise<{ found: boolean; name: string }>;
  readFile(path: string): Promise<string>;      // base64
  writeFile(path: string, base64: string): Promise<boolean>;
  listDir(path: string): Promise<string[]>;
  disconnect(): Promise<boolean>;
};

// Non-prompting: is a Bryton plugged in, and do we already hold a usable folder grant?
export async function detectBryton(): Promise<{ plugged: boolean; granted: boolean }> {
  if (!isBrytonUsbAvailable() || typeof Native.detect !== 'function') return { plugged: false, granted: false };
  try { return await Native.detect(); } catch { return { plugged: false, granted: false }; }
}

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

// ── GPS prediction data (System/AssitOff.alp + date.txt, BrytonEe.ts; André 2026-10-30) ──
const readOrNull = async (path: string): Promise<string | null> => {
  try { return await Native.readFile(path); } catch { return null; }
};

// What the device holds and until when (null: no file / unreadable).
export async function readGpsData(): Promise<EeInfo | null> {
  const alp = await readOrNull(EE_PATH);
  if (!alp) return null;
  const info = decodeAlp(base64ToBytes(alp));
  if (!info.ok) return null;
  const date = await readOrNull(EE_DATE_PATH);
  return goodUntil(info, readDate(date ? base64ToBytes(date) : null));
}

// Download u-blox's 14-day file and put it on the device with a matching date.txt - only over a
// file of the same kind (planEe), after saving both old files in the app's backups, then read
// both back to confirm.
export async function updateGpsData(): Promise<{ ok: boolean; wrote: boolean; message: string }> {
  const res = await fetch(EE_URL, { headers: { 'User-Agent': 'Sommet' } });
  if (!res.ok) return { ok: false, wrote: false, message: `download failed (HTTP ${res.status})` };
  const next = new Uint8Array(await res.arrayBuffer());
  const oldB64 = await readOrNull(EE_PATH);
  const oldDateB64 = await readOrNull(EE_DATE_PATH);
  const plan = planEe(oldB64 ? base64ToBytes(oldB64) : null,
    oldDateB64 ? base64ToBytes(oldDateB64) : null, next);
  if (!plan.write) {
    return { ok: !plan.error, wrote: false, message: plan.error ?? plan.note ?? 'nothing to do' };
  }
  await saveWatchBackup('bryton-ee', oldB64!, 'aero60');
  if (oldDateB64) await saveWatchBackup('bryton-ee-date', oldDateB64, 'aero60');
  const b64 = bytesToBase64(next), dateB64 = bytesToBase64(plan.date);
  await Native.writeFile(EE_PATH, b64);
  await Native.writeFile(EE_DATE_PATH, dateB64);
  const ok = (await Native.readFile(EE_PATH)) === b64 && (await Native.readFile(EE_DATE_PATH)) === dateB64;
  return ok
    ? { ok: true, wrote: true, message: `GPS data updated, good until ${plan.next.last}` }
    : { ok: false, wrote: true, message: 'the read-back differs from what was written' };
}
