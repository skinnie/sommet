import RNFS from 'react-native-fs';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { connect, disconnect, pickGpxFile, writeRoute, readRegion, saveToDownloads, getDeviceInfo, readLegacyNav } from '../native/AmbitUsbModule';
import { isAmbit12 } from './AmbitSettingsService';
import { parseRouteGpx, nearestPointIndex, RoutePoint } from './RouteGpxParser';
import { simplifyRoute } from './RouteSimplify';
import {
  decodeNavigation, navigationToGpx, WatchNavigation, WatchRoute, WatchWaypoint,
} from './RouteReader';
import { readNavBases } from './MemoryMap';

// AMBIT3_MAX_ROUTE_POINTS / AMBIT3_MAX_NAME_BYTES from
// android/app/src/main/cpp/libambit/device_driver_ambit3_navigation.h —
// kept in sync by hand, same convention as the other native constants
// mirrored on the JS side elsewhere in this codebase.
const MAX_ROUTE_POINTS = 1000;
const MAX_NAME_BYTES = 15;

// Transport is auto-detected, not chosen per-call (2026-08-09). connect()/
// disconnect() from AmbitUsbModule are transport-aware: over an active BLE link
// they no-op (the watch is already connected, see setBleTransportActive), over
// USB they open/close the cable. writeRoute()/readRegion() act on the same
// shared native device either way. So route send/read need no BLE-specific path
// or re-pairing — they just use connect()/disconnect() like every other op, and
// work over whichever transport is currently connected.

export interface SendRouteState {
  phase: 'idle' | 'picking' | 'parsing' | 'connecting' | 'writing' | 'done' | 'error';
  routeName?: string;
  pointCount?: number;
  waypointCount?: number;
  error?: string;
}

type Listener = (state: SendRouteState) => void;

function haversineM(a: { latitude: number; longitude: number }, b: { latitude: number; longitude: number }): number {
  const R = 6371000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(b.latitude - a.latitude);
  const dLon = toRad(b.longitude - a.longitude);
  const s = Math.sin(dLat / 2) ** 2 + Math.cos(toRad(a.latitude)) * Math.cos(toRad(b.latitude)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(s), Math.sqrt(1 - s));
}

// Exported, 2026-08-10 ("Garmin: POIs and routes, please follow the same logic as suunto,
// showing them on the maps") - GarminGpxExportService.ts's new preview list needs the same
// distance/ascent/descent stats this file already computes for the Suunto pending-route
// preview, rather than a second copy of the same math.
export function computeDistanceAscentDescent(points: RoutePoint[]): { distanceM: number; ascentM: number; descentM: number } {
  let distanceM = 0;
  let ascentM = 0;
  let descentM = 0;
  for (let i = 1; i < points.length; i++) {
    distanceM += haversineM(points[i - 1], points[i]);
    const a = points[i - 1].elevation;
    const b = points[i].elevation;
    if (a !== null && b !== null) {
      const d = b - a;
      if (d > 0) ascentM += d; else descentM += -d;
    }
  }
  return { distanceM: Math.round(distanceM), ascentM: Math.round(ascentM), descentM: Math.round(descentM) };
}

/**
 * Full pipeline: pick a GPX file, parse it, simplify to the watch's point
 * budget, and write it to the connected watch.
 *
 * IMPORTANT, surfaced to the caller via SendRouteState so the UI can show
 * it: a route written this way is NOT durable. Any subsequent SuuntoLink
 * cable sync, or the Suunto phone app merely coming into BLE range, will
 * silently wholesale-replace whatever is in the watch's Routes region —
 * confirmed on hardware. POIs are preserved automatically on the native
 * side; existing on-watch routes are not. This is a "load right before you
 * go" feature, not a save.
 *
 * Transport (BLE vs USB) is auto-detected via the shared connect()/disconnect()
 * — nothing to choose or pass. If a BLE link is already up it's used as-is (no
 * re-scan/re-pair); otherwise the cable is opened.
 */
export interface PendingRoute {
  name: string;
  points: { lat: number; lon: number; alt: number | null }[];
  waypoints: { lat: number; lon: number; name: string; pointIndex: number }[];
  distanceM: number;
  ascentM: number;
  descentM: number;
  timestampSec: number;
}

