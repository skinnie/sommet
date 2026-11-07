// The desktop's "already have this ride" rule (ActivityService::importBikeActivitiesInto).
jest.mock('@react-native-async-storage/async-storage', () => ({ getItem: jest.fn(async () => null), setItem: jest.fn() }));
jest.mock('../../database/db', () => ({ getAllActivities: jest.fn(async () => []), getDeletedIds: jest.fn(async () => []) }));
jest.mock('../GpxService', () => ({ activityExists: jest.fn(async () => false) }));
import { BikeImportDedup } from '../BikeImportDedup';

describe('bike ride dedup', () => {
  const d = new BikeImportDedup();
  d.add('x', 1_000_000, 3600);            // an existing 1 h ride
  it('same UTC start within 3 min = same ride (before the download, start only)', () => {
    expect(d.hasRide(1_000_000 + 170, null)).toBe(true);
    expect(d.hasRide(1_000_000 + 190, null)).toBe(false);
  });
  it('after the download, distance or duration must agree too (SameMove.ts)', () => {
    const e = new BikeImportDedup();
    e.add('y', 2_000_000, 3600, 30_000);
    expect(e.hasRide(2_000_000 + 60, 2400, 30_200)).toBe(true);    // pauses differ, distance agrees
    expect(e.hasRide(2_000_000 + 60, 3600, 20_000)).toBe(false);   // a different ride
    expect(e.hasRide(2_000_000 + 3600, 3600, 30_000)).toBe(true);  // exactly 1 h: a clock in the wrong zone
    expect(e.hasRide(2_000_000 + 3630, 3600, 30_000)).toBe(false); // 1 h and 30 s: a different ride
  });
  it('seen / deleted ids are never re-imported', async () => {
    d.markSeen('c406_1');
    expect(await d.hasId('c406_1')).toBe(true);
    expect(await d.hasId('c406_2')).toBe(false);
  });
});
