import {
  sportNameForIntervalsType, sportNameForGarminType, humanizeSportType, canonicalSportName,
  isFootSport, isJunkActivity,
} from '../SportNames';
import { ACTIVITY_TYPES } from '../ActivityColors';

const table = require('../../config/sport_names.json');
const SUUNTO = new Set(Object.values(ACTIVITY_TYPES).map(a => a.name));

describe('SportNames', () => {
  it('maps every table entry onto one of Suunto\'s own sport names', () => {
    for (const sec of ['intervals', 'garmin', 'aliases'])
      for (const [k, v] of Object.entries(table[sec]))
        expect([k, SUUNTO.has(v as string)]).toEqual([k, true]);
    for (const f of table.foot) expect([f, SUUNTO.has(f)]).toEqual([f, true]);
  });

  it('names the two types the tablet showed raw (2026-10-03)', () => {
    expect(sportNameForIntervalsType('EMountainBikeRide')).toBe('Mountain biking');
    expect(sportNameForIntervalsType('HighIntensityIntervalTraining')).toBe('Circuit training');
    expect(sportNameForIntervalsType('VirtualRide')).toBe('Indoor cycling');
    expect(sportNameForGarminType('open_water_swimming')).toBe('Openwater swimming');
  });

  it('never shows an unknown type raw', () => {
    expect(humanizeSportType('SomeNewSportType')).toBe('Some new sport type');
    expect(humanizeSportType('resort_skiing')).toBe('Resort skiing');
    expect(sportNameForIntervalsType('')).toBe('Unspecified sport');
    expect(sportNameForIntervalsType('WheelchairRace')).toBe('Wheelchair race');
  });

  it('canonicalises stored names without touching real Suunto ones', () => {
    expect(canonicalSportName('Rowing')).toBe('Rowing');               // a watch sport, not icu's
    expect(canonicalSportName('Trekking')).toBe('Trekking');
    expect(canonicalSportName('Indoor training ')).toBe('Indoor training');
    expect(canonicalSportName('Trail Running')).toBe('Trail running');
    expect(canonicalSportName('EMountainBikeRide')).toBe('Mountain biking');
    expect(canonicalSportName('Swimming')).toBe('Pool swimming');
    expect(canonicalSportName('My custom mode')).toBe('My custom mode');
    expect(canonicalSportName('')).toBe('');
  });

  it('pace is for foot sports only', () => {
    expect(isFootSport('Running')).toBe(true);
    expect(isFootSport('hiking')).toBe(true);
    expect(isFootSport('Cycling')).toBe(false);
    expect(isFootSport('Mountain biking')).toBe(false);
    expect(isFootSport('Indoor cycling')).toBe(false);
  });

  it('junk = under two minutes AND under 100 m', () => {
    expect(isJunkActivity(21, 0)).toBe(true);
    expect(isJunkActivity(90, 0)).toBe(true);
    expect(isJunkActivity(50, 300)).toBe(false);
    expect(isJunkActivity(120, 0)).toBe(false);
    expect(isJunkActivity(600, 0)).toBe(false);
  });
});
