import RNFS from 'react-native-fs';
import { readGpxFile, fitPathForGpx } from './GpxService';
import { parseTrackPoints, computeElevationStats } from './GpxParser';
import type { ActivityRecord } from '../database/db';

// ─── Constantes FIT ───────────────────────────────────────────────────────────

// Garmin epoch = 31/12/1989 00:00:00 UTC = Unix + 631065600 s
const GARMIN_EPOCH = 631065600;

// CRC-16 Garmin (table de 16 entrées)
const CRC_TABLE = [
  0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
  0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400,
];

function fitCrc(bytes: number[]): number {
  let crc = 0;
  for (const byte of bytes) {
    let tmp = CRC_TABLE[crc & 0x0F];
    crc = (crc >> 4) ^ tmp ^ CRC_TABLE[byte & 0x0F];
    tmp = CRC_TABLE[crc & 0x0F];
    crc = (crc >> 4) ^ tmp ^ CRC_TABLE[(byte >> 4) & 0x0F];
  }
  return crc & 0xFFFF;
}

// ─── Écriture binaire little-endian ──────────────────────────────────────────

type Buf = number[];

function u8(b: Buf, v: number)  { b.push(v & 0xFF); }
function u16(b: Buf, v: number) { b.push(v & 0xFF, (v >>> 8) & 0xFF); }
function u32(b: Buf, v: number) {
  b.push(v & 0xFF, (v >>> 8) & 0xFF, (v >>> 16) & 0xFF, (v >>> 24) & 0xFF);
}
function s32(b: Buf, v: number) { u32(b, v >>> 0); }

// ─── Messages FIT ─────────────────────────────────────────────────────────────

interface FieldDef { num: number; size: number; baseType: number; }

function writeDef(b: Buf, local: number, global: number, fields: FieldDef[]) {
  u8(b, 0x40 | local); // definition record header
  u8(b, 0);            // reserved
  u8(b, 0);            // little-endian
  u16(b, global);
  u8(b, fields.length);
  for (const f of fields) { u8(b, f.num); u8(b, f.size); u8(b, f.baseType); }
}

// Base types
const E  = 0x00; // enum  (uint8)
const U8 = 0x02; // uint8
const U16 = 0x84; // uint16 LE
const U32 = 0x86; // uint32 LE
const S32 = 0x85; // sint32 LE

// ─── Mapping activité → sport FIT ─────────────────────────────────────────────

function toFitSport(activityType: string): number {
  const t = activityType.toLowerCase();
  if (t.includes('run') || t.includes('course') || t.includes('jogging')) return 1;
  if (t.includes('cycl') || t.includes('vtt') || t.includes('vélo') || t.includes('bike')) return 2;
  if (t.includes('alpin')) return 13;
  if (t.includes('fond') || t.includes('nordic') || t.includes('cross')) return 12;
  if (t.includes('randon') || t.includes('hik')) return 17;
  if (t.includes('walk') || t.includes('march')) return 11;
  return 0; // generic (orientation, etc.)
}

// ─── Génération du fichier FIT ────────────────────────────────────────────────

