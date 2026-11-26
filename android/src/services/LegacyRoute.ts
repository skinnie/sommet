import { Buffer } from 'buffer';

/**
 * Ambit1/2 route region decoder - the Android half of tools/legacy_route.py.
 *
 * Routes on this family live in their own flash region at 0x041EB0, not in the waypoint list
 * and not in the SBEM object model. Until 2026-08-27 nothing here could read it (openambit
 * writes routes but has no reader), so RouteScreen's "On the watch" list inferred routes from
 * route-tagged waypoints - which recovers the A/B markers and never the track.
 *
 * Format and constants come from André's own SuuntoLink capture and from reading his Ambit1
 * back; openambit was only a cross-check on field names, and the hardware overruled it on the
 * table size. See docs/ambit1_route_region.md for the full write-up.
 *
 * parse* read; appendLegacyRoutes/legacyRouteWaypoints BUILD the bytes for a route send (same
 * as tools/legacy_route.py append/route_waypoints, parity-tested) - the write itself is
 * AmbitLegacyNav.sendLegacyRoute. removeLegacyRouteFromRegion is legacy_route.py remove(), for
 * AmbitLegacyNav.removeLegacyRoute.
 */

export const ROUTE_REGION_ADDR = 0x041eb0;
export const HEAD_MAGIC = 0x3008;

const HEAD_LEN = 32;
const INFO_LEN = 48;
const POINT_LEN = 8;
/** The route_info table is a FIXED 50 slots, so points always start here however many routes
 *  exist. Packing it to route_count instead shifts every point and still decodes to
 *  believable coordinates - which is exactly how that bug hid until the watch was read. */
const ROUTE_INFO_SLOTS = 50;
export const POINTS_OFFSET = HEAD_LEN + INFO_LEN * ROUTE_INFO_SLOTS;

/** openambit's distance.c sphere, which the stored offsets were produced with. */
const EARTH_RADIUS_KM = 6367.0;

export interface LegacyRoute {
  name: string;
  distanceM: number;
  points: { latitude: number; longitude: number; altitude: null }[];
}

/** route_count and routepoint_count, from the 32-byte head alone - enough to decide how much
 *  of the region is worth fetching. Returns null when there is no route region written. */
export function parseHead(b64: string): { routeCount: number; pointCount: number } | null {
  const b = Buffer.from(b64, 'base64');
  if (b.length < HEAD_LEN || b.readUInt16LE(0) !== HEAD_MAGIC) return null;
  return { routeCount: b.readUInt16LE(4), pointCount: b.readUInt32LE(8) };
}

export function parseRoutes(b64: string): LegacyRoute[] {
  const b = Buffer.from(b64, 'base64');
  if (b.length < HEAD_LEN || b.readUInt16LE(0) !== HEAD_MAGIC) return [];
  const routeCount = b.readUInt16LE(4);

  const out: LegacyRoute[] = [];
  for (let i = 0; i < routeCount; i++) {
    const o = HEAD_LEN + INFO_LEN * i;
    if (o + INFO_LEN > b.length) break;

    const name = b.slice(o, o + 16).toString('latin1').split('\0')[0].trim();
    const startIndex = b.readUInt32LE(o + 16);
    const count = b.readUInt16LE(o + 20);
    const distanceM = b.readUInt32LE(o + 22);
    // NOT the first point: the bounding-box CENTRE that every point is measured from.
    const midLat = b.readInt32LE(o + 26) / 1e7;
    const midLon = b.readInt32LE(o + 30) / 1e7;

    // Invert the two one-axis haversines the writer used. Along a meridian distance is linear
    // in latitude; along a parallel it scales with cos(latitude).
    const degPerKmLat = 1.0 / ((EARTH_RADIUS_KM * Math.PI) / 180.0);
    const cosMid = Math.cos((midLat * Math.PI) / 180);

    const points: LegacyRoute['points'] = [];
    for (let p = 0; p < count; p++) {
      const po = POINTS_OFFSET + POINT_LEN * (startIndex + p);
      if (po + POINT_LEN > b.length) break;
      // Signed METRES from the centre, x east / y north. Reading these as millimetres
      // collapses the route a thousandfold toward its own centre and still looks plausible.
      const dxM = b.readInt32LE(po);
      const dyM = b.readInt32LE(po + 4);
      points.push({
        latitude: midLat + (dyM / 1000.0) * degPerKmLat,
        longitude: cosMid ? midLon + ((dxM / 1000.0) * degPerKmLat) / cosMid : midLon,
        altitude: null,
      });
    }
    if (points.length > 0) out.push({ name, distanceM, points });
  }
  return out;
}

