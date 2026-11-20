import { ActivityRecord, getAllActivities } from '../database/db';
import { readGpxFile } from './GpxService';
import { getMapProvider } from './MapProviderService';
import { ensureTileCached, LatLonPt, TileXYZ } from './TileCache';
import { getKeepRecentTiles, lightCutoffMinute } from './LibraryPrefs';
import { isIndoorSport } from './SportNames';

// "Keep maps of the last 90 days offline" (sync/13 option 7b; the desktop's
// TileCacheService::downloadCorridors + ActivityService::collectRecentTracks). Every outdoor move
// of the last 90 days with a Track here gets the map around it saved: each tile z12..16 holding a
// point of the Track, plus its 8 neighbours, at most 20000 tiles, 4 at a time in the background.
// Same files as the map screens' own cache (TileCache.ts), so those maps show without a
// connection. Tiles already saved are skipped. Off by default.
//
// The tablet is slow: Tracks are read one at a time and the tile set grows one Track at a time,
// with a yield to the event loop after each, so a run never holds the JS thread for long.

export const MIN_Z = 12;
export const MAX_Z = 16;
export const MAX_TILES = 20000;      // ~400 MB at worst; a normal 90 days is far less
export const THIN_M = 150;           // one point every ~150 m is plenty for z16 tiles (~600 m)
export const CONCURRENCY = 4;        // a background job: gentle on the tablet and the tile hosts
export const FIRST_DELAY_MS = 90 * 1000;
export const EVERY_MS = 6 * 3600 * 1000;

// ── Pure parts (tests) ────────────────────────────────────────────────────────────────────

/** Outdoor moves started on or after `cutoff` ("YYYY-MM-DDTHH:MM") that have a Track here. */
export function recentOutdoorMoves<T extends Pick<ActivityRecord, 'date' | 'gpx_path' | 'activity_type'>>(
  rows: T[], cutoff: string,
): T[] {
  return rows.filter(r => !!r.gpx_path && (r.date ?? '').slice(0, 16) >= cutoff
    && !isIndoorSport(r.activity_type || ''));
}

/** The <trkpt> positions of a GPX, without a full XML parse (either attribute order). */
export function gpxPositions(gpx: string): LatLonPt[] {
  const out: LatLonPt[] = [];
  const re = /<trkpt\b([^>]*)>/g;
  let m: RegExpExecArray | null;
  while ((m = re.exec(gpx)) !== null) {
    const lat = /\blat\s*=\s*["']([^"']+)["']/.exec(m[1]);
    const lon = /\blon\s*=\s*["']([^"']+)["']/.exec(m[1]);
    if (!lat || !lon) continue;
    const p = { lat: parseFloat(lat[1]), lon: parseFloat(lon[1]) };
    if (Number.isFinite(p.lat) && Number.isFinite(p.lon)) out.push(p);
  }
  return out;
}

/** One point every ~`minM` metres (the first always kept); 0,0 points dropped. Same flat-earth
 *  step as the desktop's collectRecentTracks. */
export function thinTrack(points: LatLonPt[], minM = THIN_M): LatLonPt[] {
  const out: LatLonPt[] = [];
  let lastLat = 1000, lastLon = 1000;
  for (const { lat, lon } of points) {
    if (lat === 0 && lon === 0) continue;
    const dy = (lat - lastLat) * 111000;
    const dx = (lon - lastLon) * 111000 * Math.cos((lat * Math.PI) / 180);
    if (dx * dx + dy * dy < minM * minM) continue;
    lastLat = lat; lastLon = lon;
    out.push({ lat, lon });
  }
  return out;
}

function lonToTileX(lon: number, z: number): number {
  return Math.floor(((lon + 180) / 360) * 2 ** z);
}
function latToTileY(lat: number, z: number): number {
  const r = (lat * Math.PI) / 180;
  return Math.floor(((1 - Math.log(Math.tan(r) + 1 / Math.cos(r)) / Math.PI) / 2) * 2 ** z);
}

/** Grows the tile set one Track at a time (so the caller can yield between Tracks), each tile
 *  once however many moves pass through it, stopping at `cap`. */
export class TileSetBuilder {
  readonly tiles: TileXYZ[] = [];
  private seen = new Set<number>();
  constructor(private cap = MAX_TILES, private minZ = MIN_Z, private maxZ = MAX_Z) {}

  get full(): boolean { return this.tiles.length >= this.cap; }