// v3.0 UI port (2026-08-09, "re do routes... to match entirely desktop") - split from the
// old single sendRouteToWatch() into pick+parse / upload, matching desktop's own real
// RouteService.pendingRoute pattern (RoutesPage.qml: import shows a live preview + a real
// "Upload to watch" tap, not one opaque button that picks-and-immediately-writes). Same
// underlying calls as before (pickGpxFile/parseRouteGpx/simplifyRoute/writeRoute), just the
// intermediate parsed route is now a real value the caller can render before committing to
// a write, instead of being hidden inside one function.
export async function pickAndParseRoute(): Promise<PendingRoute | null> {
  let gpxPath: string;
  try {
    gpxPath = await pickGpxFile();
  } catch (e: any) {
    if (e?.code === 'GPX_PICK_CANCELLED') return null;
    throw new Error(e?.message ?? 'Sélection du fichier annulée');
  }

  const xml = await RNFS.readFile(gpxPath, 'utf8');
  const fallbackName = (gpxPath.split('/').pop() ?? 'Route').replace(/\.gpx$/i, '');
  const parsed = parseRouteGpx(xml, fallbackName);

  const kept = simplifyRoute(
    parsed.points,
    MAX_ROUTE_POINTS,
    parsed.waypoints.map(w => nearestPointIndex(parsed.points, w)),
  );
  if (kept === null) {
    throw new Error(`Route too complex to simplify under ${MAX_ROUTE_POINTS} points`);
  }

  const keptSet = new Map(kept.map((origIdx, newIdx) => [origIdx, newIdx]));
  const simplifiedPoints = kept.map(i => parsed.points[i]);
  const { distanceM, ascentM, descentM } = computeDistanceAscentDescent(parsed.points);

  const waypoints = parsed.waypoints.map(w => {
    const origIdx = nearestPointIndex(parsed.points, w);
    // simplifyRoute is called with every waypoint's original index in `forced`, which
    // guarantees each one survives simplification - so this lookup cannot miss. If it
    // somehow did, the native side refuses a route with zero resolvable waypoints rather
    // than silently writing a route invisible in the watch's own Navigation menu.
    const newIdx = keptSet.get(origIdx);
    return { lat: w.latitude, lon: w.longitude, name: w.name.slice(0, MAX_NAME_BYTES), pointIndex: newIdx ?? 0 };
  });

  return {
    name: parsed.name.slice(0, MAX_NAME_BYTES),
    points: simplifiedPoints.map(p => ({
      lat: p.latitude,
      lon: p.longitude,
      alt: p.elevation !== null ? Math.round(p.elevation) : null,
    })),
    waypoints,
    distanceM,
    ascentM,
    descentM,
    timestampSec: Math.floor(Date.now() / 1000),
  };
}

/**
 * Uploads an already-picked-and-parsed route.
 *
 * IMPORTANT, surfaced to the caller via SendRouteState so the UI can show it: a route
 * written this way is NOT durable. Any subsequent SuuntoLink cable sync, or the Suunto phone
 * app merely coming into BLE range, will silently wholesale-replace whatever is in the
 * watch's Routes region - confirmed on hardware. POIs are preserved automatically on the
 * native side; existing on-watch routes are not. This is a "load right before you go"
 * feature, not a save.
 *
 * Transport (BLE vs USB) is auto-detected via the shared connect()/disconnect() - nothing to
 * choose or pass. If a BLE link is already up it's used as-is (no re-scan/re-pair);
 * otherwise the cable is opened.
 */
