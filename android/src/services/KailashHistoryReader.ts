import { base64ToBytes } from './Base64';

// Decodes the raw sml.DeviceHistory reply (0x1200, entry 0x67) read off a Suunto Kailash -
// mirrors the companion research project's tools/kailash_history.py exactly (same entry
// IDs, same two real unit conversions), using the field layout read directly from this
// exact watch's own SBEM schema descriptor (`assets/APK/kailash/Suunto 7R/Container/
// Documents/descr+79DC39510E000100+2.0.5` in that project, via `tools/sbem_schema.py`) -
// not re-derived here. See that Python tool's own docstring for how each entry was found
// and cross-checked live against the watch's own "7R" screen and the 7R iOS app's local
// SQLite database (1 city/country).
//
// Real field table (from the schema, not guessed):
//   0x54  Device.Name                          utf8
//   0x55  Device.SerialNumber                  utf8
//   0x56  VisitedCities.NumberOfVisitedPlaces   uint16
//   0x57  VisitedCountries.NumberOfVisitedCountries  uint16
//   0x5B  GROUP: Location  [0x58 Longitude float32, 0x59 Latitude float32]
//   0x5C  GROUP: CountryCode [0x5A CountryCode utf8]
//   0x5D  LastKnownTime                        utf8
//   0x5E  TravellingDays                        uint16
//   0x5F  TravelledDistance                      uint32
//   0x60  CumulatedDistance                      uint32
//   0x61  FurthestFromHome                       uint32
//   0x66  GROUP: LogHeaders.Header (the real "activity mode" logbook, repeated - one
//         record per recorded session) [0x62 DateTime utf8, 0x63 Duration uint32 (x/10),
//         0x64 Distance uint32, 0x65 Speed.Max uint16 (x/360)]
//
// 0x58/0x59 (Location) are plain float32 - genuinely different from every *other* lat/lon
// field this project has ever seen on this watch family (all int32, degrees*1e7 - see
// RouteReader.ts/PoiService.ts). Decoding them as degrees directly gives implausible values;
// as **radians** (`value * 180 / Math.PI`), they land on the watch's own real last-known
// location - confirmed in kailash_history.py against the watch's known location, not re-verified here
// since this ports that same confirmed formula rather than re-deriving it.

function decodeUtf8(bytes: Uint8Array, start: number, end: number): string {
  // Identical to PoiService.ts's own decodeUtf8() - duplicated here rather than shared,
  // matching this project's existing per-file convention (RouteReader.ts/PoiService.ts
  // each already keep their own small decode helpers rather than a shared module).
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
    } else { out += '�'; i += 1; }
  }
  return out;
}

interface SbemEntry { id: number; start: number; end: number }

const MAGIC = [0x53, 0x42, 0x45, 0x4d, 0x30, 0x31, 0x30, 0x32]; // "SBEM0102"

function findMagic(bytes: Uint8Array): number {
  // Real, 2026-08-08: kailash_history.py's own read_history() searches for the magic
  // (`payload.find(sbem_schema.MAGIC)`) rather than assuming a fixed offset - live-checked
  // against a real Kailash reply this same session and found it sitting at a fixed offset 6
  // there, but that's one sample, not a guarantee across firmware/reply variations, so this
  // mirrors the search rather than hardcoding 6.
  for (let i = 0; i + MAGIC.length <= bytes.length; i++) {
    let match = true;
    for (let j = 0; j < MAGIC.length; j++) {
      if (bytes[i + j] !== MAGIC[j]) { match = false; break; }
    }
    if (match) return i;
  }
  return -1;
}

