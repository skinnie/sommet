import RNFS from 'react-native-fs';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { connect, disconnect, updateSgee, writeRegion, readRegion, isBleTransportActive } from '../native/AmbitUsbModule';
import { readMemoryMap } from './MemoryMap';
import { base64ToBytes, bytesToBase64 } from './Base64';

// A minimal writer shape so this service needn't import the full DeviceProvider (avoids a
// cycle). Over BLE the caller passes the BLE provider, whose updateSgee() writes over the
// already-open GATT link and, crucially, does NOT do a USB connect() (see
// AmbitBleDeviceProvider.ts). Over USB no writer is passed and we open our own short-lived
// connection below, exactly as before.
type SgeeWriter = { updateSgee(path: string): Promise<boolean> };

// GPS orbital data (AGPS/SGEE) — live, unauthenticated Suunto endpoint.
// Verified working 2026-08-05 against real hardware: see sommet/sgee_andre.md
// and sommet/tools/README.md. NOT the same as the account-tied cloudapi.suunto.com
// host — this is devices.suunto-operations.com, found in SuuntoLink's own
// production.json, and it needs no AppKey/serial/account for this specific path.
const SGEE_URL = 'https://devices.suunto-operations.com/devices/gpsorbit/binary';
const SGEE_LOCAL_PATH = `${RNFS.DocumentDirectoryPath}/gpsorbit.bin`;

// GLONASS extended ephemeris - a SECOND constellation region only some watches carry
// (Traverse / Traverse Alpha / Ambit3 Vertical / Kailash declare a GlonassSGEE region; the
// Ambit3 Peak/Sport do not). Same live host, its own path. Desktop parity: tools/sgee.py's
// --glonass mode + server.py's _handle_agps_update, which write BOTH constellations in one
// "update" wherever the watch supports the second.
const GLONASS_URL = 'https://devices.suunto-operations.com/devices/glonassorbit/binary';
const GLONASS_LOCAL_PATH = `${RNFS.DocumentDirectoryPath}/glonassorbit.bin`;

// App preference (parity with the desktop's ephemeris "GPS only" switch, DeviceService's
// ephemerisGpsOnly): when on, skip the GLONASS write even on a watch that supports it.
// Persisted by the UI; default off, i.e. send GLONASS wherever the watch declares the region.
export const EPHEMERIS_GPS_ONLY_KEY = 'ephemeris.gpsOnly';

/**
 * Downloads the current GPS orbit file and writes it to the watch.
 * The watch must already be connected (connect() called before this).
 *
 * No local staleness/caching logic here on purpose: the download is small
 * (tens of KB) and fast, and the native write path (device_driver_ambit3.c's
 * gps_orbit_write) already compares the new data's embedded generation date
 * against what the watch currently holds and skips the actual flash write if
 * nothing changed — so a manual "update" tap can just always fetch fresh and
 * let that existing check decide whether anything really needs writing.
 */
export async function updateWatchSgee(
  writer?: SgeeWriter,
  onProgress?: (received: number, total: number) => void
): Promise<void> {
  const download = RNFS.downloadFile({
    fromUrl: SGEE_URL,
    toFile: SGEE_LOCAL_PATH,
    headers: {
      'User-Agent': 'Sommet/1.0',
      'Accept': 'application/octet-stream',
    },
    progress: onProgress
      ? (res) => onProgress(res.bytesWritten, res.contentLength)
      : undefined,
  });

  const result = await download.promise;
  if (result.statusCode !== 200) {
    throw new Error(`SGEE download failed: HTTP ${result.statusCode}`);
  }

  // Over BLE the writer (the BLE provider) writes on the already-open link; over USB we
  // call the native op directly on the connection opened in updateOrbitalData().
  if (writer) {
    await writer.updateSgee(SGEE_LOCAL_PATH);
  } else {
    await updateSgee(SGEE_LOCAL_PATH);
  }

  // GLONASS rides the same "update" for the watches that carry it (desktop parity: one action
  // writes both constellations). Best-effort: a GLONASS hiccup (offline, unsupported) must not
  // undo the GPS write that just succeeded, so its own failures are swallowed with a log.
  try {
    await writeGlonassIfSupported();
  } catch (e: any) {
    console.warn('[sgee] GLONASS ephemeris skipped:', e?.message ?? e);
  }
}

