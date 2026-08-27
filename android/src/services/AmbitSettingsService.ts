import { connect, disconnect, getDeviceInfo, readSettingsRaw, readPersonalSettings, isBleTransportActive } from '../native/AmbitUsbModule';
import { writePersonalField } from './AmbitPersonalSettingsWriter';
import {
  AMBIT3_SETTINGS_FIELDS, TRAVERSE_SETTINGS_FIELDS, KAILASH_SETTINGS_FIELDS, SettingField,
  decodeSettings, DecodedSetting,
} from './AmbitSettingsReader';
import { decodePersonalSettings } from './AmbitPersonalSettingsReader';
import { writeSetting as writeSettingRaw, WriteSettingResult, WriteDevice } from './AmbitSettingsWriter';
import { computeActivityClass } from './IntervalsStats';

// The Ambit 1 / Ambit 2 family (Ambit, Ambit2, Ambit2 S, Ambit2 R) uses the older legacy
// personal-settings mechanism, not the Ambit3/Kailash SBEM 0x1100 - a different read path
// (readPersonalSettings) and read-only (no write in libambit). Detected by the watch's own
// name from getDeviceInfo()'s device list. USB-only family (no Bluetooth).
export function isAmbit12(name?: string): boolean {
  if (!name) return false;
  return name === 'Suunto Ambit' || name.startsWith('Suunto Ambit 2');
}

// Traverse and Traverse Alpha. openambit drives them with the SAME ambit3 driver as the Ambit3
// family, but their SCHEMA assigns different entry ids (Personal.Weight is 0x1b, not the
// Ambit3's 0x19; nearly every id is shifted). 2026-08-16: they now have their OWN generated
// field table (TRAVERSE_SETTINGS_FIELDS) + write templates from the real Traverse fw 2.0.22
// descriptor, so they read correctly and are writable via the same 0x1101 mechanism (the
// per-screen templates resolved through the Traverse schema).
function isTraverse(name?: string): boolean {
  if (!name) return false;
  return name.startsWith('Suunto Traverse');
}

// Thin connect/read/disconnect and connect/write/disconnect orchestration, matching
// PoiService.ts's own exportPoisToGpx()/addPoiToWatch() pattern exactly - this screen owns
// its own short-lived connection per action rather than assuming one is already open
// (HomeScreen's own auto-connect-on-USB-attach flow is separate and unrelated).
//
// Real, 2026-08-08: Kailash settings are confirmed writable over cable too (same day as
// the Ambit3 result - custom_modes_andre.md's "Kailash settings ARE writable over cable
// too" section), using its own separately-curated field table. Which table applies is
// only known after connecting (getDeviceInfo().model === 'Hoopoe' is Kailash - the same
// check HomeScreen.tsx's own isKailash() already uses), so readAmbitSettings() detects it
// once per read and hands the matching table back in state for the caller to reuse on any
// subsequent write - no separate device-detection round trip needed there.

export interface ReadSettingsState {
  phase: 'idle' | 'connecting' | 'reading' | 'done' | 'error';
  settings?: DecodedSetting[];
  fields?: SettingField[];
  // Which write plumbing the caller should pass back on a write - 'ambit3' | 'traverse' |
  // 'kailash'. Undefined means read-only (Ambit 1/2). See AmbitSettingsWriter.WriteDevice.
  writeDevice?: WriteDevice;
  isKailash?: boolean;
  // The connected watch's own friendly name (e.g. "Suunto Kailash", "Suunto Ambit3
  // Peak") from getDeviceInfo()'s device list, so the UI can label the section with the
  // real device instead of a hardcoded "Ambit3".
  deviceName?: string;
  // True for the Ambit 1/2 family: settings are shown but not editable (no personal-
  // settings write exists in libambit). The UI renders values statically and hides the
  // write controls.
  readOnly?: boolean;
  error?: string;
}

