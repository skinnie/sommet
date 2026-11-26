// Ambit1/2 displays and sport-mode menu (AmbitLegacySportModes): the same bytes as the desktop's
// tools/legacy_sport_modes.py patch_displays. tools/test_legacy_display_edit.py builds this same
// synthetic region, applies these same edits and checks the same FNV-1a; it also replays every
// display change SuuntoLink made in its captures on both watches through the Python writer. Not
// seen on a real watch (watch-protocol/10 L5, 2026-11-26).

const mockState: { flash: Uint8Array; writes: { addr: number; len: number; extra: number }[] } =
  { flash: new Uint8Array(0), writes: [] };

jest.mock('../../native/AmbitUsbModule', () => {
  const { Buffer } = require('buffer');
  return {
    isBleTransportActive: () => false,
    connect: jest.fn(async () => {}),
    disconnect: jest.fn(async () => {}),
    getDeviceInfo: jest.fn(async () => ({ serial: 'TEST' })),
    readLegacyRegion: jest.fn(async (_addr: number, len: number) =>
      Buffer.from(mockState.flash.subarray(0, len)).toString('base64')),
    writeLegacyRegion: jest.fn(async (addr: number, b64: string, extra: number) => {
      const data = Uint8Array.from(Buffer.from(b64, 'base64'));
      mockState.writes.push({ addr, len: data.length, extra });
      mockState.flash = data;
      return true;
    }),
  };
});
jest.mock('../WatchBackup', () => ({ saveWatchBackup: jest.fn(async () => '/files/backups/x') }));

import {
  patchLegacyDisplayRegion, decodeLegacyRegion, writeLegacyDisplays, LegacyDisplayEdit,
} from '../AmbitLegacySportModes';

const le16 = (v: number) => [v & 0xff, (v >> 8) & 0xff];
const tlv = (tag: number, body: number[]) => [...le16(tag), ...le16(body.length), ...body];
const latin1 = (s: string, n: number) => Array.from({ length: n }, (_, i) => (i < s.length ? s.charCodeAt(i) : 0));
const blob = (size: number, name: string) =>
  Array.from({ length: size }, (_, i) => (i < 16 ? latin1(name, 16)[i] : (i * 7 + 3) & 0xff));
/** One 0x0106 display: rows are [row, item, multi-value list]. */
const display = (layout: number, rows: [number, number, number[]][], kind = 10) =>
  tlv(0x0106, [...tlv(0x0107, [...le16(layout), ...le16(kind)]),
    ...rows.flatMap(([r, item, values]) => tlv(0x0108, [...tlv(0x0109, [...le16(r), ...le16(item)]),
      ...values.flatMap(v => tlv(0x010a, le16(v)))]))]);

/** tools/test_legacy_display_edit.py synthetic(size). */
function synthetic(size: number): Uint8Array {
  const builtin = [...display(273, [[0, 8, []], [1, 8, []], [2, 0, [16, 1, 0xfffe]]], 4), ...display(336, [], 7)];
  const run = [...display(260, [[0, 70, []], [1, 28, []], [2, 0, [73, 1, 71]]]), ...display(262, [[0, 1, []]]), ...builtin];
  const bike = [...display(257, [[0, 6, []], [1, 32, []], [2, 5, []]]), ...builtin];
  const modes = [
    ...tlv(0x010b, [2, 0]),
    ...tlv(0x0101, [...tlv(0x0102, blob(size, 'Running')), ...tlv(0x01ff, new Array(8).fill(0)), ...tlv(0x0105, run)]),
    ...tlv(0x0101, [...tlv(0x0102, blob(size, 'Cycling')), ...tlv(0x0105, bike)]),
  ];
  let root = tlv(0x0100, modes);
  if (size === 90) {
    root = [...root, ...tlv(0x0200, tlv(0x0210, [...tlv(0x0212, latin1('Duathlon', 24)), ...tlv(0x0213, [2, 0]),
      ...tlv(0x0214, [0, 0]), ...tlv(0x0214, [1, 0])]))];
  }
  const out = new Uint8Array(10240).fill(0xff);
  out.set(tlv(0x0003, root));
  return out;
}

