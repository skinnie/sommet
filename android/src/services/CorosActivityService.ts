import { listActivities, downloadText, downloadFitBase64, type CorosActivity } from './ApiCoros';
import { writeGpxFile, writeFitFile } from './GpxService';

// Pulls Coros Training Hub activities into the local library, mirroring the Garmin
// sync (GarminActivityService): each activity becomes an <id>.gpx (the track that
// drives the library) plus an <id>.fit sidecar (the rich HR/power/cadence channels).
// The Dura has no on-device files to read, so the source is the cloud API in ApiCoros.
//
// Dedup is by writeGpxFile returning null when <id>.gpx already exists — so re-syncing
// only fetches what's new. id = `coros_<labelId>` (labelId is Coros' stable activity key).

export interface CorosActivitySyncState {
  phase: 'idle' | 'listing' | 'writing' | 'done' | 'error';
  current: number;
  total: number;
  newCount: number;
  error?: string;
}

function idFor(a: CorosActivity): string {
  return `coros_${a.labelId}`;
}

export async function syncCorosActivities(
  onState: (s: CorosActivitySyncState) => void
): Promise<number> {
  const emit = (p: Partial<CorosActivitySyncState> & { phase: CorosActivitySyncState['phase'] }) =>
    onState({ current: 0, total: 0, newCount: 0, ...p });

  emit({ phase: 'listing' });
  let activities: CorosActivity[];
  try {
    activities = await listActivities();
  } catch (e: any) {
    emit({ phase: 'error', error: e?.message ?? 'Failed to list Coros activities' });
    return 0;
  }

  emit({ phase: 'writing', total: activities.length });
  let newCount = 0;
  for (let i = 0; i < activities.length; i++) {
    const a = activities[i];
    const id = idFor(a);
    try {
      const gpx = await downloadText(a, 'gpx');
      const written = await writeGpxFile(id, gpx); // null if already imported — not new
      if (written) {
        newCount++;
        // Only fetch the (larger) FIT for activities we actually kept.
        try {
          const fitB64 = await downloadFitBase64(a);
          await writeFitFile(id, fitB64);
        } catch {
          // A missing/failed FIT just means no rich channels for this one — the GPX
          // library entry still stands; don't fail the whole activity over it.
        }
      }
    } catch {
      // one bad activity shouldn't abort the whole sync — skip it, keep going
    }
    emit({ phase: 'writing', current: i + 1, total: activities.length, newCount });
  }

  emit({ phase: 'done', current: activities.length, total: activities.length, newCount });
  return newCount;
}
