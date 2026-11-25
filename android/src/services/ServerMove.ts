import AsyncStorage from '@react-native-async-storage/async-storage';

// Moving Sommet Sync to another server (sync/14, André 2026-11-25: "still fix"; desktop does the
// same in ActivityService). What this device knows about "the server" (what it already sent, the
// pull cursor) belonged to the old address, and the light library (sync/13) left older Tracks and
// FITs on that old server only. So when the saved address changes:
// - the moves whose Track or FIT only the old server holds are listed, with that server's address
//   and token, before anything else changes (the rescue);
// - every per-server mark is forgotten, so the next sync sends everything to the new one;
// - each sync first copies the listed files back from the old server, then sends them on;
// - the light library trims nothing while the old server still holds some, so nothing ends up
//   on neither.
// A server has no identity of its own, so a new address for the same NAS counts as a move too: it
// costs one full send, and nothing is lost. A new token for the same address is not a move.

export const RESCUE_KEY = 'sommet.sync.rescue';

/** One move to copy back from the previous server: its Track (gpx) and/or its FIT (fit). */
export interface RescueItem { id: string; uid: string; gpx: boolean; fit: boolean }
export interface Rescue { url: string; token: string; items: RescueItem[] }

/** Trimmed, without a trailing '/': the form two saved addresses are compared in. */
export function normalizeServerUrl(u: string | null | undefined): string {
  return (u ?? '').trim().replace(/\/+$/, '');
}

/** A move: a saved, non-empty address replaced by a different one (or by none: sync turned off). */
export function isServerMove(oldUrl: string | null | undefined, newUrl: string | null | undefined): boolean {
  const a = normalizeServerUrl(oldUrl);
  return a !== '' && a !== normalizeServerUrl(newUrl);
}

export interface RescueRow {
  id: string; date: string; device?: string; gpx_path?: string;
  track_remote?: number; track_cached_at?: number;
}

/** What only the old server holds of this device's moves. Pure, for the tests.
 *  - A Track kept on the server (track_remote, no local file) whose GPX the old server had
 *    (`onOld`: blobsPushed, sent from here or fetched from there).
 *  - Its FIT when the old server confirmed holding it (`fitsOnOld`) and none is here.
 *  - An opened older move (cached Track, here for now but evictable): only its missing FIT.
 *  - intervals.icu imports are left out while intervals.icu is connected: it still holds them. */
export function planRescue(rows: RescueRow[], o: {
  onOld: Set<string>; fitsOnOld: Set<string>; intervalsOn: boolean;
  hasLocalFit: Set<string>; uidOf: (r: RescueRow) => string;
}): RescueItem[] {
  const out: RescueItem[] = [];
  for (const r of rows) {
    const remote = r.track_remote === 1 && !r.gpx_path;
    const cached = !!r.gpx_path && (r.track_cached_at ?? 0) > 0;
    if (!remote && !cached) continue;
    if (o.intervalsOn && r.id.startsWith('icu:')) continue;
    const uid = o.uidOf(r);
    const gpx = remote && o.onOld.has(uid);
    const fit = o.fitsOnOld.has(uid) && !o.hasLocalFit.has(r.id);
    if (gpx || fit) out.push({ id: r.id, uid, gpx, fit });
  }
  return out;
}

/** Opened older moves whose Track came from the old server: kept for good from the move on, so
 *  the size limit can't drop one before the new server has it. Pure. */
export function cachedToKeep(rows: RescueRow[], onOld: Set<string>, uidOf: (r: RescueRow) => string): RescueRow[] {
  return rows.filter(r => !!r.gpx_path && (r.track_cached_at ?? 0) > 0 && onOld.has(uidOf(r)));
}

export async function loadRescue(): Promise<Rescue | null> {
  try {
    const v = JSON.parse((await AsyncStorage.getItem(RESCUE_KEY)) || 'null');
    if (!v || typeof v.url !== 'string' || !Array.isArray(v.items) || v.items.length === 0) return null;
    return { url: v.url, token: typeof v.token === 'string' ? v.token : '', items: v.items };
  } catch {
    return null;
  }
}

/** null (or nothing left): forgets the previous server, its token included. */
export async function saveRescue(r: Rescue | null): Promise<void> {
  if (!r || r.items.length === 0) { await AsyncStorage.removeItem(RESCUE_KEY); return; }
  await AsyncStorage.setItem(RESCUE_KEY, JSON.stringify(r));
}

