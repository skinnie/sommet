import { base64ToBytes } from './Base64';

// Decodes the raw sml.DeviceLog reply (0x1200, entry 0x53) read off a Suunto Kailash - the
// watch's EPHEMERAL per-activity GPS sample store. Distinct from BOTH:
//   - the persistent sml.DeviceHistory (0x67) summaries in KailashHistoryReader.ts, and
//   - the sml.TrackLog *flash region* (0x48a1c0) in KailashTrackLogReader.ts (a 20-byte
//     fixed-stride record format read over cable via readRegion, ground-truth-validated
//     56/56 against the 7R app's own database).
// This is the SBEM0102 form of the same track, and the one that comes back over Bluetooth:
// confirmed live 2026-08-09 (KAILASH-BLE-FINDINGS.md Finding 7) - cable DeviceLog reads
// always returned 0 samples, but the phone's own live BLE session had them. A real activity
// decoded end to end this way, its coordinates matching the watch's own confirmed last-known
// location (the same ground truth KailashHistoryReader.ts cites).
//
// Reply layout (an SBEM0102 payload behind a 6-byte prefix - findMagic() skips it):
//   0x49  sml.BinaryDataArea.Size   uint32
//   0x4A  header timestamp          utf8
//   0x4B  Device.Name               utf8 ("Kailash")
//   0x4C  Device.SerialNumber       utf8 (the descriptor id)
//   0x52  GROUP: the sample block (u8 len 0xFF -> u32 length, e.g. 925 B) - repeated, one
//         entry per page when a longer activity paginates.
//
// One sample = a fixed 16-byte head then a NUL-terminated ISO timestamp:
//   offset 0  uint32  unknown (~900-3000, likely altitude or GPS accuracy - NOT surfaced,
//                     unconfirmed, this project doesn't emit unverified data)
//   offset 4  int32   Longitude, degrees * 1e7   (longitude comes BEFORE latitude here)
//   offset 8  int32   Latitude,  degrees * 1e7
//   offset 12 uint32  elapsed time, ms  (+31000 per 31 s sample in the reference capture)
//   offset 16 utf8    timestamp, e.g. "2026-08-09T08:32:36Z"
//
// lat/lon are int32 degrees*1e7 - the SAME encoding as the flash TrackLog region and the
// Ambit3's own route/POI coordinates (RouteReader.ts/PoiService.ts), read SIGNED so
// western/southern coordinates decode correctly. (Note: the DeviceHistory Location field
// 0x58/0x59 is the odd one out - float32 radians there, int32 degrees*1e7 here.) An earlier
// semicircle reading of this field was a coincidental near-miss; degrees*1e7 is what matches
// the validated ground truth, so that's what this uses.

function decodeUtf8(bytes: Uint8Array, start: number, end: number): string {
  // Per-file helper, same convention as KailashHistoryReader.ts / PoiService.ts. These
  // timestamps are ASCII in practice but decode UTF-8 properly to match the sibling readers.
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
    } else { out += '�'; i += 1; }
  }
  return out;
}

const MAGIC = [0x53, 0x42, 0x45, 0x4d, 0x30, 0x31, 0x30, 0x32]; // "SBEM0102"

function findMagic(bytes: Uint8Array): number {
  for (let i = 0; i + MAGIC.length <= bytes.length; i++) {
    let match = true;
    for (let j = 0; j < MAGIC.length; j++) {
      if (bytes[i + j] !== MAGIC[j]) { match = false; break; }
    }
    if (match) return i;
  }
  return -1;
}

interface SbemEntry { id: number; start: number; end: number }

