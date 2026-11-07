import * as Garmin from '../native/GarminModule';
import type { GarminConnectResult, GarminVolumeInfo } from '../native/GarminModule';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { writeFitFile, writeGpxFile } from './GpxService';
import { BikeImportDedup } from './BikeImportDedup';
import { decodeFit, rideToGpx } from './MageneFit';
import { saveWatchBackup } from './WatchBackup';
import {
  readEdge500Profile, buildEdge500Profile, SETTINGS_PATH, CYCLING_PATH, NEWFILES_DIR,
  type Edge500Profile, type Edge500Field,
} from './Edge500Profile';
import { parseGpx, toTcx, courseName, courseFileName } from './Edge500Course';
import { encodeEdge500Workout, workoutName, workoutFileName } from './Edge500Workout';

// Garmin Edge 500 (and the other 2009-2013 mass-storage Edges) over USB-OTG - the mobile twin of
// the desktop's Edge 500 support (tools/mtp_import.py, edge500_profile.py, edge500_course.py,
// edge500_workout.py; all four checked on a real Edge 500, fw 3.30, 2026-11-05). The Edge is a
// plain USB drive with the Garmin vendor id, so Home's Garmin connect (GarminModule.kt, libaums)
// opens it like the eTrex; GarminDevice.xml tells the two apart (edgeVolume()). Every write goes
// into Garmin/NewFiles, never over the Edge's own files; the Edge takes the files in when it is
// unplugged (it leaves USB mode).

let connected: GarminConnectResult | null = null;

/** Home calls this when a Garmin connects (null when it goes away). */
export function setConnectedGarmin(info: GarminConnectResult | null) { connected = info; }

/** The Edge volume right now, or null (no Garmin, or it's an eTrex). */
export function connectedEdge(): GarminVolumeInfo | null { return Garmin.edgeVolume(connected); }

/** "Garmin Edge 500", like the desktop's device name (tools/mtp_import.py). */
export function edgeDisplayName(v: GarminVolumeInfo | null): string {
  return `Garmin ${(v?.model ?? 'Edge').trim()}`;
}

function need(): GarminVolumeInfo {
  const v = connectedEdge();
  if (!v) throw new Error('No Garmin Edge 500 connected');
  return v;
}

const readBytes = async (v: GarminVolumeInfo, path: string) => base64ToBytes(await Garmin.readFile(v.volumeIndex, path));

/** Writes one file into Garmin/NewFiles; the native side reads it back to check. */
async function drop(v: GarminVolumeInfo, fileName: string, data: Uint8Array): Promise<string> {
  const path = `${NEWFILES_DIR}/${fileName}`;
  await Garmin.writeFile(v.volumeIndex, path, bytesToBase64(data));
  return path;
}

// ── profile ──────────────────────────────────────────────────────────────────

export async function readEdgeProfile(): Promise<Edge500Profile> {
  const v = need();
  return readEdge500Profile(await readBytes(v, SETTINGS_PATH), await readBytes(v, CYCLING_PATH));
}

/** Patched copies of Settings.fit / Cycling.fit into Garmin/NewFiles (tools/edge500_profile.py
 *  write()). The device's current files are kept in the app's backups first. */
export async function writeEdgeProfile(changes: Partial<Record<Edge500Field, number>>) {
  const v = need();
  const settings = await readBytes(v, SETTINGS_PATH), cycling = await readBytes(v, CYCLING_PATH);
  const out = buildEdge500Profile(settings, cycling, changes);
  if (!out.settings && !out.cycling) return { changed: out.changed, written: [] as string[] };
  await saveWatchBackup('edge500-settings', bytesToBase64(settings), 'edge500');
  await saveWatchBackup('edge500-cycling', bytesToBase64(cycling), 'edge500');
  const written: string[] = [];
  if (out.settings) written.push(await drop(v, 'SETTINGS.FIT', out.settings));
  if (out.cycling) written.push(await drop(v, 'CYCLING.FIT', out.cycling));
  return { changed: out.changed, written };
}

