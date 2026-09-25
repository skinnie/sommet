import { concat, fitCrc16, le32 } from './MageneBle';
import { withMagene } from './MageneDevice';
import { packets, pbInt32, pbMsg, pbSint32, transferFile } from './MageneTransfer';

// Send a route to the Magene C406 - the TypeScript port of tools/magene_route.py (HW-verified
// there 2026-09-25). The C406 holds ONE route; sending replaces it.

type Pt = [number, number]; // [lat, lon]
const PRECISION = 2 ** 31;
const fixed = (deg: number) => Math.trunc((deg * PRECISION) / 180);

function haversine(a: Pt, b: Pt): number {
  const r = 6371000, rad = Math.PI / 180;
  const la1 = a[0] * rad, la2 = b[0] * rad, dla = la2 - la1, dlo = (b[1] - a[1]) * rad;
  const h = Math.sin(dla / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dlo / 2) ** 2;
  return 2 * r * Math.asin(Math.min(1, Math.sqrt(h)));
}

const pathType = (lat: number, lon: number) => [...pbSint32(1, fixed(lat)), ...pbSint32(2, fixed(lon))];

function roadPlan(points: Pt[]): number[] {
  let dist = 0;
  for (let i = 0; i < points.length - 1; i++) dist += haversine(points[i], points[i + 1]);
  const first = points[0], last = points[points.length - 1];
  const step = [
    ...pbInt32(1, Math.round(dist)), ...pbInt32(2, 0),
    ...pbMsg(3, pathType(first[0], first[1])), ...pbMsg(4, pathType(last[0], last[1])),
    ...pbMsg(5, [...pbInt32(1, 1), ...pbInt32(2, 0)]),
  ];
  const body = [...pbMsg(1, step), ...pbInt32(2, points.length)];
  for (const [la, lo] of points) body.push(...pbMsg(3, pathType(la, lo)));
  return body;
}

export function buildRouteFile(points: Pt[], segSize = 50, previewMax = 180) {
  const parts: number[][] = [];
  let steps = 0;
  for (let i = 0; i < points.length - 1; i += segSize) {
    const seg = points.slice(i, i + segSize + 1);
    if (seg.length >= 2) {
      const plan = roadPlan(seg);
      parts.push([...le32(plan.length), ...plan]);
      steps++;
    }
  }
  const head = concat(...parts);
  const previewOffset = head.length + 4;
  const stride = Math.max(1, Math.floor(points.length / previewMax));
  const dots: number[][] = [];
  for (let i = 0; i < points.length; i += stride) {
    const blk = [...pbMsg(1, pathType(points[i][0], points[i][1])), ...pbInt32(2, 0)];
    dots.push([...le32(blk.length), ...blk]);
  }
  return { file: concat(head, [0xff, 0xff, 0xff, 0xff], ...dots), steps, previewOffset, previewCount: dots.length };
}

export function parseGpxPoints(gpx: string): Pt[] {
  const pts: Pt[] = [];
  const re = /<(?:\w+:)?(?:trkpt|rtept)\b([^>]*)>/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(gpx))) {
    const lat = /\blat\s*=\s*["']([-\d.eE+]+)["']/.exec(m[1]);
    const lon = /\blon\s*=\s*["']([-\d.eE+]+)["']/.exec(m[1]);
    if (lat && lon) pts.push([parseFloat(lat[1]), parseFloat(lon[1])]);
  }
  return pts;
}

function s32(v: number) { return le32(fixed(v) >>> 0); }

export async function sendRoute(address: string, gpx: string): Promise<{ ok: boolean; error?: string; points: number }> {
  const points = parseGpxPoints(gpx);
  if (points.length < 2) return { ok: false, error: 'GPX has fewer than 2 points', points: points.length };
  const { file, steps, previewOffset, previewCount } = buildRouteFile(points);
  const crc = fitCrc16(file);
  return withMagene(address, async mtu => {
    const pkts = packets(file, mtu);
    const size = pkts.reduce((a, p) => a + p.length, 0);
    const lats = points.map(p => p[0]), lons = points.map(p => p[1]);
    const ne: Pt = [Math.max(...lats), Math.max(...lons)];
    const sw: Pt = [Math.min(...lats), Math.min(...lons)];
    const center: Pt = [(ne[0] + sw[0]) / 2, (ne[1] + sw[1]) / 2];
    const lonSpan = Math.max(1e-6, ne[1] - sw[1]);
    const zoom = Math.trunc(Math.log2(86400 / (lonSpan * 256)) - 3);
    let total = 0;
    for (let i = 0; i < points.length - 1; i++) total += haversine(points[i], points[i + 1]);
    const routeId = Math.floor(Date.now() / 1000) & 0x7fffffff;
    // 54-byte WriteRouteInfoWithPreviewCommand (magene_route._route_info).
    const info = new Uint8Array(54);
    info.set([0x40, 0x8d], 0);
    info.set(le32(routeId), 2);
    info.set(le32(Math.trunc(total)), 6);
    info[10] = zoom & 0xff;
    info.set([crc & 0xff, (crc >> 8) & 0xff], 11);
    info[13] = steps & 0xff;
    info.set(le32(size), 14);
    info.set(s32(center[1]), 18); info.set(s32(center[0]), 22);
    info.set(s32(ne[1]), 26); info.set(s32(ne[0]), 30);
    info.set(s32(sw[1]), 34); info.set(s32(sw[0]), 38);
    info.set(le32(previewOffset), 42);
    info.set(le32(previewCount), 46);
    const res = await transferFile(info, pkts);
    return { ok: res.error === null, error: res.error ?? undefined, points: points.length };
  });
}
