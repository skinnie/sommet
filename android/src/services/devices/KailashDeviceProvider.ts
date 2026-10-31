// ─── KailashDeviceProvider ────────────────────────────────────────────────────
// DeviceProvider for the Suunto Kailash ("Hoopoe") - real, 2026-08-08 ("Yes I want to
// implement it both to desktop and android version"). Kailash has no sport-mode logbook or
// ExerciseLog flash region the way Ambit3 does (confirmed from the watch's own real memory
// map - see the companion research project's custom_modes_andre.md), so unlike
// AmbitDeviceProvider it doesn't call the native ExerciseLog/PMEM20 walker at all: getLogs()
// reads the real TrackLog region directly (readRegion(), the same generic primitive
// RouteReader.ts already uses) and decodes it in TS (KailashTrackLogReader.ts).
//
// connect()/disconnect() are unchanged from the Ambit path - Kailash already answers the
// same USB init + 0x0000 device-info commands (AmbitUsbModule.kt's SUUNTO_PID_NAMES/
// device_filter.xml both now include its real product ID, 0x002a) - only the log-reading
// step differs. This is why HomeScreen.tsx doesn't need a third `detectAttachedDeviceType()`
// branch: Kailash already comes back as "ambit" from that check (any known Suunto PID does),
// and only routes to this provider once getDeviceInfo().model says "Hoopoe" - see
// HomeScreen.tsx's own comment at the point it picks between ambitDeviceProvider and this.

import * as AmbitUsbModule from '../../native/AmbitUsbModule';
import { DeviceProvider, DeviceInfo } from './DeviceProvider';
import { SyncProgressEvent } from '../../native/AmbitUsbModule';
import { decodeTrackLogToActivities, KAILASH_TRACKLOG_BASE, KAILASH_TRACKLOG_SIZE } from '../KailashTrackLogReader';
import { decodeDeviceHistory, KailashSession } from '../KailashHistoryReader';

export class KailashDeviceProvider implements DeviceProvider {
  readonly deviceName = 'Suunto Kailash';
  // TrackLog + DeviceHistory are re-read whole on every sync, so refresh existing activities
  // rather than skip them - a re-sync should adopt a better decode (e.g. the session's real
  // distance now carried as a <distance> extension) instead of keeping the first, stale one.
  readonly refreshExisting = true;

  connect(): Promise<DeviceInfo> {
    return AmbitUsbModule.connect();
  }

  disconnect(): Promise<void> {
    return AmbitUsbModule.disconnect();
  }

  // knownIds is intentionally unused: TrackLog is one continuous passive-tracking buffer,
  // not a per-session logbook to filter - SyncService.ts's own isActivitySynced(id) check
  // (keyed off each GPX's own <metadata><time>) already skips writing an activity again if
  // that hasn't changed since the last sync.
  //
  // Real, 2026-08-09 ("Something is bizarre on the activities, they say no gps, but they
  // have gps") - this used to bundle the whole TrackLog region into one giant GPX with no
  // per-"Walk" correlation at all. Now reads DeviceHistory too (the same real 0x67 summaries
  // HomeScreen.tsx's own kailashHistory.sessions already surfaces) and splits TrackLog's
  // points across them - direct port of tools/kailash_tracklog.py's own
  // split_into_activities() fix, see KailashTrackLogReader.ts's own comment for the full
  // story. A DeviceHistory read failure here doesn't fail the whole sync - it just falls
  // back to the old bundled-everything behavior (splitIntoActivities()'s own fallback for
  // an empty sessions list).
  async getLogs(): Promise<string[]> {
    const b64 = await AmbitUsbModule.readRegion(KAILASH_TRACKLOG_BASE, KAILASH_TRACKLOG_SIZE);
    let sessions: KailashSession[] = [];
    try {
      const historyB64 = await AmbitUsbModule.readDeviceHistoryRaw();
      const history = decodeDeviceHistory(historyB64);
      if (history) sessions = history.sessions;
    } catch {
      // Real, honest fallback - DeviceHistory read failed (or the watch has none yet), same
      // as the Python original's own "no sessions to correlate against" path.
    }
    return decodeTrackLogToActivities(b64, sessions);
  }

  onSyncProgress(_callback: (event: SyncProgressEvent) => void): () => void {
    // No incremental native progress event exists for a plain readRegion() call (unlike the
    // native ExerciseLog walker's own per-log callback) - honestly reporting nothing rather
    // than fabricating fake progress ticks. SyncService.ts's own UI already tolerates a
    // fetching phase with no progress events (it starts current/total at 0 and only updates
    // them if an event actually fires).
    return () => {};
  }

  // libambit sends the Kailash its own clock push (0x1201 ISO 8601), see date_time_set().
  setClock(): Promise<boolean> {
    return AmbitUsbModule.setDateTime();
  }

  // The serial the desktop saves moves under (its device column), from the device-info read.
  async serial(): Promise<string> {
    try { return (await AmbitUsbModule.getDeviceInfo()).serial || ''; } catch { return ''; }
  }
}

export const kailashDeviceProvider = new KailashDeviceProvider();
