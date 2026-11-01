import { entryLabel, installRecord, isManaged, isExpired, planDiff, rebuildAppsRegion, staleKept } from '../TrainingCalendarCore';
import { buildAppsRegion, decodeApps } from '../AppsCodec';

// Same reasoning/values as this feature's desktop hardware+unit tests (training_calendar.py) -
// kept a direct parallel so a divergence between the two ports would show up as a test
// difference, not a silent drift (see ambit_app_android_decoder_drift memory).

function fakeAppsRegion(names: string[]): Uint8Array {
  let region: Uint8Array = new Uint8Array(0);
  let existing: Uint8Array[] = [];
  for (const name of names) {
    const compiled = { binary: new Uint8Array([1, 2, 3]), activityId: 3, name };
    region = buildAppsRegion(existing, compiled, 1);
    existing = decodeApps(region).map((e) => e.rawBlock);
  }
  return region;
}

// The on-watch label the tool writes; mirrors the Python test's lbl() so neither hardcodes the
// naming format (weekday+date, since 2026-10-20). Default workout name "W<dd>" matches run().
const lbl = (d: string, name?: string) => entryLabel(d, name ?? `W${d.slice(-2)}`);

describe('TrainingCalendarCore', () => {
  test('entryLabel formats "Www dd/mm name" and truncates to fit NAME_LEN', () => {
    expect(entryLabel('2026-08-25', 'Long run')).toMatch(/^[A-Z][a-z]{2} 25\/08 Long run$/);
    expect(entryLabel('2026-01-05', 'x'.repeat(40))).toMatch(/^[A-Z][a-z]{2} 05\/01 x+$/);
  });

  test('isManaged recognizes the weekday+date prefix (and the legacy dd/mm_ for migration)', () => {
    expect(isManaged('Tue 25/08 Long run')).toBe(true);
    expect(isManaged('25/08_Long run')).toBe(true); // legacy, still matched so a re-sync renames
    expect(isManaged('Couch-to-5K Week1')).toBe(false);
  });

  test('isExpired compares (month, day) against today, ignoring managed-status of other names', () => {
    const today = new Date(2026, 7, 27); // 2026-08-27 (Date months are 0-based)
    expect(isExpired('Tue 25/08 Long run', today)).toBe(true);
    expect(isExpired('25/08_Long run', today)).toBe(true); // legacy format still parsed
    expect(isExpired('28/08_Interval', today)).toBe(false);
    expect(isExpired('Couch-to-5K', today)).toBe(false);
  });

  test('planDiff drops expired managed entries, keeps the rest, and finds what still needs installing', () => {
    const region = fakeAppsRegion([lbl('2026-08-25', 'Long run'), lbl('2026-08-28', 'Interval')]);
    const existing = decodeApps(region);
    const today = new Date(2026, 7, 27); // 2026-08-27
    const plan = [
      { date: '2026-08-25', mode: 'Running', workoutName: 'Long run' },
      { date: '2026-08-28', mode: 'Running', workoutName: 'Interval' },
      { date: '2026-09-01', mode: 'Running', workoutName: 'Tempo' },
    ];
    const { keptRawBlocks, toAdd } = planDiff(existing, plan, today);
    expect(keptRawBlocks.length).toBe(1); // only the 28/08 one survives
    expect(toAdd.map((e) => e.date)).toEqual(['2026-09-01']);
  });

  test('planDiff erases a still-future entry of its own that was removed from the plan', () => {
    const region = fakeAppsRegion(['Couch-to-5K', lbl('2026-08-28', 'Interval'), lbl('2026-08-30', 'Tempo')]);
    const existing = decodeApps(region);
    const today = new Date(2026, 7, 27);
    const plan = [{ date: '2026-08-30', mode: 'Running', workoutName: 'Tempo' }];  // 28/08 removed
    const mine = new Set([lbl('2026-08-28', 'Interval'), lbl('2026-08-30', 'Tempo')]);   // the tablet put both there
    const { keptRawBlocks, toAdd } = planDiff(existing, plan, today, undefined, mine);
    const keptNames = existing.filter((e) => keptRawBlocks.includes(e.rawBlock)).map((e) => e.name);
    expect(keptNames).toEqual(['Couch-to-5K', lbl('2026-08-30', 'Tempo')]);  // unmanaged app always kept
    expect(toAdd).toEqual([]);
  });

  // Same cases as tools/test_training_calendar.py PlanDiffWindow: the menu shows 5 natives.
  const run = (d: string) => ({ date: d, mode: 'Running', workoutName: `W${d.slice(-2)}` });
  const today926 = new Date(2026, 8, 26);

  test('planDiff syncs only the soonest workouts that fit the 5-slot menu', () => {
    const existing = decodeApps(fakeAppsRegion(['Mine']));  // one manual native workout
    const plan = ['2026-09-26', '2026-09-28', '2026-09-30', '2026-10-03', '2026-10-05', '2026-10-07'].map(run);
    const { keptRawBlocks, toAdd, waiting } = planDiff(existing, plan, today926);
    expect(toAdd.map((e) => e.date)).toEqual(['2026-09-26', '2026-09-28', '2026-09-30', '2026-10-03']);
    expect(waiting).toEqual([lbl('2026-10-05'), lbl('2026-10-07')]);
    expect(keptRawBlocks.length).toBe(1);
  });

  test('planDiff erases installed workouts outside the window', () => {
    const dates = ['2026-09-24', '2026-09-26', '2026-09-28', '2026-09-30', '2026-10-03', '2026-10-05', '2026-10-07'];
    const existing = decodeApps(fakeAppsRegion(dates.map((d) => lbl(d))));
    const plan = dates.map(run);
    const { keptRawBlocks, toAdd, waiting } = planDiff(existing, plan, today926);
    expect(decodeApps(rebuildAppsRegion(keptRawBlocks)).map((e) => e.name))
      .toEqual(['2026-09-26', '2026-09-28', '2026-09-30', '2026-10-03', '2026-10-05'].map((d) => lbl(d)));
    expect(toAdd).toEqual([]);
    expect(waiting).toEqual([lbl('2026-10-07')]);
  });

  test('planDiff leaves no room when 5 manual workouts fill the menu', () => {
    const existing = decodeApps(fakeAppsRegion(['M0', 'M1', 'M2', 'M3', 'M4']));
    const { toAdd, waiting } = planDiff(existing, [run('2026-09-28')], today926);
    expect(toAdd).toEqual([]);
    expect(waiting).toEqual([lbl('2026-09-28')]);
  });

  test('rebuildAppsRegion round-trips a filtered raw-block list byte-clean', () => {
    const region = fakeAppsRegion([lbl('2026-08-25', 'Long run'), lbl('2026-08-28', 'Interval')]);
    const existing = decodeApps(region);
    const kept = existing.filter((e) => e.name !== lbl('2026-08-25', 'Long run')).map((e) => e.rawBlock);
    const rebuilt = rebuildAppsRegion(kept);
    expect(decodeApps(rebuilt).map((e) => e.name)).toEqual([lbl('2026-08-28', 'Interval')]);
  });

  test('staleKept: an entry edited under the same date and name is reinstalled', () => {
    const today = new Date(2026, 9, 24);
    const label = lbl('2026-10-24', 'Test');
    const existing = decodeApps(fakeAppsRegion([label, 'Couch-to-5K Week1']));
    const { keptRawBlocks } = planDiff(existing, [{ date: '2026-10-24', mode: 'Running', workoutName: 'Test' }], today);
    // fakeAppsRegion wrote [1, 2, 3]; the plan now holds another program -> stale.
    expect(staleKept(existing, keptRawBlocks, () => new Uint8Array([9, 9])).map((e) => e.name)).toEqual([label]);
    // Same program -> left alone (whatever the sport byte); nothing to install yet -> left alone.
    expect(staleKept(existing, keptRawBlocks, () => new Uint8Array([1, 2, 3]))).toEqual([]);
    expect(staleKept(existing, keptRawBlocks, () => null)).toEqual([]);
    // A workout installed by hand (not managed) is never touched.
    expect(staleKept(existing, existing.map((e) => e.rawBlock), (l) => (l === label ? null : new Uint8Array([7]))))
      .toEqual([]);
  });
});

