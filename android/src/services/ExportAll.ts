// "Export all" (a user's request, 2026-11-12; desktop parity with ActivitiesPage's Export all):
// copies every activity in the list (all of them, or one sport when a filter chip is on) into a
// new folder in Downloads, one GPX each, plus its FIT where the phone has one. Files are named
// "YYYY-MM-DD HHmm <sport>.gpx"; nothing already in Downloads is overwritten.
import RNFS from 'react-native-fs';
import { getAllActivities, ActivityRecord } from '../database/db';
import { fitPathForGpx } from './GpxService';
import { saveToDownloadsIn } from '../native/AmbitUsbModule';

export interface ExportAllResult {
  folder: string;     // "Sommet export 2026-11-12 1704", inside Downloads
  gpxFiles: number;
  fitFiles: number;
  skipped: number;    // activities with no file on the phone
}

const pad = (n: number) => String(n).padStart(2, '0');

export function stampOf(d: Date): string {
  return `${d.getFullYear()}-${pad(d.getMonth() + 1)}-${pad(d.getDate())} ${pad(d.getHours())}${pad(d.getMinutes())}`;
}

export function exportFileName(a: Pick<ActivityRecord, 'date' | 'activity_type'>, ext: string): string {
  const d = new Date(a.date);
  const stamp = isNaN(d.getTime()) ? (a.date || 'activity').slice(0, 16) : stampOf(d);
  const sport = (a.activity_type || '').trim();
  return `${sport ? `${stamp} ${sport}` : stamp}.${ext}`.replace(/[\\/:*?"<>|]/g, '_');
}

export async function exportAllToDownloads(
  sport: string,
  withFit: boolean,
  onProgress: (done: number, total: number) => void,
  shouldStop: () => boolean = () => false,
): Promise<ExportAllResult> {
  const all = await getAllActivities();
  const list = sport ? all.filter(a => a.activity_type === sport) : all;
  const folder = `Sommet export ${stampOf(new Date())}`;
  const res: ExportAllResult = { folder, gpxFiles: 0, fitFiles: 0, skipped: 0 };
  for (let i = 0; i < list.length; i++) {
    if (shouldStop()) break;
    const a = list[i];
    let wrote = false;
    if (a.gpx_path && await RNFS.exists(a.gpx_path)) {
      await saveToDownloadsIn(a.gpx_path, folder, exportFileName(a, 'gpx'), 'application/gpx+xml');
      res.gpxFiles++;
      wrote = true;
      if (withFit) {
        const fit = await fitPathForGpx(a.gpx_path);
        if (fit) {
          await saveToDownloadsIn(fit, folder, exportFileName(a, 'fit'), 'application/vnd.ant.fit');
          res.fitFiles++;
        }
      }
    }
    if (!wrote) res.skipped++;
    if (i % 10 === 9 || i === list.length - 1) onProgress(i + 1, list.length);
  }
  return res;
}
