import { AppState, AppStateStatus } from 'react-native';
import { runSommetSync } from './SommetSync';
import { runSommetGearSync } from './SommetGearSync';
import { trimOldTracks } from './LightLibrary';
import { downloadRecentTiles, startRecentTiles } from './RecentTiles';

// Sommet Sync when the app opens (André, 2026-11-18): the desktop pulls from the NAS ~20 s after
// launch and every 15 min; Android used to pull only after a watch sync or from Settings. Now it
// also syncs shortly after launch and whenever the app comes back to the front, at most once per
// MIN_GAP_MS so switching apps back and forth doesn't hit the NAS each time. No-op when Sommet
// Sync isn't set up; failures are silent (the next open or watch sync tries again).

export const FIRST_DELAY_MS = 5000;
export const MIN_GAP_MS = 15 * 60 * 1000;

let lastRun = 0;

export async function syncNowIfDue(now = Date.now()): Promise<boolean> {
  if (lastRun && now - lastRun < MIN_GAP_MS) return false;
  lastRun = now;
  const r = await runSommetSync().catch(() => null);
  await runSommetGearSync().catch(() => {});
  // Light library (sync/13): older Tracks the server holds leave this device. Also runs with no
  // Sommet Sync (intervals.icu only); a watch-only setup keeps everything.
  await trimOldTracks().catch(() => {});
  // sync/13 option 7b: new moves came in, so the map around them (no-op when off or running).
  if (r && r.pulled > 0) downloadRecentTiles().catch(() => {});
  return true;
}

/** Starts the open/foreground sync (and the recent maps' timer); returns a function that stops it. */
export function startSommetSyncOnOpen(): () => void {
  const stopTiles = startRecentTiles();
  const first = setTimeout(() => { syncNowIfDue().catch(() => {}); }, FIRST_DELAY_MS);
  let prev: AppStateStatus = AppState.currentState;
  const sub = AppState.addEventListener('change', next => {
    if (prev !== 'active' && next === 'active') syncNowIfDue().catch(() => {});
    prev = next;
  });
  return () => { clearTimeout(first); sub.remove(); stopTiles(); };
}

/** Tests only. */
export function _resetSommetSyncOnOpen(): void { lastRun = 0; }