// ─── Building (route send) ──────────────────────────────────────────────────────────────────
// Port of tools/legacy_route.py build()/append()/route_waypoints(); keep the two in step.

/** Room on the watch: SuuntoLink's route.js limits, and the user guides' 100-entry list. */
export const MAX_ROUTE_POINTS = 1000;
export const MAX_TOTAL_POINTS = 10000;
export const MAX_LIST_WAYPOINTS = 100;
const ROUTE_SLOTS = ROUTE_INFO_SLOTS;

export interface NewLegacyRoute { name: string; points: { lat: number; lon: number }[]; distanceM: number }

/** Python's round(): half to even, so the packed integers match tools/legacy_route.py. */
function pyRound(x: number): number {
  const f = Math.floor(x); const d = x - f;
  if (d > 0.5) return f + 1;
  if (d < 0.5) return f;
  return f % 2 === 0 ? f : f + 1;
}

function haversineKm(latA: number, lonA: number, latB: number, lonB: number): number {
  const r = Math.PI / 180;
  const la = latA * r, lb = latB * r, oa = lonA * r, ob = lonB * r;
  const t = Math.sin((lb - la) / 2) ** 2 + Math.cos(la) * Math.cos(lb) * Math.sin((ob - oa) / 2) ** 2;
  return EARTH_RADIUS_KM * 2 * Math.atan2(Math.sqrt(t), Math.sqrt(1 - t));
}

export function routeDistanceM(points: { lat: number; lon: number }[]): number {
  let km = 0;
  for (let i = 1; i < points.length; i++) km += haversineKm(points[i - 1].lat, points[i - 1].lon, points[i].lat, points[i].lon);
  return Math.trunc(km * 1000);
}

function crc16(data: Uint8Array): number {
  let crc = 0xffff;
  for (const b of data) {
    crc ^= b << 8;
    for (let k = 0; k < 8; k++) crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
  }
  return crc;
}

/** One route's 48-byte info entry and its 8-byte points, start index `start`. */
function packRoute(r: NewLegacyRoute, start: number): { info: Uint8Array; pts: Uint8Array } {
  const latI = r.points.map((p) => pyRound(p.lat * 1e7));
  const lonI = r.points.map((p) => pyRound(p.lon * 1e7));
  const span = (v: number[]) => Math.max(...v) - Math.floor((Math.max(...v) - Math.min(...v)) / 2);
  const alat = span(latI); const alon = span(lonI);
  const mlat = alat / 1e7; const mlon = alon / 1e7;
  const pts = new Uint8Array(POINT_LEN * r.points.length);
  const pv = new DataView(pts.buffer);
  let mx = 0, my = 0;
  r.points.forEach((p, i) => {
    let rx = Math.trunc(haversineKm(mlat, mlon, mlat, p.lon) * 1000);
    if (lonI[i] < alon) rx = -rx;
    let ry = Math.trunc(haversineKm(mlat, mlon, p.lat, mlon) * 1000);
    if (latI[i] < alat) ry = -ry;
    pv.setInt32(POINT_LEN * i, rx, true); pv.setInt32(POINT_LEN * i + 4, ry, true);
    if (i === 0 || rx > mx) mx = rx;
    if (i === 0 || ry > my) my = ry;
  });
  const info = new Uint8Array(INFO_LEN);
  const iv = new DataView(info.buffer);
  const name = Buffer.from(r.name, 'latin1').subarray(0, 15);
  info.set(name, 0);
  iv.setUint32(16, start, true); iv.setUint16(20, r.points.length, true);
  iv.setUint32(22, r.distanceM >>> 0, true); iv.setInt32(26, alat, true); iv.setInt32(30, alon, true);
  iv.setInt32(34, Math.max(0, mx), true); iv.setInt32(38, Math.max(0, my), true);
  iv.setUint16(42, 0xffff, true); iv.setUint16(44, 0xffff, true); iv.setUint16(46, 0, true);
  return { info, pts };
}

