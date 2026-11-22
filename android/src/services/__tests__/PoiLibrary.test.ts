// The POI library's rules give what the desktop's give (tools/poi_library.py): one file of
// cases, written by `python3 tools/test_poi_library.py --write-fixture`, is checked on both
// sides. Then the file side, with the phone's storage held in memory.

const files: Record<string, string> = {};
jest.mock('react-native-fs', () => ({
  DocumentDirectoryPath: '/docs',
  exists: jest.fn(async (p: string) => p in files || Object.keys(files).some(k => k.startsWith(p + '/'))),
  mkdir: jest.fn(async () => {}),
  readFile: jest.fn(async (p: string) => {
    if (!(p in files)) throw new Error('ENOENT ' + p);
    return files[p];
  }),
  writeFile: jest.fn(async (p: string, text: string) => { files[p] = text; }),
  unlink: jest.fn(async (p: string) => { delete files[p]; }),
  moveFile: jest.fn(async (from: string, to: string) => { files[to] = files[from]; delete files[from]; }),
}));

import {
  keepInto, listFrom, PoiLibraryData, listLibraryPois, saveLibraryPois, renameLibraryPoi,
  deleteLibraryPoi, countKept,
} from '../PoiLibrary';
import { poiTypeBetween, PoiTypeList } from '../PoiTypes';

const fixture = require('./poiLibrary.fixture.json');
const INDEX = '/docs/poi_library/index.json';

describe('the rules, against the desktop', () => {
  test('each save is answered as the desktop answers it', () => {
    const data: PoiLibraryData = { version: 1, pois: [], deleted: [] };
    let n = 0;
    const results = keepInto(data, fixture.saves, 1000, () => `id${n++}`);
    const entryOf: Record<string, number> = {};
    const answers = results.map(r => {
      if (!r.ok) return { ok: false, error: r.error };
      if (!(r.id in entryOf)) entryOf[r.id] = Object.keys(entryOf).length;
      return { ok: true, entry: entryOf[r.id], existed: r.existed };
    });
    expect(answers).toEqual(fixture.answers);

    for (const list of ['ambit', 'traverse'] as PoiTypeList[]) {
      const listed = listFrom(data, list).map(p => ({
        name: p.name, lat: p.lat, lon: p.lon, ele: p.ele, type: p.type, typeList: p.typeList,
        typeHere: p.typeHere,
      }));
      expect(listed).toEqual(fixture.listed[list]);
    }
  });

  test('a type goes to the other kind of watch by its icon, as on the desktop', () => {
    for (const from of ['ambit', 'traverse'] as PoiTypeList[]) {
      for (const to of ['ambit', 'traverse'] as PoiTypeList[]) {
        const got = [];
        for (let id = -1; id <= 50; id++) got.push(poiTypeBetween(id, from, to));
        expect(got).toEqual(fixture.typeBetweenFromMinus1To50[`${from}>${to}`]);
      }
    }
    expect(poiTypeBetween(null, 'ambit', 'traverse')).toBe(26);
    expect(poiTypeBetween(undefined, 'traverse', 'ambit')).toBe(17);
  });
});

describe('the file', () => {
  beforeEach(() => { for (const k of Object.keys(files)) delete files[k]; });

  test('save, list, rename, delete', async () => {
    const first = await saveLibraryPois([
      { name: 'Hut', lat: 46.5, lon: 7.5, type: 10 },
      { name: 'Lake', lat: 46.6, lon: 7.5, type: 16 },
    ]);
    expect(countKept(first)).toEqual({ saved: 2, existed: 0, failed: null });
    const again = await saveLibraryPois([{ name: 'hut', lat: 46.5, lon: 7.5 }, { name: '', lat: 1, lon: 1 }]);
    expect(countKept(again)).toEqual({ saved: 0, existed: 1, failed: 'a POI needs a name' });

    let list = await listLibraryPois('traverse');
    expect(list.map(p => [p.name, p.type, p.typeHere])).toEqual([['Hut', 10, 9], ['Lake', 16, 12]]);

    const hut = list[0];
    expect(await renameLibraryPoi(hut.id, '  Cabane ')).toBe(true);
    expect(await renameLibraryPoi(hut.id, '   ')).toBe(false);
    expect(await deleteLibraryPoi(list[1].id)).toBe(true);
    expect(await deleteLibraryPoi(list[1].id)).toBe(false);
    list = await listLibraryPois();
    expect(list.map(p => p.name)).toEqual(['Cabane']);
    expect(list[0].typeHere).toBeUndefined();

    const onDisk = JSON.parse(files[INDEX]);
    expect(onDisk.version).toBe(1);
    expect(onDisk.deleted.map((d: any) => d.id)).toHaveLength(1);       // remembered
    expect(onDisk.pois[0].id).toMatch(/^[0-9a-f]{32}$/);
  });

  test('two saves started together both land', async () => {
    await Promise.all([
      saveLibraryPois([{ name: 'A', lat: 1, lon: 1 }]),
      saveLibraryPois([{ name: 'B', lat: 2, lon: 2 }]),
    ]);
    expect((await listLibraryPois()).map(p => p.name)).toEqual(['A', 'B']);
  });

  test('a damaged file reads as empty', async () => {
    files[INDEX] = '{not json';
    expect(await listLibraryPois()).toEqual([]);
    expect(countKept(await saveLibraryPois([{ name: 'X', lat: 1, lon: 1 }])).saved).toBe(1);
  });

  test('the desktop\'s file is read as it is', async () => {
    files[INDEX] = JSON.stringify({ version: 1, deleted: [], pois: [
      { id: 'ee4717aba40d4470ba4fd7ce6fe23dff', name: 'Refuge du Goûter', lat: 45.8512, lon: 6.8306,
        ele: 3835.0, type: 10, typeList: 'ambit', created: 1795340000, updated: 1795340000 },
    ] });
    const [p] = await listLibraryPois('ambit');
    expect([p.name, p.ele, p.typeHere]).toEqual(['Refuge du Goûter', 3835, 10]);
  });
});
