import * as Garmin from '../native/GarminModule';
import type { GarminConnectResult } from '../native/GarminModule';
import { writeGpxFile } from './GpxService';
import { extractGpxMetadata } from './GpxParser';

// v2.3.2 beta — extracted from the old combined GarminScreen so Home's "Sync
// Activities" button can run this directly, like the Ambit "Activities"
// button does, with no intermediate screen (per André's feedback: "just like
// the suunto counterpart, it should read the activities and log them. no sub
// menu needed"). The device is already connected by the time this runs —
// HomeScreen's connecting-flow calls Garmin.connect() once and keeps the
// volumes open (see GarminModule.kt), so no connect()/disconnect() here.

export interface GarminActivitySyncState {
  phase: 'idle' | 'reading' | 'writing' | 'done' | 'error';
  current: number;
  total: number;
  newCount: number;
  error?: string;
}

/** eTrex tracks carry their own titles ("Morning walk", "Park loop") that match none of the
 * 84 Suunto activity types, so GpxParser -> activityForName() would land every one on the
 * generic "Unspecified sport" badge. Name every eTrex activity "Hiking" - Suunto's own sport
 * name (id 96), and what intervals.icu's "Hike" maps to - so it lands in the Hiking
 * bucket/colour and Totals groups it (GpxParser reads gpx.trk.name for the type). It was
 * "Walking" until 2026-10-04 (André: "etrex should be classified as hike/trekking whatever is
 * the suunto equivalent"). */
function forceWalkTrackName(gpx: string): string {
  if (/<trk\b[^>]*>\s*<name>/.test(gpx)) {
    return gpx.replace(/(<trk\b[^>]*>\s*<name>)[\s\S]*?(<\/name>)/, '$1Hiking$2');
  }
  // No track name to replace - inject one right after the opening <trk>.
  return gpx.replace(/(<trk\b[^>]*>)/, '$1<name>Hiking</name>');
}

/** Non-cryptographic, just for de-duplicating imported activities that have
 * no parseable <time> in their GPX metadata — same content in, same ID out. */
function simpleChecksum(s: string): string {
  let h = 0;
  for (let i = 0; i < s.length; i++) {
    h = (Math.imul(31, h) + s.charCodeAt(i)) | 0;
  }
  return Math.abs(h).toString(36);
}

export async function syncGarminActivities(
  info: GarminConnectResult,
  onState: (s: GarminActivitySyncState) => void
): Promise<number> {
  const emit = (p: Partial<GarminActivitySyncState> & { phase: GarminActivitySyncState['phase'] }) =>
    onState({ current: 0, total: 0, newCount: 0, ...p });

  // The volume carrying GarminDevice.xml is internal memory — that's where
  // recordings live (see GARMIN_USB_IMPORT_SPEC.md's discovery strategy).
  const internalVolume = info.volumes.find(v => v.hasGarminDeviceXml && v.activityPath);
  if (!internalVolume) {
    emit({ phase: 'error', error: 'No activity folder found on this device' });
    return 0;
  }

  emit({ phase: 'reading' });
  let files: string[];
  try {
    files = await Garmin.listActivityFiles(internalVolume.volumeIndex);
  } catch (e: any) {
    emit({ phase: 'error', error: e?.message ?? 'Failed to list activities' });
    return 0;
  }
  const gpxFiles = files.filter(f => f.toLowerCase().endsWith('.gpx'));
  const modelSlug = (internalVolume.model ?? 'garmin').toLowerCase().replace(/[^a-z0-9]+/g, '');

  emit({ phase: 'writing', total: gpxFiles.length });
  let newCount = 0;
  for (let i = 0; i < gpxFiles.length; i++) {
    try {
      const content = await Garmin.readActivityFile(internalVolume.volumeIndex, gpxFiles[i]);
      const meta = extractGpxMetadata(content);
      // id from the ORIGINAL content so the walk-name rewrite below never changes an
      // activity's identity (no spurious re-import of anything already synced).
      const idBase = meta.date ? meta.date.replace(/[^0-9]/g, '') : simpleChecksum(content);
      const id = `garmin_${modelSlug}_${idBase}`;
      const written = await writeGpxFile(id, forceWalkTrackName(content)); // null if already imported — not an error
      if (written) newCount++;
    } catch {
      // one bad file shouldn't abort the whole sync — skip it, keep going
    }
    emit({ phase: 'writing', current: i + 1, total: gpxFiles.length, newCount });
  }

  emit({ phase: 'done', current: gpxFiles.length, total: gpxFiles.length, newCount });
  return newCount;
}
