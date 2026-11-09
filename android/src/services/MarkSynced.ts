// ─── MarkSynced ───────────────────────────────────────────────────────────────
// Experimental "mark synced workouts as synced for Suunto app and SuuntoLink" toggle,
// OFF by default. After the app reads a move, it can write the watch's own per-move synced
// flag (command 0x1201) so the official Suunto app / SuuntoLink treat it as already synced
// and don't duplicate it. Tradeoff: the move can no longer be re-retrieved from the watch
// if the Suunto app later fails to keep it - which is why it's opt-in. Nothing is deleted;
// the watch reclaims log space by circular-buffer wraparound regardless.
//
// This file is the PORTABLE GUARD, mirroring the desktop tools/mark_synced.py exactly: only
// the Ambit3 GEN4 firmware has a known log_synced entry id (0x8b, capture-confirmed vs a real
// SuuntoLink cable sync), so we only mark on those watches and refuse everything else rather
// than guess a wrong id. The native nativeAmbitMarkReadLogsSynced builds the actual SBEM push via
// libambit; this only decides support. BLE marking is unverified (the Suunto app never wrote
// this flag - it uses EventBoard events) and rides the same path by analogy.
//
// See the sommet activity-sync-no-delete finding and tools/ble_schema.py.

import AsyncStorage from '@react-native-async-storage/async-storage';
import * as AmbitUsbModule from '../native/AmbitUsbModule';

export const MARK_SYNCED_STORAGE_KEY = 'sommet:markSynced';

/** Whether the user has opted into the synced write-back. Default false. */
export async function isMarkSyncedEnabled(): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(MARK_SYNCED_STORAGE_KEY)) === '1';
  } catch {
    return false;
  }
}

export async function setMarkSyncedEnabled(enabled: boolean): Promise<void> {
  try {
    await AsyncStorage.setItem(MARK_SYNCED_STORAGE_KEY, enabled ? '1' : '0');
  } catch {
    // AsyncStorage failure just means the toggle won't persist; not fatal.
  }
}

/** '2.5.11' -> [2, 5, 11]. Missing parts read as 0. */
function fwTuple(fwVersion?: string): [number, number, number] {
  const p = (fwVersion ?? '0.0.0').split('.').map(n => parseInt(n, 10) || 0);
  return [p[0] ?? 0, p[1] ?? 0, p[2] ?? 0];
}

function gte(a: [number, number, number], b: [number, number, number]): boolean {
  for (let i = 0; i < 3; i++) {
    if (a[i] !== b[i]) return a[i] > b[i];
  }
  return true;
}

// Ambit3 GEN4 threshold, from openambit get_ambit3_fw_gen() (fw >= 2.4.1 on the Ambit3
// Peak/Sport/Run family). GEN4 is the only generation with a known log_synced_data_id.
const GEN4_MIN: [number, number, number] = [2, 4, 1];

export interface MarkSyncedSupport {
  supported: boolean;
  reason?: string;
}

/** Decide whether the connected watch supports the synced write-back, from its device_info
 * model + firmware. Cross-transport (model string is populated for both USB and BLE), and
 * intentionally matches the desktop mark_synced.py guard: Ambit3 Peak/Sport/Run on GEN4 fw
 * only. Vertical, Traverse/Alpha, Kailash, Ambit1/2 are all refused - openambit never mapped
 * a real synced entry id for them. */
export function resolveMarkSyncedSupport(model?: string, fwVersion?: string): MarkSyncedSupport {
  const m = (model ?? '').toLowerCase();
  const isAmbit3 = m.includes('ambit3') || m.includes('ambit 3');
  const isVertical = m.includes('vertical');
  if (!isAmbit3 || isVertical) {
    return { supported: false, reason: `mark-synced not known for this watch (${model || 'unknown'})` };
  }
  if (!gte(fwTuple(fwVersion), GEN4_MIN)) {
    return {
      supported: false,
      reason: `firmware ${fwVersion || '?'} predates the GEN4 mark-synced support (>= 2.4.1)`,
    };
  }
  return { supported: true };
}

/** Mark the `count` moves just read this session (native cache indices 0..count-1) synced on
 * the watch, IF the device supports it. Best-effort: resolves support once from device_info,
 * then marks each index, swallowing per-move failures so one bad write never aborts the sync
 * (nor the moves the user already got). Returns how many were marked. Shared by the USB and
 * BLE providers - both act on the same native g_device. `transportNote` only tags the logs
 * (the BLE path is unverified and says so). */
export async function markReadLogsSynced(count: number, transportNote = ''): Promise<number> {
  if (count <= 0) return 0;
  let model: string | undefined;
  let fwVersion: string | undefined;
  try {
    const info = await AmbitUsbModule.getDeviceInfo();
    model = info.model;
    fwVersion = info.fwVersion;
  } catch {
    // device info unreadable → treat as unsupported below
  }
  const support = resolveMarkSyncedSupport(model, fwVersion);
  if (!support.supported) {
    console.log(`[mark-synced]${transportNote} skipped: ${support.reason}`);
    return 0;
  }
  // The native side marks every move it actually read this session (its own g_log_dates
  // cache), not `count` - `count` is only the caller's "did we read anything" hint, which may
  // differ from the true read count if the GPX list ever drops an entry. Best-effort inside
  // native; a single move's failure never aborts the rest.
  try {
    const marked = await AmbitUsbModule.markReadLogsSynced();
    console.log(`[mark-synced]${transportNote} marked ${marked} move(s) on the watch`);
    return marked;
  } catch (e: any) {
    console.log(`[mark-synced]${transportNote} failed: ${e?.message ?? e}`);
    return 0;
  }
}
