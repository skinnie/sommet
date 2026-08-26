import { NativeModules, NativeEventEmitter } from 'react-native';
import type { DeviceConnector, DeviceInfo, SyncProgressEvent } from './DeviceConnector';

const { AmbitUsbModule: NativeAmbit } = NativeModules;

if (!NativeAmbit) {
  throw new Error(
    'AmbitUsbModule natif introuvable. ' +
    'Vérifiez que AmbitUsbPackage est bien enregistré dans MainApplication.kt ' +
    'et que le build NDK a réussi.'
  );
}

// ─── Re-export des types génériques ───────────────────────────────────────────

export type { DeviceInfo, SyncProgressEvent };

// ─── Implémentation DeviceConnector pour Suunto Ambit (USB OTG + libambit) ───

const emitter = new NativeEventEmitter(NativeAmbit);

/**
 * Connecteur Suunto Ambit — implémente DeviceConnector.
 * Supporte Ambit 1, 2, 2S, 2R, 3 Peak, 3 Sport, 3 Run, 3 Vertical.
 */
export const ambitConnector: DeviceConnector = {
  connect(): Promise<DeviceInfo> {
    return NativeAmbit.connect();
  },

  disconnect(): Promise<void> {
    return NativeAmbit.disconnect();
  },

  getLogs(knownIds: string[] = []): Promise<string[]> {
    return NativeAmbit.getLogs(knownIds);
  },

  updateSgee(path: string): Promise<boolean> {
    return NativeAmbit.updateSgee(path);
  },

  onSyncProgress(callback: (event: SyncProgressEvent) => void): () => void {
    const subscription = emitter.addListener('AmbitSyncProgress', callback);
    return () => subscription.remove();
  },
};

// ─── API fonctionnelle (rétro-compatibilité) ──────────────────────────────────

// Transport-awareness (2026-08-09): every watch operation (sync, POI, orbital,
// sport modes, route) calls connect()/disconnect() around its native call, and
// the native operations themselves act on the shared g_device regardless of
// transport. So the ONLY thing that made these USB-only was connect() opening a
// USB device. When a BLE connection is already established (AmbitBleModule set
// this flag), connect() must be a no-op that returns the info already learned
// from the BLE handshake, and disconnect() must leave the BLE link up (the Home
// screen owns its lifecycle). Making that switch HERE means every existing
// service works over BLE with zero changes to it.
let bleActive = false;
export function setBleTransportActive(active: boolean) { bleActive = active; }
export function isBleTransportActive() { return bleActive; }

export const connect = async (): Promise<DeviceInfo> => {
  if (bleActive) {
    try {
      const info = await NativeAmbit.getDeviceInfo();
      return { name: info?.name || 'Suunto Ambit', vendorId: 0x1493, productId: 0 };
    } catch {
      return { name: 'Suunto Ambit', vendorId: 0x1493, productId: 0 };
    }
  }
  return ambitConnector.connect();
};
export const disconnect = async (): Promise<void> => {
  if (bleActive) return;           // leave the BLE link up; HomeScreen manages it
  return ambitConnector.disconnect();
};
export const getLogs    = (knownIds?: string[]) => ambitConnector.getLogs(knownIds);
export const updateSgee = (path: string) => ambitConnector.updateSgee!(path);

/** Experimental "mark synced" write-back (OFF by default). Marks every move read this session
 * (the native cache from getLogs) synced on the watch via 0x1201, and resolves how many were
 * marked. Shared by USB and BLE (native g_device). The caller (MarkSynced.ts) must first
 * confirm the device supports it. */
export const markReadLogsSynced = (): Promise<number> =>
  NativeAmbit.markReadLogsSynced();
export const onSyncProgress = (cb: (e: SyncProgressEvent) => void) =>
  ambitConnector.onSyncProgress(cb);

export function shareFile(filePath: string, mimeType = 'application/gpx+xml'): Promise<void> {
  return NativeAmbit.shareFile(filePath, mimeType);
}

export function saveToDownloads(filePath: string, fileName: string, mimeType = 'application/gpx+xml'): Promise<void> {
  return NativeAmbit.saveToDownloads(filePath, fileName, mimeType);
}

/** Opens the system "Save as" picker (Storage Access Framework), defaulting to
 * the Downloads folder — the user can browse elsewhere, but accepting the
 * picker's default is equivalent to saveToDownloads(). Resolves with the
 * chosen destination's content:// URI, or rejects with SAVE_AS_CANCELLED if
 * the user backs out. */
export function saveFileAs(sourcePath: string, suggestedName: string, mimeType: string): Promise<string> {
  return NativeAmbit.saveFileAs(sourcePath, suggestedName, mimeType);
}