  /** Adds the tiles around `points`; false once the cap is reached. */
  add(points: LatLonPt[]): boolean {
    for (const { lat, lon } of points) {
      if ((lat === 0 && lon === 0) || Math.abs(lat) > 85) continue;
      for (let z = this.minZ; z <= this.maxZ; z++) {
        const n = 2 ** z;
        const cx = lonToTileX(lon, z), cy = latToTileY(lat, z);
        for (let dx = -1; dx <= 1; dx++) {
          for (let dy = -1; dy <= 1; dy++) {
            const x = (((cx + dx) % n) + n) % n, y = cy + dy;
            if (y < 0 || y >= n) continue;
            const key = (z * 65536 + x) * 65536 + y;   // z ≤ 16, so x and y < 65536: exact
            if (this.seen.has(key)) continue;
            this.seen.add(key);
            this.tiles.push({ z, x, y });
            if (this.tiles.length >= this.cap) return false;
          }
        }
      }
    }
    return true;
  }
}

/** Every tile for these Tracks (tests; the runner uses TileSetBuilder to yield between them). */
export function corridorTiles(tracks: LatLonPt[][], cap = MAX_TILES): TileXYZ[] {
  const b = new TileSetBuilder(cap);
  for (const t of tracks) if (!b.add(t)) break;
  return b.tiles;
}

// ── The background run ────────────────────────────────────────────────────────────────────

export interface RecentTilesProgress { running: boolean; done: number; total: number; }

let progress: RecentTilesProgress = { running: false, done: 0, total: 0 };
const listeners = new Set<(p: RecentTilesProgress) => void>();
let lastEmit = 0;

function emit(force = false) {
  const now = Date.now();
  // The Settings line re-renders on each one: a few times a second is plenty.
  if (!force && now - lastEmit < 400) return;
  lastEmit = now;
  const p = { ...progress };
  listeners.forEach(cb => { try { cb(p); } catch { /* a screen's own problem */ } });
}

/** Settings' "Downloading map tiles: N of M" line. Returns the unsubscribe. */
export function onRecentTilesProgress(cb: (p: RecentTilesProgress) => void): () => void {
  listeners.add(cb);
  cb({ ...progress });
  return () => { listeners.delete(cb); };
}

export function recentTilesRunning(): boolean { return progress.running; }

let cancelled = false;
/** Stops a run going (the switch was turned off); tiles saved so far stay. */
export function stopRecentTiles(): void { if (progress.running) cancelled = true; }

const yieldToUi = () => new Promise<void>(r => setTimeout(r, 0));

/** Collects the last 90 days' Tracks and saves the map around them. No-op when the option is
 *  off or a run is going. Resolves with the number of tiles in the set. */
export async function downloadRecentTiles(): Promise<number> {
  if (progress.running || !(await getKeepRecentTiles())) return 0;
  progress = { running: true, done: 0, total: 0 };
  cancelled = false;
  emit(true);
  try {
    const provider = await getMapProvider();
    const moves = recentOutdoorMoves(await getAllActivities(), lightCutoffMinute());
    const set = new TileSetBuilder();
    for (const m of moves) {
      if (cancelled) break;
      try {
        const pts = thinTrack(gpxPositions(await readGpxFile(m.gpx_path)));
        if (!set.add(pts)) break;
      } catch { /* a missing or unreadable Track: skip it */ }
      await yieldToUi();
    }
    const tiles = set.tiles;
    progress.total = tiles.length;
    emit(true);

    let next = 0;
    const worker = async () => {
      while (next < tiles.length) {
        if (cancelled) { next = tiles.length; break; }   // switched off: stop
        const t = tiles[next++];
        try { await ensureTileCached(provider, t.z, t.x, t.y); } catch { /* offline: next run */ }
        progress.done++;
        emit();
      }
    };
    await Promise.all(Array.from({ length: Math.min(CONCURRENCY, tiles.length) }, worker));
    return tiles.length;
  } finally {
    progress = { ...progress, running: false };
    emit(true);
  }
}

/** First run 90 s after start (out of the start-up's way on the tablet), then every 6 hours.
 *  Returns a function that stops it. */
export function startRecentTiles(): () => void {
  const kick = () => { downloadRecentTiles().catch(() => {}); };
  let every: ReturnType<typeof setInterval> | null = null;
  const first = setTimeout(() => { kick(); every = setInterval(kick, EVERY_MS); }, FIRST_DELAY_MS);
  return () => { clearTimeout(first); if (every) clearInterval(every); };
}
