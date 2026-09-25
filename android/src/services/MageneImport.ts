import { activityExists, writeFitFile, writeGpxFile } from './GpxService';
import { bytesToBase64 } from './Base64';
import { downloadRide, rideList, withMagene } from './MageneDevice';
import { decodeFit, rideToGpx } from './MageneFit';

// Import rides from a Magene C406 into the Android library - the mobile twin of the desktop's
// /api/magene/import (tools/magene_import.py + fit_decode.py). Each ride is stored as its GPX
// (the library's format; "Cycling" vs "Indoor cycling" from the FIT sub_sport) plus the
// ORIGINAL FIT next to it, which MapScreen prefers for charts and uploads to intervals.icu.
// Rides already in the library are skipped BEFORE the slow BLE download.

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
      const todo: number[] = [];
      for (const id of all) if (!(await activityExists(mageneActivityId(id)))) todo.push(id);
      let newCount = 0;
      emit({ phase: 'writing', total: todo.length });
      for (let i = 0; i < todo.length; i++) {
        try {
          const fit = await downloadRide(todo[i]);
          if (fit) {
            const ride = decodeFit(fit);
            const id = mageneActivityId(todo[i]);
            if (await writeGpxFile(id, rideToGpx(ride))) {
              await writeFitFile(id, bytesToBase64(fit));
              newCount++;
            }
          }
        } catch {
          // one bad ride shouldn't abort the sync
        }
        emit({ phase: 'writing', current: i + 1, total: todo.length, newCount });
      }
      emit({ phase: 'done', current: todo.length, total: todo.length, newCount });
      return newCount;
    });
  } catch (e: any) {
    emit({ phase: 'error', error: e?.message ?? String(e) });
    return 0;
  }
}
