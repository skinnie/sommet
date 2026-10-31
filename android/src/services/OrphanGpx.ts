import { listGpxFiles, readGpxFile, activityFileStem } from './GpxService';
import { extractGpxMetadata } from './GpxParser';
import { getAllSyncedIds, isActivityDeleted, rejectJunkActivity, markActivitySynced } from '../database/db';

// Background upkeep when Activities opens: GPX files the DB doesn't know yet (orphans from an
// interrupted sync) become rows. Returns how many were added.
//
// A file is matched to its row by the name the row's file is saved under (activityFileStem), not
// by the bare id (tablet audit F21, 2026-10-31). Since 23/10 an intervals.icu move "icu:<n>"
// keeps its track as "icu_<n>.gpx", and comparing that file name with the ids took every such
// file for a lost move: 816 copies without a date on the tablet, about 40 more per import, and
// deleting one of them would also have deleted the real move's track.
export async function adoptOrphanGpx(): Promise<number> {
  const [gpxPaths, ids] = await Promise.all([listGpxFiles(), getAllSyncedIds()]);
  const known = new Set(ids.map(activityFileStem));   // getAllSyncedIds includes deleted ids
  let added = 0;
  for (const path of gpxPaths) {
    const id = path.split('/').pop()?.replace(/\.gpx$/, '') ?? '';
    if (!id || known.has(id) || await isActivityDeleted(id)) continue;
    try {
      const meta = extractGpxMetadata(await readGpxFile(path));
      // A track with no time can't be a lost move (a watch move always has its start time):
      // it would only become a row with no date.
      if (!meta.date) continue;
      if (await rejectJunkActivity(id, meta.durationS, meta.distanceM)) continue;
      await markActivitySynced({
        id, synced_at: Date.now(), gpx_path: path, date: meta.date, duration_s: meta.durationS,
        distance_m: meta.distanceM, d_plus: meta.dPlus, activity_type: meta.activityType,
      });
      added++;
    } catch (_) {}
  }
  return added;
}