export async function generateFitFile(
  gpxPath: string,
  activity: ActivityRecord,
): Promise<string> {
  const xml    = await readGpxFile(gpxPath);
  const points = parseTrackPoints(xml);
  if (points.length === 0) throw new Error('No GPS points in this GPX');

  const stats = computeElevationStats(points);

  const startSec = Math.floor(points[0].timestamp / 1000);
  const endSec   = Math.floor(points[points.length - 1].timestamp / 1000);
  const startG   = startSec - GARMIN_EPOCH;
  const endG     = endSec   - GARMIN_EPOCH;

  const durationSec = activity.duration_s || (endSec - startSec);
  const distM       = activity.distance_m || stats.totalDistance;
  const dPlus       = Math.round(activity.d_plus || stats.dPlus);
  const dMinus      = Math.round(stats.dMinus);
  const sport       = toFitSport(activity.activity_type ?? '');

  const data: Buf = [];

  // ── file_id (local 0, global 0) ───────────────────────────────────────────
  writeDef(data, 0, 0, [
    { num: 0, size: 1, baseType: E   }, // type
    { num: 1, size: 2, baseType: U16 }, // manufacturer
    { num: 2, size: 2, baseType: U16 }, // product
    { num: 4, size: 4, baseType: U32 }, // time_created
  ]);
  u8(data, 0);           // data header: local 0
  u8(data, 4);           // type = 4 (activity)
  u16(data, 255);        // manufacturer = 255 (development)
  u16(data, 0);          // product
  u32(data, startG);     // time_created

  // ── activity (local 1, global 34) ─────────────────────────────────────────
  writeDef(data, 1, 34, [
    { num: 253, size: 4, baseType: U32 }, // timestamp
    { num: 1,   size: 2, baseType: U16 }, // num_sessions
    { num: 2,   size: 1, baseType: E   }, // type
    { num: 3,   size: 1, baseType: E   }, // event
    { num: 4,   size: 1, baseType: E   }, // event_type
  ]);
  u8(data, 1);           // data header: local 1
  u32(data, endG);       // timestamp
  u16(data, 1);          // num_sessions
  u8(data, 0);           // type = 0 (manual)
  u8(data, 26);          // event = 26 (activity)
  u8(data, 1);           // event_type = 1 (stop)

  // ── session (local 2, global 18) ──────────────────────────────────────────
  writeDef(data, 2, 18, [
    { num: 254, size: 2, baseType: U16 }, // message_index
    { num: 253, size: 4, baseType: U32 }, // timestamp
    { num: 2,   size: 4, baseType: U32 }, // start_time
    { num: 7,   size: 4, baseType: U32 }, // total_elapsed_time (×1000 → scale=1000 ms)
    { num: 8,   size: 4, baseType: U32 }, // total_timer_time
    { num: 9,   size: 4, baseType: U32 }, // total_distance (×100 → cm)
    { num: 22,  size: 2, baseType: U16 }, // total_ascent (m)
    { num: 23,  size: 2, baseType: U16 }, // total_descent (m)
    { num: 5,   size: 1, baseType: E   }, // sport
    { num: 0,   size: 1, baseType: E   }, // event
    { num: 1,   size: 1, baseType: E   }, // event_type
  ]);
  u8(data, 2);
  u16(data, 0);
  u32(data, endG);
  u32(data, startG);
  u32(data, Math.round(durationSec * 1000));  // total_elapsed_time (scale=1000)
  u32(data, Math.round(durationSec * 1000));  // total_timer_time
  u32(data, Math.round(distM * 100));         // total_distance (scale=100 → cm)
  u16(data, dPlus);
  u16(data, dMinus);
  u8(data, sport);
  u8(data, 8);   // event = 8 (session)
  u8(data, 1);   // event_type = 1 (stop)

  // ── lap (local 3, global 19) ──────────────────────────────────────────────
  writeDef(data, 3, 19, [
    { num: 254, size: 2, baseType: U16 },
    { num: 253, size: 4, baseType: U32 },
    { num: 2,   size: 4, baseType: U32 },
    { num: 7,   size: 4, baseType: U32 },
    { num: 9,   size: 4, baseType: U32 },
    { num: 0,   size: 1, baseType: E   },
    { num: 1,   size: 1, baseType: E   },
  ]);
  u8(data, 3);
  u16(data, 0);
  u32(data, endG);
  u32(data, startG);
  u32(data, Math.round(durationSec * 1000));
  u32(data, Math.round(distM * 100));
  u8(data, 9);  // event = 9 (lap)
  u8(data, 1);

  // ── record (local 4, global 20) — définition ─────────────────────────────
  writeDef(data, 4, 20, [
    { num: 253, size: 4, baseType: U32 }, // timestamp
    { num: 0,   size: 4, baseType: S32 }, // position_lat (semicircles)
    { num: 1,   size: 4, baseType: S32 }, // position_long (semicircles)
    { num: 2,   size: 2, baseType: U16 }, // altitude (scale=5, offset=500)
    { num: 5,   size: 4, baseType: U32 }, // distance cumulée (scale=100, cm)
  ]);

  // ── record data — un point par TrackPoint ─────────────────────────────────
  const SEMI = Math.pow(2, 31) / 180; // degrés → semicircles
  let cumDist = 0;

  for (let i = 0; i < points.length; i++) {
    const p  = points[i];
    const ts = Math.floor(p.timestamp / 1000) - GARMIN_EPOCH;
    const lat = Math.round(p.latitude  * SEMI);
    const lng = Math.round(p.longitude * SEMI);
    const alt = Math.max(0, Math.round((p.elevation + 500) * 5));

    if (i > 0) {
      const q   = points[i - 1];
      const dy  = (p.latitude  - q.latitude)  * 111320;
      const dx  = (p.longitude - q.longitude) * 111320 * Math.cos(p.latitude * Math.PI / 180);
      cumDist  += Math.sqrt(dy * dy + dx * dx);
    }

    u8(data, 4);
    u32(data, ts);
    s32(data, lat);
    s32(data, lng);
    u16(data, alt);
    u32(data, Math.round(cumDist * 100));
  }

  // ── Assemblage final ──────────────────────────────────────────────────────

  // En-tête FIT (14 bytes)
  const hdr: Buf = [];
  u8(hdr, 14);           // taille de l'en-tête
  u8(hdr, 0x10);         // version protocole 1.0
  u16(hdr, 0x0834);      // version profil 2100
  u32(hdr, data.length); // taille des données
  hdr.push(0x2E, 0x46, 0x49, 0x54); // ".FIT"
  u16(hdr, fitCrc(hdr)); // CRC de l'en-tête

  // CRC fichier — doit couvrir le header ET les données (tout ce qui précède
  // ce CRC dans le fichier final), pas seulement `data` : le CRC de fin de
  // fichier valide l'intégrité de l'ensemble, contrairement au CRC d'en-tête
  // (déjà écrit ci-dessus) qui ne couvre que les 12 premiers octets du header.
  const fileCrc: Buf = [];
  u16(fileCrc, fitCrc([...hdr, ...data]));

  // Buffer final
  const all = new Uint8Array([...hdr, ...data, ...fileCrc]);

  // Écriture sur disque (base64 via RNFS)
  const fitPath = gpxPath.replace(/\.gpx$/, '.fit');
  await RNFS.writeFile(fitPath, uint8ToBase64(all), 'base64');
  return fitPath;
}

/**
 * The FIT file to export/upload for an activity. Prefers the native <id>.fit written at sync time
 * (it carries every sensor channel - HR/cadence/speed/power/temperature - and exists for indoor
 * moves that have no GPS track), and only falls back to converting the GPX when that file isn't
 * present (older syncs, imported GPX, or a build that didn't produce one). This is what makes
 * indoor activities exportable: generateFitFile() throws "No GPS points" on a track-less GPX,
 * whereas the native FIT is already on disk.
 */
export async function getFitFile(gpxPath: string, activity: ActivityRecord): Promise<string> {
  const native = await fitPathForGpx(gpxPath);
  if (native) return native;
  return generateFitFile(gpxPath, activity);
}

// ─── Helper base64 ────────────────────────────────────────────────────────────

function uint8ToBase64(bytes: Uint8Array): string {
  const CHUNK = 0x8000;
  let s = '';
  for (let i = 0; i < bytes.length; i += CHUNK) {
    s += String.fromCharCode(...bytes.subarray(i, i + CHUNK));
  }
  return btoa(s);
}
