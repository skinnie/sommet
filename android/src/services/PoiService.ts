import RNFS from 'react-native-fs';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { connect, disconnect, addPoi, pickGpxFile, readPoiListRaw, saveToDownloads, getDeviceInfo, readLegacyNav } from '../native/AmbitUsbModule';
import { isAmbit12 } from './AmbitSettingsService';
import { parseGpxWaypoints } from './RouteGpxParser';
import { base64ToBytes } from './Base64';

// ─── SBEM0102 POI decoding ─────────────────────────────────────────────────────
// Mirrors ambit-app's tools/ambit_format.py parse_sbem_poi_list(): after the
// fixed 14-byte [u32 0][u8 1][u8 1]["SBEM0102"] prefix, a run of
// [u8 id][u8 len | 0xff + u32 LE len][len bytes]. Entry 0x55 is a POI: three
// NUL-terminated strings (name, route_name, stamp), five u8, then lat/lon as
// i32 LE x 1e7.

const POI_ENTRY_ID = 0x55;

function decodeUtf8(bytes: Uint8Array, start: number, end: number): string {
  let out = '';
  let i = start;
  while (i < end) {
    const b0 = bytes[i];
    if (b0 < 0x80) { out += String.fromCharCode(b0); i += 1; }
    else if ((b0 & 0xe0) === 0xc0 && i + 1 < end) {
      out += String.fromCharCode(((b0 & 0x1f) << 6) | (bytes[i + 1] & 0x3f));
      i += 2;
    } else if ((b0 & 0xf0) === 0xe0 && i + 2 < end) {
      out += String.fromCharCode(((b0 & 0x0f) << 12) | ((bytes[i + 1] & 0x3f) << 6) | (bytes[i + 2] & 0x3f));
      i += 3;
    } else if ((b0 & 0xf8) === 0xf0 && i + 3 < end) {
      const cp = ((b0 & 0x07) << 18) | ((bytes[i + 1] & 0x3f) << 12) | ((bytes[i + 2] & 0x3f) << 6) | (bytes[i + 3] & 0x3f);
      out += String.fromCodePoint(cp);
      i += 4;
    } else { out += '�'; i += 1; } // malformed byte, same spirit as Python's decode(..., "replace")
  }
  return out;
}

interface SbemEntry { id: number; start: number; end: number }

function sbemEntries(bytes: Uint8Array): SbemEntry[] {
  if (bytes.length < 14) return [];
  // bytes[6:14] should read "SBEM0102" — not hard-asserted here, an empty/
  // malformed reply just yields zero entries below (offset walk stops safely).
  const out: SbemEntry[] = [];
  let off = 14;
  while (off + 2 <= bytes.length) {
    const id = bytes[off];
    let len = bytes[off + 1];
    off += 2;
    if (len === 0xff) {
      if (off + 4 > bytes.length) break;
      len = (bytes[off] | (bytes[off + 1] << 8) | (bytes[off + 2] << 16) | (bytes[off + 3] << 24)) >>> 0;
      off += 4;
    }
    const end = off + len;
    if (end > bytes.length) break;
    out.push({ id, start: off, end });
    off = end;
  }
  return out;
}

export interface WatchPoi {
  name: string;
  latitude: number;
  longitude: number;
}

/** Reads and decodes the watch's current POI list. Read-only, no risk to the watch. */
export async function readPoisFromWatch(): Promise<WatchPoi[]> {
  // Ambit1/2 (Bluebird): the SBEM POI read (readPoiListRaw / 0x0b24) is empty on this family;
  // waypoints come from libambit_navigation_read (readLegacyNav) instead - desktop parity.
  try {
    if (isAmbit12((await getDeviceInfo()).name)) {
      const nav = JSON.parse(await readLegacyNav());
      return (nav.waypoints ?? []).map((w: any) => ({
        name: w.name || 'POI',
        latitude: w.lat_e7 / 1e7,
        longitude: w.lon_e7 / 1e7,
      }));
    }
  } catch { /* not a legacy watch, or detection failed - fall through to the SBEM read */ }

  const b64 = await readPoiListRaw();
  if (!b64) return [];
  const bytes = base64ToBytes(b64);
  const pois: WatchPoi[] = [];

  for (const entry of sbemEntries(bytes)) {
    if (entry.id !== POI_ENTRY_ID) continue;
    let p = entry.start;
    const nulAt = (from: number) => {
      let i = from;
      while (i < entry.end && bytes[i] !== 0) i++;
      return i;
    };
    const nameEnd = nulAt(p);
    const name = decodeUtf8(bytes, p, nameEnd);
    p = nameEnd + 1;
    const routeNameEnd = nulAt(p);
    p = routeNameEnd + 1;
    const stampEnd = nulAt(p);
    p = stampEnd + 1;
    p += 5; // route_index, type, sub_type, type_index, flags — not needed for a GPX export
    if (p + 8 > entry.end) continue; // malformed entry, skip rather than throw
    const lat = (new DataView(bytes.buffer, bytes.byteOffset + p, 4)).getInt32(0, true) / 1e7;
    const lon = (new DataView(bytes.buffer, bytes.byteOffset + p + 4, 4)).getInt32(0, true) / 1e7;
    pois.push({ name: name || 'POI', latitude: lat, longitude: lon });
  }
  return pois;
}

function poisToGpx(pois: WatchPoi[]): string {
  const wpts = pois.map(p =>
    `  <wpt lat="${p.latitude.toFixed(7)}" lon="${p.longitude.toFixed(7)}"><name>${escapeXml(p.name)}</name></wpt>`
  ).join('\n');
  return `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n` +
    `${wpts}\n</gpx>`;
}