function sbemEntries(bytes: Uint8Array, magicOffset: number): SbemEntry[] {
  const out: SbemEntry[] = [];
  let off = magicOffset + 8; // past the "SBEM0102" magic
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

const DEG_SCALE = 1e7;
const SAMPLE_GROUP_ID = 0x52;
const SAMPLE_HEAD_BYTES = 16; // 4 x uint32 before the timestamp string

export interface KailashTrackPoint {
  time: string;      // ISO 8601, straight from the watch
  latitude: number;  // degrees
  longitude: number; // degrees
  elapsedMs: number; // watch's own elapsed-time field for this sample
}

/** Parses one 0x52 sample block [start,end) into track points. Each record is the 16-byte
 * head then a NUL-terminated timestamp; longitude/latitude are the 2nd/3rd int32 as signed
 * degrees*1e7. Stops cleanly on a truncated trailing record. */
function decodeSampleBlock(bytes: Uint8Array, start: number, end: number, out: KailashTrackPoint[]): void {
  let off = start;
  while (off + SAMPLE_HEAD_BYTES < end) {
    const view = new DataView(bytes.buffer, bytes.byteOffset + off, SAMPLE_HEAD_BYTES);
    // view.getInt32(0) is the unknown field (altitude/accuracy?), not surfaced.
    const lon = view.getInt32(4, true) / DEG_SCALE;
    const lat = view.getInt32(8, true) / DEG_SCALE;
    const elapsedMs = view.getUint32(12, true);
    off += SAMPLE_HEAD_BYTES;
    let nulAt = off;
    while (nulAt < end && bytes[nulAt] !== 0) nulAt++;
    const time = decodeUtf8(bytes, off, nulAt);
    off = nulAt + 1;
    out.push({ time, latitude: lat, longitude: lon, elapsedMs });
  }
}

export interface KailashDeviceLog {
  deviceName: string;
  serial: string;
  points: KailashTrackPoint[]; // every decoded sample, in wire order (may include a stale lead point)
}

/** Decodes a base64 sml.DeviceLog reply (see readDeviceLogRaw() in native/AmbitUsbModule.ts).
 * Returns null if there's no SBEM0102 payload at all (same "nothing to show, not a crash"
 * spirit as KailashHistoryReader.ts). An empty `points` array means the store was present but
 * drained (e.g. the 7R app already synced this activity - Finding 7). */
export function decodeDeviceLog(b64: string): KailashDeviceLog | null {
  if (!b64) return null;
  const bytes = base64ToBytes(b64);
  const head = findMagic(bytes);
  if (head < 0) return null;

  const result: KailashDeviceLog = { deviceName: '', serial: '', points: [] };
  for (const entry of sbemEntries(bytes, head)) {
    switch (entry.id) {
      case 0x4b:
        result.deviceName = decodeUtf8(bytes, entry.start, entry.end);
        break;
      case 0x4c:
        result.serial = decodeUtf8(bytes, entry.start, entry.end);
        break;
      case SAMPLE_GROUP_ID:
        decodeSampleBlock(bytes, entry.start, entry.end, result.points);
        break;
      default:
        break; // header size/timestamp fields - not needed for the track itself
    }
  }
  return result;
}

/** Real track points only: drops the stale carried-over lead sample (elapsedMs === 0, a
 * known Kailash pattern - KAILASH-BLE-FINDINGS.md Finding 6) and any zero/no-fix coordinate,
 * so callers get a clean walkable track. */
export function realTrackPoints(log: KailashDeviceLog): KailashTrackPoint[] {
  return log.points.filter(p => p.elapsedMs !== 0 && (p.latitude !== 0 || p.longitude !== 0));
}

function esc(s: string): string {
  return s.replace(/[<>&'"]/g, c => (
    { '<': '&lt;', '>': '&gt;', '&': '&amp;', "'": '&apos;', '"': '&quot;' }[c] as string
  ));
}

/** One GPX track from the real sample points, with a <metadata><time> (matching the
 * convention SyncService.ts / KailashDeviceProvider.ts already read metadata.time from) so it
 * slots into the same sync pipeline. No elevation - the watch's altitude field isn't
 * confirmed yet, and this project doesn't emit unverified data. Returns null if there's
 * nothing real to export. */
export function deviceLogToGpx(log: KailashDeviceLog, name = 'Kailash Activity'): string | null {
  const pts = realTrackPoints(log);
  if (pts.length === 0) return null;
  const lines = [
    '<?xml version="1.0" encoding="UTF-8"?>',
    '<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">',
    `  <metadata><time>${esc(pts[0].time)}</time></metadata>`,
    `  <trk><name>${esc(name)}</name><trkseg>`,
  ];
  for (const p of pts) {
    lines.push(`    <trkpt lat="${p.latitude.toFixed(7)}" lon="${p.longitude.toFixed(7)}"><time>${esc(p.time)}</time></trkpt>`);
  }
  lines.push('  </trkseg></trk>', '</gpx>');
  return lines.join('\n');
}
