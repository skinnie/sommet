// Testing mode's sample data: what the app shows as "on the watch" when it pretends a watch is
// connected (Settings > Testing mode). The same sample moves and POIs as the desktop app
// (android/src/config/demoData.json is generated from desktop/backend/demo_data by
// tools/gen_activity_view.py). Nothing here touches a watch, the activity database or any sync:
// the moves are files in the cache folder, the POI list lives in memory for the session.

import RNFS from 'react-native-fs';
import demo from '../config/demoData.json';
import { ActivityRecord } from '../database/db';
import { extractGpxMetadata, GpxMetadata } from './GpxParser';
import { WatchPoi } from './PoiService';
import { AMBIT_TO_TRAVERSE_POI_TYPE, isTraverseWatch } from './PoiTypes';

export const DEMO_ID_PREFIX = 'demo:';
export function isDemoActivity(id: string): boolean { return id.startsWith(DEMO_ID_PREFIX); }

/** The sample moves' GPX for a sample watch: three for every watch, and a Hunting move with
 *  counted shots when it is a Traverse Alpha ("Loon"). */
export function demoMoveGpxs(variant: string): string[] {
  const moves = (demo.activities as string[]).slice();
  if (variant === 'Loon') moves.push(demo.huntingTraverseAlpha as string);
  return moves;
}

export interface DemoMove { activity: ActivityRecord; gpx: GpxMetadata }

/** The sample moves as list rows, their GPX written to the cache folder so the activity screen
 *  opens them like any other move. `sport` filters by activity type ('' = all). */
export async function demoMoves(variant: string, sport: string = ''): Promise<DemoMove[]> {
  const dir = `${RNFS.CachesDirectoryPath}/demo`;
  await RNFS.mkdir(dir).catch(() => {});
  const out: DemoMove[] = [];
  const gpxs = demoMoveGpxs(variant);
  for (let i = 0; i < gpxs.length; i++) {
    const gpx = extractGpxMetadata(gpxs[i]);
    if (sport && gpx.activityType.toLowerCase() !== sport.toLowerCase()) continue;
    // The watch's own ascent (the <ascent> summary), as the desktop shows it; the sum over the
    // track points is only the fallback.
    const ascent = gpxs[i].match(/<ascent>(-?\d+(?:\.\d+)?)<\/ascent>/);
    const path = `${dir}/sample-${i}.gpx`;
    await RNFS.writeFile(path, gpxs[i], 'utf8');
    out.push({
      gpx,
      activity: {
        id: `${DEMO_ID_PREFIX}${i}`, synced_at: 0, gpx_path: path, date: gpx.date,
        duration_s: gpx.durationS, distance_m: Math.round(gpx.distanceM),
        d_plus: Math.round(ascent ? parseFloat(ascent[1]) : gpx.dPlus),
        activity_type: gpx.activityType, device: 'demo', energy_kcal: gpx.energyKcal,
      } as ActivityRecord,
    });
  }
  return out.sort((a, b) => (a.activity.date < b.activity.date ? 1 : -1));
}

// ---- POIs: the sample watch's list, kept in memory until the app is closed or the sample
// watch changes. Adding and removing work on this list only.
let poiVariant = '';
let poiList: WatchPoi[] = [];

function freshPois(variant: string): WatchPoi[] {
  const traverse = isTraverseWatch(variant);
  return (demo.pois as { name: string; type: number; latitude: number; longitude: number }[]).map(p => ({
    name: p.name, latitude: p.latitude, longitude: p.longitude,
    // the sample POIs carry Ambit3 numbers: a sample Traverse shows the type with the same icon
    type: traverse ? (AMBIT_TO_TRAVERSE_POI_TYPE[p.type] ?? 26) : p.type,
  }));
}

export function demoPois(variant: string): WatchPoi[] {
  if (variant !== poiVariant) { poiVariant = variant; poiList = freshPois(variant); }
  return poiList.slice();
}

export function demoAddPoi(variant: string, poi: WatchPoi): WatchPoi[] {
  demoPois(variant);
  poiList = [poi, ...poiList];
  return poiList.slice();
}

export function demoRemovePoi(variant: string, poi: WatchPoi): WatchPoi[] {
  demoPois(variant);
  const i = poiList.findIndex(p => p.name === poi.name && p.latitude === poi.latitude && p.longitude === poi.longitude);
  if (i >= 0) poiList = poiList.filter((_, k) => k !== i);
  return poiList.slice();
}
