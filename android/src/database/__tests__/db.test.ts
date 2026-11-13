// getDb(): one open + setup however many callers ask at once, and an ADD COLUMN only for a column
// the table doesn't have (an existing one made SQLite log an error on every start).
const mockSql: string[] = [];
const mockColumns: Record<string, string[]> = {
  activities: ['id', 'activity_type', 'device', 'updated_at', 'start_utc'],   // energy_kcal missing
  gear: ['id', 'starting_distance_m', 'starting_time_s', 'baseline_at'],
  gear_reminder: ['id', 'distance_m', 'time_s', 'days', 'activities', 'percent_used', 'snoozed_until',
                  'remote_id', 'starting_distance_m', 'starting_time_s', 'starting_activities', 'last_reset'],
  activity_gear: ['activity_id', 'distance_m', 'time_s', 'activity_date'],
  // a metrics cache made before the Shots column (Traverse Alpha, 2026-11-13)
  activity_metrics: ['id', 'synced_at', 'json', 'descent', 'calories', 'avgHr', 'maxHr', 'avgCadence',
                     'maxCadence', 'avgSpeed', 'maxSpeed', 'recovery', 'peakTe', 'maxAltitude',
                     'poolLengths', 'pace'],
};
const mockOpen = jest.fn(async () => ({
  executeSql: jest.fn(async (sql: string) => {
    mockSql.push(sql.trim());
    const m = /^PRAGMA table_info\((\w+)\)/.exec(sql.trim());
    const names = m ? mockColumns[m[1]] ?? [] : [];
    return [{ rows: { length: names.length, item: (i: number) => ({ name: names[i] }) }, rowsAffected: 0 }];
  }),
}));
jest.mock('react-native-sqlite-storage', () => ({
  __esModule: true,
  default: { enablePromise: jest.fn(), openDatabase: (...a: unknown[]) => mockOpen(...(a as [])) },
}));
jest.mock('react-native-fs', () => ({ DocumentDirectoryPath: '/data/user/0/app/files' }));

import { getDb } from '../db';

test('callers at start-up share one open and one setup', async () => {
  const [a, b, c] = await Promise.all([getDb(), getDb(), getDb()]);
  expect(mockOpen).toHaveBeenCalledTimes(1);
  expect(a).toBe(b);
  expect(b).toBe(c);
  expect(mockSql.filter(s => s.startsWith('PRAGMA table_info(activities)'))).toHaveLength(1);
  expect(await getDb()).toBe(a);
  expect(mockOpen).toHaveBeenCalledTimes(1);
});

test('only the missing columns are added', () => {
  const alters = mockSql.filter(s => s.startsWith('ALTER TABLE'));
  expect(alters).toEqual(['ALTER TABLE activities ADD COLUMN energy_kcal INTEGER NOT NULL DEFAULT 0',
                          'ALTER TABLE activity_metrics ADD COLUMN shots REAL']);
});