const EDITS: [number, number, LegacyDisplayEdit[]][] = [
  [90, 0, [{ op: 'setRow', display: 0, row: 'Top', values: [11] }]],
  [90, 0, [{ op: 'setRow', display: 0, row: 'Bottom', values: [9, 31, 17, 34, 1] }]],
  [90, 1, [{ op: 'add', type: '2-row' }, { op: 'add', type: '1-row' }]],
  [90, 0, [{ op: 'remove', display: 1 }, { op: 'setType', display: 0, type: 'graph' }]],
  [90, 1, [{ op: 'setRow', display: 1, row: 'Center', values: [21] }]],
  [76, 0, [{ op: 'setType', display: 1, type: '3-row' }, { op: 'setRow', display: 1, row: 'Bottom', values: [5, 10] }]],
  [76, 1, [{ op: 'add', type: '3-row' }, { op: 'remove', display: 0 }]],
];

const fnv = (data: Uint8Array, h: number) => { for (const x of data) h = Math.imul(h ^ x, 0x01000193) >>> 0; return h; };

describe('Ambit1/2 display patch (same bytes as the desktop)', () => {
  it('gives the Python writer\'s bytes for the shared synthetic region', () => {
    const regions: Record<number, Uint8Array> = { 90: synthetic(90), 76: synthetic(76) };
    let h = 0x811c9dc5;
    for (const [size, mode, edits] of EDITS) {
      regions[size] = patchLegacyDisplayRegion(regions[size], mode, edits);
      h = fnv(regions[size], h);
    }
    expect(h.toString(16)).toBe((0x47a0085f).toString(16));
  });

  it('a Top row change is two bytes; nothing else moves', () => {
    const before = synthetic(90);
    const after = patchLegacyDisplayRegion(before, 0, [{ op: 'setRow', display: 0, row: 'Top', values: [11] }]);
    const changed = Array.from(after).map((x, i) => (x !== before[i] ? i : -1)).filter(i => i >= 0);
    expect(changed.length).toBe(1);
    expect(after[changed[0]]).toBe(11);
  });

  it('refuses what no capture shows', () => {
    const r = synthetic(90);
    const bad: LegacyDisplayEdit[][] = [
      [{ op: 'add', type: '4-row' }],
      [{ op: 'setRow', display: 0, row: 'Top', values: [1, 2] }],
      [{ op: 'setRow', display: 0, row: 'Bottom', values: [1, 2, 3, 4, 5, 6] }],
      [{ op: 'setRow', display: 1, row: 'Center', values: [5] }],
      [{ op: 'remove', display: 5 }],
    ];
    for (const edits of bad) expect(() => patchLegacyDisplayRegion(r, 0, edits)).toThrow();
    expect(() => patchLegacyDisplayRegion(r, 1, [{ op: 'remove', display: 0 }])).toThrow(/at least one/);
  });
});

describe('decodeLegacyRegion', () => {
  it('lists each mode\'s displays like the Ambit3\'s and the multisport groups', () => {
    const { modes, displays, groups } = decodeLegacyRegion(synthetic(90));
    expect(modes.map(m => m.name)).toEqual(['Running', 'Cycling']);
    expect(displays[0].map(d => [d.template, d.isBuiltIn, d.screenNumber])).toEqual(
      [[260, false, 1], [262, false, 2], [273, true, null], [336, true, null]]);
    expect(displays[0][0].fields.map(f => [f.index, f.type, f.shortcuts])).toEqual(
      [[0, 70, []], [1, 28, []], [2, 0, [73, 1, 71]]]);
    expect(groups).toEqual([{ name: 'Duathlon', activityId: 2, legs: [0, 1],
      legNames: ['Running', 'Cycling'], isMultisport: true }]);
    expect(decodeLegacyRegion(synthetic(76)).groups).toEqual([]);
  });
});

describe('writeLegacyDisplays', () => {
  it('writes SuuntoLink\'s 10240 bytes with the 0x0b18 tail and re-reads', async () => {
    mockState.flash = synthetic(90);
    mockState.writes = [];
    const back = await writeLegacyDisplays(1, [{ op: 'setRow', display: 0, row: 'Center', values: [33] }]);
    expect(mockState.writes).toEqual([{ addr: 0x2000, len: 10240, extra: 0xffffffff }]);
    expect(back.displays[1][0].fields[1].type).toBe(33);
  });
});
