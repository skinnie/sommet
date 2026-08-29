import { connect, disconnect, readRegion, readCustomModesRaw, writeRegion, writeCustomModesRaw, isBleTransportActive } from '../native/AmbitUsbModule';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { decode as decodeCM, encodeRegion } from './SportModeCodec';
import { decodeApps, buildAppsRegion, CompiledApp, AppEntry } from './AppsCodec';
import { resolveRegion } from './MemoryMap';
import { findModeIndex, ensureGuidanceDisplay, GUIDANCE_ENTRY_TYPE } from './GuidedWorkoutCore';
import { entryLabel, isExpired, planDiff, rebuildAppsRegion, CalendarEntry } from './TrainingCalendarCore';

// Orchestration for the Calendar feature (native I/O) - the Android counterpart to
// tools/training_calendar.py's sync(), reusing the same read/write/verify shape as
// AppInstall.installCompiledApp. On sync: every managed "dd/mm_..." entry dated before today
// is erased, and every plan entry dated today or later that already has a compiled binary
// attached gets installed, all in one Apps-region rewrite + one CustomModes rewrite (adding a
// guidance display to any newly-used mode).
//
// MANUAL COMPILE, DELIBERATELY (André, 2026-08-21 direction, matching IntervalsService.ts's
// existing policy from 2026-08-14): this app ships no compiler key and makes no automated
// compile call - the community compiler endpoint's owner flagged that as legally sensitive,
// and an earlier version that did POST automatically was removed for that reason ("do not
// reintroduce either" - see IntervalsService.ts). So a CalendarPlanEntry only carries a
// `compiled` binary once the USER has pasted the workout JSON into the compiler site
// themselves and imported the result back (IntervalsService.parseCompiledApp handles that
// import already, reused as-is here). An entry with no `compiled` yet is real and stays in the
// plan, just not synced - reported back as `pendingCompile`, not an error.

const CUSTOM_MODES_SIZE = 12288;

export interface CalendarPlanEntry extends CalendarEntry {
  compiled?: CompiledApp; // set once the user has pasted-and-imported the compiled result
  // The structured workout, carried on entries imported from intervals.icu so a pending entry can
  // regenerate its compiler JSON (IntervalsWorkouts). Undefined on hand-built entries. Not used by
  // syncCalendar (which only installs `compiled`).
  workout?: import('./WorkoutSource').Workout;
}

export interface SyncState {
  phase: 'idle' | 'connecting' | 'reading' | 'writingApps' | 'writingModes' | 'verifying' | 'done' | 'error';
  error?: string;
}

export interface SyncResult {
  removed: string[];
  added: string[];
  pendingCompile: string[]; // plan entries that are due but have no compiled binary yet
  displaysAdded: string[];
}

