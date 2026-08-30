import { base64ToBytes } from './Base64';

// Decodes the raw Waypoints/Routes flash regions read off the watch, using
// the exact byte layouts from ambit-app/tools/ambit_format.py (struct.Struct
// definitions, cross-checked against device_driver_ambit3_navigation.c's own
// forward math for the relative-coordinate projection this inverts).
//
// Scope note: this does NOT attempt to associate a route with "its"
// waypoints via the RouteIndexEntry.waypoint_start/count fields — per
// tools/README.md those are in a different order than the route descriptors
// once there's more than one route, and even ambit-app's own reference
// decoder (show_navigation in write_nav.py) doesn't resolve that
// association programmatically; it just prints each waypoint's own
// route_name string. Routes and waypoints are exported as independent GPX
// elements here too, same as that reference tool's approach.

const AMBIT3_WAYPOINT_BASE = 0x005000;
const AMBIT3_WAYPOINT_REGION_SIZE = 16384;
const AMBIT3_ROUTE_BASE = 0x14c080;
const AMBIT3_ROUTE_REGION_SIZE = 130000;

const WAYPOINT_HEADER_MAGIC = 0x0334;
const ROUTE_HEADER_MAGIC = 0x340c;
const ALTITUDE_NONE = 30000;

const ROUTE_RADIUS_M = (10800.0 * 1852.0) / Math.PI;

// Offsets relative to each region's own base (region base subtracted from
// the absolute addresses in tools/ambit_format.py).
const WAYPOINT_DESC_OFFSET = 0x005020 - AMBIT3_WAYPOINT_BASE; // 0x20
const ROUTE_DESC_OFFSET    = 0x14c0a0 - AMBIT3_ROUTE_BASE;     // 0x20
const ROUTE_POINTS_OFFSET  = 0x14cac8 - AMBIT3_ROUTE_BASE;     // 0xa48

function readU16(b: Uint8Array, off: number): number {
  return b[off] | (b[off + 1] << 8);
}
function readI32(b: Uint8Array, off: number): number {
  const v = new DataView(b.buffer, b.byteOffset + off, 4);
  return v.getInt32(0, true);
}
function readU32(b: Uint8Array, off: number): number {
  const v = new DataView(b.buffer, b.byteOffset + off, 4);
  return v.getUint32(0, true);
}
function readNulString(b: Uint8Array, off: number, maxLen: number): string {
  let end = off;
  const limit = off + maxLen;
  while (end < limit && b[end] !== 0) end++;
  // Same decode(..., "replace") spirit as decode_name() in ambit_format.py:
  // best-effort UTF-8, never throws on a malformed byte.
  let out = '';
  let i = off;
  while (i < end) {
    const b0 = b[i];
    if (b0 < 0x80) { out += String.fromCharCode(b0); i += 1; }
    else if ((b0 & 0xe0) === 0xc0 && i + 1 < end) { out += String.fromCharCode(((b0 & 0x1f) << 6) | (b[i + 1] & 0x3f)); i += 2; }
    else if ((b0 & 0xf0) === 0xe0 && i + 2 < end) { out += String.fromCharCode(((b0 & 0x0f) << 12) | ((b[i + 1] & 0x3f) << 6) | (b[i + 2] & 0x3f)); i += 3; }
    else { out += '�'; i += 1; }
  }
  return out;
}

export interface WatchRoutePoint {
  latitude: number;
  longitude: number;
  altitude: number | null;
}

export interface WatchRoute {
  name: string;
  points: WatchRoutePoint[];
  distanceM: number;
  ascentM: number;
  descentM: number;
}

export interface WatchWaypoint {
  name: string;
  routeName: string;
  latitude: number;
  longitude: number;
}

export interface WatchNavigation {
  routes: WatchRoute[];
  waypoints: WatchWaypoint[];
}

export { AMBIT3_WAYPOINT_BASE, AMBIT3_WAYPOINT_REGION_SIZE, AMBIT3_ROUTE_BASE, AMBIT3_ROUTE_REGION_SIZE };

// How many header bytes to read from each region to size the real read down. For routes this is
// the whole header+descriptor block (up to the points), so route/point counts are all known.
export function navHeaderReadLen(): { waypointHeaderLen: number; routeHeaderLen: number } {
  return { waypointHeaderLen: 4, routeHeaderLen: ROUTE_POINTS_OFFSET };
}

// Given each region's header bytes (base64), work out how many bytes are actually USED, so the
// caller reads only that instead of the full allocated region. The routes region is ~130 KB
// allocated but a handful of routes use a few KB — reading it all over BLE is the ~1-min stall.
// Falls back to the full size if the magic/header doesn't parse, so a bad guess never truncates
// real data. (2026-08-30.)
export function navUsedSizes(
  waypointHeaderB64: string, routeHeaderB64: string,
  waypointFull: number, routeFull: number,
): { waypointUsed: number; routeUsed: number } {
  let waypointUsed = waypointFull;
  let routeUsed = routeFull;
  try {
    const w = base64ToBytes(waypointHeaderB64);
    if (w.length >= 4 && readU16(w, 0) === WAYPOINT_HEADER_MAGIC) {
      const n = readU16(w, 2);
      waypointUsed = Math.min(waypointFull, WAYPOINT_DESC_OFFSET + 52 * n + 4);
    }
  } catch { /* keep full */ }
  try {
    const r = base64ToBytes(routeHeaderB64);
    if (r.length >= 6 && readU16(r, 0) === ROUTE_HEADER_MAGIC) {
      const count = readU16(r, 4);
      let maxIdx = 0;
      let ok = true;
      for (let i = 0; i < count; i++) {
        const doff = ROUTE_DESC_OFFSET + 52 * i;
        if (doff + 22 > r.length) { ok = false; break; }   // header shorter than expected → full
        const startIndex = readU32(r, doff + 16);
        const pointCount = readU16(r, doff + 20);
        maxIdx = Math.max(maxIdx, startIndex + pointCount);
      }
      if (ok) routeUsed = Math.min(routeFull, ROUTE_POINTS_OFFSET + 12 * maxIdx + 12);
    }
  } catch { /* keep full */ }
  return { waypointUsed, routeUsed };
}