function sbemEntries(bytes: Uint8Array, start: number): SbemEntry[] {
  const out: SbemEntry[] = [];
  let off = start + 8; // past the "SBEM0102" magic itself
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

// One field slot inside a GROUP entry (0x5B/0x5C/0x66), in schema-declared order - each
// record is these fields back to back, no per-field length prefix (unlike top-level
// entries): a fixed-size struct field, or a NUL-terminated utf8 string.
type GroupFieldType = 'utf8' | 'uint16' | 'uint32' | 'float32';
interface GroupField { id: number; type: GroupFieldType }

function decodeGroupRecords(bytes: Uint8Array, start: number, end: number, fields: GroupField[]): Map<number, number | string>[] {
  const records: Map<number, number | string>[] = [];
  let off = start;
  while (off < end) {
    const record = new Map<number, number | string>();
    for (const f of fields) {
      if (f.type === 'utf8') {
        let nulAt = off;
        while (nulAt < end && bytes[nulAt] !== 0) nulAt++;
        record.set(f.id, decodeUtf8(bytes, off, nulAt));
        off = nulAt + 1;
      } else {
        const view = new DataView(bytes.buffer, bytes.byteOffset + off, 4);
        if (f.type === 'uint16') { record.set(f.id, view.getUint16(0, true)); off += 2; }
        else if (f.type === 'uint32') { record.set(f.id, view.getUint32(0, true)); off += 4; }
        else { record.set(f.id, view.getFloat32(0, true)); off += 4; } // float32
      }
    }
    records.push(record);
  }
  return records;
}

const LOCATION_FIELDS: GroupField[] = [{ id: 0x58, type: 'float32' }, { id: 0x59, type: 'float32' }];
const COUNTRY_FIELDS: GroupField[] = [{ id: 0x5a, type: 'utf8' }];
const LOGHEADER_FIELDS: GroupField[] = [
  { id: 0x62, type: 'utf8' }, { id: 0x63, type: 'uint32' },
  { id: 0x64, type: 'uint32' }, { id: 0x65, type: 'uint16' },
];

export interface KailashSession {
  when: string;
  durationSeconds: number;
  distanceMeters: number;
  maxSpeed: number;
}

export interface VisitedPlace {
  lat: number;
  lon: number;
  country: string | null;
}

export interface KailashHistory {
  name: string;
  serial: string;
  citiesVisited: number;
  countriesVisited: number;
  hasLastKnownLocation: boolean;
  lastKnownLatitude: number;
  lastKnownLongitude: number;
  lastKnownCountry: string;
  lastKnownTime: string;
  travellingDays: number;
  travelledDistanceMeters: number;
  cumulatedDistanceMeters: number;
  furthestFromHomeMeters: number;
  sessions: KailashSession[];
  // Real, 2026-08-09 ("Add a world map with the points were kailash has been") - every real
  // VisitedCities.Location/VisitedCountries.CountryCode pair encountered, not just the last
  // one. Direct port of tools/kailash_history.py's own visited_places fix - lastKnown* above
  // (kept for backward compatibility, same real meaning as before) only ever held the final
  // value, silently discarding every earlier visited place on a watch with real multi-place
  // travel history. Same real caveat as the Python side: genuinely unconfirmed whether a
  // second real place preserves this Location-then-CountryCode pairing order until a watch
  // with real multi-place history is available to check against.
  visitedPlaces: VisitedPlace[];
}

function emptyHistory(): KailashHistory {
  return {
    name: '', serial: '', citiesVisited: 0, countriesVisited: 0,
    hasLastKnownLocation: false, lastKnownLatitude: 0, lastKnownLongitude: 0,
    lastKnownCountry: '', lastKnownTime: '', travellingDays: 0,
    travelledDistanceMeters: 0, cumulatedDistanceMeters: 0, furthestFromHomeMeters: 0,
    sessions: [], visitedPlaces: [],
  };
}

/** Decodes a base64 sml.DeviceHistory reply (see readDeviceHistoryRaw() in
 * native/AmbitUsbModule.ts). Returns null if the reply doesn't contain a real SBEM0102
 * payload at all (same "not a crash, just nothing to show" spirit as PoiService.ts's own
 * empty-list return on a malformed POI reply). */
export function decodeDeviceHistory(b64: string): KailashHistory | null {
  if (!b64) return null;
  const bytes = base64ToBytes(b64);
  const head = findMagic(bytes);
  if (head < 0) return null;

  const history = emptyHistory();
  for (const entry of sbemEntries(bytes, head)) {
    switch (entry.id) {
      case 0x54:
        history.name = decodeUtf8(bytes, entry.start, entry.end);
        break;
      case 0x55:
        history.serial = decodeUtf8(bytes, entry.start, entry.end);
        break;
      case 0x56: {
        const v = new DataView(bytes.buffer, bytes.byteOffset + entry.start, 2);
        history.citiesVisited = v.getUint16(0, true);
        break;
      }
      case 0x57: {
        const v = new DataView(bytes.buffer, bytes.byteOffset + entry.start, 2);
        history.countriesVisited = v.getUint16(0, true);
        break;
      }
      case 0x5b: {
        // Real, 2026-08-09 - every record, not just the last (see visitedPlaces' own
        // interface comment). lastKnown* still tracks the final value seen, same real
        // meaning as before.
        const records = decodeGroupRecords(bytes, entry.start, entry.end, LOCATION_FIELDS);
        for (const record of records) {
          const lonRad = record.get(0x58) as number;
          const latRad = record.get(0x59) as number;
          const lat = (latRad * 180) / Math.PI;
          const lon = (lonRad * 180) / Math.PI;
          history.hasLastKnownLocation = true;
          history.lastKnownLatitude = lat;
          history.lastKnownLongitude = lon;
          history.visitedPlaces.push({ lat, lon, country: null });
        }
        break;
      }
      case 0x5c: {
        const records = decodeGroupRecords(bytes, entry.start, entry.end, COUNTRY_FIELDS);
        for (const record of records) {
          const country = record.get(0x5a) as string;
          history.lastKnownCountry = country;
          const lastPlace = history.visitedPlaces[history.visitedPlaces.length - 1];
          if (lastPlace) lastPlace.country = country;
        }
        break;
      }
      case 0x5d:
        history.lastKnownTime = decodeUtf8(bytes, entry.start, entry.end);
        break;
      case 0x5e: {
        const v = new DataView(bytes.buffer, bytes.byteOffset + entry.start, 2);
        history.travellingDays = v.getUint16(0, true);
        break;
      }
      case 0x5f: {
        const v = new DataView(bytes.buffer, bytes.byteOffset + entry.start, 4);
        history.travelledDistanceMeters = v.getUint32(0, true);
        break;
      }
      case 0x60: {
        const v = new DataView(bytes.buffer, bytes.byteOffset + entry.start, 4);
        history.cumulatedDistanceMeters = v.getUint32(0, true);
        break;
      }
      case 0x61: {
        const v = new DataView(bytes.buffer, bytes.byteOffset + entry.start, 4);
        history.furthestFromHomeMeters = v.getUint32(0, true);
        break;
      }
      case 0x66: {
        const records = decodeGroupRecords(bytes, entry.start, entry.end, LOGHEADER_FIELDS);
        for (const r of records) {
          history.sessions.push({
            when: (r.get(0x62) as string) ?? '',
            durationSeconds: ((r.get(0x63) as number) ?? 0) / 10,
            distanceMeters: (r.get(0x64) as number) ?? 0,
            maxSpeed: ((r.get(0x65) as number) ?? 0) / 360,
          });
        }
        break;
      }
      default:
        break; // real, but not needed for this UI - not every schema field is surfaced
    }
  }
  return history;
}
