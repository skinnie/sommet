import AsyncStorage from '@react-native-async-storage/async-storage';

// Light library settings (sync/13, André 2026-11-20): "the average joe don't want 4000 activities
// on his pc or tablet.. he wants to consult it and be safe.. either intervals.. or nas". Every
// move stays in the list; with "Last 90 days" (the default) the Tracks and FITs of older moves
// stay on the server once it holds them, and load when one is opened. "Everything" keeps them all
// here. The desktop keeps the same choice as storage/originals ("nas" | "device").
// Only AsyncStorage here, so the services that just need the choice (IntervalsTracks) can read it.

const KEEP_KEY = 'sommet.library.keep';          // 'recent' (default) | 'all'
const CACHE_MB_KEY = 'sommet.library.cacheMb';  // space for opened older moves, 100..2000
// sync/13 option 7b: keep the map tiles around the last 90 days' moves (desktop map/keepRecentTiles).
const KEEP_TILES_KEY = 'sommet.library.keepRecentTiles';   // '1' = on; off by default

export const LIGHT_DAYS = 90;
/** Default space for opened older moves on a tablet or phone (desktop: 800 MB). */
export const DEFAULT_CACHE_MB = 300;
export const MIN_CACHE_MB = 100;
export const MAX_CACHE_MB = 2000;

/** True with "Last 90 days" (the default), false with "Everything". */
export async function getKeepRecentOnly(): Promise<boolean> {
  try { return (await AsyncStorage.getItem(KEEP_KEY)) !== 'all'; } catch { return true; }
}

export async function setKeepRecentOnly(recent: boolean): Promise<void> {
  await AsyncStorage.setItem(KEEP_KEY, recent ? 'recent' : 'all');
}

/** "Keep maps of the last 90 days offline" (Settings → Database). Off by default. */
export async function getKeepRecentTiles(): Promise<boolean> {
  try { return (await AsyncStorage.getItem(KEEP_TILES_KEY)) === '1'; } catch { return false; }
}

export async function setKeepRecentTiles(on: boolean): Promise<void> {
  await AsyncStorage.setItem(KEEP_TILES_KEY, on ? '1' : '0');
}

export async function getLibraryCacheMb(): Promise<number> {
  try {
    const v = Number(await AsyncStorage.getItem(CACHE_MB_KEY));
    return v > 0 ? clampCacheMb(v) : DEFAULT_CACHE_MB;
  } catch { return DEFAULT_CACHE_MB; }
}

export async function setLibraryCacheMb(mb: number): Promise<void> {
  await AsyncStorage.setItem(CACHE_MB_KEY, String(clampCacheMb(mb)));
}

export function clampCacheMb(mb: number): number {
  return Math.max(MIN_CACHE_MB, Math.min(MAX_CACHE_MB, Math.round(mb)));
}

/** Start minute ("YYYY-MM-DDTHH:MM") before which a move counts as older: 90 days back, the same
 *  rule as the desktop's lightCutoffMinute(). Compared with the first 16 characters of `date`. */
export function lightCutoffMinute(nowMs: number = Date.now()): string {
  return new Date(nowMs - LIGHT_DAYS * 86400_000).toISOString().slice(0, 16);
}

/** "300 MB" / "1.5 GB", as the desktop's slider label writes it. */
export function formatCacheMb(mb: number): string {
  return mb >= 1000 ? `${(mb / 1000).toFixed(1)} GB` : `${mb} MB`;
}
