import { writeFitFile, writeGpxFile } from './GpxService';
import { BikeImportDedup } from './BikeImportDedup';
import { bytesToBase64 } from './Base64';
import { downloadRide, rideList, withMagene } from './MageneDevice';
import { decodeFit, rideToGpx } from './MageneFit';

// Import rides from a Magene C406 into the Android library - the mobile twin of the desktop's
// /api/magene/import (tools/magene_import.py + fit_decode.py). Each ride is stored as its GPX
// (the library's format; "Cycling" vs "Indoor cycling" from the FIT sub_sport) plus the
// ORIGINAL FIT next to it, which MapScreen prefers for charts and uploads to intervals.icu.
// Rides already in the library (any source), or deleted by the user, are skipped BEFORE the slow
// BLE download (BikeImportDedup - the desktop's rule).

export interface MageneSyncState {
  phase: 'idle' | 'connecting' | 'reading' | 'writing' | 'done' | 'error';
  current: number;
  total: number;
  newCount: number;
  error?: string;
}

export const mageneActivityId = (rideId: number) => `c406_${rideId}`;

export async function syncMageneRides(address: string, onState: (s: MageneSyncState) => void): Promise<number> {
  const emit = (p: Partial<MageneSyncState> & { phase: MageneSyncState['phase'] }) =>
    onState({ current: 0, total: 0, newCount: 0, ...p });
  emit({ phase: 'connecting' });
  try {
    return await withMagene(address, async () => {
      emit({ phase: 'reading' });
      const all = await rideList();
      const dedup = await BikeImportDedup.load();
      // Ride id = its UTC start, so already-known rides are skipped BEFORE the BLE download.
      const todo: number[] = [];
      for (const id of all) {
        if (await dedup.hasId(mageneActivityId(id))) continue;
        if (dedup.hasRide(id, null)) { dedup.markSeen(mageneActivityId(id)); continue; }
        todo.push(id);
      }
      let newCount = 0;
      emit({ phase: 'writing', total: todo.length });
      for (let i = 0; i < todo.length; i++) {
        try {
          const fit = await downloadRide(todo[i]);
          if (fit) {
            const ride = decodeFit(fit);
            const id = mageneActivityId(todo[i]);
            const start = ride.startTime ? Date.parse(ride.startTime) / 1000 : todo[i];
            const junk = ride.durationSeconds < 60 && ride.distanceMeters < 100;   // desktop rule
            if (!junk && !dedup.hasRide(start, ride.durationSeconds)
                && (await writeGpxFile(id, rideToGpx(ride)))) {
              await writeFitFile(id, bytesToBase64(fit));
              dedup.add(id, start, ride.durationSeconds);
              newCount++;
            }
            dedup.markSeen(id);
          }
        } catch {
          // one bad ride shouldn't abort the sync
        }
        emit({ phase: 'writing', current: i + 1, total: todo.length, newCount });
      }
      await dedup.save();
      emit({ phase: 'done', current: todo.length, total: todo.length, newCount });
      return newCount;
    });
  } catch (e: any) {
    emit({ phase: 'error', error: e?.message ?? String(e) });
    return 0;
  }
}
