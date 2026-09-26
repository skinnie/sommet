import { AppEntry, NAME_LEN } from './AppsCodec';

// Pure (native-free) core of the Calendar feature - André's locked design (2026-08-21):
// dated native guided workouts named "dd/mm_name" in the WORKOUT menu, sidestepping the
// unreachable native TrainingProgram flash region entirely (see
// assets/Firmware/re-out/training_program_CONCLUSION.md). Exact port of the pure parts of
// tools/training_calendar.py's entry_label/is_managed/is_expired/plan_diff/
// rebuild_apps_region - the byte-format logic, kept native-free so it's unit-testable the same
// way AppInstallCore/AppsCodec are, and shareable between the "what should change" diff and
// whatever screen presents it.
//
// Same v1 simplification as the desktop tool, on purpose (not a bug): the watch only stores
// "dd/mm" in the app name, no year, so expiry compares (month, day) only - correct for
// planning days/weeks/months ahead within one calendar year, wrong across a Dec->Jan boundary.

const MANAGED_RE = /^(\d{2})\/(\d{2})_/;

/** "dd/mm_name", truncated to fit the on-watch name field (AppsCodec.NAME_LEN). */
export function entryLabel(dateIso: string, workoutName: string): string {
  const d = new Date(dateIso + 'T00:00:00');
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const prefix = `${dd}/${mm}_`;
  const budget = Math.max((NAME_LEN - 1) - prefix.length, 0);
  return prefix + workoutName.slice(0, budget);
}

export function isManaged(name: string): boolean {
  return MANAGED_RE.test(name || '');
}

/** True if a managed "dd/mm_..." name's (month, day) is before today's - see the module
 * docstring on why this ignores year. */
export function isExpired(name: string, today: Date): boolean {
  const m = MANAGED_RE.exec(name || '');
  if (!m) return false;
  const day = parseInt(m[1], 10);
  const month = parseInt(m[2], 10);
  const todayMonth = today.getMonth() + 1, todayDay = today.getDate();
  return month < todayMonth || (month === todayMonth && day < todayDay);
}

export interface CalendarEntry { date: string; mode: string; workoutName: string }

/** Returns (keptRawBlocks, toAdd) - keptRawBlocks is every existing Apps entry that isn't an
 * expired managed one; toAdd is the plan entries (date >= today) not already installed under
 * their computed label. Exact port of training_calendar.py's plan_diff. */
export function planDiff(existing: AppEntry[], planEntries: CalendarEntry[], today: Date):
    { keptRawBlocks: Uint8Array[]; toAdd: CalendarEntry[] } {
  const namesPresent = new Set(existing.map((e) => e.name));
  const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  // A managed entry stays only if it's unexpired AND still in the plan - one removed from the
  // calendar is erased now, not left on the watch until its date passes (tools/training_calendar.py
  // plan_diff, fixed 2026-09-22). Unmanaged apps are never touched.
  const futureLabels = new Set(planEntries.filter((e) => e.date >= todayIso)
    .map((e) => entryLabel(e.date, e.workoutName)));
  const keptRawBlocks = existing
    .filter((e) => !isManaged(e.name) || (!isExpired(e.name, today) && futureLabels.has(e.name)))
    .map((e) => e.rawBlock);

  const toAdd = [...planEntries]
    .sort((a, b) => a.date.localeCompare(b.date))
    .filter((e) => e.date >= todayIso)
    .filter((e) => !namesPresent.has(entryLabel(e.date, e.workoutName)));
  return { keptRawBlocks, toAdd };
}

function writeU32(b: Uint8Array, o: number, v: number) {
  b[o] = v & 0xff; b[o + 1] = (v >> 8) & 0xff; b[o + 2] = (v >> 16) & 0xff; b[o + 3] = (v >>> 24) & 0xff;
}

/** Same directory-format assembly as AppsCodec.buildAppsRegion's tail, for the pure-removal
 * case (no new entry to hand it) - see that function's docstring for the format. */
export function rebuildAppsRegion(rawBlocks: Uint8Array[]): Uint8Array {
  const numEntries = rawBlocks.length;
  const tableLen = 4 + 4 * (numEntries + 1);
  const offsets: number[] = [];
  let cursor = tableLen;
  for (const b of rawBlocks) { offsets.push(cursor); cursor += b.length; }
  const totalLength = cursor;

  const out = new Uint8Array(totalLength);
  out[0] = numEntries & 0xff; out[1] = (numEntries >> 8) & 0xff;
  const x = numEntries ^ 0x02; out[2] = x & 0xff; out[3] = (x >> 8) & 0xff;
  let p = 4;
  for (const o of offsets) { writeU32(out, p, o); p += 4; }
  writeU32(out, p, totalLength); p += 4;
  for (const b of rawBlocks) { out.set(b, p); p += b.length; }
  return out;
}
