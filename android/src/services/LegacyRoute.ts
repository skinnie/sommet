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
 * Read-only. Nothing here writes to a watch.
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