// ─── Navigation (GPX-to-watch route write) ────────────────────────────────────

/** Ouvre le sélecteur de fichiers Android et renvoie le chemin local du fichier copié. */
export function pickGpxFile(): Promise<string> {
  return NativeAmbit.pickGpxFile();
}

export interface NativeRoutePoint {
  lat: number;
  lon: number;
  alt: number | null; // null -> AMBIT3_ALTITUDE_NONE côté natif
}

export interface NativeRouteWaypoint {
  lat: number;
  lon: number;
  name: string;
  pointIndex: number; // rang du point de route auquel ce waypoint est rattaché
}

export interface NativeRoute {
  name: string;
  points: NativeRoutePoint[];      // déjà simplifiés, <= 1000 (voir RouteSimplify.ts)
  waypoints: NativeRouteWaypoint[]; // au moins 1, sinon la route n'apparaît pas sur la montre
  distanceM: number;
  ascentM: number;
  descentM: number;
  timestampSec: number; // timestamp Unix réel ; la conversion vers l'epoch de la montre se fait côté natif
}

/** La montre doit déjà être connectée (connect() appelé avant). */
export function writeRoute(route: NativeRoute): Promise<boolean> {
  return NativeAmbit.writeRoute(route);
}

/**
 * Ajoute un POI, en préservant ceux déjà présents sur la montre.
 * La montre doit déjà être connectée (connect() appelé avant).
 */
// `type` is the Ambit POI type byte 0-17 (the icon the watch shows); default 17 ("Waypoint"),
// what the watch itself uses. GPX re-imports (which have no Ambit type) rely on that default.
export function addPoi(name: string, lat: number, lon: number, type: number = 17): Promise<boolean> {
  return NativeAmbit.addPoi(name, lat, lon, type);
}

/**
 * Lit `length` octets à `address` sur la montre, renvoyés en base64.
 * Lecture seule, aucun risque pour la montre. Décodage côté TS (RouteReader.ts).
 * La montre doit déjà être connectée (connect() appelé avant).
 */
export function readRegion(address: number, length: number): Promise<string> {
  return NativeAmbit.readRegion(address, length);
}

/**
 * Lit la liste brute des POI (0x0b24) en base64, entrées SBEM0102 incluses.
 * Décodage côté TS (PoiService.ts). La montre doit déjà être connectée.
 */
export function readPoiListRaw(): Promise<string> {
  return NativeAmbit.readPoiListRaw();
}

/**
 * Reads the watch's raw 0x0b21 memory-map reply in base64 — the region table declaring
 * where Waypoints/Routes/CustomModes/Apps/… live on THIS watch. Decoding is in TS
 * (MemoryMap.ts), mirroring tools/write_nav.py read_memory_map(). Per-device navigation
 * port (2026-08-15): a Traverse's region bases differ from the Ambit3 Peak's, so callers
 * ask the watch instead of using the hardcoded AMBIT3_*_BASE constants. Watch must already
 * be connected. Read-only.
 */
export function readMemoryMapRaw(): Promise<string> {
  return NativeAmbit.readMemoryMapRaw();
}

// ─── Firmware flasher (THE ONE WRITE THAT CAN BRICK) ───────────────────────────
export interface FirmwarePlan {
  deviceInfoJson: string;   // JSON from getDeviceInfo (model/serial/fw/hw/battery)
  headerLen: number;        // always 32
  payloadLen: number;
  chunks: number;
}

/** Reads the .sfi at `path` and checks it against the connected watch WITHOUT sending
 * anything (the desktop's safe "dry connection"). Path is content-agnostic - pickGpxFile()
 * returns a cached copy of any picked file, whose bytes are validated by the SFI2ST magic. */
export function firmwarePreflight(path: string): Promise<FirmwarePlan> {
  return NativeAmbit.firmwarePreflight(path);
}

/** THE LIVE FLASH. `confirm` must be true (the UI gates it behind an explicit confirmation).
 * `commit=false` streams the whole image but stops before the irreversible commit - the watch
 * stays in BSL, fully recoverable. Progress arrives as 'AmbitFirmwarePhase' events. */
export function firmwareFlash(path: string, commit: boolean, confirm: boolean): Promise<any> {
  return NativeAmbit.firmwareFlash(path, commit, confirm);
}

/** Subscribe to firmware-flash phase updates. Returns an unsubscribe function. */
export function onFirmwarePhase(cb: (e: { phase: string; message: string }) => void): () => void {
  const sub = emitter.addListener('AmbitFirmwarePhase', cb);
  return () => sub.remove();
}