/** Real, read-only (0x1100, four zero bytes) - safe any time the watch is connected. */
export async function readAmbitSettings(onState: (s: ReadSettingsState) => void): Promise<void> {
  // Over BLE the link is already open (HomeScreen owns it); the USB connect() would pop the
  // OTG prompt and tear down the BLE session. read/writeSettingsRaw act on the shared native
  // device either way. Same transport fix as CustomModesService. André, 2026-08-17.
  const overBle = isBleTransportActive();
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
    let isKailash = false;
    let deviceName: string | undefined;
    try {
      const info = await getDeviceInfo();
      isKailash = info.model === 'Hoopoe';
      deviceName = info.name || undefined;
    } catch { /* non-fatal - assume Ambit3 */ }

    // Ambit 1/2 family: legacy personal-settings read, read-only (no `fields` -> no write).
    if (isAmbit12(deviceName)) {
      const settings = decodePersonalSettings(await readPersonalSettings());
      onState({ phase: 'done', settings, deviceName, readOnly: true });
      return;
    }

    // Per-device field table AND write plumbing, keyed off the connected watch: each schema
    // family assigns its own entry ids, so one table can't serve all three.
    const traverse = isTraverse(deviceName);
    const fields = isKailash ? KAILASH_SETTINGS_FIELDS
      : traverse ? TRAVERSE_SETTINGS_FIELDS
      : AMBIT3_SETTINGS_FIELDS;
    const writeDevice: WriteDevice = isKailash ? 'kailash' : traverse ? 'traverse' : 'ambit3';
    const settings = decodeSettings(await readSettingsRaw(), fields);
    onState({ phase: 'done', settings, fields, writeDevice, isKailash, deviceName });
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Failed to read settings' });
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

export interface WriteSettingState {
  phase: 'idle' | 'connecting' | 'writing' | 'done' | 'error';
  result?: WriteSettingResult;
  error?: string;
}

/** Real, hardware-confirmed write (2026-08-08, both device types - see
 * AmbitSettingsWriter.ts's own writeSetting() for the read-patch-write-confirm dance
 * itself). `fields` must be the same table readAmbitSettings() returned in its own state -
 * the caller (SettingsScreen.tsx) already has it from the read that produced the row being
 * edited, so no extra device-detection round trip happens here. */
export async function writeAmbitSetting(
  key: string,
  value: number,
  fields: SettingField[],
  device: WriteDevice,
  onState: (s: WriteSettingState) => void,
): Promise<void> {
  // Over BLE the link is already open (HomeScreen owns it); the USB connect() would pop the
  // OTG prompt and tear down the BLE session. read/writeSettingsRaw act on the shared native
  // device either way. Same transport fix as CustomModesService. André, 2026-08-17.
  const overBle = isBleTransportActive();
  onState({ phase: overBle ? 'writing' : 'connecting' });
  if (!overBle) {
    try {
      await connect();
    } catch (e: any) {
      onState({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' });
      return;
    }
  }
  onState({ phase: 'writing' });
  try {
    const result = await writeSettingRaw(key, value, fields, device);
    onState({
      phase: result.ok ? 'done' : 'error',
      result,
      error: result.ok ? undefined : (result.error ?? 'Write sent but not confirmed by re-read'),
    });
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Failed to write the setting' });
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

/**
 * Ambit 1/2 (Bluebird) legacy personal-settings write - the 0x0b01 path
 * (AmbitPersonalSettingsWriter.writePersonalField, reverse-engineered from a real capture,
 * docs/ambit2_protocol_findings.md). Same connect -> write+confirm -> disconnect shape and
 * WriteSettingState contract as writeAmbitSetting() so SettingsScreen handles both the same
 * way. `displayValue` is what the user typed (e.g. kg for weight); the writer converts it to
 * the raw struct value, writes, and re-reads to confirm. USB-only family (no BLE branch).
 */
export async function writeLegacyPersonalSetting(
  key: string,
  displayValue: number,
  onState: (s: WriteSettingState) => void,
): Promise<void> {
  onState({ phase: 'connecting' });
  try {
    await connect();
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' });
    return;
  }
  onState({ phase: 'writing' });
  try {
    const confirmed = await writePersonalField(key, displayValue);
    onState({ phase: 'done', result: { ok: true, key, previousValue: null,
                                       requestedValue: displayValue, confirmedValue: confirmed } });
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Failed to write the setting' });
  } finally {
    await disconnect().catch(() => {});
  }
}

/**
 * Recalculate the Suunto activity class from the athlete's latest intervals.icu training and
 * write Personal.ActivityLevel to the watch - the sync-time refresh (André, 2026-08-18:
 * "recalculate activity level on each sync usb and bluetooth"). Only the class (it drifts with
 * training; weight/height/HR are static), and only when it actually changed. No-op when
 * intervals.icu isn't connected, on Ambit 1/2 (read-only), or on the Kailash (no Personal
 * fields). Uses the shared read/write path, so it works over USB and BLE. Returns a short
 * status for logging; never throws (a background refresh must not break a sync).
 */
export async function refreshActivityClassOnWatch(): Promise<
  'skipped' | 'unsupported' | 'unchanged' | 'written' | 'error'
> {
  let cls: number | null;
  try {
    cls = await computeActivityClass();
  } catch {
    return 'error';
  }
  if (cls === null) return 'skipped'; // intervals.icu not connected - nothing to do

  let read: ReadSettingsState | undefined;
  try {
    await readAmbitSettings(s => { if (s.phase === 'done' || s.phase === 'error') read = s; });
  } catch {
    return 'error';
  }
  if (!read || read.phase !== 'done' || read.readOnly || read.isKailash
      || !read.fields || !read.writeDevice) {
    return 'unsupported';
  }
  const cur = read.settings?.find(x => x.key === 'activity_level')?.value;
  if (cur !== undefined && Math.abs(cur - cls) < 1e-6) return 'unchanged';

  let ok = false;
  try {
    await writeAmbitSetting('activity_level', cls, read.fields, read.writeDevice,
      s => { if (s.phase === 'done') ok = true; });
  } catch {
    return 'error';
  }
  return ok ? 'written' : 'error';
}
