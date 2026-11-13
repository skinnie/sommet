import RNFS from 'react-native-fs';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { connect, disconnect, addPoi, pickGpxFile, readPoiListRaw, saveToDownloads, getDeviceInfo, readLegacyNav, setPoiListRaw } from '../native/AmbitUsbModule';
import { isAmbit12 } from './AmbitSettingsService';
import { parseGpxWaypoints } from './RouteGpxParser';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { saveWatchBackup } from './WatchBackup';
import { t } from '../i18n';

// ─── SBEM0102 POI decoding ─────────────────────────────────────────────────────
// Mirrors sommet's tools/ambit_format.py parse_sbem_poi_list(): after the
// fixed 14-byte [u32 0][u8 1][u8 1]["SBEM0102"] prefix, a run of
// [u8 id][u8 len | 0xff + u32 LE len][len bytes]. Entry 0x55 is a POI: three
// NUL-terminated strings (name, route_name, stamp), five u8, then lat/lon as
// i32 LE x 1e7.
//
// 0x55 is the Ambit3's entry. From SuuntoLink's own descriptors a Traverse / Traverse Alpha
// sends its POIs in entry 0x53 (firmware 2.0.22) or 0x52 (1.0.4), with an int16 altitude after
// the longitude; the Traverse Alpha 2.0.22 program answers that way in the PC emulator
// (2026-11-13). A 0x0b24 reply holds POI entries only, so all three numbers are read, and a
// list written back keeps the number each entry came in. Twin of tools/ambit_format.py
// POI_ENTRIES. Not seen on a real Traverse.

const POI_ENTRY_ID = 0x55;
const POI_ENTRY_IDS = [POI_ENTRY_ID, 0x53, 0x52];

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
  /** The type byte (the watch's icon), in the watch's own numbering (see PoiTypes.ts). */
  type?: number;
  /** Elevation in metres of a POI imported from a GPX; a Traverse keeps it, an Ambit3 cannot. */
  elevation?: number;
}

/** Reads and decodes the watch's current POI list. Read-only, no risk to the watch. */
export async function readPoisFromWatch(): Promise<WatchPoi[]> {
  // Ambit1/2 (Bluebird): the SBEM POI read (readPoiListRaw / 0x0b24) is empty on this family;
  // waypoints come from libambit_navigation_read (readLegacyNav) instead - desktop parity.
  try {
    if (isAmbit12((await getDeviceInfo()).name)) {
      const nav = JSON.parse(await readLegacyNav());
      const wps: any[] = nav.waypoints ?? [];
      // A waypoint belongs to a route (shown on the Routes screen, not here) only if its
      // route_name is shared by >=2 waypoints. A standalone POI has an empty route_name, or a
      // route_name equal to its own name that no other waypoint shares - keep those as POIs so
      // they don't vanish. Matches NavigationService's >=2-points route rule.
      const rnCount = new Map<string, number>();
      for (const w of wps) {
        const rn = (w.routeName || '').trim();
        if (rn) rnCount.set(rn, (rnCount.get(rn) ?? 0) + 1);
      }
      return wps
        .filter((w: any) => {
          const rn = (w.routeName || '').trim();
          return !rn || (rnCount.get(rn) ?? 0) < 2;
        })
        .map((w: any) => ({
          name: w.name || 'POI',
          latitude: w.lat_e7 / 1e7,
          longitude: w.lon_e7 / 1e7,
          ...(typeof w.type === 'number' ? { type: w.type } : {}),
        }));
    }
  } catch { /* not a legacy watch, or detection failed - fall through to the SBEM read */ }

  const b64 = await readPoiListRaw();
  if (!b64) return [];
  return poiEntries(base64ToBytes(b64)).flatMap(e => (e.poi ? [e.poi] : []));
}

/** A 0x55 entry of a raw POI read, and the POI it holds: null for an empty entry (`55 00`, a
 * name the watch could not keep) or one this decoder does not understand. */
export interface PoiEntry { start: number; end: number; poi: WatchPoi | null; id?: number }

function decodePoi(bytes: Uint8Array, entry: SbemEntry): WatchPoi | null {
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
  const type = p + 1 < entry.end ? bytes[p + 1] : undefined;
  p += 5; // route_index, type, sub_type, type_index, flags
  if (p + 8 > entry.end) return null; // malformed entry, skip rather than throw
  const lat = (new DataView(bytes.buffer, bytes.byteOffset + p, 4)).getInt32(0, true) / 1e7;
  const lon = (new DataView(bytes.buffer, bytes.byteOffset + p + 4, 4)).getInt32(0, true) / 1e7;
  return { name: name || 'POI', latitude: lat, longitude: lon, type };
}

export function poiEntries(bytes: Uint8Array): PoiEntry[] {
  return sbemEntries(bytes)
    .filter(e => POI_ENTRY_IDS.includes(e.id))
    .map(e => ({ start: e.start, end: e.end, poi: decodePoi(bytes, e), id: e.id }));
}

/** A raw POI list shaped like a 0x0b24 read (`read`'s 14-byte header, then `keep`'s entries,
 * byte for byte, in that order): what setPoiListRaw takes. Empty when nothing is kept. */
