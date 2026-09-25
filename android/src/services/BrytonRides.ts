import { NativeModules } from 'react-native';
import { base64ToBytes } from './Base64';
import { writeFitFile, writeGpxFile } from './GpxService';
import { BikeImportDedup } from './BikeImportDedup';
import { decodeFit, rideToGpx } from './MageneFit';

// Import rides from a Bryton Aero 60 over USB into the Android library - the mobile twin of the
// desktop's mass-storage import (tools/mtp_import.py: rides are the .fit files at the volume
// root). Same storage as the Magene import: the library GPX ("Cycling" / "Indoor cycling" from
// the FIT sub_sport, summary tags for totals) plus the ORIGINAL FIT next to it, which is what
// the activity screen uploads to intervals.icu. Rides already in the library (any source) or deleted
// by the user are skipped (BikeImportDedup - the desktop's rule).

const Native = (NativeModules as any).BrytonUsb as {
  connect(): Promise<{ found: boolean; name: string }>;
  listDir(path: string): Promise<string[]>;
  readFile(path: string): Promise<string>;
};

export interface BrytonRideSync { phase: 'reading' | 'writing' | 'done' | 'error'; current: number; total: number; newCount: number; error?: string }

export const brytonActivityId = (file: string) => `bryton_${file.replace(/\.fit$/i, '')}`;

export async function syncBrytonRides(onState: (s: BrytonRideSync) => void): Promise<number> {
  const emit = (p: Partial<BrytonRideSync> & { phase: BrytonRideSync['phase'] }) =>
    onState({ current: 0, total: 0, newCount: 0, ...p });
  try {
    emit({ phase: 'reading' });
    await Native.connect();
    const files = (await Native.listDir('')).filter(f => /\.fit$/i.test(f)).sort();
    const dedup = await BikeImportDedup.load();
    // Known/deleted ids are skipped before reading the file; the start-time match needs the FIT.
    const todo: string[] = [];
    for (const f of files) if (!(await dedup.hasId(brytonActivityId(f)))) todo.push(f);
    let newCount = 0;
    for (let i = 0; i < todo.length; i++) {
      emit({ phase: 'writing', current: i, total: todo.length, newCount });
      try {
        const b64 = await Native.readFile(todo[i]);
        const ride = decodeFit(base64ToBytes(b64));
        const id = brytonActivityId(todo[i]);
        const start = ride.startTime ? Date.parse(ride.startTime) / 1000 : NaN;
        const junk = ride.durationSeconds < 60 && ride.distanceMeters < 100;   // desktop rule
        if (!junk && !(isFinite(start) && dedup.hasRide(start, ride.durationSeconds))
            && (await writeGpxFile(id, rideToGpx(ride, 'Sommet (Bryton Aero 60)')))) {
          await writeFitFile(id, b64);
          if (isFinite(start)) dedup.add(id, start, ride.durationSeconds);
          newCount++;
        }
        dedup.markSeen(id);
      } catch { /* one bad file shouldn't stop the sync */ }
    }
    await dedup.save();
    emit({ phase: 'done', current: todo.length, total: todo.length, newCount });
    return newCount;
  } catch (e: any) {
    emit({ phase: 'error', error: e?.message ?? String(e) });
    return 0;
  }
}
