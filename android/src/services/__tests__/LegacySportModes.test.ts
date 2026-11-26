// Ambit1/2 sport-mode edits (AmbitLegacySportModes): the same bytes as the desktop's
// tools/legacy_sport_modes.py patch_region. tools/test_legacy_sport_mode_edit.py builds this same
// synthetic region, applies these same edits and checks the same FNV-1a; it also replays
// SuuntoLink's own captured writes on both watches through the Python writer. Not seen on a real
// watch since the Ambit1 layout and the 10240-byte extent were added (2026-11-26).

const mockState: { flash: Uint8Array; writes: { addr: number; len: number; extra: number }[]; short: boolean } =
  { flash: new Uint8Array(0), writes: [], short: false };
const mockBackups: string[] = [];

jest.mock('../../native/AmbitUsbModule', () => {
  const { Buffer } = require('buffer');
  return {
    isBleTransportActive: () => false,
    connect: jest.fn(async () => {}),
    disconnect: jest.fn(async () => {}),
    getDeviceInfo: jest.fn(async () => ({ serial: 'TEST' })),
    readLegacyRegion: jest.fn(async (_addr: number, len: number) =>
      Buffer.from(mockState.flash.subarray(0, mockState.short ? 4096 : len)).toString('base64')),
    writeLegacyRegion: jest.fn(async (addr: number, b64: string, extra: number) => {
      const data = Uint8Array.from(Buffer.from(b64, 'base64'));
      mockState.writes.push({ addr, len: data.length, extra });
      mockState.flash = data;
      return true;
    }),
  };
});
jest.mock('../WatchBackup', () => ({
  saveWatchBackup: jest.fn(async (kind: string) => { mockBackups.push(kind); return `/files/backups/${kind}`; }),
}));

import { patchLegacySportModeRegion, writeLegacySportMode, LegacyModePatch } from '../AmbitLegacySportModes';

const tlv = (tag: number, body: number[]) => [tag & 0xff, tag >> 8, body.length & 0xff, body.length >> 8, ...body];
const latin1 = (s: string, n: number) => Array.from({ length: n }, (_, i) => (i < s.length ? s.charCodeAt(i) : 0));
const blob = (size: number, name: string) =>
  Array.from({ length: size }, (_, i) => (i < 16 ? latin1(name, 16)[i] : (i * 7 + 3) & 0xff));
const group = (name: string, members: number[]) =>
  tlv(0x0210, [...tlv(0x0212, latin1(name, 24)), ...members.flatMap(m => tlv(0x0214, [m & 0xff, m >> 8]))]);

/** tools/test_legacy_sport_mode_edit.py synthetic(size). */
function synthetic(size: number): Uint8Array {
  const modes = [
    ...tlv(0x010b, [2, 0]),
    ...tlv(0x0101, [...tlv(0x0102, blob(size, 'Running')), ...tlv(0x01ff, new Array(8).fill(0))]),
    ...tlv(0x0101, tlv(0x0102, blob(size, 'Cycling'))),
  ];
  let root = tlv(0x0100, modes);
  if (size === 90) root = [...root, ...tlv(0x0200, [...group('Running', [0]), ...group('Cycling', [1]), ...group('Duathlon', [0, 1])])];
  const out = new Uint8Array(1024).fill(0xff);
  out.set(tlv(0x0003, root));
  return out;
}

const EDITS: [number, number, LegacyModePatch][] = [
  [90, 0, { autolapM: 1500 }],
  [90, 0, { useHw: 0x0000 }],
  [90, 1, { heartrateMax: 180, heartrateMin: 60, useHrLimits: true }],
  [90, 1, { heartrateMax: 170, heartrateMin: 50, useHrLimits: false }],
  [90, 0, { name: 'Corrida Ação' }],
  [90, 1, { name: 'Bike 16 chars!!!', useHw: 0x0801 }],
  [76, 1, { autolapM: 2000, useHw: 0x0106, heartrateMax: 200, heartrateMin: 40, useHrLimits: true, name: 'Trilho' }],
  [76, 0, { useHrLimits: false, heartrateMax: 150, heartrateMin: 90 }],
];

const fnv = (data: Uint8Array, h: number) => { for (const x of data) h = Math.imul(h ^ x, 0x01000193) >>> 0; return h; };

describe('Ambit1/2 sport-mode patch (same bytes as the desktop)', () => {
  it('gives the Python writer\'s bytes for the shared synthetic region', () => {
    const regions: Record<number, Uint8Array> = { 90: synthetic(90), 76: synthetic(76) };
    let h = 0x811c9dc5;
    for (const [size, mode, patch] of EDITS) {
      regions[size] = patchLegacySportModeRegion(regions[size], mode, patch);
      h = fnv(regions[size], h);
    }
    expect(h.toString(16)).toBe((0x1df68f5d).toString(16));
  });

  it('Ambit1 offsets: autolap at 26, HR at 28/30, pods at 18, no "use" field', () => {
    const r = patchLegacySportModeRegion(synthetic(76), 0, { autolapM: 1000, heartrateMax: 239, heartrateMin: 30, useHrLimits: true });
    const b = synthetic(76);
    const blobAt = 4 + 4 + 6 + 4 + 4; // root, modes, 0x010b, mode, settings headers
    const changed = Array.from(r).map((x, i) => (x !== b[i] ? i - blobAt : null)).filter(x => x !== null);
    expect(changed).toEqual([26, 27, 28, 29, 30, 31]);
  });

  it('refuses an unknown mode', () => {
    expect(() => patchLegacySportModeRegion(synthetic(90), 5, { autolapM: 1 })).toThrow(/not found/);
  });
});

describe('writeLegacySportMode', () => {
  beforeEach(() => { mockState.writes = []; mockState.short = false; mockBackups.length = 0; });

  it('writes SuuntoLink\'s 10240 bytes with the 0x0b18 tail, after a copy', async () => {
    const region = new Uint8Array(10240).fill(0xff);
    region.set(synthetic(90).subarray(0, 1024));
    mockState.flash = region;
    const modes = await writeLegacySportMode(1, { autolapM: 500 });
    expect(mockState.writes).toEqual([{ addr: 0x2000, len: 10240, extra: 0xffffffff }]);
    expect(mockBackups).toEqual(['LegacySportModes']);
    expect(modes[1].autolapM).toBe(500);
  });

  it('writes nothing when nothing changes, and nothing over a short read', async () => {
    const region = new Uint8Array(10240).fill(0xff);
    region.set(synthetic(90).subarray(0, 1024));
    mockState.flash = region;
    await writeLegacySportMode(0, { name: 'Running' });
    mockState.short = true;
    await expect(writeLegacySportMode(0, { autolapM: 9 })).rejects.toThrow(/nothing written/);
    expect(mockState.writes).toEqual([]);
  });
});
