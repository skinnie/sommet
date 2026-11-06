import { NativeModules, NativeEventEmitter } from 'react-native';

const { GarminModule: NativeGarmin } = NativeModules;

if (!NativeGarmin) {
  throw new Error(
    'GarminModule native module not found. ' +
    'Check that GarminPackage is registered in MainApplication.kt ' +
    'and that the libaums Gradle dependency resolved.'
  );
}

/**
 * v2.3 beta — Garmin USB Mass Storage support. See GARMIN_USB_IMPORT_SPEC.md.
 *
 * Garmin devices work completely differently from the Ambit3 (plain GPX files on
 * a FAT filesystem, not an NSP flash protocol) — this is a separate device
 * integration, not an extension of AmbitUsbModule.
 */

export interface GarminVolumeInfo {
  volumeIndex: number;
  hasGarminDeviceXml: boolean;
  model: string | null;
  firmwareVersion: string | null; // e.g. "5.01" — already formatted, see GarminModule.kt
  partNumber: string | null;
  activityPath: string | null; // resolved from GarminDevice.xml, e.g. "Garmin/GPX/Current"
  // Edge 500 & co. (2026-11-05): the FIT_TYPE_4 ride folder ("Garmin/Activities"); null on an eTrex.
  // Optional so an older native build (without these fields) still type-checks at the call sites.
  fitActivityPath?: string | null;
  isEdge?: boolean;             // GarminDevice.xml's Description starts with "Edge"
}

/** The volume of a mass-storage Garmin Edge (Edge 500 & co.), or null when it's an eTrex.
 *  Same rule as the desktop's tools/mtp_import.py: the model text starts with "Edge". */
export function edgeVolume(info: GarminConnectResult | null | undefined): GarminVolumeInfo | null {
  return info?.volumes.find(v => v.hasGarminDeviceXml
    && (v.isEdge ?? /^edge/i.test((v.model ?? '').trim()))) ?? null;
}

export interface GarminConnectResult {
  volumes: GarminVolumeInfo[];
  hasSdCard: boolean; // heuristic: a volume without GarminDeviceXml alongside one that has it
}

const emitter = new NativeEventEmitter(NativeGarmin);

/** Fires while connect() is retrying — Garmin devices can take up to ~40s to
 * finish mounting after the USB link comes up (real-world reference: see
 * GARMIN_USB_IMPORT_SPEC.md), so connect() polls rather than failing fast. */
export interface GarminMountWaitingEvent {
  attempt: number;
  secondsLeft: number;
}
export function onMountWaiting(callback: (e: GarminMountWaitingEvent) => void): () => void {
  const subscription = emitter.addListener('GarminMountWaiting', callback);
  return () => subscription.remove();
}

/** Finds a connected Garmin device, requests USB permission if needed, and mounts
 * every volume it exposes (internal memory + SD card show up separately). Retries
 * internally for up to ~45s — see onMountWaiting() to show progress meanwhile. */
export function connect(): Promise<GarminConnectResult> {
  return NativeGarmin.connect();
}

/** Lists activity file names in the given volume's resolved activity folder
 * (GarminConnectResult.volumes[i].activityPath). Content, not just names — use
 * readActivityFile() next. */
export function listActivityFiles(volumeIndex: number): Promise<string[]> {
  return NativeGarmin.listActivityFiles(volumeIndex);
}

/** Reads one activity file's full text content (GPX is plain XML) — parse the
 * result with RouteGpxParser.ts/GpxParser.ts, same as any other GPX source. */
export function readActivityFile(volumeIndex: number, fileName: string): Promise<string> {
  return NativeGarmin.readActivityFile(volumeIndex, fileName);
}

/** Lists .gpx file names directly inside `<volume>/Garmin/GPX` (not recursive —
 * Current/'s activity files are excluded automatically). This is where saved
 * routes/tracks and BaseCamp-authored "Waypoints*.gpx" POI files live. */
export function listGpxDirFiles(volumeIndex: number): Promise<string[]> {
  return NativeGarmin.listGpxDirFiles(volumeIndex);
}

/** Reads one file's content from `<volume>/Garmin/GPX` (see listGpxDirFiles()). */
export function readGpxDirFile(volumeIndex: number, fileName: string): Promise<string> {
  return NativeGarmin.readGpxDirFile(volumeIndex, fileName);
}

/**
 * Writes a GPX file (route or POI — same mechanism, see GARMIN_USB_IMPORT_SPEC.md)
 * to `<volume>/Garmin/GPX/<fileName>`.
 *
 * SAFETY: refuses (GARMIN_REFUSED_INTERNAL_WRITE) if `volumeIndex` refers to the
 * internal-memory volume — confirmed with André, 2026-08-07: never write to
 * internal memory, no exceptions. Only ever call this with an SD-card volume's
 * index (GarminConnectResult.volumes[i] where hasGarminDeviceXml === false).
 */
export function writeGpxToSdCard(volumeIndex: number, fileName: string, gpxContent: string): Promise<boolean> {
  return NativeGarmin.writeGpxToSdCard(volumeIndex, fileName, gpxContent);
}

/** File names (not folders) directly inside `path`, relative to the volume root. */
export function listDir(volumeIndex: number, path: string): Promise<string[]> {
  return NativeGarmin.listDir(volumeIndex, path);
}

/** One file's bytes (base64), `path` relative to the volume root. */
export function readFile(volumeIndex: number, path: string): Promise<string> {
  return NativeGarmin.readFile(volumeIndex, path);
}

/**
 * Writes one file (base64) into an Edge's Garmin/NewFiles, which the Edge reads when it leaves
 * USB mode, then reads it back to check. SAFETY: the native side refuses any other path, and any
 * volume that isn't an Edge (the eTrex keeps its SD-card-only rule, writeGpxToSdCard()).
 */
export function writeFile(volumeIndex: number, path: string, base64: string): Promise<boolean> {
  return NativeGarmin.writeFile(volumeIndex, path, base64);
}

export function disconnect(): Promise<boolean> {
  return NativeGarmin.disconnect();
}
