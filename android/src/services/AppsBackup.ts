// A copy of the watch's App Zone (the Apps region: Suunto Apps and the WORKOUT menu's workouts)
// before Sommet rewrites it, like the sport modes' copy (CustomModesBackup). The Training Program
// sync and an app install rewrite the whole region, and Android did it with no copy at all
// (tablet audit F24, 2026-10-31): a bad write could only be undone by installing everything again.
// Saved under backups/Apps/<serial>/Apps_pre_<time>.bin.

import { getDeviceInfo } from '../native/AmbitUsbModule';
import { bytesToBase64 } from './Base64';
import { saveWatchBackup } from './WatchBackup';

/** Saves the Apps bytes as they were read (the used extent; the first block when the region is
 * empty). Throws if they could not be saved: the caller must not write without the copy. */
export async function backupApps(bytes: Uint8Array): Promise<string> {
  if (!bytes.length) throw new Error('No App Zone image to back up');
  let serial = '';
  try { serial = (await getDeviceInfo()).serial ?? ''; } catch { /* named 'unknown' */ }
  return saveWatchBackup('Apps', bytesToBase64(bytes), serial);
}