function u16(b: Uint8Array, o: number) { return b[o] | (b[o + 1] << 8); }
function u32(b: Uint8Array, o: number) { return (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0; }
function appsUsedLength(probe: Uint8Array): number {
  if (probe.length < 8) return 0;
  const n = u16(probe, 0);
  const tableLen = 4 + 4 * (n + 1);
  if (n === 0 || n > 1000 || tableLen > probe.length) return 0;
  return u32(probe, 4 + 4 * n);
}
function bytesEqualPrefix(a: Uint8Array, b: Uint8Array, len: number): boolean {
  for (let i = 0; i < len; i++) if (a[i] !== b[i]) return false;
  return true;
}

/** A region that legitimately holds no apps: unwritten flash (all 0xFF header) or a valid
 * num_entries==0 directory. Any other probe that yields no decodable entries is a suspect read. */
function isCleanEmpty(probe: Uint8Array): boolean {
  if (probe.length < 4) return true;
  if (probe[0] === 0xff && probe[1] === 0xff) return true; // unwritten flash
  return u16(probe, 0) === 0;                              // a real empty directory
}

/** Read + decode the App Zone, re-reading when a read decodes to empty but the region does NOT
 * look genuinely empty - a glitchy read must never be believed, because syncCalendar rewrites the
 * WHOLE region and an empty "existing" would wipe apps that are really on the watch. */
async function readExistingAppsStable(base: number): Promise<AppEntry[]> {
  let last: AppEntry[] = [];
  for (let attempt = 0; attempt < 3; attempt++) {
    const probe = base64ToBytes(await readRegion(base, 8192));
    const usedLen = appsUsedLength(probe);
    const region = usedLen > 0 ? base64ToBytes(await readRegion(base, usedLen)) : new Uint8Array(0);
    const existing = decodeApps(region);
    if (existing.length > 0 || isCleanEmpty(probe)) return existing; // trustworthy result
    last = existing;                                                 // suspect empty - re-read
    await new Promise<void>((r) => setTimeout(() => r(), 300));
  }
  return last;
}

/** Diffs `plan` against what's on the watch and, if `write` is true, applies it: erases every
 * expired managed entry and installs every due entry that already has a compiled binary. A
 * dry-run (write=false) still reads the watch and computes the real diff, just doesn't write -
 * same "always a real dry-run" shape as guided_workout.py's --write gate. */
export async function syncCalendar(
  plan: CalendarPlanEntry[], today: Date, write: boolean, onState: (s: SyncState) => void,
): Promise<SyncResult | null> {
  const overBle = isBleTransportActive();
  onState({ phase: overBle ? 'reading' : 'connecting' });
  if (!overBle) {
    try { await connect(); }
    catch (e: any) { onState({ phase: 'error', error: e?.message ?? 'Connection to the watch failed' }); return null; }
  }
  try {
    onState({ phase: 'reading' });
    const apps = await resolveRegion('Apps');
    if (!apps) { onState({ phase: 'error', error: 'This watch has no App Zone (Apps) region.' }); return null; }

    // Read the App Zone, guarding against a transient/garbled read: a glitchy read that decodes
    // to "empty" must never be trusted for a write, or the whole-region rewrite would WIPE apps
    // that are actually on the watch (observed once during a heavy multi-watch session, 2026-08-27).
    // A genuinely empty region is either unwritten flash (0xFF) or a valid num_entries==0 header;
    // anything else that fails to decode is suspect, so re-read before believing it.
    const existing = await readExistingAppsStable(apps.base);

    const removed = existing.filter((e) => isExpired(e.name, today)).map((e) => e.name);
    const { keptRawBlocks, toAdd } = planDiff(existing, plan, today);

    const ready = toAdd.filter((e) => (e as CalendarPlanEntry).compiled);
    const pendingCompile = toAdd.filter((e) => !(e as CalendarPlanEntry).compiled)
      .map((e) => entryLabel(e.date, e.workoutName));

    let currentList = keptRawBlocks.map((b) => ({ rawBlock: b }));
    let newAppsBytes: Uint8Array | null = null;
    const added: string[] = [];
    for (const e of ready as CalendarPlanEntry[]) {
      const label = entryLabel(e.date, e.workoutName);
      const compiled: CompiledApp = { ...e.compiled!, name: label };
      newAppsBytes = buildAppsRegion(currentList.map((x) => x.rawBlock), compiled, GUIDANCE_ENTRY_TYPE);
      currentList = decodeApps(newAppsBytes).map((x) => ({ rawBlock: x.rawBlock }));
      added.push(label);
    }
    if (newAppsBytes === null) newAppsBytes = rebuildAppsRegion(keptRawBlocks);
    if (newAppsBytes.length > apps.size) {
      onState({ phase: 'error', error: 'The Apps region would overflow - the plan has too much installed at once.' });
      return null;
    }

    // Ensure every mode named by a kept-or-added entry has the guidance display.
    let cmDecoded = decodeCM(base64ToBytes(await readCustomModesRaw()));
    const modesNeeded = new Set<string>();
    for (const e of plan) if (e.date >= isoDate(today)) modesNeeded.add(e.mode);
    for (const e of ready as CalendarPlanEntry[]) modesNeeded.add(e.mode);
    const displaysAdded: string[] = [];
    for (const modeName of [...modesNeeded].sort()) {
      const idx = findModeIndex(cmDecoded, modeName);
      const { decoded, added: wasAdded } = ensureGuidanceDisplay(cmDecoded, idx);
      cmDecoded = decoded;
      if (wasAdded) displaysAdded.push(modeName);
    }
    const cmImage = displaysAdded.length ? encodeRegion(cmDecoded, CUSTOM_MODES_SIZE)
      : base64ToBytes(await readCustomModesRaw());

    const result: SyncResult = { removed, added, pendingCompile, displaysAdded };
    if (!write) { onState({ phase: 'done' }); return result; }

    onState({ phase: 'writingApps' });
    if (!await writeRegion(apps.base, bytesToBase64(newAppsBytes), newAppsBytes.length)) {
      onState({ phase: 'error', error: 'Apps region write was not acknowledged.' }); return null;
    }
    onState({ phase: 'writingModes' });
    if (!await writeCustomModesRaw(bytesToBase64(cmImage))) {
      onState({ phase: 'error', error: 'Sport-mode write was not acknowledged.' }); return null;
    }

    onState({ phase: 'verifying' });
    const appsBack = base64ToBytes(await readRegion(apps.base, newAppsBytes.length));
    if (!bytesEqualPrefix(appsBack, newAppsBytes, newAppsBytes.length)) {
      onState({ phase: 'error', error: 'Apps region read back different bytes than written.' }); return null;
    }
    const cmBack = base64ToBytes(await readCustomModesRaw());
    if (!bytesEqualPrefix(cmBack, cmImage, cmImage.length)) {
      onState({ phase: 'error', error: 'Sport-mode region read back different bytes than written.' }); return null;
    }

    onState({ phase: 'done' });
    return result;
  } catch (e: any) {
    onState({ phase: 'error', error: e?.message ?? 'Sync failed' });
    return null;
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

function isoDate(d: Date): string {
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}
