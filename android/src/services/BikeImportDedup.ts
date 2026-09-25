import AsyncStorage from '@react-native-async-storage/async-storage';
import { getAllActivities, getDeletedIds } from '../database/db';
import { activityExists } from './GpxService';

// "Already have this ride?" for bike-computer imports (Magene over BLE, Bryton over USB) - the
// exact rule of the desktop's ActivityService::importBikeActivitiesInto, so the two apps agree:
//   * same id already in the library, a GPX file waiting to be listed, or DELETED by the user
//     (deleted_activities) -> never re-import (a deleted ride used to come back on the next Sync);
//   * an activity from ANY source (intervals.icu import, the watch, another device) starting
//     within 5 min -> it's the same ride;
//   * or a whole 1-2 h offset (+-2 min) with a comparable duration - intervals.icu sometimes
//     stores rides with a timezone/DST hour shift (verified on the desktop, 2026-09-04).
//   * every ride handled once (imported, duplicate or junk) is remembered in a "seen" list - the
//     desktop's bike_seen table - so a Sync never downloads or re-reads it again.
// Rides are checked by id and start BEFORE the slow download when the start is known up front
// (the Magene's ride id is its start time), and again with the duration once decoded.

interface Known { epoch: number; dur: number }
export const SEEN_KEY = 'bikeImportSeen.v1';

const durLike = (a: number, b: number) => a <= 0 || b <= 0 || (a / b >= 0.5 && a / b <= 2);

export class BikeImportDedup {
  private known: Known[] = [];
  private ids = new Set<string>();
  private deleted = new Set<string>();
  private seen = new Set<string>();

  static async load(): Promise<BikeImportDedup> {
    const d = new BikeImportDedup();
    const [acts, del] = await Promise.all([getAllActivities(), getDeletedIds()]);
    for (const a of acts) {
      d.ids.add(a.id);
      const t = Date.parse(a.date);
      if (!isNaN(t)) d.known.push({ epoch: t / 1000, dur: a.duration_s || 0 });
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

  /** Same ride from any source (start epoch seconds; duration unknown before the download). */
  hasRide(epoch: number, dur: number | null): boolean {
    for (const e of this.known) {
      const dd = Math.abs(epoch - e.epoch);
      if (dd <= 300) return true;
      if (dur != null && (Math.abs(dd - 3600) <= 120 || Math.abs(dd - 7200) <= 120) && durLike(dur, e.dur)) return true;
    }
    return false;
  }

  /** Remember a ride imported during this sync. */
  add(id: string, epoch: number, dur: number) {
    this.ids.add(id);
    this.known.push({ epoch, dur });
  }
}
