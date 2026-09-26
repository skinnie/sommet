import { entryLabel, isManaged, isExpired, planDiff, rebuildAppsRegion } from '../TrainingCalendarCore';
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

describe('TrainingCalendarCore', () => {
  test('entryLabel formats dd/mm_name and truncates to fit NAME_LEN', () => {
    expect(entryLabel('2026-08-25', 'Long run')).toBe('25/08_Long run');
    expect(entryLabel('2026-01-05', 'x'.repeat(40)).startsWith('05/01_')).toBe(true);
  });

  test('isManaged recognizes only the dd/mm_ prefix this tool writes', () => {
    expect(isManaged('25/08_Long run')).toBe(true);
    expect(isManaged('Couch-to-5K Week1')).toBe(false);
  });

  test('isExpired compares (month, day) against today, ignoring managed-status of other names', () => {
    const today = new Date(2026, 7, 27); // 2026-08-27 (Date months are 0-based)
    expect(isExpired('25/08_Long run', today)).toBe(true);
    expect(isExpired('28/08_Interval', today)).toBe(false);
    expect(isExpired('Couch-to-5K', today)).toBe(false);
  });

  test('planDiff drops expired managed entries, keeps the rest, and finds what still needs installing', () => {
    const region = fakeAppsRegion(['25/08_Long run', '28/08_Interval']);
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

  test('planDiff erases a still-future managed entry that was removed from the plan', () => {
    const region = fakeAppsRegion(['Couch-to-5K', '28/08_Interval', '30/08_Tempo']);
    const existing = decodeApps(region);
    const today = new Date(2026, 7, 27);
    const plan = [{ date: '2026-08-30', mode: 'Running', workoutName: 'Tempo' }];  // 28/08 removed
    const { keptRawBlocks, toAdd } = planDiff(existing, plan, today);
    const keptNames = existing.filter((e) => keptRawBlocks.includes(e.rawBlock)).map((e) => e.name);
    expect(keptNames).toEqual(['Couch-to-5K', '30/08_Tempo']);  // unmanaged app always kept
    expect(toAdd).toEqual([]);
  });

  test('rebuildAppsRegion round-trips a filtered raw-block list byte-clean', () => {
    const region = fakeAppsRegion(['25/08_Long run', '28/08_Interval']);
    const existing = decodeApps(region);
    const kept = existing.filter((e) => e.name !== '25/08_Long run').map((e) => e.rawBlock);
    const rebuilt = rebuildAppsRegion(kept);
    expect(decodeApps(rebuilt).map((e) => e.name)).toEqual(['28/08_Interval']);
  });
});