export function decodeNavigation(waypointsB64: string, routesB64: string): WatchNavigation {
  const wRegion = base64ToBytes(waypointsB64);
  const rRegion = base64ToBytes(routesB64);

  // ── Waypoints ──────────────────────────────────────────────────────────────
  const wptMagic = readU16(wRegion, 0);
  const wptCount = readU16(wRegion, 2);
  const waypoints: WatchWaypoint[] = [];
  if (wptMagic === WAYPOINT_HEADER_MAGIC) {
    for (let i = 0; i < wptCount; i++) {
      const off = WAYPOINT_DESC_OFFSET + 52 * i;
      if (off + 52 > wRegion.length) break;
      const latitude = readI32(wRegion, off) / 1e7;
      const longitude = readI32(wRegion, off + 4) / 1e7;
      const name = readNulString(wRegion, off + 8, 16);
      const routeName = readNulString(wRegion, off + 24, 16);
      waypoints.push({ name: name || 'Waypoint', routeName, latitude, longitude });
    }
  }

  // ── Routes ─────────────────────────────────────────────────────────────────
  const routeMagic = readU16(rRegion, 0);
  const routeCount = readU16(rRegion, 4);
  const routes: WatchRoute[] = [];
  if (routeMagic === ROUTE_HEADER_MAGIC) {
    for (let i = 0; i < routeCount; i++) {
      const doff = ROUTE_DESC_OFFSET + 52 * i;
      if (doff + 52 > rRegion.length) break;
      const name = readNulString(rRegion, doff, 16);
      const startIndex = readU32(rRegion, doff + 16);
      const pointCount = readU16(rRegion, doff + 20);
      const distance = readU32(rRegion, doff + 22);
      const midLat = readI32(rRegion, doff + 26);
      const midLon = readI32(rRegion, doff + 30);
      const ascent = readU16(rRegion, doff + 48);
      const descent = readU16(rRegion, doff + 50);

      const midLatDeg = midLat / 1e7;
      const cosMid = Math.cos((midLatDeg * Math.PI) / 180);

      const points: WatchRoutePoint[] = [];
      for (let k = 0; k < pointCount; k++) {
        const poff = ROUTE_POINTS_OFFSET + 12 * (startIndex + k);
        if (poff + 12 > rRegion.length) break;
        const x = readI32(rRegion, poff);
        const y = readI32(rRegion, poff + 4);
        const altRaw = readU16(rRegion, poff + 8);
        // Inverse of ambit3_nav_relative_xy() in device_driver_ambit3_navigation.c.
        const latitude = midLatDeg + ((y / ROUTE_RADIUS_M) * 180) / Math.PI;
        const longitude = midLon / 1e7 + ((x / (ROUTE_RADIUS_M * cosMid)) * 180) / Math.PI;
        points.push({ latitude, longitude, altitude: altRaw === ALTITUDE_NONE ? null : altRaw });
      }
      routes.push({ name: name || `Route ${i + 1}`, points, distanceM: distance, ascentM: ascent, descentM: descent });
    }
  }

  return { routes, waypoints };
}

export function navigationToGpx(nav: WatchNavigation): string {
  const escapeXml = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');

  const routeBlocks = nav.routes.map(r => {
    const pts = r.points.map(p =>
      `      <rtept lat="${p.latitude.toFixed(7)}" lon="${p.longitude.toFixed(7)}">${p.altitude !== null ? `<ele>${p.altitude}</ele>` : ''}</rtept>`
    ).join('\n');
    return `  <rte>\n    <name>${escapeXml(r.name)}</name>\n    <extensions><distance>${r.distanceM}</distance><ascent>${r.ascentM}</ascent><descent>${r.descentM}</descent></extensions>\n${pts}\n  </rte>`;
  }).join('\n');

  const wptBlocks = nav.waypoints.map(w =>
    `  <wpt lat="${w.latitude.toFixed(7)}" lon="${w.longitude.toFixed(7)}"><name>${escapeXml(w.name)}</name>${w.routeName ? `<desc>${escapeXml(w.routeName)}</desc>` : ''}<type>Waypoint</type></wpt>`
  ).join('\n');

  return `<?xml version="1.0" encoding="UTF-8"?>\n` +
    `<gpx version="1.1" creator="Sommet" xmlns="http://www.topografix.com/GPX/1/1">\n` +
    `${wptBlocks}${wptBlocks && routeBlocks ? '\n' : ''}${routeBlocks}\n</gpx>`;
}