/** The route region with `routes` added after the ones already in `region` (null = none). The
 *  existing routes are copied byte for byte; only the head is rewritten. Throws past the room. */
export function appendLegacyRoutes(region: Uint8Array | null, routes: NewLegacyRoute[]): Uint8Array {
  let count = 0, points = 0;
  let oldInfo = new Uint8Array(0), oldPts = new Uint8Array(0);
  if (region && region.length >= HEAD_LEN && (region[0] | (region[1] << 8)) === HEAD_MAGIC) {
    const dv = new DataView(region.buffer, region.byteOffset, region.byteLength);
    count = dv.getUint16(4, true); points = dv.getUint32(8, true);
    oldInfo = region.slice(HEAD_LEN, HEAD_LEN + INFO_LEN * count);
    oldPts = region.slice(POINTS_OFFSET, POINTS_OFFSET + POINT_LEN * points);
  }
  for (const r of routes) {
    if (r.points.length > MAX_ROUTE_POINTS) throw new Error(`Route "${r.name}" has ${r.points.length} points; the watch takes ${MAX_ROUTE_POINTS} per route.`);
    if (r.points.length === 0) throw new Error(`Route "${r.name}" has no points.`);
  }
  if (count + routes.length > ROUTE_SLOTS) throw new Error(`The watch holds ${ROUTE_SLOTS} routes and has ${count}: remove some first.`);
  const total = points + routes.reduce((n, r) => n + r.points.length, 0);
  if (total > MAX_TOTAL_POINTS) throw new Error(`${total} route points in all; the watch holds ${MAX_TOTAL_POINTS}. Remove a route from the watch first.`);

  const infos: Uint8Array[] = [oldInfo]; const ptss: Uint8Array[] = [oldPts];
  let start = points;
  for (const r of routes) { const p = packRoute(r, start); infos.push(p.info); ptss.push(p.pts); start += r.points.length; }
  const cat = (a: Uint8Array[]) => { const o = new Uint8Array(a.reduce((n, x) => n + x.length, 0)); let k = 0; for (const x of a) { o.set(x, k); k += x.length; } return o; };
  const info = cat(infos); const pts = cat(ptss);
  const out = new Uint8Array(POINTS_OFFSET + pts.length);
  const ov = new DataView(out.buffer);
  ov.setUint16(0, HEAD_MAGIC, true); out[2] = 0; out[3] = 1;
  ov.setUint16(4, count + routes.length, true); ov.setUint16(6, 0, true);
  ov.setUint32(8, total, true); ov.setUint16(12, crc16(cat([info, pts])), true);
  out.set(info, HEAD_LEN); out.set(pts, POINTS_OFFSET);
  return out;
}

/** The route region without the first route named `name` (compared without padding spaces, as
 *  the lists show it) - tools/legacy_route.py remove(), byte for byte. The other routes' info
 *  entries and point blocks are copied unchanged, the blocks in their stored order; only the start
 *  index of a route whose block lay after the removed one moves down. Head rebuilt as
 *  appendLegacyRoutes builds it. No capture shows SuuntoLink removing a route: this is the captured
 *  region layout, written the way route send writes it. The last route may go too (André
 *  2026-11-26, Q12 b): the region is then the head with 0 routes and the 50 empty slots, what
 *  appendLegacyRoutes(null, []) makes; no capture shows it. Throws for an unknown name and for
 *  overlapping point blocks. */