export function buildPoiList(read: Uint8Array, keep: { start: number; end: number; id?: number }[]): Uint8Array {
  if (keep.length === 0) return new Uint8Array(0);
  const out: number[] = Array.from(read.subarray(0, 14));
  for (const e of keep) {
    const len = e.end - e.start;
    const id = e.id ?? POI_ENTRY_ID;      // the entry number it was read in (a Traverse's differs)
    if (len < 0xff) out.push(id, len);
    else out.push(id, 0xff, len & 0xff, (len >> 8) & 0xff, (len >> 16) & 0xff, (len >>> 24) & 0xff);
    for (let i = e.start; i < e.end; i++) out.push(read[i]);
  }
  return Uint8Array.from(out);
}

/** The entries to keep when removing `targets` from `entries`: each target takes the first
 * entry with its name and coordinates not already taken, and empty entries go too (nothing in
 * them can be written back). Throws, naming the POI, when a target is not there. */
export function poisToKeep(entries: PoiEntry[], targets: WatchPoi[]): { keep: PoiEntry[]; empty: number } {
  const drop = new Set<number>();
  for (const target of targets) {
    const i = entries.findIndex((e, k) => !drop.has(k) && e.poi !== null
      && e.poi.name === target.name && e.poi.latitude === target.latitude && e.poi.longitude === target.longitude);
    if (i < 0) throw new Error(t.poiRemoveNotFound(target.name));
    drop.add(i);
  }
  const keep = entries.filter((e, k) => !drop.has(k) && e.end > e.start);
  return { keep, empty: entries.filter(e => e.end === e.start).length };
}

export interface RemovePoisResult { left: number; emptyCleared: number; backupPath: string }

/**
 * Removes `targets` from the watch (Ambit3 family). Reads the list, saves a copy of it
 * (backups/POIs/<serial>/), then has the watch replace its list with the rest
 * (AmbitUsbModule.setPoiList: 0x0b04 empties it, 0x0b25 writes the rest, the count is
 * checked). The watch writes nothing if its list changed since the read. When the write
 * fails after the list was emptied, the copy is written back once.
 */
export async function removePoisFromWatch(targets: WatchPoi[]): Promise<RemovePoisResult> {
  await connect();
  try {
    const info = await getDeviceInfo();
    if (isAmbit12(info.name)) throw new Error(t.poiRemoveLegacy);
    const b64 = (await readPoiListRaw()) ?? '';
    const bytes = b64 ? base64ToBytes(b64) : new Uint8Array(0);
    const { keep, empty } = poisToKeep(poiEntries(bytes), targets);
    const backupPath = await saveWatchBackup('POIs', b64, info.serial ?? '');
    console.log(`[pois] copy saved before removing: ${backupPath}`);
    try {
      const left = await setPoiListRaw(b64, bytesToBase64(buildPoiList(bytes, keep)));
      return { left, emptyCleared: empty, backupPath };
    } catch (e: any) {
      if (e?.code === 'POI_LIST_CHANGED') throw new Error(t.poiRemoveChanged);
      // Put the copy back: read what is there now, and replace it with the copy.
      let restored = false;
      try {
        const now = (await readPoiListRaw()) ?? '';
        await setPoiListRaw(now, b64);
        restored = true;
      } catch { /* the copy stays on the phone */ }
      throw new Error(restored ? t.poiRemoveFailedRestored(e?.message ?? String(e))
                               : t.poiRemoveFailed(backupPath));
    }
  } finally {
    await clearPoiCache();
    await disconnect().catch(() => {});
  }
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

/** The altitude sent with a POI: metres, or 30000 for "none" (what the watch itself writes for a
 * POI defined by hand). Twin of tools/ambit_format.py poi_altitude(). Only a Traverse stores it. */
export const POI_ALTITUDE_NONE = 30000;
export function poiAltitude(elevationM: number | null | undefined): number {
  if (elevationM === null || elevationM === undefined || !Number.isFinite(elevationM)) return POI_ALTITUDE_NONE;
  const m = Math.round(elevationM);
  return m >= -500 && m <= 9000 ? m : POI_ALTITUDE_NONE;
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
  // The POI type byte (icon) in the watch's own numbering (PoiTypes.ts). -1 = that watch's
  // plain type: the native add writes 17 ("Waypoint") on an Ambit3 and 26 ("POI") on a Traverse.
  type: number = -1,
  // Elevation in metres (a GPX waypoint's <ele>), undefined when there is none.
  elevation?: number,
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
    await addPoi(name, lat, lon, type, poiAltitude(elevation));
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
const POI_CACHE_KEY = 'sommet:cachedPois';
// The watch the cached list was read from, by the name it gives itself ("Emu", "Jabiru",
// "Loon"): the POI screen offers that watch's own type list (PoiTypes.ts) before any read.
const POI_WATCH_MODEL_KEY = 'sommet:cachedPoisWatchModel';

/** The model of the watch the POI list was last read from, '' when never read. */
export async function getCachedPoiWatchModel(): Promise<string> {
  try {
    return (await AsyncStorage.getItem(POI_WATCH_MODEL_KEY)) ?? '';
  } catch {
    return '';
  }
}

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
    try {
      const info: any = await getDeviceInfo();
      await AsyncStorage.setItem(POI_WATCH_MODEL_KEY, String(info?.model || info?.name || ''));
    } catch { /* the list is still good; the type names fall back to the Ambit3 list */ }
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
  return waypoints.map(w => ({ name: w.name, latitude: w.latitude, longitude: w.longitude,
                               ...(w.elevation !== undefined ? { elevation: w.elevation } : {}) }));
}
