import RNFS from 'react-native-fs';
import { MapProvider } from './MapProviderService';
import { tileUrl } from './MapTile';

// Real, 2026-08-10 ("let's go for the offline maps solution") - a plain on-disk tile cache
// under RNFS.CachesDirectoryPath (same directory convention FirmwareBackupService.ts/
// PoiService.ts already use for re-derivable, non-user data - tiles are exactly that,
// always re-fetchable from the provider). Every tile requested through ensureTileCached()
// gets written here once and reused after, online or offline; downloadRegion() is the
// explicit "grab this route's area before I lose signal" action (MapScreen.tsx's own
// download-for-offline button), not a background prefetch-everything scheme.

const TILE_CACHE_DIR = `${RNFS.CachesDirectoryPath}/maptiles`;

// MapScreen.tsx's own tile-layer URL templates (MapHtml.ts's mapTileLayersJs) need this
// same root as a file:// URI, to build '<dir>/<provider>/{z}/{x}/{y}.png' templates that
// exactly match localTilePath()'s own layout below - exported so that stays one real path
// convention, not two copies.
export const TILE_CACHE_DIR_URI = `file://${TILE_CACHE_DIR}`;

function localTilePath(provider: MapProvider, z: number, x: number, y: number): string {
  return `${TILE_CACHE_DIR}/${provider}/${z}/${x}/${y}.png`;
}

/** file:// URI a WebView (or RN <Image>) can load directly, for a tile that's already cached. */
export function localTileUri(provider: MapProvider, z: number, x: number, y: number): string {
  return `file://${localTilePath(provider, z, x, y)}`;
}

export async function isTileCached(provider: MapProvider, z: number, x: number, y: number): Promise<boolean> {
  return RNFS.exists(localTilePath(provider, z, x, y));
}

/**
 * Downloads one tile to disk if it isn't already cached. Safe to call repeatedly - a cache
 * hit is just an RNFS.exists() check. Returns the local file:// URI on success; throws if
 * the download itself fails (no network, provider down, etc.) so callers (downloadRegion's
 * per-tile loop) can tell a real failure from "already had it".
 */
export async function ensureTileCached(provider: MapProvider, z: number, x: number, y: number): Promise<string> {
  const path = localTilePath(provider, z, x, y);
  if (await RNFS.exists(path)) return `file://${path}`;

  const dir = path.substring(0, path.lastIndexOf('/'));
  await RNFS.mkdir(dir);

  const result = await RNFS.downloadFile({
    fromUrl: tileUrl(provider, z, x, y),
    toFile: path,
    headers: { 'User-Agent': 'Sommet/2.0' },
  }).promise;

  if (result.statusCode !== 200) {
    await RNFS.unlink(path).catch(() => {});
    throw new Error(`tile ${provider} ${z}/${x}/${y}: HTTP ${result.statusCode}`);
  }
  return `file://${path}`;
}

// Real Web Mercator tile-index math (MapTile.ts's own worldPixel(), just stopping at the
// tile index instead of continuous world-pixel coordinates - the same formula every slippy
// map uses).
function latLonToTile(lat: number, lon: number, z: number): { x: number; y: number } {
  const latRad = (lat * Math.PI) / 180;
  const n = 2 ** z;
  return {
    x: Math.floor(((lon + 180) / 360) * n),
    y: Math.floor(((1 - Math.log(Math.tan(latRad) + 1 / Math.cos(latRad)) / Math.PI) / 2) * n),
  };
}

export interface DownloadRegionProgress {
  done: number;
  total: number;
  failed: number;
}

export interface TileXYZ { z: number; x: number; y: number; }
export type LatLonPt = { lat: number; lon: number };

/**
 * Every {z,x,y} tile covering `points`' bounding box (plus `marginDeg` on each side) across
 * `zooms`. Two corner points = an explicit rectangle (use marginDeg 0 for a hand-drawn box);
 * a full track = its area (default ~2km margin so panning slightly off-track still hits cache).
 * Shared by downloadRegion (fetch), countRegionTiles (size estimate) and deletion.
 */
