import { base64ToBytes } from './Base64';
import { poiEntries } from './PoiService';

// The POIs made safe around a write of the watch's route list (ticket watch-protocol/05,
// 2026-11-22). The watch takes its routes in one write whose commit (0x0b04) empties the POI
// store; the native side reads the POIs first and puts them back after. Until they are back they
// used to exist only in memory, and a put-back the watch did not keep was only written to the
// log while the send said "done". The desktop already did what this does (tools/write_nav.py
// backup_pois_before_route_write and put_pois_back_safely):
//   1. read the POIs and save a copy on the phone before anything is written;
//   2. do the write;
//   3. read the POIs again; if they are not the ones of the copy, write the copy back once,
//      from an empty store (AmbitUsbModule.setPoiList: 0x0b04, count 0, write, count);
//   4. if that fails too, the error names where the copy is.
// "The same POIs" compares the records with something in them, in order: an empty entry (a name
// the watch could not keep) carries nothing and is not written back.

export interface PoiKeeperDeps {
  /** The watch's POI list as a 0x0b24 read, base64 ('' when it has none). */
  readPoiListRaw: () => Promise<string>;
  /** Replaces the watch's POI list (`expected` = what is there now) with `newList`. */
  setPoiListRaw: (expectedB64: string, newListB64: string) => Promise<number>;
  /** Saves the copy and gives back where it is. */
  saveCopy: (listB64: string) => Promise<string>;
}

export interface KeptPois {
  /** How many POIs the watch had before the write. */
  count: number;
  /** Where the copy is, or null when there was nothing to copy. */
  copyPath: string | null;
  /** True when the POIs had to be written back a second time. */
  putBackAgain: boolean;
}

/** The records with something in them, as strings that can be compared. */
export function poiRecords(listB64: string): string[] {
  if (!listB64) return [];
  const bytes = base64ToBytes(listB64);
  return poiEntries(bytes)
    .filter(e => e.end > e.start)
    .map(e => `${e.id ?? ''}:${Array.from(bytes.subarray(e.start, e.end)).join(',')}`);
}

function same(a: string[], b: string[]): boolean {
  return a.length === b.length && a.every((x, i) => x === b[i]);
}

/** Runs `write` (a write of the watch's route list) with the POIs made safe around it. Throws
 *  `write`'s own error when it failed and the POIs are as they were, or an error that names the
 *  copy when the POIs could not be put back. */
export async function writeKeepingPois(write: () => Promise<void>, deps: PoiKeeperDeps): Promise<KeptPois> {
  let before: string;
  try {
    before = (await deps.readPoiListRaw()) ?? '';
  } catch (e: any) {
    throw new Error(`Couldn't read the POIs on the watch, nothing was written: ${e?.message ?? e}`);
  }
  const wanted = poiRecords(before);
  const copyPath = wanted.length > 0 ? await deps.saveCopy(before) : null;

  let writeError: any = null;
  try {
    await write();
  } catch (e: any) {
    writeError = e;
  }
  if (wanted.length === 0) {
    if (writeError) throw writeError;
    return { count: 0, copyPath: null, putBackAgain: false };
  }

  // Are the POIs back? A read that fails counts as "not known", and the copy is tried.
  let now: string | null = null;
  try {
    now = (await deps.readPoiListRaw()) ?? '';
  } catch {
    now = null;
  }
  if (now !== null && same(poiRecords(now), wanted)) {
    if (writeError) throw writeError;
    return { count: wanted.length, copyPath, putBackAgain: false };
  }

  const what = writeError ? `The route write failed (${writeError?.message ?? writeError})` : 'The routes were written';
  try {
    const current = now ?? ((await deps.readPoiListRaw()) ?? '');
    await deps.setPoiListRaw(current, before);
  } catch (e: any) {
    throw new Error(`${what}, but the POIs could not be put back (${e?.message ?? e}). ` +
      `A copy of them is saved in ${copyPath}.`);
  }
  if (writeError) {
    throw new Error(`${what}. The POIs were put back from the copy saved in ${copyPath}.`);
  }
  return { count: wanted.length, copyPath, putBackAgain: true };
}
