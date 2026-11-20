const mockRun = jest.fn(async () => ({ pulled: 0, pushed: 0, ok: true }));
const mockGear = jest.fn(async () => ({ pulled: 0, pushed: 0, ok: true }));
jest.mock('react-native', () => ({
  AppState: { currentState: 'active', addEventListener: jest.fn(() => ({ remove() {} })) },
}));
jest.mock('../SommetSync', () => ({ runSommetSync: () => mockRun() }));
jest.mock('../SommetGearSync', () => ({ runSommetGearSync: () => mockGear() }));
jest.mock('../LightLibrary', () => ({ trimOldTracks: async () => {} }));
const mockTiles = jest.fn(async () => 0);
jest.mock('../RecentTiles', () => ({ downloadRecentTiles: () => mockTiles(), startRecentTiles: () => () => {} }));

import { AppState } from 'react-native';
import { syncNowIfDue, startSommetSyncOnOpen, _resetSommetSyncOnOpen, MIN_GAP_MS, FIRST_DELAY_MS } from '../SommetSyncOnOpen';

beforeEach(() => { _resetSommetSyncOnOpen(); mockRun.mockClear(); mockGear.mockClear(); mockTiles.mockClear(); });

test('a sync that pulled moves refreshes the recent maps; one that pulled nothing does not', async () => {
  await syncNowIfDue(10);
  expect(mockTiles).not.toHaveBeenCalled();
  _resetSommetSyncOnOpen();
  mockRun.mockResolvedValueOnce({ pulled: 3, pushed: 0, ok: true });
  await syncNowIfDue(20);
  expect(mockTiles).toHaveBeenCalledTimes(1);
});

test('syncs activities and gear, then waits the gap before syncing again', async () => {
  expect(await syncNowIfDue(1_000_000)).toBe(true);
  expect(mockRun).toHaveBeenCalledTimes(1);
  expect(mockGear).toHaveBeenCalledTimes(1);
  expect(await syncNowIfDue(1_000_000 + MIN_GAP_MS - 1)).toBe(false);
  expect(await syncNowIfDue(1_000_000 + MIN_GAP_MS)).toBe(true);
  expect(mockRun).toHaveBeenCalledTimes(2);
});

test('a failed sync does not throw', async () => {
  mockRun.mockRejectedValueOnce(new Error('offline'));
  await expect(syncNowIfDue(5)).resolves.toBe(true);
  expect(mockGear).toHaveBeenCalledTimes(1);
});

test('syncs shortly after launch and when the app comes back to the front', async () => {
  jest.useFakeTimers();
  let onChange: (s: string) => void = () => {};
  (AppState.addEventListener as jest.Mock).mockImplementation((_e, cb) => { onChange = cb; return { remove() {} }; });
  const stop = startSommetSyncOnOpen();
  expect(mockRun).not.toHaveBeenCalled();
  jest.advanceTimersByTime(FIRST_DELAY_MS);
  await Promise.resolve(); await Promise.resolve();
  expect(mockRun).toHaveBeenCalledTimes(1);
  _resetSommetSyncOnOpen();
  onChange('background'); onChange('active');
  await Promise.resolve();
  expect(mockRun).toHaveBeenCalledTimes(2);
  stop();
  jest.useRealTimers();
});