// The PC and the tablet keep separate plans: a sync erases past workouts and its own, and leaves
// another device's upcoming ones - same cases as test_training_calendar.py's PlanDiffOtherDevice.
describe('planDiff with another device on the watch', () => {
  const today = new Date(2026, 9, 31); // 31/10
  const keptNames = (blocks: Uint8Array[]) => decodeApps(rebuildAppsRegion(blocks)).map((e) => e.name);
  const run = (...dates: string[]) => dates.map((d) => ({ date: d, mode: 'Running', workoutName: `W${d.slice(-2)}` }));

  test("another device's upcoming workout stays", () => {
    const w9 = entryLabel('2026-10-31', 'W9 Final Workout');
    const r = planDiff(decodeApps(fakeAppsRegion(['Couch-to-5K', w9])), [], today);
    expect(keptNames(r.keptRawBlocks)).toEqual(['Couch-to-5K', w9]);
    expect(r.othersKept).toEqual([w9]);
  });

  test('past ones go whoever put them there', () => {
    const r = planDiff(decodeApps(fakeAppsRegion([entryLabel('2026-10-24', 'T10s')])), [], today);
    expect(r.keptRawBlocks).toEqual([]);
  });

  test('its own taken out of the plan goes', () => {
    const own = lbl('2026-11-03');
    const r = planDiff(decodeApps(fakeAppsRegion([own])), [], today, undefined, new Set([own]));
    expect(r.keptRawBlocks).toEqual([]);
  });

  test("another device's workouts take menu slots", () => {
    const theirs = ['2026-11-01', '2026-11-02', '2026-11-04'].map((d) => entryLabel(d, 'PC'));
    const r = planDiff(decodeApps(fakeAppsRegion(['Manual', ...theirs])),
      run('2026-11-03', '2026-11-05', '2026-11-07'), today);
    expect(r.keptRawBlocks).toHaveLength(4);
    expect(r.toAdd.map((e) => e.date)).toEqual(['2026-11-03']);
    expect(r.waiting).toEqual([lbl('2026-11-05'), lbl('2026-11-07')]);
  });

  test('the same workout in both plans goes on once', () => {
    const r = planDiff(decodeApps(fakeAppsRegion([lbl('2026-11-03')])), run('2026-11-03'), today);
    expect(r.keptRawBlocks).toHaveLength(1);
    expect(r.toAdd).toEqual([]);
  });

  test('an old-style name is still renamed', () => {
    const r = planDiff(decodeApps(fakeAppsRegion(['03/11_W03'])), run('2026-11-03'), today);
    expect(r.keptRawBlocks).toEqual([]);
    expect(r.toAdd.map((e) => e.date)).toEqual(['2026-11-03']);
  });

  test("the record keeps the plan's and its own, never another device's", () => {
    const own = lbl('2026-11-03');
    const pcs = entryLabel('2026-11-04', 'PC');
    const planned = lbl('2026-11-05');
    expect(installRecord(['Manual', own, pcs, planned], new Set([planned]), new Set([own])))
      .toEqual([own, planned].sort());
  });
});