/** For the light library: while true, nothing more is trimmed. */
export async function rescuePending(): Promise<boolean> {
  return (await loadRescue()) !== null;
}

/** What Settings shows: how many moves the previous server still holds alone, and its address
 *  (never the token). */
export async function rescueSummary(): Promise<{ count: number; url: string } | null> {
  const r = await loadRescue();
  return r ? { count: r.items.length, url: r.url } : null;
}

/** The Settings line while a rescue is pending (desktop says the same). `syncOn`: a server is
 *  saved now; with none (sync turned off) the files wait until one is set up again. */
export function rescueNoteText(sum: { count: number; url: string } | null, syncOn = true): string {
  if (!sum || sum.count <= 0) return '';
  const one = sum.count === 1;
  if (syncOn) {
    return `${one ? '1 older activity is' : `${sum.count} older activities are`} still only on your `
      + `previous server (${sum.url}). Keep it running: Sommet copies ${one ? 'it' : 'them'} to the `
      + 'new one at each sync.';
  }
  return `${one ? '1 older activity is' : `${sum.count} older activities are`} only on your previous `
    + `server (${sum.url}). Set up a server again and Sommet copies ${one ? 'it' : 'them'} over.`;
}

/** The confirm step of "Forget the previous server". */
export function forgetConfirmText(count: number): string {
  return count === 1
    ? 'The activity keeps its details here but loses its map and original file. Forget the previous server?'
    : `The ${count} activities keep their details here but lose their map and original file. `
      + 'Forget the previous server?';
}

/** The moves whose Track was not copied yet: forgetting the previous server leaves them without
 *  one. Pure. */
export function uncopiedTrackIds(r: Rescue | null): string[] {
  return r ? r.items.filter(i => i.gpx).map(i => i.id) : [];
}

export type Fetched<T> = { status: 'ok'; body: T } | { status: 'missing' } | { status: 'failed' };

export interface RescueIO {
  fetchGpx(uid: string): Promise<Fetched<string>>;
  fetchFit(uid: string): Promise<Fetched<Uint8Array>>;
  storeGpx(item: RescueItem, gpx: string): Promise<boolean>;
  storeFit(item: RescueItem, fit: Uint8Array): Promise<boolean>;
  /** The previous server has no Track for this move after all: it no longer counts as kept on a
   *  server. */
  missingGpx?(item: RescueItem): Promise<void>;
  save(r: Rescue | null): Promise<void>;
}

export function isFitBytes(b: Uint8Array): boolean {
  return b.length > 12 && String.fromCharCode(b[8], b[9], b[10], b[11]) === '.FIT';
}

/** Copies the listed files back from the previous server, item by item. A file the server answers
 *  404 for (or answers with nothing usable) is crossed off: it has none. Any other failure stops
 *  this run and keeps the rest for the next sync. Saves what is left (nothing left: the rescue,
 *  and the old token with it, is forgotten). */
export async function runRescue(r: Rescue, io: RescueIO): Promise<{ done: number; left: number; stopped: boolean }> {
  const items = r.items.map(i => ({ ...i }));
  let done = 0;
  let stopped = false;
  const left: RescueItem[] = [];
  for (let n = 0; n < items.length; n++) {
    const it = items[n];
    if (stopped) { left.push(it); continue; }
    if (it.gpx) {
      const got = await io.fetchGpx(it.uid);
      if (got.status === 'failed') stopped = true;
      else if (got.status === 'missing' || got.body.indexOf('<trkpt') < 0) {
        it.gpx = false;
        if (io.missingGpx) await io.missingGpx(it);
      } else if (await io.storeGpx(it, got.body)) it.gpx = false;
      else stopped = true;
    }
    if (!stopped && it.fit) {
      const got = await io.fetchFit(it.uid);
      if (got.status === 'failed') stopped = true;
      else if (got.status === 'missing' || !isFitBytes(got.body)) it.fit = false;
      else if (await io.storeFit(it, got.body)) it.fit = false;
      else stopped = true;
    }
    if (it.gpx || it.fit) left.push(it);
    else done++;
  }
  await io.save(left.length ? { ...r, items: left } : null);
  return { done, left: left.length, stopped };
}
