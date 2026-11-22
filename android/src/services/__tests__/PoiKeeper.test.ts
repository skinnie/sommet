// A route write empties the watch's POIs and puts them back. These check the safety net around
// it (PoiKeeper.ts): a copy on the phone first, a second put-back when the first did not hold,
// and an error that names the copy when nothing works. The watch here is a small stand-in.
export {};

jest.mock('react-native-fs', () => ({ DocumentDirectoryPath: '/files', CachesDirectoryPath: '/cache' }));
jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(async () => null), setItem: jest.fn(async () => {}), removeItem: jest.fn(async () => {}),
}));
jest.mock('../../native/AmbitUsbModule', () => ({}));
jest.mock('../AmbitSettingsService', () => ({ isAmbit12: () => false }));
jest.mock('../WatchBackup', () => ({ saveWatchBackup: jest.fn() }));

import { writeKeepingPois, poiRecords } from '../PoiKeeper';
import { bytesToBase64 } from '../Base64';

/** A 0x0b24 reply: 6 header bytes, "SBEM0102", one 0x55 entry per record ('' = an empty entry). */
function list(records: string[]): string {
  if (records.length === 0) return '';
  const out: number[] = [0, 0, 0, 0, 1, 0, ...Array.from('SBEM0102').map(c => c.charCodeAt(0))];
  for (const r of records) out.push(0x55, r.length, ...Array.from(r).map(c => c.charCodeAt(0)));
  return bytesToBase64(Uint8Array.from(out));
}

/** The stand-in watch: its POI list, and what was asked of it. */
function watch(initial: string[]) {
  const w = {
    pois: initial.slice(),
    calls: [] as string[],
    readFails: 0,
    setFails: false,
    deps: {
      readPoiListRaw: async () => {
        w.calls.push('read');
        if (w.readFails > 0) { w.readFails--; throw new Error('link lost'); }
        return list(w.pois);
      },
      setPoiListRaw: async (_expected: string, next: string) => {
        w.calls.push('set');
        if (w.setFails) throw new Error('the watch refused');
        w.pois = ['Hut', 'Lake', 'Col'].filter(n => poiRecords(next).some(r => r === poiRecords(list([n]))[0]));
        return w.pois.length;
      },
      saveCopy: async (_b64: string) => { w.calls.push('copy'); return '/backups/POIs/x.bin'; },
    },
  };
  return w;
}

test('a write that puts the POIs back: a copy was saved first, nothing else is done', async () => {
  const w = watch(['Hut', 'Lake']);
  const r = await writeKeepingPois(async () => { w.calls.push('write'); }, w.deps);
  expect(w.calls).toEqual(['read', 'copy', 'write', 'read']);
  expect(r).toEqual({ count: 2, copyPath: '/backups/POIs/x.bin', putBackAgain: false });
});

test('no POIs on the watch: no copy, no second read', async () => {
  const w = watch([]);
  const r = await writeKeepingPois(async () => { w.calls.push('write'); }, w.deps);
  expect(w.calls).toEqual(['read', 'write']);
  expect(r).toEqual({ count: 0, copyPath: null, putBackAgain: false });
});

test('the write left the POIs empty: the copy is written back once', async () => {
  const w = watch(['Hut', 'Lake']);
  const r = await writeKeepingPois(async () => { w.calls.push('write'); w.pois = []; }, w.deps);
  expect(w.calls).toEqual(['read', 'copy', 'write', 'read', 'set']);
  expect(w.pois).toEqual(['Hut', 'Lake']);
  expect(r.putBackAgain).toBe(true);
});

test('the write put back only some: the copy is written back', async () => {
  const w = watch(['Hut', 'Lake', 'Col']);
  await writeKeepingPois(async () => { w.pois = ['Hut']; }, w.deps);
  expect(w.pois).toEqual(['Hut', 'Lake', 'Col']);
});

test('an empty entry in the list is not a missing POI', async () => {
  const w = watch(['Hut', '', 'Lake']);
  const r = await writeKeepingPois(async () => { w.pois = ['Hut', 'Lake']; }, w.deps);
  expect(w.calls).not.toContain('set');
  expect(r.count).toBe(2);
});

test('the write failed before touching the POIs: its own error, nothing written back', async () => {
  const w = watch(['Hut']);
  await expect(writeKeepingPois(async () => { throw new Error('data chunk refused'); }, w.deps))
    .rejects.toThrow('data chunk refused');
  expect(w.calls).toEqual(['read', 'copy', 'read']);
  expect(w.pois).toEqual(['Hut']);
});

test('the write failed after emptying the POIs: they are put back and the error says so', async () => {
  const w = watch(['Hut']);
  await expect(writeKeepingPois(async () => { w.pois = []; throw new Error('commit failed'); }, w.deps))
    .rejects.toThrow(/commit failed.*put back from the copy saved in \/backups\/POIs\/x\.bin/);
  expect(w.pois).toEqual(['Hut']);
});

test('nothing works: the error names the copy', async () => {
  const w = watch(['Hut']);
  w.setFails = true;
  await expect(writeKeepingPois(async () => { w.pois = []; }, w.deps))
    .rejects.toThrow(/could not be put back \(the watch refused\)\. A copy of them is saved in \/backups\/POIs\/x\.bin/);
});

test('the link is gone after the write: the copy is still named', async () => {
  const w = watch(['Hut']);
  await expect(writeKeepingPois(async () => { w.pois = []; w.readFails = 5; }, w.deps))
    .rejects.toThrow(/A copy of them is saved in \/backups\/POIs\/x\.bin/);
});

test('the POIs cannot be read first: nothing is written', async () => {
  const w = watch(['Hut']);
  w.readFails = 1;
  let wrote = false;
  await expect(writeKeepingPois(async () => { wrote = true; }, w.deps)).rejects.toThrow(/nothing was written/);
  expect(wrote).toBe(false);
});
