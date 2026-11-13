// Testing mode's sample data in the mobile app: the desktop's own sample moves and POIs, a
// Hunting move with shots for a sample Traverse Alpha, and a POI list that only lives in memory.
export {};

const mockFiles = new Map<string, string>();
jest.mock('react-native-fs', () => ({
  CachesDirectoryPath: '/cache', DocumentDirectoryPath: '/files',
  mkdir: jest.fn(async () => {}),
  writeFile: jest.fn(async (path: string, data: string) => { mockFiles.set(path, data); }),
}));
jest.mock('fast-xml-parser', () => jest.requireActual('fast-xml-parser'));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(async () => null), setItem: jest.fn(async () => {}), removeItem: jest.fn(async () => {}),
}));
jest.mock('../../native/AmbitUsbModule', () => ({}));
jest.mock('../AmbitSettingsService', () => ({ isAmbit12: () => false }));
jest.mock('../WatchBackup', () => ({ saveWatchBackup: jest.fn() }));
jest.mock('../../database/db', () => ({}));

import { demoMoves, demoMoveGpxs, demoPois, demoAddPoi, demoRemovePoi, isDemoActivity } from '../DemoData';
import { poiTypeOf } from '../PoiTypes';

test('a sample Peak has the three sample moves, a sample Traverse Alpha a Hunting move too', async () => {
  expect(demoMoveGpxs('Emu')).toHaveLength(3);
  expect(demoMoveGpxs('Jabiru')).toHaveLength(3);
  const alpha = await demoMoves('Loon');
  expect(alpha).toHaveLength(4);
  const hunt = alpha.find(m => m.activity.activity_type === 'Hunting')!;
  expect(hunt.gpx.shots).toBe(3);
  expect(hunt.gpx.shotPoints).toHaveLength(3);
  expect(isDemoActivity(hunt.activity.id)).toBe(true);
  expect(hunt.activity.device).toBe('demo');
  expect(mockFiles.get(hunt.activity.gpx_path)).toContain('<shots>3</shots>');   // opens like any move
});

test('a sample move shows the ascent the watch stored, like the desktop', async () => {
  const hunt = (await demoMoves('Loon', 'hunting'))[0];           // the filter ignores capitals
  expect(hunt.activity.d_plus).toBe(124);
  expect(hunt.activity.distance_m).toBe(4830);
  expect(hunt.activity.duration_s).toBe(7200);
});

test('the sport filter applies to the sample moves', async () => {
  expect((await demoMoves('Loon', 'Hunting')).map(m => m.activity.activity_type)).toEqual(['Hunting']);
  expect(await demoMoves('Emu', 'Hunting')).toEqual([]);
});

test('sample POIs keep their icon on a sample Traverse', () => {
  const peak = demoPois('Emu');
  const alpha = demoPois('Loon');
  expect(peak.map(p => p.name)).toEqual(alpha.map(p => p.name));
  peak.forEach((p, i) => {
    expect(poiTypeOf(alpha[i].type, 'Loon').glyph).toBe(poiTypeOf(p.type, 'Emu').glyph);
  });
});

test('adding and removing a sample POI changes only the list in memory', () => {
  const before = demoPois('Loon').length;
  const hide = { name: 'Hide', latitude: 46.02, longitude: 7.75, type: 43 };
  expect(demoAddPoi('Loon', hide)[0]).toEqual(hide);
  expect(demoPois('Loon')).toHaveLength(before + 1);
  expect(demoRemovePoi('Loon', hide)).toHaveLength(before);
  expect(demoPois('Emu')).toHaveLength(before);            // another sample watch: its own fresh list
});