/** Today's date in UTC as YYYY-MM-DD, to compare against the region's own generation date. */
function todayUtc(): string {
  return new Date().toISOString().slice(0, 10);
}

export interface GlonassStatus {
  supported: boolean; // does this watch declare a GlonassSGEE region at all?
  valid?: boolean;    // is that region written with a decodable ephemeris (not erased)?
  date?: string;      // its generation date (YYYY-MM-DD), when valid
}

/**
 * Reads the GLONASS region's own state, mirroring the desktop tools/sgee.py glonass_status():
 * capability comes from the WATCH (does its 0x0b21 map declare GlonassSGEE), and freshness from
 * the region's own header (offset 10..13: big-endian year, then month, day; erased flash is all
 * 0xFF) since there is no 0x0b15-style query for GLONASS. Read-only, and assumes the caller
 * already holds the link open (readMemoryMap()/readRegion() act on the shared native device).
 */
/**
 * getGlonassStatus() - readGlonassStatus() with its OWN short-lived connection, so it is fully
 * isolated from the settings read. Deliberately NOT folded into readAmbitSettings: the extra
 * 0x0b21 map read it does is only hardware-confirmed on the Traverse (the Ambit3 Peak reconfirm
 * is still pending), and sharing the settings link risked desyncing it - a real regression seen
 * live 2026-08-17 (the Ambit3 then "can't read settings"). Over BLE the link is already open and
 * owned by HomeScreen, so we must not connect()/disconnect(); over USB this owns a throwaway
 * connection, exactly like PoiService/updateOrbitalData. Never throws - returns unsupported.
 */
