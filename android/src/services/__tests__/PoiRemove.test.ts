// Removing a POI: a copy first, then the watch is handed the list without it, byte for byte
// what it held otherwise (PoiService.removePoisFromWatch -> AmbitUsbModule.setPoiList).
export {};   // a module: its mocks don't clash with the other test files' names

const mockLog: string[] = [];
const mockFiles = new Map<string, string>();

jest.mock('react-native-fs', () => ({
  DocumentDirectoryPath: '/files',
  CachesDirectoryPath: '/cache',
  mkdir: jest.fn(async () => {}),
  writeFile: jest.fn(async (path: string, data: string) => {
    mockLog.push(`backup ${path}`);
    mockFiles.set(path, data);
  }),
  readDir: jest.fn(async () => []),
  unlink: jest.fn(async () => {}),
}));

jest.mock('@react-native-async-storage/async-storage', () => ({
  getItem: jest.fn(async () => null),
  setItem: jest.fn(async () => {}),
  removeItem: jest.fn(async () => {}),
}));

jest.mock('../AmbitSettingsService', () => ({
  isAmbit12: (name: string) => /Ambit[12]\b/.test(name ?? ''),
}));

const mockWatch = {
  raw: '',                       // what readPoiListRaw returns
  setResult: [] as any[],        // queued results of setPoiList: a number, or an error to throw
  name: 'Suunto Ambit3 Peak',
};

const mockNative = {
  connect: jest.fn(async () => ({ name: mockWatch.name, vendorId: 0x1493, productId: 0x1c })),
  disconnect: jest.fn(async () => {}),
  getDeviceInfo: jest.fn(async () => ({ name: mockWatch.name, serial: '8A153C5111000900' })),
  readPoiListRaw: jest.fn(async () => { mockLog.push('read'); return mockWatch.raw; }),
  setPoiList: jest.fn(async (expected: string, next: string) => {
    mockLog.push(`set ${expected === mockWatch.raw ? 'expected=read' : 'expected=?'}`);
    const r = mockWatch.setResult.shift();
    if (r instanceof Error) throw r;
    mockWatch.raw = next;
    return r;
  }),
};

jest.mock('react-native', () => ({
  NativeModules: { AmbitUsbModule: mockNative },
  NativeEventEmitter: class { addListener() { return { remove() {} }; } },
  Platform: { OS: 'android' },
}));

// Loaded after the mocks above exist (an import would run before them).
const { poiEntries, buildPoiList, poisToKeep, removePoisFromWatch } =
  require('../PoiService') as typeof import('../PoiService');
const { base64ToBytes, bytesToBase64 } = require('../Base64') as typeof import('../Base64');

const enc = (s: string): number[] => Array.from(s, c => c.charCodeAt(0));   // ASCII names
const i32 = (v: number): number[] => {
  const b = new Uint8Array(4);
  new DataView(b.buffer).setInt32(0, Math.round(v * 1e7), true);
  return Array.from(b);
};

// One POI record as the watch stores it (tools/write_nav.py build_poi_record).
function record(name: string, lat: number, lon: number): number[] {
  return [...enc(name), 0, 0, ...enc('2026-10-25T09:00:00'), 0, 0, 17, 0, 0, 1, ...i32(lat), ...i32(lon)];
}

// A raw read: page header (cursor 0, last), "SBEM0102", then one 0x55 entry per body.
function rawList(bodies: number[][]): Uint8Array {
  const out = [0, 0, 0, 0, 1, 0, ...enc('SBEM0102')];
  for (const b of bodies) out.push(0x55, b.length, ...b);
  return Uint8Array.from(out);
}

const A = record('SommetA', 50.6, 3.06);
const B = record('SommetB', 50.61, 3.07);
const HOME = record('Home Test', 45.92, 6.87);

beforeEach(() => {
  mockLog.length = 0;
  mockFiles.clear();
  mockWatch.setResult = [];
  mockWatch.name = 'Suunto Ambit3 Peak';
  jest.clearAllMocks();
});