/** The Edge profile in the shape MageneProfileSync.diffProfile compares with intervals.icu
 *  (desktop _EDGE500_COMPARE_FIELDS: ftp, lthr, max_hr, weight, height, gender, age). */
export function edgeProfileForDiff(p: Edge500Profile) {
  const n = (x: number | null) => (x == null ? undefined : x);
  return { ftp: n(p.ftp), lthr: n(p.lthr), maxHr: n(p.max_hr), weight: n(p.weight), height: n(p.height), sex: n(p.gender), age: n(p.age) };
}

/** A diff field (MageneProfileSync) -> the Edge field it writes. */
export const EDGE_FIELD: Record<string, Edge500Field> = {
  ftp: 'ftp', lthr: 'lthr', maxHr: 'max_hr', weight: 'weight', height: 'height', sex: 'gender', age: 'age',
};

// ── routes and workouts ────────────────────────────────────────────────────

/** A GPX route as a TCX course (Training > Courses after unplugging). */
export async function sendEdgeCourse(gpx: string, name: string): Promise<{ name: string; path: string }> {
  const v = need();
  const tcx = toTcx(parseGpx(gpx), name);
  const data = new Uint8Array(Array.from(unescape(encodeURIComponent(tcx)), c => c.charCodeAt(0)));
  return { name: courseName(name), path: await drop(v, courseFileName(name), data) };
}

/** A workout in the project schema as a FIT workout (Training > Workouts after unplugging). */
export async function sendEdgeWorkout(workout: any, name?: string): Promise<{ name: string; path: string }> {
  const v = need();
  const fit = encodeEdge500Workout(workout, name);
  return { name: workoutName(workout, name), path: await drop(v, workoutFileName(workout, name), fit) };
}

// ── rides ──────────────────────────────────────────────────────────────────

export interface EdgeRideSync { phase: 'reading' | 'writing' | 'done' | 'error'; current: number; total: number; newCount: number; error?: string }

// Same names on the Edge as on the desktop (Garmin/Activities/YYYY-MM-DD-HH-MM-SS.fit, source "edge").
export const edgeActivityId = (file: string) => `edge_${file.replace(/\.fit$/i, '')}`;

/** Import new rides into the library, like the Bryton (BrytonRides.ts): the library GPX plus the
 *  original FIT next to it; rides already there (any source) or deleted are skipped. */
export async function syncEdgeRides(onState: (s: EdgeRideSync) => void): Promise<number> {
  const emit = (p: Partial<EdgeRideSync> & { phase: EdgeRideSync['phase'] }) =>
    onState({ current: 0, total: 0, newCount: 0, ...p });
  try {
    const v = need();
    emit({ phase: 'reading' });
    const dir = v.fitActivityPath || 'Garmin/Activities';
    const files = (await Garmin.listDir(v.volumeIndex, dir)).filter(f => /\.fit$/i.test(f)).sort();
    const dedup = await BikeImportDedup.load();
    const todo: string[] = [];
    for (const f of files) if (!(await dedup.hasId(edgeActivityId(f)))) todo.push(f);
    let newCount = 0;
    const creator = `Sommet (${edgeDisplayName(v)})`;
    for (let i = 0; i < todo.length; i++) {
      emit({ phase: 'writing', current: i, total: todo.length, newCount });
      try {
        const b64 = await Garmin.readFile(v.volumeIndex, `${dir}/${todo[i]}`);
        const ride = decodeFit(base64ToBytes(b64));
        const id = edgeActivityId(todo[i]);
        const start = ride.startTime ? Date.parse(ride.startTime) / 1000 : NaN;
        const junk = ride.durationSeconds < 60 && ride.distanceMeters < 100;   // desktop rule
        if (!junk && !(isFinite(start) && dedup.hasRide(start, ride.durationSeconds, ride.distanceMeters))
            && (await writeGpxFile(id, rideToGpx(ride, creator)))) {
          await writeFitFile(id, b64);
          if (isFinite(start)) dedup.add(id, start, ride.durationSeconds, ride.distanceMeters);
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