export async function getGlonassStatus(): Promise<GlonassStatus> {
  const overBle = isBleTransportActive();
  if (!overBle) {
    try {
      await connect();
    } catch {
      return { supported: false };
    }
  }
  try {
    return await readGlonassStatus();
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

export async function readGlonassStatus(): Promise<GlonassStatus> {
  let region;
  try {
    region = (await readMemoryMap())['GlonassSGEE'];
  } catch {
    return { supported: false };
  }
  if (!region) return { supported: false };
  try {
    const head = base64ToBytes(await readRegion(region.base, 16));
    if (head.every((b) => b === 0xff)) return { supported: true, valid: false };
    const year = (head[10] << 8) | head[11];
    const month = head[12];
    const day = head[13];
    if (year >= 2000 && year <= 2100 && month >= 1 && month <= 12 && day >= 1 && day <= 31) {
      const date = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
      return { supported: true, valid: true, date };
    }
    return { supported: true, valid: false };
  } catch {
    return { supported: true }; // region declared but header unreadable - still "supported"
  }
}

/**
 * Writes GLONASS extended ephemeris to the watches that declare a GlonassSGEE region. Mirrors
 * the desktop tools/sgee.py --glonass byte-for-byte: the SAME length-prefixed blob as GPS
 * ([u32 LE length][raw file]), written to the region base the WATCH itself reports in its
 * 0x0b21 memory map (capability is asked of the watch, never a model list), finalized with the
 * generic region tail - hash over the written bytes, NO nav commit - which writeRegion() /
 * ambit3_write_region_raw already produce for the Apps and TrainingProgram regions.
 *
 * writeRegion()/readRegion()/readMemoryMap() all act on the shared native device, identically
 * over USB and BLE, so no separate connect() is needed here - the caller already established
 * the link (updateOrbitalData's USB connect, or the open BLE session).
 *
 * Returns 'unsupported' (no region on this watch), 'skipped' (GPS-only preference or already
 * today's file), or 'written'.
 */
async function writeGlonassIfSupported(): Promise<'unsupported' | 'skipped' | 'written'> {
  let region;
  try {
    region = (await readMemoryMap())['GlonassSGEE'];
  } catch {
    return 'unsupported'; // a watch that can't answer its map has no GLONASS region to fill
  }
  if (!region) return 'unsupported'; // the whole Ambit3 Peak/Sport family - no GLONASS region

  if ((await AsyncStorage.getItem(EPHEMERIS_GPS_ONLY_KEY)) === 'true') return 'skipped';

  // Freshness: there is no 0x0b15-style status query for GLONASS, but the region's own header
  // carries the generation date (region offset 10..13: big-endian year, then month, day; erased
  // flash is all 0xFF). Skip the download+write when it already holds today's file - the same
  // "no update needed" the GPS path gets natively.
  try {
    const head = base64ToBytes(await readRegion(region.base, 16));
    if (!head.every((b) => b === 0xff)) {
      const year = (head[10] << 8) | head[11];
      const month = head[12];
      const day = head[13];
      if (year >= 2000 && year <= 2100 && month >= 1 && month <= 12 && day >= 1 && day <= 31) {
        const date = `${String(year).padStart(4, '0')}-${String(month).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        if (date === todayUtc()) return 'skipped';
      }
    }
  } catch {
    // A header read that fails is not fatal - fall through and write fresh data.
  }

  const dl = await RNFS.downloadFile({
    fromUrl: GLONASS_URL,
    toFile: GLONASS_LOCAL_PATH,
    headers: { 'User-Agent': 'Sommet/1.0', 'Accept': 'application/octet-stream' },
  }).promise;
  if (dl.statusCode !== 200) throw new Error(`GLONASS download failed: HTTP ${dl.statusCode}`);

  const file = base64ToBytes(await RNFS.readFile(GLONASS_LOCAL_PATH, 'base64'));
  // image = [u32 LE length][raw file], byte-identical to build_sgee_for_region().
  const image = new Uint8Array(4 + file.length);
  image[0] = file.length & 0xff;
  image[1] = (file.length >>> 8) & 0xff;
  image[2] = (file.length >>> 16) & 0xff;
  image[3] = (file.length >>> 24) & 0xff;
  image.set(file, 4);

  // Hard bounds check BEFORE the write - never write past the region the watch declares (this
  // project has already had one real out-of-bounds flash write from an unchecked offset).
  if (image.length > region.size) {
    throw new Error(
      `GLONASS ephemeris is ${file.length} bytes (+4 prefix = ${image.length}); the watch ` +
      `declares only ${region.size} bytes for GlonassSGEE. Refusing to overrun it.`);
  }

  const ok = await writeRegion(region.base, bytesToBase64(image), image.length);
  if (!ok) throw new Error('GLONASS region write was not acknowledged.');
  return 'written';
}

export interface OrbitalUpdateState {
  phase: 'idle' | 'connecting' | 'downloading' | 'writing' | 'done' | 'error';
  error?: string;
}

/**
 * Full pipeline for the HomeScreen button: connect, download, write, disconnect.
 *
 * Over USB (no provider) this opens its own short-lived connection, since the app does not
 * hold a USB link open between actions. Over BLE the caller passes the BLE provider: the GATT
 * session is already open and owned by HomeScreen, so we must NOT connect()/disconnect() (that
 * would try a USB open and tear down the live BLE session) - we just download and let the
 * provider write on the existing link. This mirrors handleSync's own USB-vs-BLE branch, and is
 * why the GPS-orbit action now works over Bluetooth instead of silently failing a USB connect.
 */
export async function updateOrbitalData(
  onState: (s: OrbitalUpdateState) => void,
  provider?: SgeeWriter,
): Promise<void> {
  const overBle = !!provider;
  onState({ phase: 'connecting' });
  if (!overBle) {
    try {
      await connect();
    } catch (e: any) {
      onState({ phase: 'error', error: e?.message ?? 'Connexion à la montre échouée' });
      return;
    }
  }

  onState({ phase: 'downloading' });
  try {
    await updateWatchSgee(provider);
    onState({ phase: 'writing' }); // le téléchargement et l'écriture native sont rapides, phase surtout indicative
    onState({ phase: 'done' });
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Échec de la mise à jour des données GPS' });
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}
