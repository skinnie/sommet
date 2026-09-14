import AsyncStorage from '@react-native-async-storage/async-storage';

// One-time "re-import everything from the watch to rebuild native FIT files" migration.
//
// Why: before the native-FIT build, a synced move stored only its GPS track in the phone's GPX -
// never heart rate / power / cadence (those lived only in the watch's raw samples, discarded right
// after the GPX was built). So an existing library can't be turned into rich FITs on-device; the
// only source is the watch itself. On first launch of a build whose FIT_REIMPORT_GENERATION is
// higher than what's stored, we flag the NEXT sync to re-read + overwrite every move still on the
// watch (SyncService reimportForFit), which rebuilds each <id>.fit with the full channels.
//
// The flag is set once (per generation) and cleared only AFTER a re-import actually completes, so a
// first launch with no watch connected simply re-imports whenever the watch is next synced. Bumping
// the generation in a future build re-triggers it (e.g. if the encoder gains channels) - the only
// legitimate reason to re-run, so there's no manual button.

// Bump this when a new build should re-import to pick up FIT-encoder improvements.
const FIT_REIMPORT_GENERATION = 1;

const GEN_KEY     = 'fit_reimport_generation_done';   // last generation that finished re-importing
const PENDING_KEY = 'fit_reimport_pending';           // '1' while a re-import is owed

/** Call once at app start. If this build's generation hasn't been re-imported yet, arm the pending
 *  flag so the next sync does a FIT re-import. Idempotent and best-effort. */
export async function initFitReimport(): Promise<void> {
  try {
    const done = parseInt((await AsyncStorage.getItem(GEN_KEY)) ?? '0', 10) || 0;
    if (done < FIT_REIMPORT_GENERATION) {
      await AsyncStorage.setItem(PENDING_KEY, '1');
    }
  } catch { /* best-effort - a missed arm just means no auto re-import this launch */ }
}

/** Whether the next sync should run as a FIT re-import (re-read + overwrite, keeping deletions). */
export async function isFitReimportPending(): Promise<boolean> {
  try {
    return (await AsyncStorage.getItem(PENDING_KEY)) === '1';
  } catch {
    return false;
  }
}

/** Mark this generation's re-import complete: clear the pending flag and record the generation, so
 *  it never re-runs until a future build bumps FIT_REIMPORT_GENERATION. Call only after a sync that
 *  ran as a FIT re-import actually SUCCEEDED. */
export async function markFitReimportDone(): Promise<void> {
  try {
    await AsyncStorage.setItem(GEN_KEY, String(FIT_REIMPORT_GENERATION));
    await AsyncStorage.removeItem(PENDING_KEY);
  } catch { /* best-effort - if this fails the re-import simply runs again next sync (harmless) */ }
}