/**
 * Kailash only. Reads the watch's raw sml.DeviceHistory reply (0x1200, entry 0x67) in
 * base64 - visited cities/countries, travel stats, and a real activity-mode logbook bundled
 * in the same reply. Decoding happens in TS (KailashHistoryReader.ts), the same split
 * readPoiListRaw() above already uses. The watch must already be connected.
 */
export function readDeviceHistoryRaw(): Promise<string> {
  return NativeAmbit.readDeviceHistoryRaw();
}

/**
 * Kailash test hook (2026-08-09). Reads the watch's raw sml.DeviceLog reply
 * (0x1200, entry 0x53) in base64 — the ephemeral per-activity GPS sample store,
 * distinct from readDeviceHistoryRaw()'s persistent 0x67 summaries. Exists to
 * confirm KAILASH-BLE-FINDINGS.md Finding 7 live over BLE; no decoder yet, a
 * non-empty result is the signal. The watch must already be connected.
 */
export function readDeviceLogRaw(): Promise<string> {
  return NativeAmbit.readDeviceLogRaw();
}

/**
 * Ambit3 (and Traverse/Ambit2, same schema family). Reads the watch's raw
 * sml.DeviceSettings reply (0x1100, four zero bytes) in base64. Decoding happens in TS
 * (AmbitSettingsReader.ts). The watch must already be connected.
 */
export function readSettingsRaw(): Promise<string> {
  return NativeAmbit.readSettingsRaw();
}

/**
 * Ambit 1 / Ambit 2 family (USB-only). The legacy personal-settings read (a fixed struct,
 * not the Ambit3/Kailash SBEM 0x1100), returned as a JSON string of field -> raw value.
 * Decoding/labelling happens in TS (AmbitPersonalSettingsReader.ts). Read-only — libambit
 * implements no write for these. The watch must already be connected.
 */
export function readPersonalSettings(): Promise<string> {
  return NativeAmbit.readPersonalSettings();
}

/**
 * Ambit 1/2 (Bluebird) legacy personal-settings WRITE (0x0b01), reverse-engineered from a
 * real SuuntoLink<->Ambit2 USB capture (2026-08-26, docs/ambit2_protocol_findings.md). The
 * native side does the read-modify-write: read the whole struct (188 B on Ambit2 / 132 on
 * Ambit1), patch `value` at `offset` (`width` 1 or 2, little-endian), write it back at the
 * device's own length. See AmbitPersonalSettingsWriter.ts for the field offset table. Resolves
 * true when the write was sent OK - the caller re-reads to confirm the watch applied it.
 */
export function writePersonalSetting(offset: number, width: number, value: number): Promise<boolean> {
  return NativeAmbit.writePersonalSetting(offset, width, value);
}

/**
 * Real, hardware-confirmed 2026-08-08: writes a full sml.DeviceSettings blob back via
 * 0x1101 - André confirmed on a real connected Ambit3's own screen that this exact
 * mechanism visibly switched the display Light -> Dark. `dataBase64` must be the *entire*
 * settings blob (read via readSettingsRaw(), patch one field, send the whole thing back) -
 * see AmbitSettingsWriter.ts's own writeSetting() for that dance. Resolving `true` here
 * only means the write was sent without error - it does NOT by itself confirm the watch
 * applied it; the caller re-reads to check, matching this project's own "prove it, don't
 * just trust the ACK" rule found necessary during live testing.
 */
export function writeSettingsRaw(dataBase64: string): Promise<boolean> {
  return NativeAmbit.writeSettingsRaw(dataBase64);
}

/**
 * Real, 2026-08-08. Reads the watch's raw 12288-byte CustomModes region (sport modes) in
 * base64 - the same region tools/custom_modes.py already reads. Decoding happens in TS
 * (CustomModesReader.ts). The watch must already be connected.
 */
export function readCustomModesRaw(): Promise<string> {
  return NativeAmbit.readCustomModesRaw();
}

/**
 * Writes the phone's own current local time to the connected watch. Real, 2026-08-10 - see
 * AmbitUsbModule.kt's own setDateTime() comment for the full story: which underlying
 * mechanism actually runs (cable's plain date/time pair vs. Kailash BLE's real 0x1201
 * single-entry SBEM0102 pushes, found byte-exact in the real 7R app's own BLE captures) is
 * decided natively, not here - this function is transport/watch-agnostic on purpose.
 */
export function setDateTime(): Promise<boolean> {
  return NativeAmbit.setDateTime();
}

