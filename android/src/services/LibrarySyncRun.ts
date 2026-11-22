import { runLibrarySync } from './LibrarySync';
import { routeFromGpx } from './NavigationService';

// When the route library and the POI library are synced with Sommet Sync (LibrarySync.ts):
// when the app opens (SommetSyncOnOpen), when the Routes or POIs screen is opened, and a few
// seconds after a change made in a library, so several changes in a row make one run.
// The figures of a route that came from another device are worked out here from its GPX, the
// way an imported GPX's are.

const stats = (gpx: string, name: string) => {
  const r = routeFromGpx(gpx, name);
  return { distanceM: r.distanceM, ascentM: r.ascentM, points: r.points.map(p => ({ lat: p.lat, lon: p.lon })) };
};

/** Now. Resolves true when this device's libraries got something from another device. */
export async function syncLibrariesNow(): Promise<boolean> {
  return (await runLibrarySync(stats)).changed;
}

let soon: ReturnType<typeof setTimeout> | null = null;
/** A few seconds after a change here. `onChanged` runs if the run also brought something. */
export function syncLibrariesSoon(onChanged?: () => void): void {
  if (soon) clearTimeout(soon);
  soon = setTimeout(() => {
    soon = null;
    syncLibrariesNow().then(changed => { if (changed) onChanged?.(); }).catch(() => {});
  }, 5000);
}