test('the list without the removed POI keeps every other entry byte for byte, in order', () => {
  const read = rawList([A, [], B, HOME]);
  const entries = poiEntries(read);
  expect(entries.map(e => e.poi?.name ?? null)).toEqual(['SommetA', null, 'SommetB', 'Home Test']);

  const { keep, empty } = poisToKeep(entries, [entries[2].poi!]);
  expect(empty).toBe(1);
  expect(Array.from(buildPoiList(read, keep))).toEqual(Array.from(rawList([A, HOME])));
});

test('two POIs of one name and place: removing one keeps the other', () => {
  const read = rawList([A, A, B]);
  const entries = poiEntries(read);
  const { keep } = poisToKeep(entries, [entries[0].poi!]);
  expect(Array.from(buildPoiList(read, keep))).toEqual(Array.from(rawList([A, B])));
});

test('a POI that is not there stops it, naming the POI', () => {
  const entries = poiEntries(rawList([A]));
  expect(() => poisToKeep(entries, [{ name: 'SommetB', latitude: 50.61, longitude: 3.07 }]))
    .toThrow(/SommetB/);
});

test('removing the last POI hands the watch an empty list', () => {
  const read = rawList([A]);
  const { keep } = poisToKeep(poiEntries(read), [poiEntries(read)[0].poi!]);
  expect(buildPoiList(read, keep).length).toBe(0);
});

test('removePoisFromWatch saves the copy before the watch is asked to change anything', async () => {
  mockWatch.raw = bytesToBase64(rawList([A, [], B]));
  const before = mockWatch.raw;
  mockWatch.setResult = [1];

  const r = await removePoisFromWatch([{ name: 'SommetA', latitude: 50.6, longitude: 3.06 }]);

  expect(r).toMatchObject({ left: 1, emptyCleared: 1 });
  expect(r.backupPath).toMatch(/^\/files\/backups\/POIs\/8A153C5111000900\/POIs_pre_.*\.bin$/);
  expect(mockFiles.get(r.backupPath)).toBe(before);
  expect(mockLog).toEqual(['read', `backup ${r.backupPath}`, 'set expected=read']);
  expect(Array.from(base64ToBytes(mockWatch.raw))).toEqual(Array.from(rawList([B])));
});

test('a POI no longer on the watch writes nothing', async () => {
  mockWatch.raw = bytesToBase64(rawList([B]));
  await expect(removePoisFromWatch([{ name: 'SommetA', latitude: 50.6, longitude: 3.06 }]))
    .rejects.toThrow(/SommetA/);
  expect(mockNative.setPoiList).not.toHaveBeenCalled();
});

test('a list that changed on the watch meanwhile is reported, not retried', async () => {
  mockWatch.raw = bytesToBase64(rawList([A, B]));
  mockWatch.setResult = [Object.assign(new Error('changed'), { code: 'POI_LIST_CHANGED' })];
  await expect(removePoisFromWatch([{ name: 'SommetA', latitude: 50.6, longitude: 3.06 }]))
    .rejects.toThrow();
  expect(mockNative.setPoiList).toHaveBeenCalledTimes(1);
});

test('a failed write puts the copy back', async () => {
  const before = bytesToBase64(rawList([A, B]));
  mockWatch.raw = before;
  mockWatch.setResult = [Object.assign(new Error('link lost'), { code: 'POI_SET_FAILED' }), 2];
  await expect(removePoisFromWatch([{ name: 'SommetA', latitude: 50.6, longitude: 3.06 }]))
    .rejects.toThrow(/link lost/);
  expect(mockNative.setPoiList).toHaveBeenCalledTimes(2);
  expect(mockNative.setPoiList.mock.calls[1][1]).toBe(before);
  expect(mockWatch.raw).toBe(before);
});

test('Ambit1/2: not offered, nothing read or written', async () => {
  mockWatch.name = 'Suunto Ambit2';
  await expect(removePoisFromWatch([{ name: 'X', latitude: 0, longitude: 0 }])).rejects.toThrow();
  expect(mockNative.readPoiListRaw).not.toHaveBeenCalled();
  expect(mockNative.setPoiList).not.toHaveBeenCalled();
});
