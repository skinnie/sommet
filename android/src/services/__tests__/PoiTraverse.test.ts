// POIs of a Traverse / Traverse Alpha: their own entry number (0x53, with an altitude after the
// longitude) and their own 48 types. The reply read here is built by hand in the watch's form (one
// entry per POI, as tools/test_traverse_poi_link.py builds it); the reply the emulated Traverse
// Alpha gave is not part of this repository. Not seen on a real Traverse.
export {};

jest.mock('react-native-fs', () => ({ DocumentDirectoryPath: '/files', CachesDirectoryPath: '/cache' }));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(async () => null), setItem: jest.fn(async () => {}), removeItem: jest.fn(async () => {}),
}));
jest.mock('../../native/AmbitUsbModule', () => ({}));
jest.mock('../AmbitSettingsService', () => ({ isAmbit12: () => false }));
jest.mock('../WatchBackup', () => ({ saveWatchBackup: jest.fn() }));

import { poiEntries, buildPoiList } from '../PoiService';
import {
  AMBIT_POI_TYPES, TRAVERSE_POI_TYPES, isTraverseWatch, poiTypesFor, poiTypeDefaultFor, poiTypeOf,
} from '../PoiTypes';

/** A 0x0b24 reply as a Traverse sends it: 6 header bytes, "SBEM0102", one 0x53 entry per POI. */
function traverseReply(pois: { name: string; type: number; lat: number; lon: number; altitude: number }[]): Uint8Array {
  const out: number[] = [0, 0, 0, 0, 1, 0, ...Array.from('SBEM0102').map(c => c.charCodeAt(0))];
  const text = (t: string) => [...Array.from(t).map(c => c.charCodeAt(0)), 0];
  const le = (v: number, bytes: number) => Array.from({ length: bytes }, (_, i) => (v >> (8 * i)) & 0xff);
  for (const p of pois) {
    const record = [...text(p.name), 0, ...text('2026-11-08T07:00:30'), 0, p.type, 0, 1, 1,
      ...le(Math.round(p.lat * 1e7), 4), ...le(Math.round(p.lon * 1e7), 4), ...le(p.altitude, 2)];
    out.push(0x53, record.length, ...record);
  }
  return Uint8Array.from(out);
}

// Two POIs as a Traverse Alpha's own Hunting shortlist names them.
const REPLY = traverseReply([
  { name: 'Shot 01', type: 46, lat: 46.0207, lon: 7.7491, altitude: 1612 },
  { name: 'Rub 01', type: 39, lat: 46.0207, lon: 7.7491, altitude: 1612 },
]);

test('both POIs are read from the 0x53 entries, with their type', () => {
  const entries = poiEntries(REPLY);
  expect(entries.map(e => e.id)).toEqual([0x53, 0x53]);
  expect(entries.map(e => e.poi)).toEqual([
    { name: 'Shot 01', latitude: 46.0207, longitude: 7.7491, type: 46 },
    { name: 'Rub 01', latitude: 46.0207, longitude: 7.7491, type: 39 },
  ]);
});

test('a type byte is named in the list of the watch it came from', () => {
  expect(poiTypeOf(46, 'Loon').name).toBe('Shot');
  expect(poiTypeOf(39, 'Loon').name).toBe('Rub');
  expect(poiTypeOf(2, 'Jabiru').name).toBe('Car');
  expect(poiTypeOf(2, 'Emu').name).toBe('Camp');
  expect(poiTypeOf(200, 'Loon').name).toBe('POI');
  expect(poiTypeOf(undefined, 'Emu').name).toBe('Waypoint');
});

test('the picker offers the 48 Traverse types only for a Traverse', () => {
  expect(isTraverseWatch('Loon')).toBe(true);
  expect(isTraverseWatch('Suunto Traverse Alpha')).toBe(true);
  expect(isTraverseWatch('Emu')).toBe(false);
  expect(isTraverseWatch('')).toBe(false);
  expect(poiTypesFor('Jabiru')).toBe(TRAVERSE_POI_TYPES);
  expect(poiTypesFor('Suunto Ambit3 Peak')).toBe(AMBIT_POI_TYPES);
  expect(TRAVERSE_POI_TYPES).toHaveLength(48);
  expect(poiTypeDefaultFor('Loon')).toBe(26);
  expect(poiTypeDefaultFor('Emu')).toBe(17);
  expect(TRAVERSE_POI_TYPES.filter(t => t.set === 'hunting').map(t => t.name).sort()).toEqual(
    ['Bedding', 'Big game', 'Bird', 'Prints', 'Rub', 'Scrape', 'Shot', 'Small game', 'Stand', 'Trail cam']);
});

test('a list written back keeps the entry number each POI came in', () => {
  const bytes = REPLY;
  const entries = poiEntries(bytes);
  const kept = buildPoiList(bytes, [entries[1]]);            // "Shot 01" removed
  expect(kept[14]).toBe(0x53);
  expect(poiEntries(kept).map(e => e.poi?.name)).toEqual(['Rub 01']);
  expect(Array.from(kept.subarray(16))).toEqual(Array.from(bytes.subarray(entries[1].start, entries[1].end)));
});