export async function uploadRoute(route: PendingRoute, onState: Listener): Promise<void> {
  const emit = (s: SendRouteState) => onState(s);
  const meta = { routeName: route.name, pointCount: route.points.length, waypointCount: route.waypoints.length };

  emit({ phase: 'connecting', ...meta });
  try {
    await connect();
  } catch (e: any) {
    emit({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' });
    return;
  }

  emit({ phase: 'writing', ...meta });
  try {
    await writeRoute(route);
    await clearNavigationCache(); // real watch state just changed - see below
    emit({ phase: 'done', ...meta });
  } catch (e: any) {
    emit({ phase: 'error', error: e?.message ?? 'Failed to write the route' });
  } finally {
    await disconnect().catch(() => {});
  }
}

// Real, 2026-08-10 ("cache activities/POIs and only import the differences from the
// watch, making it faster" -> "it is not upon the watch to give you that, is on the app to
// store the activities, so they can load almost immediately and just refresh what is new") -
// this app's own Activities already work this way (a local SQLite table - see database/
// db.ts - loads instantly, a Sync only fetches what's new off the watch). Routes/Waypoints
// had no such local copy at all before this: readOnWatchNavigation() always needed a real
// ~146KB watch round trip before RouteScreen could show anything, even offline. Persisted
// via AsyncStorage (not SQLite - this is one JSON blob, the watch's own last-known full
// snapshot, not query-able rows) so it survives app restarts, not just navigation. Any real
// write (uploadRoute, above) clears it immediately, same as before.
const NAV_CACHE_KEY = 'ambitapp:cachedNavigation';

/** The locally persisted copy of the watch's Routes/Waypoints, if any - for an instant
 * "On the watch" list before (or entirely without) a real watch round trip. */
export async function getCachedNavigation(): Promise<WatchNavigation | null> {
  try {
    const raw = await AsyncStorage.getItem(NAV_CACHE_KEY);
    return raw ? (JSON.parse(raw) as WatchNavigation) : null;
  } catch {
    return null;
  }
}

async function clearNavigationCache(): Promise<void> {
  await AsyncStorage.removeItem(NAV_CACHE_KEY).catch(() => {});
}

/** Read-only: the watch's own current Routes/Waypoints, for a real "On the watch" list -
 * matches desktop's own RouteService.onWatchRoutes/GarminService.onDeviceRoutes shape
 * (RoutesPage.qml). Reuses exportNavigationToGpx()'s own read half, just returns the rich
 * per-route/per-waypoint data instead of only a count + a combined GPX file. Always does a
 * real watch read (the "refresh what's new" half) - RouteScreen's own loadOnWatch() is what
 * shows getCachedNavigation()'s result first so this doesn't block the UI. */
export async function readOnWatchNavigation(): Promise<WatchNavigation> {
  await connect();
  try {
    // Ambit1/2 (Bluebird): no SBEM memory map (readNavBases is empty/wrong here). Waypoints
    // and routes come from libambit_navigation_read (readLegacyNav) instead - desktop parity.
    // Legacy routes carry no per-point track here (this family rarely has routes; the Ambit2
    // reports 0), so points is empty - waypoints are the real payload.
    try {
      if (isAmbit12((await getDeviceInfo()).name)) {
        const nav = JSON.parse(await readLegacyNav());
        const wps: any[] = nav.waypoints ?? [];
        // On the Ambit1/2 a "route" is a set of waypoints sharing a route_name (libambit reads
        // them as waypoints and never fills ps->routes - hence "0 routes" everywhere until now).
        // Reconstruct: group the route-tagged waypoints by route name (ordered by their index),
        // and keep the rest as standalone POIs/waypoints.
        const byRoute = new Map<string, any[]>();
        const loose: any[] = [];
        for (const w of wps) {
          const rn = (w.routeName || '').trim();
          if (rn) { if (!byRoute.has(rn)) byRoute.set(rn, []); byRoute.get(rn)!.push(w); }
          else loose.push(w);
        }
        const routes: WatchRoute[] = [];
        for (const [name, pts] of byRoute) {
          // A real route has >=2 points; a lone route-tagged waypoint is just a POI whose
          // route_name happens to equal its own name - keep it as a waypoint, not a 1-pt route.
          if (pts.length < 2) { loose.push(...pts); continue; }
          pts.sort((a, b) => (a.index ?? 0) - (b.index ?? 0));
          const rpts = pts.map(w => ({ latitude: w.lat_e7 / 1e7, longitude: w.lon_e7 / 1e7, altitude: null }));
          // Sum the great-circle distance between consecutive route waypoints (haversine).
          // Legacy routes carry no stored distance - this is the length along the turn-points.
          let distanceM = 0;
          for (let i = 1; i < rpts.length; i++) {
            const a = rpts[i - 1], b = rpts[i];
            const R = 6371000, toRad = Math.PI / 180;
            const dLat = (b.latitude - a.latitude) * toRad;
            const dLon = (b.longitude - a.longitude) * toRad;
            const h = Math.sin(dLat / 2) ** 2 +
              Math.cos(a.latitude * toRad) * Math.cos(b.latitude * toRad) * Math.sin(dLon / 2) ** 2;
            distanceM += 2 * R * Math.asin(Math.min(1, Math.sqrt(h)));
          }
          routes.push({ name, points: rpts, distanceM: Math.round(distanceM), ascentM: 0, descentM: 0 });
        }
        const data: WatchNavigation = {
          waypoints: loose.map((w: any) => ({
            name: w.name || 'Waypoint', routeName: '',
            latitude: w.lat_e7 / 1e7, longitude: w.lon_e7 / 1e7,
          })),
          routes,
        };
        await AsyncStorage.setItem(NAV_CACHE_KEY, JSON.stringify(data)).catch(() => {});
        return data;
      }
    } catch { /* not legacy, or detection failed - fall through to the SBEM path */ }

    const bases = await readNavBases();
    const [waypointsB64, routesB64] = await Promise.all([
      readRegion(bases.waypointBase, bases.waypointSize),
      readRegion(bases.routeBase, bases.routeSize),
    ]);
    const data = decodeNavigation(waypointsB64, routesB64);
    await AsyncStorage.setItem(NAV_CACHE_KEY, JSON.stringify(data)).catch(() => {});
    return data;
  } finally {
    await disconnect().catch(() => {});
  }
}

/** Exports one already-read on-watch route/waypoint as its own GPX file in Downloads -
 * matches desktop's own per-route/per-POI "Export" button (RoutesPage.qml/PoisPage.qml),
 * which needs no new watch round trip since readOnWatchNavigation() already has the data. */
export async function exportSingleRouteToGpx(route: WatchRoute): Promise<void> {
  const gpx = navigationToGpx({ routes: [route], waypoints: [] });
  const safeName = route.name.replace(/[\\/:*?"<>|]/g, '_') || 'route';
  const fileName = `${safeName}.gpx`;
  const path = `${RNFS.CachesDirectoryPath}/${fileName}`;
  await RNFS.writeFile(path, gpx, 'utf8');
  await saveToDownloads(path, fileName, 'application/gpx+xml');
}

export async function exportSingleWaypointToGpx(wp: WatchWaypoint): Promise<void> {
  const gpx = navigationToGpx({ routes: [], waypoints: [wp] });
  const safeName = wp.name.replace(/[\\/:*?"<>|]/g, '_') || 'waypoint';
  const fileName = `${safeName}.gpx`;
  const path = `${RNFS.CachesDirectoryPath}/${fileName}`;
  await RNFS.writeFile(path, gpx, 'utf8');
  await saveToDownloads(path, fileName, 'application/gpx+xml');
}

export interface ExportNavState {
  phase: 'idle' | 'connecting' | 'reading' | 'done' | 'error';
  routeCount?: number;
  waypointCount?: number;
  error?: string;
}

/**
 * Reads the Waypoints and Routes flash regions off the watch and saves them
 * as a GPX file in Downloads. Read-only, no risk to the watch — unlike
 * sendRouteToWatch(), this never writes anything.
 *
 * Transport auto-detected (BLE if connected, else USB) — see sendRouteToWatch.
 */
export async function exportNavigationToGpx(
  onState: (s: ExportNavState) => void,
): Promise<void> {
  onState({ phase: 'connecting' });
  try {
    await connect();
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' });
    return;
  }

  onState({ phase: 'reading' });
  try {
    const bases = await readNavBases();
    const [waypointsB64, routesB64] = await Promise.all([
      readRegion(bases.waypointBase, bases.waypointSize),
      readRegion(bases.routeBase, bases.routeSize),
    ]);
    const nav = decodeNavigation(waypointsB64, routesB64);
    if (nav.routes.length === 0 && nav.waypoints.length === 0) {
      throw new Error('The watch has no routes or waypoints to export');
    }
    const gpx = navigationToGpx(nav);
    const fileName = `navigation_${Date.now()}.gpx`;
    const path = `${RNFS.CachesDirectoryPath}/${fileName}`;
    await RNFS.writeFile(path, gpx, 'utf8');
    await saveToDownloads(path, fileName, 'application/gpx+xml');
    onState({ phase: 'done', routeCount: nav.routes.length, waypointCount: nav.waypoints.length });
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Failed to export navigation data' });
  } finally {
    await disconnect().catch(() => {});
  }
}
