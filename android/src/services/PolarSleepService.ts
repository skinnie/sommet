import { NativeModules } from 'react-native';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { processRecording, type Recording, type SleepResult } from './sleepStage';

// Overnight HRV + resting HR from a Polar Verity Sense, recorded OFFLINE so André sleeps with just
// the band and connects in the morning. The native `PolarSleep` module drives the band's offline-
// recording lifecycle (Polar BLE SDK); all the DSP+HRV runs on-device in sleepStage.ts (which is
// the twin of the desktop's tools/sleep_stage.py). This service is the thin glue + the local
// night-history that the Sleep screen charts.
//
// Bedtime:  armForNight(deviceId)   -> band starts recording PPG+ACC to its own flash, disconnect.
// Morning:  fetchAndProcess(deviceId) -> download the night, derive HRV, store it, return it.
//
// Modeled on HrStrapService.ts: a thin wrapper over an OPTIONAL native module. Until a build ships
// the module, isPolarSleepAvailable() is false and the screen degrades gracefully.

const Native = (NativeModules as any).PolarSleep as
  | {
      search(): Promise<PolarBand[]>;
      arm(deviceId: string): Promise<{ armed: boolean; types: string[] }>;
      status(deviceId: string): Promise<{ recording: boolean; types: string[] }>;
      stop(deviceId: string): Promise<{ stopped: boolean }>;
      fetchLatest(deviceId: string, deleteAfter: boolean): Promise<Recording>;
    }
  | undefined;

export interface PolarBand {
  deviceId: string;
  name: string;
  rssi: number;
}

// One stored night: the headline numbers + the per-window curve, keyed by the morning's date.
export interface SleepNight {
  date: string; // YYYY-MM-DD (the morning we processed it)
  startTime?: string;
  overnightRmssdMs: number | null;
  restingHrBpm: number | null;
  minHrBpm: number | null;
  overnightLnRmssdX20: number | null;
  beatsUsed: number;
  windows: { startS: number; rmssdMs: number | null; meanHrBpm: number | null }[];
}

const DEVICE_KEY = 'polarsleep/deviceId';
const HISTORY_KEY = 'polarsleep/history';
const MAX_NIGHTS = 120; // ~4 months of trend is plenty; keep storage bounded

export function isPolarSleepAvailable(): boolean {
  return !!Native;
}

/** Scan for nearby Polar bands (Verity Sense advertises as "Polar Sense <id>"). */
export async function searchBands(): Promise<PolarBand[]> {
  if (!Native) throw new Error('native-missing');
  return Native.search();
}

/** Remember which band is André's, so bedtime/morning don't need a scan every time. */
export async function getSavedDeviceId(): Promise<string | null> {
  try {
    return await AsyncStorage.getItem(DEVICE_KEY);
  } catch {
    return null;
  }
}

export async function saveDeviceId(deviceId: string): Promise<void> {
  try {
    await AsyncStorage.setItem(DEVICE_KEY, deviceId);
  } catch {
    /* non-fatal */
  }
}

/** Bedtime: start offline PPG+ACC recording on the band, then it can be disconnected all night. */
export async function armForNight(deviceId: string): Promise<{ armed: boolean; types: string[] }> {
  if (!Native) throw new Error('native-missing');
  await saveDeviceId(deviceId);
  return Native.arm(deviceId);
}

export async function getStatus(
  deviceId: string,
): Promise<{ recording: boolean; types: string[] }> {
  if (!Native) throw new Error('native-missing');
  return Native.status(deviceId);
}

export async function stopRecording(deviceId: string): Promise<{ stopped: boolean }> {
  if (!Native) throw new Error('native-missing');
  return Native.stop(deviceId);
}

/**
 * Morning: download the night's raw PPG+ACC from the band, derive overnight HRV + resting HR
 * on-device, store the summary in the local night-history, and return the full result. By default
 * the band's copy is deleted after a successful download so its flash is freed for the next night.
 */
export async function fetchAndProcess(
  deviceId: string,
  deleteAfter = true,
): Promise<SleepResult> {
  if (!Native) throw new Error('native-missing');
  await saveDeviceId(deviceId);
  const recording = await Native.fetchLatest(deviceId, deleteAfter);
  const result = processRecording(recording);
  if (result.ok) await storeNight(result);
  return result;
}

/** Process a recording we already have in hand (e.g. exported from the desktop) - no band needed. */
export function processLocalRecording(recording: Recording): SleepResult {
  return processRecording(recording);
}

async function storeNight(result: SleepResult): Promise<void> {
  const date = new Date().toISOString().slice(0, 10);
  const night: SleepNight = {
    date,
    startTime: result.startTime,
    overnightRmssdMs: result.overnight.overnightRmssdMs,
    restingHrBpm: result.overnight.restingHrBpm,
    minHrBpm: result.overnight.minHrBpm,
    overnightLnRmssdX20: result.overnight.overnightLnRmssdX20,
    beatsUsed: result.beatsUsed,
    windows: result.windows.map((w) => ({
      startS: w.startS,
      rmssdMs: w.rmssdMs,
      meanHrBpm: w.meanHrBpm,
    })),
  };
  try {
    const hist = await getHistory();
    // One night per date - a re-process replaces that morning's entry rather than duplicating it.
    const merged = [...hist.filter((n) => n.date !== date), night].sort((a, b) =>
      a.date < b.date ? -1 : 1,
    );
    const trimmed = merged.slice(-MAX_NIGHTS);
    await AsyncStorage.setItem(HISTORY_KEY, JSON.stringify(trimmed));
  } catch {
    /* non-fatal: the reading still returned to the caller */
  }
}

/** The stored night-history, oldest first, for the Sleep screen's resting-HR / HRV trend. */
export async function getHistory(): Promise<SleepNight[]> {
  try {
    const raw = await AsyncStorage.getItem(HISTORY_KEY);
    return raw ? (JSON.parse(raw) as SleepNight[]) : [];
  } catch {
    return [];
  }
}

export async function clearHistory(): Promise<void> {
  try {
    await AsyncStorage.removeItem(HISTORY_KEY);
  } catch {
    /* non-fatal */
  }
}
