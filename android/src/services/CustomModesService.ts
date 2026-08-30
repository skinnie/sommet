import { connect, disconnect, getDeviceInfo, readCustomModesRaw } from '../native/AmbitUsbModule';
import { base64ToBytes } from './Base64';
import { setCustomModesCache } from './CustomModesCache';
import { decode, ExerciseMode } from './CustomModesReader';
import { isAmbit12 } from './AmbitSettingsService';
import { readLegacySportModes, LegacyMode } from './AmbitLegacySportModes';
import {
  renameMode as renameModeRaw, RenameModeResult,
  writeField as writeFieldRaw, WriteFieldResult,
  writeDisplayField as writeDisplayFieldRaw, WriteDisplayFieldResult,
} from './CustomModesWriter';

// Thin connect/read/disconnect and connect/write/disconnect orchestration, matching
// AmbitSettingsService.ts's own pattern exactly - over USB this screen owns its own
// short-lived connection per action rather than assuming one is already open.
//
// Transport (André, 2026-08-17): every op takes `overBle`. Over BLE the GATT link is already
// open and owned by the Home screen, and the underlying readCustomModesRaw()/writeField() act
// on the same shared native g_device regardless of transport - so over BLE we must NOT call
// the USB connect()/disconnect() (that opens a USB device, popping the "insert USB / OTG"
// permission prompt and tearing down the live BLE session). This mirrors SgeeService's own
// USB-vs-BLE branch: over USB (overBle=false) open/close a short-lived cable link; over BLE
// (overBle=true) just run the op on the already-open link. The caller (SportModesScreen) gets
// `overBle` from HomeScreen's bleConnected via the navigation param.
//
// Ambit3-only: Kailash's own memory map reports no CustomModes region at all (confirmed empty,
// see custom_modes_andre.md's Kailash section) - the caller is responsible for not showing this
// feature for a Kailash connection, the same !HomeViewModel.isKailash gate the desktop app's
// own NavRail.qml uses.

export interface ReadCustomModesState {
  phase: 'idle' | 'connecting' | 'reading' | 'done' | 'error';
  modes?: ExerciseMode[];
  // True when the connected watch is an Ambit1/2 read via the legacy path (region 0x2000),
  // not the Ambit3 CustomModes region. The screen renders these read-only and skips the
  // Ambit3-only structural/summary read (which would crash on this family).
  legacy?: boolean;
  error?: string;
}

// Ambit1/2 legacy mode -> the ExerciseMode shape the Sport Modes screen renders. displays is
// left empty (legacy display config isn't decoded here); useHw carries the raw hrbelt/pods
// bitfield so the pod row shows.
function legacyToExerciseMode(m: LegacyMode): ExerciseMode {
  return {
    settings: {
      name: m.name,
      activityId: m.activityId,
      useHw: m.useHw,
      altiBaroMode: m.altiBaroMode,
      recordingInterval: m.recordingInterval,
      autolap: m.autolapM,
      hrHigh: m.heartrateMax,
      hrLow: m.heartrateMin,
      hrLimitsUse: m.useHrLimits ? 1 : 0,
    },
    displays: [],
  };
}

/** Real, read-only (0x0b17 flash read) - safe any time the watch is connected. Over BLE
 * (overBle) runs on the already-open link; over USB opens its own short-lived connection. */
