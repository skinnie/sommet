import { NativeModules } from 'react-native';
import { bytesToBase64 } from './Base64';

// Bryton Aero 60 "Follow Track" route - the TypeScript port of tools/bryton_track.py (format
// reverse-engineered from the device's own tracks, hardware-confirmed on the desktop): write
// Tracks/<name>.track + .smy + an empty .tinfo over USB. BrytonTrack.test.ts pins it to the Python.

const Native = (NativeModules as any).BrytonUsb as { writeFile(path: string, base64: string): Promise<boolean> };

type Pt = [number, number, number]; // lat, lon, ele

// Python's round(): halves go to the even neighbour (JS Math.round goes up) - kept for parity.
function pyRound(x: number): number {
  const f = Math.floor(x), d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

export function parseGpx(text: string): Pt[] {
  const pts: Pt[] = [];
  const re = /<(?:trk|rte)pt[^>]*\blat="([-\d.]+)"[^>]*\blon="([-\d.]+)"([\s\S]*?)<\/(?:trk|rte)pt>/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    const ele = /<ele>([-\d.]+)<\/ele>/.exec(m[3]);
    pts.push([parseFloat(m[1]), parseFloat(m[2]), ele ? parseFloat(ele[1]) : 0]);
  }
  if (!pts.length) {
    const re2 = /<(?:trk|rte)pt[^>]*\blat="([-\d.]+)"[^>]*\blon="([-\d.]+)"/g;
    while ((m = re2.exec(text))) pts.push([parseFloat(m[1]), parseFloat(m[2]), 0]);
  }
  return pts;
}

export function decimate(pts: Pt[], maxPoints: number): Pt[] {
  if (maxPoints <= 0 || pts.length <= maxPoints) return pts;
  const step = Math.ceil(pts.length / maxPoints);
  const kept = pts.filter((_, i) => i % step === 0);
  const last = pts[pts.length - 1];
  if (kept[kept.length - 1] !== last) kept.push(last);
  return kept;
}

function haversine(a: Pt, b: Pt): number {
  const R = 6371000, rad = Math.PI / 180;
  const p1 = a[0] * rad, p2 = b[0] * rad, dl = (b[1] - a[1]) * rad;
  const x = Math.sin((p2 - p1) / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dl / 2) ** 2;
  return 2 * R * Math.asin(Math.sqrt(x));
}

export function encodeTrack(pts: Pt[]): { track: Uint8Array; smy: Uint8Array } {
  const track = new Uint8Array(pts.length * 16);
  const tv = new DataView(track.buffer);
  pts.forEach(([lat, lon, ele], i) => {
    tv.setInt32(i * 16, pyRound(lat * 1e6), true);
    tv.setInt32(i * 16 + 4, pyRound(lon * 1e6), true);
    tv.setInt32(i * 16 + 8, pyRound(ele), true);
    tv.setInt32(i * 16 + 12, 0, true);
  });
  let dist = 0;
  for (let i = 0; i < pts.length - 1; i++) dist += haversine(pts[i], pts[i + 1]);
  const lats = pts.map(p => p[0]), lons = pts.map(p => p[1]);
  const smy = new Uint8Array(60);
  const sv = new DataView(smy.buffer);
  sv.setUint16(0, 1, true);
  sv.setUint16(2, pts.length & 0xffff, true);
  sv.setInt32(4, pyRound(Math.max(...lats) * 1e6), true);
  sv.setInt32(8, pyRound(Math.min(...lats) * 1e6), true);
  sv.setInt32(12, pyRound(Math.max(...lons) * 1e6), true);
  sv.setInt32(16, pyRound(Math.min(...lons) * 1e6), true);
  sv.setUint32(20, pyRound(dist), true);
  return { track, smy };
}

export function sanitizeName(name: string): string {
  const safe = Array.from(name || 'Route').filter(c => /[\p{L}\p{N} _-]/u.test(c)).join('').trim().slice(0, 40);
  return safe || 'Route';
}

/** Install a GPX as a Follow Track route (Tracks/<name>.{track,smy,tinfo}). */
export async function sendRouteToBryton(gpx: string, name: string, maxPoints = 2500): Promise<{ name: string; points: number }> {
  let pts = parseGpx(gpx);
  if (pts.length < 2) throw new Error('GPX has fewer than 2 points');
  pts = decimate(pts, maxPoints);
  const { track, smy } = encodeTrack(pts);
  const base = `Tracks/${sanitizeName(name)}`;
  await Native.writeFile(`${base}.track`, bytesToBase64(track));
  await Native.writeFile(`${base}.smy`, bytesToBase64(smy));
  await Native.writeFile(`${base}.tinfo`, '');
  return { name: sanitizeName(name), points: pts.length };
}