/**
 * Real mechanism, NOT yet hardware-confirmed on Android specifically - see
 * ambit3_write_custom_modes_raw()'s own comment in device_driver_ambit3.c for exactly what
 * is and isn't proven (the desktop side of this same write mechanism is fully confirmed
 * working - custom_modes_andre.md; this native Android port reuses the same proven
 * building blocks but the composition itself hasn't been tested on this platform yet).
 * `dataBase64` must be the *entire* 12288-byte region (read first, patch specific bytes,
 * send the whole thing back). Resolving `true` only means the write+tail+commit sequence
 * completed without a protocol-level failure - it does NOT confirm the watch's live state
 * reflects it; the caller re-reads to check, the same "prove it" rule already established.
 */
export function writeCustomModesRaw(dataBase64: string): Promise<boolean> {
  return NativeAmbit.writeCustomModesRaw(dataBase64);
}

/**
 * EXPERIMENTAL (2026-08-14) - generic flash-region write, shared by the App-Zone and
 * Training-program (Intervals) paths. Writes the first `extent` bytes of the base64 region
 * image at `address`, finalized with the same used-extent SHA256 data-tail as
 * writeCustomModesRaw (no commit) - see ambit3_write_region_raw()/nativeAmbitWriteRegion in
 * the native layer. The image and extent are built and proven byte-exact in TS by the
 * per-region builder that owns the format; this is just the marshalling boundary. Resolving
 * true means the write+tail completed without a protocol failure, NOT that the watch's live
 * state reflects it - the caller re-reads to confirm, same "prove it" rule as every other
 * write here. NOT yet hardware-confirmed on Android.
 */
export function writeRegion(address: number, dataBase64: string, extent: number): Promise<boolean> {
  return NativeAmbit.writeRegion(address, dataBase64, extent);
}

// ─── Auto-sync on USB attach ───────────────────────────────────────────────────
// AndroidManifest.xml (launchMode="singleTask" + USB_DEVICE_ATTACHED intent-filter
// + device_filter.xml) already lets the OS launch/foreground the app when the
// watch is plugged in. Two cases:
//  - cold launch: query once on mount (wasLaunchedViaUsbAttach) — an emitted event
//    this early could race a JS listener that hasn't subscribed yet.
//  - already running: MainActivity.onNewIntent() emits "AmbitUsbAttached" live.

/** Was this app instance launched (or brought to front) by the watch being plugged in? */
export function wasLaunchedViaUsbAttach(): Promise<boolean> {
  return NativeAmbit.wasLaunchedViaUsbAttach();
}

/** Fires when the watch is plugged in while the app is already running. */
export function onUsbAttached(callback: () => void): () => void {
  const subscription = emitter.addListener('AmbitUsbAttached', callback);
  return () => subscription.remove();
}

/** v2.3 beta — USB_DEVICE_ATTACHED now fires for Garmin devices too (see
 * device_filter.xml), so it no longer implies "an Ambit was plugged in" by
 * itself. Call this before routing to either device's flow. */
export type AttachedDeviceType = 'ambit' | 'garmin' | 'none';
export function detectAttachedDeviceType(): Promise<AttachedDeviceType> {
  return NativeAmbit.detectAttachedDeviceType();
}

// ─── Device info (v2.3.2 beta) ─────────────────────────────────────────────────

export interface AmbitDeviceInfo {
  name: string;        // e.g. "Suunto Ambit3 Sport" — from the known-PID table
  model: string;        // from the watch's own device-info reply
  serial: string;
  fwVersion: string;    // "X.Y.Z"
  hwVersion: string;    // "X.Y.Z"
  battery: number;      // percent, or -1 if the read failed
}

/** Model, serial, firmware/hardware version, and a live battery read.
 * The watch must already be connected (connect() called first). */
export function getDeviceInfo(): Promise<AmbitDeviceInfo> {
  return NativeAmbit.getDeviceInfo();
}

// ─── Multi-watch switcher (2026-08-16) ─────────────────────────────────────────
// With more than one Suunto plugged in, connect() targets whichever watch selectDevice() last
// chose (by its stable USB path), so the UI can offer a picker like the desktop app's.

export interface AmbitUsbDevice {
  deviceName: string;   // stable Android USB path, e.g. "/dev/bus/usb/002/010" — the id for selectDevice
  productId: number;    // e.g. 0x002b (Traverse); identifies the model
  name: string;         // friendly name from the known-PID table, e.g. "Suunto Traverse"
}

/** Every attached Suunto watch. Use to decide whether to show the picker (length > 1). */
export function listDevices(): Promise<AmbitUsbDevice[]> {
  return NativeAmbit.listDevices();
}

/** Choose which attached watch subsequent connect() calls target (by deviceName). Pass null to
 * clear (back to "first found"). */
export function selectDevice(deviceName: string | null): Promise<boolean> {
  return NativeAmbit.selectDevice(deviceName);
}