export async function readCustomModes(
  onState: (s: ReadCustomModesState) => void, overBle = false, maxUserDisplays = 8,
): Promise<void> {
  onState({ phase: overBle ? 'reading' : 'connecting' });
  if (!overBle) {
    try {
      await connect();
    } catch (e: any) {
      onState({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' });
      return;
    }
  }
  onState({ phase: 'reading' });
  try {
    // Ambit1/2 (Bluebird): the Ambit3 CustomModes region (readCustomModesRaw) doesn't exist
    // here and reading/decoding it crashes the Sport Modes screen. Read the legacy 90-byte
    // modes off region 0x2000 instead (desktop parity). USB-only family, so only over cable.
    if (!overBle) {
      let legacy = false;
      try { legacy = isAmbit12((await getDeviceInfo()).name); } catch { /* assume Ambit3 */ }
      if (legacy) {
        const modes = (await readLegacySportModes()).map(legacyToExerciseMode);
        onState({ phase: 'done', modes, legacy: true });
        return;
      }
    }
    const bytes = base64ToBytes(await readCustomModesRaw());
    setCustomModesCache(bytes);   // seed the cache so edits don't re-read the 12KB region
    const decoded = decode(bytes, maxUserDisplays);
    onState({ phase: 'done', modes: decoded.exerciseModes });
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Failed to read CustomModes' });
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

export interface CustomModesWriteState {
  phase: 'idle' | 'connecting' | 'writing' | 'done' | 'error';
  error?: string;
}

/** Real mechanism, NOT yet hardware-confirmed on Android (see writeCustomModesRaw()'s own
 * doc comment in native/AmbitUsbModule.ts). Renames one mode everywhere it appears in the
 * real parsed tree; re-reads afterward to confirm. */
export async function renameCustomMode(
  fromName: string, toName: string, onState: (s: CustomModesWriteState) => void, overBle = false,
): Promise<RenameModeResult | undefined> {
  onState({ phase: overBle ? 'writing' : 'connecting' });
  if (!overBle) {
    try {
      await connect();
    } catch (e: any) {
      onState({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' });
      return undefined;
    }
  }
  onState({ phase: 'writing' });
  try {
    const result = await renameModeRaw(fromName, toName);
    onState({ phase: result.ok ? 'done' : 'error',
      error: result.ok ? undefined : (result.error ?? 'Write sent but not confirmed by re-read') });
    return result;
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Failed to rename' });
    return undefined;
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

/** Real mechanism, NOT yet hardware-confirmed on Android. Writes one or more of a mode's
 * fixed uint16 settings fields (Autolap, HrHigh, HrLow, HrLimitsUse, UseHw, ...). */
export async function writeCustomModeField(
  modeName: string, fields: Record<string, number>, onState: (s: CustomModesWriteState) => void,
  overBle = false,
): Promise<WriteFieldResult | undefined> {
  onState({ phase: overBle ? 'writing' : 'connecting' });
  if (!overBle) {
    try {
      await connect();
    } catch (e: any) {
      onState({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' });
      return undefined;
    }
  }
  onState({ phase: 'writing' });
  try {
    const result = await writeFieldRaw(modeName, fields);
    onState({ phase: result.ok ? 'done' : 'error',
      error: result.ok ? undefined : (result.error ?? 'Write sent but not confirmed by re-read') });
    return result;
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Failed to write field(s)' });
    return undefined;
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

/** Real mechanism, NOT yet hardware-confirmed on Android. Changes which data a display row
 * shows - give newIndexName and/or newTypeName; the omitted one is left unchanged. Real,
 * live-confirmed 2026-08-08 (on desktop): for the common Index=FT_TIME case, Type (not
 * Index) is what actually selects the rendered content - see CustomModesWriter.ts's own
 * writeDisplayField() doc comment. */
export async function writeCustomModeDisplayField(
  modeName: string, displayIndex: number, fieldIndex: number,
  newIndexName: string | undefined, newTypeName: string | undefined,
  onState: (s: CustomModesWriteState) => void, overBle = false,
): Promise<WriteDisplayFieldResult | undefined> {
  onState({ phase: overBle ? 'writing' : 'connecting' });
  if (!overBle) {
    try {
      await connect();
    } catch (e: any) {
      onState({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' });
      return undefined;
    }
  }
  onState({ phase: 'writing' });
  try {
    const result = await writeDisplayFieldRaw(modeName, displayIndex, fieldIndex, newIndexName, newTypeName);
    onState({ phase: result.ok ? 'done' : 'error',
      error: result.ok ? undefined : (result.error ?? 'Write sent but not confirmed by re-read') });
    return result;
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Failed to write display field' });
    return undefined;
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}
