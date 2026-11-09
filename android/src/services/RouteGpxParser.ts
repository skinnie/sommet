import { XMLParser } from 'fast-xml-parser';
import type { LatLon } from './RouteSimplify';

// Parses a GPX file meant to be LOADED onto the watch (a route), as opposed
// to GpxParser.ts which reads GPX files SYNCED FROM the watch (a recorded
// activity, always <trk><trkseg><trkpt> with timestamps).
//
// Route GPX conventions vary by source (per sommet/tools/README.md):
//  - <rte><rtept> is the "proper" route-point element, no timestamps needed.
//  - Some exporters use a plain <trk><trkseg><trkpt> instead — same shape as
//    an activity export, just without real timing, so it's supported as a
//    fallback.
//  - Waypoints are standalone top-level <wpt> elements. Real Komoot/Suunto
//    exports mark a route's start/end as <type>Begin</type>/<type>End</type>,
//    which is a *different* convention from an on-device waypoint and is
//    NOT treated as one here — only <type>Waypoint</type> counts. This
//    matches sommet's own build_route.py: a route needs at least one
//    real on-device waypoint to show up in the watch's Navigation menu at
//    all, confirmed on hardware, so if none is found this parser
//    synthesizes a Start/End pair from the route's own first/last point.

export interface RoutePoint extends LatLon {
  elevation: number | null; // metres, or null (-> "no data" on the watch)
}

export interface RouteWaypoint extends LatLon {
  name: string;
}

export interface ParsedRoute {
  name: string;
  points: RoutePoint[];
  waypoints: RouteWaypoint[]; // always >= 1 by the time this returns
}

const parser = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: '@_' });

function toArray<T>(v: T | T[] | undefined): T[] {
  if (v === undefined) return [];
  return Array.isArray(v) ? v : [v];
}

function parsePoint(p: any): RoutePoint | null {
  if (p?.['@_lat'] === undefined || p?.['@_lon'] === undefined) return null;
  const latitude = parseFloat(p['@_lat']);
  const longitude = parseFloat(p['@_lon']);
  if (Number.isNaN(latitude) || Number.isNaN(longitude)) return null;
  const elevation = p.ele !== undefined && p.ele !== '' ? parseFloat(p.ele) : null;
  return { latitude, longitude, elevation: elevation !== null && !Number.isNaN(elevation) ? elevation : null };
}

export function parseRouteGpx(gpxXml: string, fallbackName: string): ParsedRoute {
  const obj = parser.parse(gpxXml);
  const gpx = obj?.gpx;
  if (!gpx) throw new Error('Invalid GPX file');

  // ── Route points: <rte><rtept> first, <trk><trkseg><trkpt> as fallback ──
  let points: RoutePoint[] = [];
  const routes = toArray(gpx.rte);
  if (routes.length > 0) {
    for (const rte of routes) {
      for (const raw of toArray(rte.rtept)) {
        const pt = parsePoint(raw);
        if (pt) points.push(pt);
      }
    }
  }
  if (points.length === 0) {
    const tracks = toArray(gpx.trk);
    for (const trk of tracks) {
      for (const seg of toArray(trk.trkseg)) {
        for (const raw of toArray(seg.trkpt)) {
          const pt = parsePoint(raw);
          if (pt) points.push(pt);
        }
      }
    }
  }
  if (points.length < 2) {
    throw new Error('No route points found in this GPX (neither <rte> nor <trk>)');
  }

  // ── Waypoints: top-level <wpt>, only type="Waypoint" counts as on-device ──
  const rawWaypoints = toArray(gpx.wpt);
  const onDeviceWaypoints: RouteWaypoint[] = [];
  for (const raw of rawWaypoints) {
    const type = String(raw.type ?? '').trim().toLowerCase();
    if (type !== 'waypoint') continue; // "Begin"/"End" are a GPX-only convention, not this
    const pt = parsePoint(raw);
    if (!pt) continue;
    const name = raw.name ? String(raw.name) : `POI ${onDeviceWaypoints.length + 1}`;
    onDeviceWaypoints.push({ latitude: pt.latitude, longitude: pt.longitude, name });
  }

  // A route needs >= 1 on-device waypoint to appear in the watch's own
  // Navigation menu at all (confirmed on hardware, see HANDOFF.md). If the
  // source GPX had none, synthesize a Start/End pair from the track itself
  // — same fallback sommet's own build_route.py applies.
  const waypoints = onDeviceWaypoints.length > 0
    ? onDeviceWaypoints
    : [
        { latitude: points[0].latitude, longitude: points[0].longitude, name: 'Start' },
        { latitude: points[points.length - 1].latitude, longitude: points[points.length - 1].longitude, name: 'End' },
      ];

  const name = String(
    gpx.metadata?.name ?? routes[0]?.name ?? gpx.trk?.name ?? fallbackName
  ).trim() || fallbackName;

  return { name, points, waypoints };
}

/**
 * Extracts every top-level <wpt> from a GPX file, regardless of <type> —
 * unlike parseRouteGpx's waypoint filtering (which only counts
 * type="Waypoint" as an on-device waypoint for a route), a "POI import" GPX
 * is typically just a flat list of <wpt> elements with no <type> at all
 * (e.g. exported from a mapping tool), so nothing is filtered out here.
 */
export function parseGpxWaypoints(gpxXml: string): RouteWaypoint[] {
  const obj = parser.parse(gpxXml);
  const gpx = obj?.gpx;
  if (!gpx) throw new Error('Invalid GPX file');

  const out: RouteWaypoint[] = [];
  for (const raw of toArray(gpx.wpt)) {
    const pt = parsePoint(raw);
    if (!pt) continue;
    const name = raw.name ? String(raw.name) : `POI ${out.length + 1}`;
    out.push({ latitude: pt.latitude, longitude: pt.longitude, name });
  }
  return out;
}

/** Index of the route point closest to a given waypoint, for pointIndex. */
export function nearestPointIndex(points: LatLon[], target: LatLon): number {
  let best = 0;
  let bestDist = Infinity;
  for (let i = 0; i < points.length; i++) {
    const dLat = points[i].latitude - target.latitude;
    const dLon = points[i].longitude - target.longitude;
    const d = dLat * dLat + dLon * dLon; // planar approx is enough to pick the nearest index
    if (d < bestDist) { bestDist = d; best = i; }
  }
  return best;
}
