import RNFS from 'react-native-fs';
import {
  connect, disconnect, readRegion, readDeviceHistoryRaw, isBleTransportActive,
} from '../native/AmbitUsbModule';
import { decodeDeviceHistory } from './KailashHistoryReader';
import { decodeTrackLogToGpx, KAILASH_TRACKLOG_BASE, KAILASH_TRACKLOG_SIZE } from './KailashTrackLogReader';
import { backupsFolderPath } from './NavBackupService';

// Kailash "travel history & GPS track" archive - the Android port of the desktop's own Kailash
// backup (server.py, commit 58162fe; André, 2026-08-27: "bring android to parity"). A Kailash has
// no Routes/Waypoints database to back up (DeviceCapabilities.supportsWatchBackup is false for it);
// what IS irreplaceable is its persistent DeviceHistory (visited cities/countries, travel stats,
// activity logbook) and its flash TrackLog (the passive GPS track) - exactly what a firmware flash
// wipes. This saves both, losslessly: the history as JSON and the track as a .gpx, using the SAME
// native reads the Home travel panel and the Kailash activity path already use (readDeviceHistoryRaw,
// readRegion(KAILASH_TRACKLOG_BASE) -> decodeTrackLogToGpx).
//
// One-way ARCHIVE, not a restore point: there is no proven write path back for either region and
// this project does not invent one - same stance as the desktop.

export interface KailashArchiveResult {
  prefix: string;
  historySaved: boolean;
  trackPoints: number;      // GPS points written to the .gpx (0 if the track was empty)
  path: string;             // the backups folder
}

/** Read the Kailash's DeviceHistory + flash TrackLog and save them as {prefix}_kailash-history.json
 * and {prefix}_kailash-track.gpx in the backups folder. Read-only on the watch. */
export async function createKailashArchive(): Promise<KailashArchiveResult> {
  const dir = backupsFolderPath();
  if (!(await RNFS.exists(dir))) await RNFS.mkdir(dir);

  // Over BLE the link is already open (HomeScreen owns it); a USB connect() would tear it down and
  // pop the OTG prompt. Same transport guard as AmbitSettingsService / CustomModesService.
  const overBle = isBleTransportActive();
  if (!overBle) await connect();
  try {
    const prefix = String(Date.now());

    // DeviceHistory (visited places, travel stats, logbook) -> JSON.
    let historySaved = false;
    try {
      const history = decodeDeviceHistory(await readDeviceHistoryRaw());
      if (history) {
        await RNFS.writeFile(`${dir}/${prefix}_kailash-history.json`, JSON.stringify(history, null, 2), 'utf8');
        historySaved = true;
      }
    } catch { /* watch may have no history yet - not fatal, still try the track */ }

    // Flash TrackLog (~1.3 MB) -> GPX. decodeTrackLogToGpx returns null when there's no track.
    let trackPoints = 0;
    const trackB64 = await readRegion(KAILASH_TRACKLOG_BASE, KAILASH_TRACKLOG_SIZE);
    const gpx = decodeTrackLogToGpx(trackB64);
    if (gpx) {
      await RNFS.writeFile(`${dir}/${prefix}_kailash-track.gpx`, gpx, 'utf8');
      trackPoints = (gpx.match(/<trkpt/g) || []).length;
    }

    if (!historySaved && trackPoints === 0) {
      throw new Error('This watch had no travel history or GPS track to archive.');
    }
    return { prefix, historySaved, trackPoints, path: dir };
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

export interface KailashArchiveEntry {
  prefix: string;
  createdAt: number;
  hasHistory: boolean;
  hasTrack: boolean;
}

/** Every Kailash archive saved so far, newest first (grouped by the shared timestamp prefix). */
export async function listKailashArchives(): Promise<KailashArchiveEntry[]> {
  const dir = backupsFolderPath();
  if (!(await RNFS.exists(dir))) return [];
  const files = await RNFS.readDir(dir);
  const byPrefix = new Map<string, { hasHistory: boolean; hasTrack: boolean }>();
  for (const f of files) {
    const h = /^(\d+)_kailash-history\.json$/.exec(f.name);
    const t = /^(\d+)_kailash-track\.gpx$/.exec(f.name);
    const prefix = h?.[1] ?? t?.[1];
    if (!prefix) continue;
    const cur = byPrefix.get(prefix) ?? { hasHistory: false, hasTrack: false };
    if (h) cur.hasHistory = true;
    if (t) cur.hasTrack = true;
    byPrefix.set(prefix, cur);
  }
  return Array.from(byPrefix.entries())
    .map(([prefix, v]) => ({ prefix, createdAt: parseInt(prefix, 10), ...v }))
    .sort((a, b) => b.createdAt - a.createdAt);
}