function escapeXml(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
}

export interface ExportPoiState {
  phase: 'idle' | 'connecting' | 'reading' | 'done' | 'error';
  count?: number;
  error?: string;
}

/** Reads every POI off the watch and saves them as a GPX file in Downloads. */
export async function exportPoisToGpx(onState: (s: ExportPoiState) => void): Promise<void> {
  onState({ phase: 'connecting' });
  try {
    await connect();
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' });
    return;
  }

  onState({ phase: 'reading' });
  try {
    const pois = await readPoisFromWatch();
    if (pois.length === 0) throw new Error('The watch has no POIs to export');
    const gpx = poisToGpx(pois);
    const fileName = `pois_${Date.now()}.gpx`;
    const path = `${RNFS.CachesDirectoryPath}/${fileName}`;
    await RNFS.writeFile(path, gpx, 'utf8');
    await saveToDownloads(path, fileName, 'application/gpx+xml');
    onState({ phase: 'done', count: pois.length });
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Failed to export POIs' });
  } finally {
    await disconnect().catch(() => {});
  }
}

export interface AddPoiState {
  phase: 'idle' | 'connecting' | 'writing' | 'done' | 'error';
  error?: string;
}

/**
 * Full pipeline for the Settings "Add POI" form: connect, add, disconnect.
 * Unlike sendRouteToWatch(), this preserves every POI already on the watch
 * and never touches the Waypoints/Routes flash regions — much lower risk.
 */
export async function addPoiToWatch(
  name: string, lat: number, lon: number,
  onState: (s: AddPoiState) => void,
  type: number = 17,  // Ambit POI type byte 0-17 (icon); default 17 = Waypoint
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
    await addPoi(name, lat, lon, type);
    await clearPoiCache(); // real watch state just changed - see below
    onState({ phase: 'done' });
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Failed to add the POI' });
  } finally {
    await disconnect().catch(() => {});
  }
}

// v3.0 UI port (2026-08-09, "re do... pois to match entirely desktop") - real "On the
// watch" list (PoisPage.qml parity) and a real per-item-preview Import flow
// (PoiService.importedPois + per-item Add, not one opaque write-everything button), plus
// per-item Export - none of this existed before; the screen was action-buttons-only.
//
// Real, 2026-08-10 ("cache activities/POIs and only import the differences from the watch,
// making it faster" -> "it is not upon the watch to give you that, is on the app to store
// the activities, so they can load almost immediately and just refresh what is new") - same
// real local-first pattern as NavigationService.ts's own getCachedNavigation() (see its
// header comment): a persisted last-known snapshot for an instant "On the watch" list, kept
// fresh by a real watch read in the background, not a scheme for skipping that read.
const POI_CACHE_KEY = 'ambitapp:cachedPois';

/** The locally persisted copy of the watch's POIs, if any. */
export async function getCachedPois(): Promise<WatchPoi[] | null> {
  try {
    const raw = await AsyncStorage.getItem(POI_CACHE_KEY);
    return raw ? (JSON.parse(raw) as WatchPoi[]) : null;
  } catch {
    return null;
  }
}

async function clearPoiCache(): Promise<void> {
  await AsyncStorage.removeItem(POI_CACHE_KEY).catch(() => {});
}

/** Read-only: the watch's own current POIs, for a real "On the watch" list. Always does a
 * real watch read - PoiScreen's own loadOnWatch() shows getCachedPois()'s result first so
 * this doesn't block the UI. */
export async function readOnWatchPois(): Promise<WatchPoi[]> {
  await connect();
  try {
    const data = await readPoisFromWatch();
    await AsyncStorage.setItem(POI_CACHE_KEY, JSON.stringify(data)).catch(() => {});
    return data;
  } finally {
    await disconnect().catch(() => {});
  }
}

/** Exports one already-read on-watch (or imported) POI as its own GPX file in Downloads -
 * matches desktop's own per-POI "Export" button (PoisPage.qml). */
export async function exportSinglePoiToGpx(poi: WatchPoi): Promise<void> {
  const gpx = poisToGpx([poi]);
  const safeName = poi.name.replace(/[\\/:*?"<>|]/g, '_') || 'poi';
  const fileName = `${safeName}.gpx`;
  const path = `${RNFS.CachesDirectoryPath}/${fileName}`;
  await RNFS.writeFile(path, gpx, 'utf8');
  await saveToDownloads(path, fileName, 'application/gpx+xml');
}

/** Picks a GPX and parses its <wpt> entries, without writing anything - the real preview
 * step desktop's own "Import from GPX" card has (PoiService.importedPois), each shown with
 * its own "Add" button rather than one write-everything action. Returns null on cancel. */
export async function pickAndParseWaypoints(): Promise<WatchPoi[] | null> {
  let gpxPath: string;
  try {
    gpxPath = await pickGpxFile();
  } catch (e: any) {
    if (e?.code === 'GPX_PICK_CANCELLED') return null;
    throw new Error(e?.message ?? 'File selection failed');
  }
  const xml = await RNFS.readFile(gpxPath, 'utf8');
  const waypoints = parseGpxWaypoints(xml);
  if (waypoints.length === 0) throw new Error('No waypoints (<wpt>) found in this GPX');
  return waypoints.map(w => ({ name: w.name, latitude: w.latitude, longitude: w.longitude }));
}
