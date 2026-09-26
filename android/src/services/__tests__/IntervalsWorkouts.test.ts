// intervals.icu import: zone-1 (walk) floor = the watch's resting HR, like the desktop's
// resolve_zone_band (André's real numbers, 2026-09-26: desktop gave walk 56-157, jog 158-166).
import { convertWorkout, resolveZoneBand, resolveZonesIntoHr } from '../IntervalsWorkouts';

const ZONES = [157, 166, 175, 185, 190, 196, 205]; // intervals.icu Run hr_zones (upper bounds)

describe('IntervalsWorkouts zone floor', () => {
  test('zone 1 floor is the watch resting HR when known, flagged; else the old LTHR estimate', () => {
    expect(resolveZoneBand(1, ZONES, 186, 205, 56)).toEqual([56, 157, true]);
    expect(resolveZoneBand(1, ZONES, 186, 205)).toEqual([126, 157, false]);
    expect(resolveZoneBand(2, ZONES, 186, 205, 56)).toEqual([158, 166, false]);
  });

  test('walk/jog import matches the desktop: 56-157 and 158-166, the rest HR is not rescaled', () => {
    const steps: any[] = [
      { duration: 300, text: 'Walk', hr: { units: 'hr_zone', value: 1 } },
      { duration: 90, text: 'Jog', hr: { units: 'hr_zone', value: 2 } },
    ];
    resolveZonesIntoHr(steps, ZONES, 186, 205, 56);
    const w = convertWorkout({ steps, sportSettings: { max_hr: 205 } }, 'W4', 205, 56);
    const bands = w.steps.map(s => s.target?.valueRange);
    expect(bands).toEqual([{ min: 56, max: 157 }, { min: 158, max: 166 }]);
  });
});
