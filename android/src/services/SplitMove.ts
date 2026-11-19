// sync/06 "Split here" (André, 2026-11-18): make two moves of a file that holds two rides - a
// device that lost power mid-ride and carried on in the same file the next time it started.
// Desktop twin: ActivityService::splitMove (tools/fit_split.py through the backend). The FIT is
// cut by shared/fit_split.js (byte for byte the same as the Python cutter), each half is read
// like any bike-computer ride (MageneFit.decodeFit) and gets its GPX.
//   - the first ride keeps the move's id and start, so Sommet Sync sees the same move, updated;
//   - the second is a new move "<id>-2" from the same device;
//   - the file as recorded is kept next to it as "<name>.orig.fit".
// Nothing is changed unless both halves were written.
import RNFS from 'react-native-fs';
import { ActivityRecord, markActivitySynced, sommetUid } from '../database/db';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { fitPathForGpx, writeFitFile, writeGpxFile } from './GpxService';
import { decodeFit, rideToGpx } from './MageneFit';
import { bumpTrackRev, forgetPushedBlob, forgetPushedFit } from './SommetSync';
const { splitFit } = require('../config/fitSplit');

export interface SplitResult { ok: boolean; message?: string; secondId?: string }

export async function splitMove(activity: ActivityRecord, atS: number): Promise<SplitResult> {
  const fitPath = activity.gpx_path ? await fitPathForGpx(activity.gpx_path) : null;
  if (!fitPath) return { ok: false, message: 'This move has no file on this device to split.' };
  const original = await RNFS.readFile(fitPath, 'base64');
  let halves: Uint8Array[];
  try {
    halves = splitFit(base64ToBytes(original), atS);
  } catch (e: any) {
    return { ok: false, message: e?.message ?? String(e) };
  }
  const rides = halves.map(h => decodeFit(h));
  const now = Date.now();
  const secondId = `${activity.id}-2`;
  const creator = 'Sommet (split)';

  const keep = fitPath.replace(/\.fit$/, '.orig.fit');
  if (!(await RNFS.exists(keep))) await RNFS.copyFile(fitPath, keep);

  const gpx2 = await writeGpxFile(secondId, rideToGpx(rides[1], creator), true);
  const fit2 = await writeFitFile(secondId, bytesToBase64(halves[1]), true);
  if (!gpx2 || !fit2) return { ok: false, message: "The second move couldn't be saved. Nothing was changed." };
  const gpx1 = await writeGpxFile(activity.id, rideToGpx(rides[0], creator), true);
  await writeFitFile(activity.id, bytesToBase64(halves[0]), true);
  if (!gpx1) return { ok: false, message: "The first move couldn't be saved. The file as recorded is kept." };

  await markActivitySynced({
    ...activity, synced_at: now, updated_at: now, gpx_path: gpx1,
    duration_s: rides[0].durationSeconds, moving_s: rides[0].movingSeconds,
    distance_m: Math.round(rides[0].distanceMeters), d_plus: Math.round(rides[0].ascentMeters ?? 0),
    energy_kcal: rides[0].energyKcal,
  });
  await markActivitySynced({
    ...activity, id: secondId, synced_at: now, updated_at: now, gpx_path: gpx2,
    date: rides[1].startTime || activity.date, start_utc: 0,
    duration_s: rides[1].durationSeconds, moving_s: rides[1].movingSeconds,
    distance_m: Math.round(rides[1].distanceMeters), d_plus: Math.round(rides[1].ascentMeters ?? 0),
    energy_kcal: rides[1].energyKcal,
  });
  const uid1 = sommetUid(activity.device ?? '', activity.date);
  await forgetPushedBlob(uid1).catch(() => {});
  await forgetPushedFit(uid1).catch(() => {});
  // sync/08: other devices hold the whole file's track for the first ride - make them refetch it
  await bumpTrackRev([uid1, sommetUid(activity.device ?? '', rides[1].startTime || activity.date)]).catch(() => {});
  return { ok: true, secondId };
}
