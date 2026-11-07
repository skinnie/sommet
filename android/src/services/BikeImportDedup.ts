import AsyncStorage from '@react-native-async-storage/async-storage';
import { getAllActivities, getDeletedIds } from '../database/db';
import { activityExists } from './GpxService';
import { sameMove, startUtcOf, SameMoveRow } from './SameMove';

// "Already have this ride?" for bike-computer imports (Magene over BLE, Bryton over USB) - the
// exact rule of the desktop's ActivityService::importBikeActivitiesInto, so the two apps agree:
//   * same id already in the library, a GPX file waiting to be listed, or DELETED by the user
//     (deleted_activities) -> never re-import (a deleted ride used to come back on the next Sync);
//   * the same ride from ANY source (intervals.icu import, the watch, another device): the shared
//     rule in SameMove.ts (UTC starts within 3 min, then distance or duration agreeing);
//   * every ride handled once (imported, duplicate or junk) is remembered in a "seen" list - the
//     desktop's bike_seen table - so a Sync never downloads or re-reads it again.
// Rides are checked by id and start BEFORE the slow download when the start is known up front
// (the Magene's ride id is its start time), and again with the duration once decoded.

export const SEEN_KEY = 'bikeImportSeen.v1';


export class BikeImportDedup {
  private known: SameMoveRow[] = [];
  private ids = new Set<string>();
  private deleted = new Set<string>();
  private seen = new Set<string>();

  static async load(): Promise<BikeImportDedup> {
    const d = new BikeImportDedup();
    const [acts, del] = await Promise.all([getAllActivities(), getDeletedIds()]);
    for (const a of acts) {
      d.ids.add(a.id);
      const t = startUtcOf(a);
      if (!isNaN(t)) d.known.push({ start: t, durationS: a.duration_s || 0, distanceM: a.distance_m || 0, watch: !a.id.startsWith('icu:') && !a.device, device: a.device ?? '' });
    }
    del.forEach(id => d.deleted.add(id));
    try { (JSON.parse((await AsyncStorage.getItem(SEEN_KEY)) || '[]') as string[]).forEach(id => d.seen.add(id)); } catch { /* none */ }
    return d;
  }

  /** Library id already used, waiting on disk, or deleted by the user. */
  async hasId(id: string): Promise<boolean> {
    return this.seen.has(id) || this.ids.has(id) || this.deleted.has(id) || (await activityExists(id));
  }

  /** Handled (imported / duplicate / junk) - never look at it again. Saved by save(). */
  markSeen(id: string) { this.seen.add(id); }
  async save() {
    try { await AsyncStorage.setItem(SEEN_KEY, JSON.stringify([...this.seen])); } catch { /* best effort */ }
  }

  /** Same ride from any source (start epoch seconds UTC; duration/distance unknown before the
   *  download: then only the start is compared). */
  hasRide(epoch: number, dur: number | null, dist = 0): boolean {
    const me: SameMoveRow = { start: epoch, durationS: dur ?? 0, distanceM: dist };
    return this.known.some(k => sameMove(me, k));
  }

  /** Remember a ride imported during this sync. */
  add(id: string, epoch: number, dur: number, dist = 0) {
    this.ids.add(id);
    this.known.push({ start: epoch, durationS: dur, distanceM: dist });
  }
}
