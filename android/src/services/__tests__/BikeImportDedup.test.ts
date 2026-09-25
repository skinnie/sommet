// The desktop's "already have this ride" rule (ActivityService::importBikeActivitiesInto).
jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn() }));
jest.mock('../../database/db', () => ({ getAllActivities: jest.fn(async () => []), getDeletedIds: jest.fn(async () => []) }));
jest.mock('../GpxService', () => ({ activityExists: jest.fn(async () => false) }));
import { BikeImportDedup } from '../BikeImportDedup';

describe('bike ride dedup', () => {
  const d = new BikeImportDedup();
  d.add('x', 1_000_000, 3600);            // an existing 1 h ride
  it('same start within 5 min = same ride', () => {
    expect(d.hasRide(1_000_000 + 290, null)).toBe(true);
    expect(d.hasRide(1_000_000 + 310, null)).toBe(false);
  });
  it('whole-hour offset only with a comparable duration', () => {
    expect(d.hasRide(1_000_000 + 3600 + 60, 3500)).toBe(true);
    expect(d.hasRide(1_000_000 + 7200 - 100, 4000)).toBe(true);
    expect(d.hasRide(1_000_000 + 3600, 600)).toBe(false);     // 10 min vs 1 h: different ride
    expect(d.hasRide(1_000_000 + 3600, null)).toBe(false);    // duration unknown: not assumed
  });
  it('seen / deleted ids are never re-imported', async () => {
    d.markSeen('c406_1');
    expect(await d.hasId('c406_1')).toBe(true);
    expect(await d.hasId('c406_2')).toBe(false);
  });
});
