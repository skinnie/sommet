import { AppEntry, NAME_LEN } from './AppsCodec';
import { GUIDANCE_ENTRY_TYPE, WORKOUT_MENU_MAX } from './GuidedWorkoutCore';
import { programKey } from './WorkoutRuleIds';

// Pure (native-free) core of the Calendar feature - André's locked design (2026-08-21):
// dated native guided workouts named "Www dd/mm name" in the WORKOUT menu, sidestepping the
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

// Managed entries read "Www dd/mm name"; older syncs wrote "dd/mm_name", still matched so a
// re-sync renames rather than duplicates. Mirrors tools/training_calendar.py's MANAGED_RE.
const MANAGED_RE = /^(?:[A-Z][a-z]{2} )?(\d{2})\/(\d{2})[ _]/;
const WEEKDAY_ABBR = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']; // Date.getDay(): Sun=0

/** "Www dd/mm name" (e.g. "Tue 20/10 Beep5s"), truncated to fit the on-watch name field
 * (AppsCodec.NAME_LEN). Weekday in fixed English abbreviations, byte-identical to
 * tools/training_calendar.py's entry_label (André 2026-10-20: weekday + date). */
export function entryLabel(dateIso: string, workoutName: string): string {
  const d = new Date(dateIso + 'T00:00:00');
  const dd = String(d.getDate()).padStart(2, '0');
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const prefix = `${WEEKDAY_ABBR[d.getDay()]} ${dd}/${mm} `;
  const budget = Math.max((NAME_LEN - 1) - prefix.length, 0);
  return prefix + workoutName.slice(0, budget);
}

export function isManaged(name: string): boolean {
  return MANAGED_RE.test(name || '');
}

/** True if a managed "...dd/mm..." name's (month, day) is before today's - see the module
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

const WEEKDAY_RE = /^[A-Z][a-z]{2} /;

/** The dated workouts among `names` that another device put on the watch and whose day is still
 * to come. The tablet and the PC keep separate plans, so a sync erases only past ones and its own
 * (André, 2026-11-01): the tablet's sync had erased the PC's W9 Final Workout on its day. `mine`
 * is this tablet's record of what it installed on the watch (CalendarRecord); a label in its plan
 * counts as its own too, so the same workout in both plans goes on once. One named the old way
 * ("dd/mm_name") is also the sync's own, so a re-sync still renames it. Port of
 * training_calendar.py's left_to_others. */
export function leftToOthers(names: string[], planLabels: ReadonlySet<string>, today: Date,
                             mine: ReadonlySet<string>): string[] {
  return names.filter((n) => isManaged(n) && WEEKDAY_RE.test(n) && !isExpired(n, today)
    && !mine.has(n) && !planLabels.has(n));
}

/** What the tablet records as its own after a sync: the dated workouts now on the watch that are
 * in its plan or were already its own - never another device's (desktop sync's `fresh`). */
export function installRecord(namesOnWatch: string[], planLabels: ReadonlySet<string>,
                              mine: ReadonlySet<string>): string[] {
  return [...new Set(namesOnWatch.filter((n) => isManaged(n) && (planLabels.has(n) || mine.has(n))))].sort();
}

/** Returns (keptRawBlocks, toAdd, waiting, othersKept). The WORKOUT menu shows only the first
 * WORKOUT_MENU_MAX native workouts, so the calendar syncs to the space available: manually
 * installed native workouts keep their slots and the soonest upcoming plan entries fill the rest;
 * `waiting` is the later ones' labels, moved in by later syncs as past ones are erased. A managed
 * entry is kept only inside that window (expired, removed-from-plan and pushed-out ones are all
 * erased) - except one another device put there whose day is still to come (`othersKept`,
 * leftToOthers; `mine` is this tablet's record): it stays and keeps its slot, like a manual one.
 * Unmanaged apps are never touched. Exact port of training_calendar.py's plan_diff. */
export function planDiff(existing: AppEntry[], planEntries: CalendarEntry[], today: Date,
                         menuMax: number = WORKOUT_MENU_MAX, mine: ReadonlySet<string> = new Set()):
    { keptRawBlocks: Uint8Array[]; toAdd: CalendarEntry[]; waiting: string[]; othersKept: string[] } {
  const namesPresent = new Set(existing.map((e) => e.name));
  const todayIso = `${today.getFullYear()}-${String(today.getMonth() + 1).padStart(2, '0')}-${String(today.getDate()).padStart(2, '0')}`;
  const native = existing.filter((e) => e.rawBlock[0] === GUIDANCE_ENTRY_TYPE).map((e) => e.name);
  const manual = native.filter((n) => !isManaged(n));
  const planLabels = new Set(planEntries.map((e) => entryLabel(e.date, e.workoutName)));
  const othersKept = leftToOthers(native, planLabels, today, mine);
  const others = new Set(othersKept);
  const slots = Math.max(menuMax - manual.length - othersKept.length, 0);
  const upcoming: [string, CalendarEntry][] = [];
  const seen = new Set<string>();
  for (const e of [...planEntries].sort((a, b) => a.date.localeCompare(b.date))) {
    const label = entryLabel(e.date, e.workoutName);
    if (e.date >= todayIso && !seen.has(label)) { seen.add(label); upcoming.push([label, e]); }
  }
  const window = new Set(upcoming.slice(0, slots).map(([label]) => label));
  const waiting = upcoming.slice(slots).map(([label]) => label);

  const keptRawBlocks = existing
    .filter((e) => !isManaged(e.name) || window.has(e.name) || others.has(e.name))
    .map((e) => e.rawBlock);
  const toAdd = upcoming.slice(0, slots)
    .filter(([label]) => !namesPresent.has(label))
    .map(([, e]) => e);
  return { keptRawBlocks, toAdd, waiting, othersKept };
}

/** The kept managed entries whose program on the watch differs from what the plan now installs
 * under that label: a workout edited but left on the same date with the same name. planDiff goes
 * by name, so before this an edit never reached the watch (André's 10 s test, 2026-10-24).
 * `programFor(label)` is the binary the sync would install for that label, or null when it has
 * nothing to install (not compiled yet) - those stay as they are. Rule ids are ignored (handed
 * out per install) and so is the sport byte (every native workout gets the same one). */
export function staleKept(existing: AppEntry[], keptRawBlocks: Uint8Array[],
                          programFor: (label: string) => Uint8Array | null): AppEntry[] {
  const kept = new Set(keptRawBlocks);
  return existing.filter((e) => {
    if (!isManaged(e.name) || !kept.has(e.rawBlock)) return false;
    const want = programFor(e.name);
    return want != null && !sameBytes(programKey(want), programKey(e.binary));
  });
}

function sameBytes(a: Uint8Array, b: Uint8Array): boolean {
  if (a.length !== b.length) return false;
  for (let i = 0; i < a.length; i++) if (a[i] !== b[i]) return false;
  return true;
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