export function removeLegacyRouteFromRegion(region: Uint8Array | null, name: string):
    { bytes: Uint8Array; removed: { name: string; pointCount: number }; left: string[] } {
  if (!region || region.length < HEAD_LEN || (region[0] | (region[1] << 8)) !== HEAD_MAGIC) {
    throw new Error('The watch has no routes.');
  }
  const dv = new DataView(region.buffer, region.byteOffset, region.byteLength);
  const count = dv.getUint16(4, true), points = dv.getUint32(8, true);
  const infos = Array.from({ length: count }, (_, i) => region.slice(HEAD_LEN + INFO_LEN * i, HEAD_LEN + INFO_LEN * (i + 1)));
  const rawName = (info: Uint8Array) => { const z = info.subarray(0, 16).indexOf(0); return Buffer.from(info.subarray(0, z < 0 ? 16 : z)).toString('latin1'); };
  const names = infos.map(i => rawName(i).trim());
  const k = names.indexOf(name.trim());
  if (k < 0) throw new Error(`No route named "${name}" on the watch.`);
  const starts = infos.map(i => new DataView(i.buffer).getUint32(16, true));
  const counts = infos.map(i => new DataView(i.buffer).getUint16(20, true));
  const spans = starts.map((s, i) => [s, counts[i]]).sort((a, b) => a[0] - b[0] || a[1] - b[1]);
  const tiled = counts.reduce((n, c) => n + c, 0) === points && spans[0][0] === 0
    && spans.every((sp, i) => i === 0 || spans[i - 1][0] + spans[i - 1][1] === sp[0]);
  if (!tiled) throw new Error("The routes' point blocks overlap or leave gaps: nothing removed.");
  const pts = region.slice(POINTS_OFFSET, POINTS_OFFSET + POINT_LEN * points);
  if (pts.length !== POINT_LEN * points) throw new Error('Route region read short: nothing removed.');
  const s = starts[k], c = counts[k];
  const cat = (a: Uint8Array[]) => { const o = new Uint8Array(a.reduce((n, x) => n + x.length, 0)); let p = 0; for (const x of a) { o.set(x, p); p += x.length; } return o; };
  const newPts = cat([pts.subarray(0, POINT_LEN * s), pts.subarray(POINT_LEN * (s + c))]);
  const kept = infos.filter((_, i) => i !== k).map((info, j) => {
    const i = j < k ? j : j + 1;
    if (starts[i] > s) new DataView(info.buffer).setUint32(16, starts[i] - c, true);
    return info;
  });
  const info = cat(kept);
  const out = new Uint8Array(POINTS_OFFSET + newPts.length);
  const ov = new DataView(out.buffer);
  ov.setUint16(0, HEAD_MAGIC, true); out[2] = 0; out[3] = 1;
  ov.setUint16(4, count - 1, true); ov.setUint16(6, 0, true);
  ov.setUint32(8, points - c, true); ov.setUint16(12, crc16(cat([info, newPts])), true);
  out.set(info, HEAD_LEN); out.set(newPts, POINTS_OFFSET);
  return { bytes: out, removed: { name: rawName(infos[k]), pointCount: c }, left: kept.map(rawName) };
}

/** A route's waypoints as waypoint-list entries, as SuuntoLink writes them on an Ambit1: the
 *  route's name, type 17, and 1970-01-01 00:00:<rank> - the seconds are the place in the route. */
export function legacyRouteWaypoints(routeName: string, wps: { lat: number; lon: number; name: string }[]) {
  return wps.map((w, rank) => ({
    name: w.name, routeName, lat_e7: pyRound(w.lat * 1e7), lon_e7: pyRound(w.lon * 1e7), type: 17,
    ctime_year: 1970, ctime_month: 1, ctime_day: 1, ctime_hour: 0,
    ctime_minute: Math.floor(rank / 60), ctime_second: rank % 60,
  }));
}
