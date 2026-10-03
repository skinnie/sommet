// Twin of tools/geo_util.py (the parts the race engine uses). Parity: tools/test_race_parity.js.

export const EARTH_RADIUS_M = 6371000.0;

export interface RoutePt { lat: number; lon: number; ele?: number | null }

const rad = (d: number) => d * Math.PI / 180;

export function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const p1 = rad(lat1), p2 = rad(lat2);
  const dphi = rad(lat2 - lat1), dlmb = rad(lon2 - lon1);
  const a = Math.sin(dphi / 2) ** 2 + Math.cos(p1) * Math.cos(p2) * Math.sin(dlmb / 2) ** 2;
  return 2 * EARTH_RADIUS_M * Math.asin(Math.min(1.0, Math.sqrt(a)));
}

/** [(lat, lon), ...] -> cumulative metres (geo_util.cumulative_distances). */
export function cumulativeDistances(points: Array<[number, number]>): number[] {
  const out = [0.0];
  for (let i = 1; i < points.length; i++) {
    out.push(out[out.length - 1] + haversineM(points[i - 1][0], points[i - 1][1], points[i][0], points[i][1]));
  }
  return out;
}

export function metersPerDegree(lat: number): [number, number] {
  const r = rad(lat);
  const mLat = 111132.92 - 559.82 * Math.cos(2 * r) + 1.175 * Math.cos(4 * r);
  const mLon = 111412.84 * Math.cos(r) - 93.5 * Math.cos(3 * r);
  return [mLat, Math.max(1.0, Math.abs(mLon))];
}

// ---- GPX -----------------------------------------------------------------------------------------
// geo_util.parse_gpx_points: <trkpt> first, else <rtept>, else <wpt>; namespace-agnostic; `ele` null
// when absent or not a number. A small tag scanner (no XML dependency in the app).

function attr(tag: string, name: string): string | null {
  const m = new RegExp(`\\s${name}\\s*=\\s*("([^"]*)"|'([^']*)')`).exec(tag);
  return m ? (m[2] ?? m[3] ?? null) : null;
}

export function parseGpxPoints(gpx: string): RoutePt[] {
  for (const wanted of ['trkpt', 'rtept', 'wpt']) {
    const pts: RoutePt[] = [];
    const re = new RegExp(`<(?:[\\w.-]+:)?${wanted}\\b([^>]*?)(/?)>`, 'g');
    let m: RegExpExecArray | null;
    while ((m = re.exec(gpx))) {
      const lat = Number(attr(m[0], 'lat')), lon = Number(attr(m[0], 'lon'));
      if (attr(m[0], 'lat') === null || attr(m[0], 'lon') === null || !isFinite(lat) || !isFinite(lon)) continue;
      let ele: number | null = null;
      if (m[2] !== '/') {
        const close = gpx.indexOf(`${wanted}>`, re.lastIndex);
        const body = close >= 0 ? gpx.slice(re.lastIndex, close) : '';
        const e = /<(?:[\w.-]+:)?ele\b[^>]*>([^<]*)</.exec(body);
        if (e) {
          const v = e[1].trim();
          ele = v !== '' && isFinite(Number(v)) ? Number(v) : null;
        }
      }
      pts.push({ lat, lon, ele });
    }
    if (pts.length) return pts;
  }
  return [];
}