export function regionTiles(points: LatLonPt[], zooms: number[], marginDeg = 0.02): TileXYZ[] {
  if (points.length === 0) return [];
  const minLat = Math.min(...points.map(p => p.lat)) - marginDeg;
  const maxLat = Math.max(...points.map(p => p.lat)) + marginDeg;
  const minLon = Math.min(...points.map(p => p.lon)) - marginDeg;
  const maxLon = Math.max(...points.map(p => p.lon)) + marginDeg;
  const tiles: TileXYZ[] = [];
  for (const z of zooms) {
    // Latitude and longitude invert oppositely in tile-Y (north = smaller Y).
    const tl = latLonToTile(maxLat, minLon, z);
    const br = latLonToTile(minLat, maxLon, z);
    for (let x = tl.x; x <= br.x; x++) {
      for (let y = tl.y; y <= br.y; y++) tiles.push({ z, x, y });
    }
  }
  return tiles;
}

/** Tile count for a region — for the "≈ N tiles / ~X MB" estimate shown before downloading. */
export function countRegionTiles(points: LatLonPt[], zooms: number[], marginDeg = 0.02): number {
  return regionTiles(points, zooms, marginDeg).length;
}

/** Deletes the given tiles for a provider from disk (skips any in `keep`, keyed "z/x/y").
 * Returns how many files were actually removed. Used when deleting a saved offline area, so
 * tiles still covered by another saved area survive. */
export async function deleteTiles(provider: MapProvider, tiles: TileXYZ[], keep?: Set<string>): Promise<number> {
  let removed = 0;
  for (const t of tiles) {
    if (keep && keep.has(`${t.z}/${t.x}/${t.y}`)) continue;
    const path = localTilePath(provider, t.z, t.x, t.y);
    if (await RNFS.exists(path)) { await RNFS.unlink(path).catch(() => {}); removed++; }
  }
  return removed;
}

/**
 * Downloads every tile covering `points`' bounding box (+ `marginDeg`) across `zooms`, for
 * real offline use of an area - not a "cache the whole world" scheme. One failed tile doesn't
 * abort the rest (a flaky single request shouldn't lose an otherwise-complete download);
 * `failed` in the final progress report tells the caller if the result is incomplete.
 */
export async function downloadRegion(
  provider: MapProvider,
  points: LatLonPt[],
  zooms: number[],
  onProgress?: (p: DownloadRegionProgress) => void,
  marginDeg = 0.02,
): Promise<DownloadRegionProgress> {
  if (points.length === 0) return { done: 0, total: 0, failed: 0 };

  const tiles = regionTiles(points, zooms, marginDeg);
  const total = tiles.length;
  let done = 0, failed = 0;
  onProgress?.({ done, total, failed });

  // A handful in flight at once - fast enough without hammering the tile host, same
  // spirit as this project's other real bulk-fetch loops (SgeeService.ts's single-file
  // download has nothing comparable to borrow from; this is a fresh, modest concurrency).
  const CONCURRENCY = 6;
  let next = 0;
  async function worker() {
    while (next < tiles.length) {
      const i = next++;
      const tile = tiles[i];
      try {
        await ensureTileCached(provider, tile.z, tile.x, tile.y);
      } catch {
        failed++;
      }
      done++;
      onProgress?.({ done, total, failed });
    }
  }
  await Promise.all(Array.from({ length: Math.min(CONCURRENCY, tiles.length) }, worker));

  return { done, total, failed };
}

export async function getTileCacheSizeBytes(): Promise<number> {
  async function walk(dir: string): Promise<number> {
    if (!(await RNFS.exists(dir))) return 0;
    const entries = await RNFS.readDir(dir);
    let sum = 0;
    for (const e of entries) {
      sum += e.isDirectory() ? await walk(e.path) : e.size;
    }
    return sum;
  }
  return walk(TILE_CACHE_DIR);
}

export async function clearTileCache(): Promise<void> {
  if (await RNFS.exists(TILE_CACHE_DIR)) {
    await RNFS.unlink(TILE_CACHE_DIR);
  }
}
