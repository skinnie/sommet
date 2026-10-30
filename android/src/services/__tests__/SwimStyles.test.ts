// Learned swim styles (SwimStyles.ts, desktop twin tools/swim_styles.py): decoding them from a
// settings reply, the write that puts them back (only the swim entries), and that the watch's
// current settings are saved before anything is written. Made-up replies, never a real watch's.

export {};   // a module, so its mock names don't clash with other test files'

const mockFiles = new Map<string, string>();
const mockLog: string[] = [];
let mockSettings = '';

jest.mock('react-native-fs', () => ({
  DocumentDirectoryPath: '/files',
  mkdir: jest.fn(async () => {}),
  writeFile: jest.fn(async (path: string, data: string) => {
    mockLog.push(`backup ${path}`);
    mockFiles.set(path, data);
  }),
  readDir: jest.fn(async () => []),
  unlink: jest.fn(async () => {}),
}));

const mockNative = {
  readSettingsRaw: jest.fn(async () => { mockLog.push('read'); return mockSettings; }),
  writeSettingsRaw: jest.fn(async (b64: string) => {
    mockLog.push('write');
    mockSettings = mockApply(mockSettings, b64);
    return true;
  }),
  getDeviceInfo: jest.fn(async () => ({ serial: '8A153C5111000900' })),
};

jest.mock('react-native', () => ({
  NativeModules: { AmbitUsbModule: mockNative },
  NativeEventEmitter: class { addListener() { return { remove() {} }; } },
  Platform: { OS: 'android' },
}));

const B64 = require('../Base64') as typeof import('../Base64');
const S = require('../SwimStyles') as typeof import('../SwimStyles');

const MAGIC = [0x53, 0x42, 0x45, 0x4d, 0x30, 0x31, 0x30, 0x32];
const PREFIX = [0x1b, 0x00, 0x44, 0x00, 0x00, 0x00];

function rec(id: number, v: number[]): number[] {
  const b = new Uint8Array(9);
  const dv = new DataView(b.buffer);
  b[0] = id;
  v.forEach((x, i) => dv.setInt16(1 + i * 2, x, true));
  return Array.from(b);
}

function reply(entryId: number, styles: Array<[number, number[]]>): Uint8Array {
  const body: number[] = [0x01, 1, 0x00, 0x02, 1, 0x01];
  for (const [id, v] of styles) body.push(entryId, 9, ...rec(id, v));
  body.push(0x2c, 1, 0x00);
  return new Uint8Array([...PREFIX, ...MAGIC, ...body]);
}

// A stand-in watch: a write replaces the swim records it carries, everything else stays.
function mockApply(readB64: string, writeB64: string): string {
  const read = B64.base64ToBytes(readB64), write = B64.base64ToBytes(writeB64);
  const recs = (b: Uint8Array) => {
    const out: number[][] = [];
    for (let off = 14; off + 2 <= b.length;) {
      const id = b[off], len = b[off + 1];
      if (id === 0x40) out.push(Array.from(b.subarray(off + 2, off + 2 + len)));
      off += 2 + len;
    }
    return out;
  };
  const w = recs(write);
  const styles = recs(read).map((r, i) => {
    const src = w[i] ?? r;
    const dv = new DataView(new Uint8Array(src).buffer);
    return [src[0], [0, 1, 2, 3].map(k => dv.getInt16(1 + k * 2, true))] as [number, number[]];
  });
  return B64.bytesToBase64(reply(0x40, styles));
}

const PEAK: Array<[number, number[]]> = [
  [0, [0, 0, 0, 0]], [1, [2445, 2870, 442, 0]], [2, [510, 1715, 1748, 0]],
  [3, [2193, 2166, 207, 0]], [4, [1094, 2873, 876, 1]], [5, [0, 0, 0, 0]],
];

beforeEach(() => {
  mockFiles.clear();
  mockLog.length = 0;
  mockSettings = B64.bytesToBase64(reply(0x40, PEAK));
  jest.clearAllMocks();
});

test('decodes the six strokes in wire order', () => {
  const styles = S.decodeSwimStyles(reply(0x40, PEAK), 0x40)!;
  expect(styles.map(s => s.style)).toEqual(
    ['Other', 'Butterfly', 'Backstroke', 'Breaststroke', 'Freestyle', 'Drill']);
  expect(styles[3].vectors).toEqual([2193, 2166, 207, 0]);
  expect(styles.filter(S.isTaught)).toHaveLength(4);
});

test('the Vertical keeps them under its own entry id', () => {
  expect(S.SWIM_STYLE_ENTRY.Kaka).toBe(0x41);
  expect(S.decodeSwimStyles(reply(0x41, PEAK), 0x41)).toHaveLength(6);
  expect(S.decodeSwimStyles(reply(0x41, PEAK), 0x40)).toEqual([]);
  expect(S.supportsSwimStyles('Jabiru')).toBe(false);
});

test('the write carries only the swim records, same bytes as the desktop builds', () => {
  const plan = S.planSwimRestore(reply(0x40, PEAK), 0x40,
    [{ id: 1, style: 'Butterfly', vectors: [100, 200, 300, 0] }]);
  expect(plan.error).toBeUndefined();
  expect(plan.changes).toEqual([{ id: 1, style: 'Butterfly', from: [2445, 2870, 442, 0], to: [100, 200, 300, 0] }]);
  const expected = [0x1b, 0x00, 0x44, 0x00, 0x00, 0x01, ...MAGIC];
  for (const [id, v] of PEAK) expected.push(0x40, 9, ...rec(id, id === 1 ? [100, 200, 300, 0] : v));
  expect(Array.from(plan.payload!)).toEqual(expected);
});

test('same values: nothing to write', () => {
  const plan = S.planSwimRestore(reply(0x40, PEAK), 0x40,
    PEAK.map(([id, vectors]) => ({ id, style: '', vectors })));
  expect(plan).toEqual({ payload: null, changes: [] });
});

test('refuses what the watch cannot take', () => {
  expect(S.planSwimRestore(reply(0x40, PEAK), 0x40, [{ id: 9, style: 'X', vectors: [0, 0, 0, 0] }]).error)
    .toMatch('not on this watch');
  expect(S.planSwimRestore(reply(0x40, PEAK), 0x40, [{ id: 1, style: 'Butterfly', vectors: [40000, 0, 0, 0] }]).error)
    .toMatch('not a value');
  expect(S.planSwimRestore(reply(0x40, []), 0x40, [{ id: 1, style: 'Butterfly', vectors: [0, 0, 0, 0] }]).error)
    .toMatch('no swim styles');
});

test('writing saves the watch first, then writes, then confirms', async () => {
  const r = await S.writeSwimStyles('Emu', [{ id: 5, style: 'Drill', vectors: [1, 0, 0, 0] }]);
  expect(r.ok).toBe(true);
  expect(r.changes).toHaveLength(1);
  expect(mockLog[0]).toBe('read');
  expect(mockLog[1]).toMatch(/^backup \/files\/backups\/swim-styles\/8A153C5111000900\/swim-styles_pre_/);
  expect(mockLog.slice(2)).toEqual(['write', 'read']);
});

test('a watch without swim styles is refused before any read', async () => {
  const r = await S.writeSwimStyles('Jabiru', [{ id: 1, style: 'Butterfly', vectors: [1, 0, 0, 0] }]);
  expect(r.ok).toBe(false);
  expect(mockLog).toEqual([]);
});
