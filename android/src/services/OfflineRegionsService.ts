import AsyncStorage from '@react-native-async-storage/async-storage';
import { MapProvider } from './MapProviderService';
import { regionTiles, deleteTiles, LatLonPt, TileXYZ } from './TileCache';

// The user's saved offline map areas — the OruxMaps-style "downloaded regions" list. A region
// is just a rectangle + a provider + a set of zoom levels; the actual tiles live in TileCache's
// on-disk cache, this only records what was downloaded so the manager screen can list, size and
// delete them. Deleting a region removes only the tiles no *other* saved region still covers, so
// two overlapping downloads don't delete each other's shared tiles.

const KEY = 'sommet:offlineRegions';

export interface RegionBBox { minLat: number; minLon: number; maxLat: number; maxLon: number; }

export interface OfflineRegion {
  id: string;
  name: string;
  provider: MapProvider;
  bbox: RegionBBox;
  zooms: number[];
  tileCount: number;
  bytes: number;   // measured after download (sum of the tiles actually written)
  savedAt: number; // epoch ms
}

/** The two opposite corners of a bbox, as the point list TileCache's region math expects. */
export function bboxCorners(b: RegionBBox): LatLonPt[] {
  return [{ lat: b.minLat, lon: b.minLon }, { lat: b.maxLat, lon: b.maxLon }];
}

export async function listRegions(): Promise<OfflineRegion[]> {
  try {
    const raw = await AsyncStorage.getItem(KEY);
    if (!raw) return [];
    const arr = JSON.parse(raw);
    return Array.isArray(arr) ? arr : [];
  } catch {
    return [];
  }
}

async function writeAll(regions: OfflineRegion[]): Promise<void> {
  await AsyncStorage.setItem(KEY, JSON.stringify(regions));
}

/** Records a freshly-downloaded area. Caller has already fetched the tiles via TileCache. */
export async function addRegion(meta: Omit<OfflineRegion, 'id' | 'savedAt'>): Promise<OfflineRegion> {
  const region: OfflineRegion = {
    ...meta,
    id: `${Date.now()}_${Math.random().toString(36).slice(2, 8)}`,
    savedAt: Date.now(),
  };
  const all = await listRegions();
  all.unshift(region);
  await writeAll(all);
  return region;
}

/** Removes a saved area and the tiles unique to it (tiles shared with another saved area of the
 * same provider are kept). Returns how many tile files were deleted from disk. */
export async function deleteRegion(id: string): Promise<number> {
  const all = await listRegions();
  const target = all.find(r => r.id === id);
  if (!target) return 0;
  const rest = all.filter(r => r.id !== id);

  // Tiles still needed by another saved area of the SAME provider — keyed "z/x/y".
  const keep = new Set<string>();
  for (const r of rest) {
    if (r.provider !== target.provider) continue;
    for (const t of regionTiles(bboxCorners(r.bbox), r.zooms, 0)) keep.add(`${t.z}/${t.x}/${t.y}`);
  }
  const mine: TileXYZ[] = regionTiles(bboxCorners(target.bbox), target.zooms, 0);
  const removed = await deleteTiles(target.provider, mine, keep);

  await writeAll(rest);
  return removed;
}
