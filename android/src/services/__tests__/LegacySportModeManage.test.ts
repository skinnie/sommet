// Ambit1/2 sport-mode create and delete (AmbitLegacySportModes): the same bytes as the desktop's
// tools/legacy_sport_modes.py create_mode / delete_mode / delete_multisport.
// tools/test_legacy_sport_mode_manage.py builds this same synthetic region, applies these same
// operations and checks the same FNV-1a; it also replays SuuntoLink's four captured creates and its
// captured Triathlon delete through the Python writer, byte for byte, and (watch-protocol/10 L2) its
// captured multisport create. Not seen on a real watch (watch-protocol/10 L3, 2026-11-26).

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
  createLegacyModeRegion, deleteLegacyModeRegion, deleteLegacyMultisportRegion, decodeLegacyRegion,
  legacyNewModeFields, legacyUsage, legacyMenu, createLegacySportMode, deleteLegacySportMode,
  LEGACY_NEW_MODE_DISPLAYS, LEGACY_SELECTABLE_ACTIVITIES, LEGACY_VARIANTS,
  createLegacyMultisportRegion, legacyMultisportLegChoices, createLegacyMultisport,
} from '../AmbitLegacySportModes';

const le16 = (v: number) => [v & 0xff, (v >> 8) & 0xff];
const le32 = (v: number) => [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff];
const tlv = (tag: number, body: number[]) => [...le16(tag), ...le16(body.length), ...body];
const latin1 = (s: string, n: number) => Array.from({ length: n }, (_, i) => (i < s.length ? s.charCodeAt(i) : 0));

/** tools/test_legacy_sport_mode_manage.py synthetic(blen). */
function synthetic(blen: number): Uint8Array {
  const mode = (name: string, act: number) => {
    const b = new Array(blen).fill(0);
    latin1(name, 16).forEach((c, i) => { b[i] = c; });
    b[16] = act & 0xff; b[17] = act >> 8;
    if (blen === 90) le32(0x2e1b + act).forEach((c, i) => { b[18 + i] = c; });
    return tlv(0x0101, [...tlv(0x0102, b), ...tlv(0x0105, LEGACY_NEW_MODE_DISPLAYS.slice(0, 54))]);
  };
  const group = (name: string, act: number, legs: number[], gid: number) =>
    tlv(0x0210, [...tlv(0x0212, latin1(name, 24)), ...tlv(0x0213, le16(act)),
      ...legs.flatMap(x => tlv(0x0214, le16(x))), ...tlv(0x02fe, le32(gid))]);
  const modes = [...tlv(0x010b, [2, 0]), ...mode('Running', 3), ...mode('Transition', 1), ...mode('Cycling', 4)];
  let root = tlv(0x0100, modes);
  if (blen === 90) {
    root = [...root, ...tlv(0x0200, [...group('Running', 3, [0], 2), ...group('Duathlon', 2, [0, 1, 2, 1, 0], 1),
      ...group('Cycling', 4, [2], 3)])];
  }
  const out = new Uint8Array(10240).fill(0xff);
  out.set(tlv(0x0003, root));
  return out;
}

function fnv1a(data: Uint8Array, h = 0x811c9dc5): number {
  for (const x of data) h = Math.imul(h ^ x, 0x01000193) >>> 0;
  return h >>> 0;
}

const names = (data: Uint8Array) => decodeLegacyRegion(data).modes.map(m => m.name);

describe('Ambit1/2 sport-mode create and delete', () => {
  it('gives the Python writer\'s bytes', () => {
    const steps: Uint8Array[] = [];
    steps.push(createLegacyModeRegion(synthetic(90), 'Trilho', 82, 'Duck', [1000, 1001, 1002]));
    steps.push(createLegacyModeRegion(steps[0], 'Natação', 6, 'Colibri', 2000));
    steps.push(deleteLegacyMultisportRegion(steps[1], 'Duathlon', 'Duck'));
    steps.push(deleteLegacyModeRegion(steps[2], 'Running', 'Duck'));
    steps.push(createLegacyModeRegion(synthetic(76), 'Ski', 20, 'Bluebird', 3000));
    steps.push(deleteLegacyModeRegion(steps[4], 'Transition', 'Bluebird'));
    let h = 0x811c9dc5;
    for (const s of steps) h = fnv1a(s, h);
    expect(h.toString(16)).toBe('3d11aca4');
    const groups = Object.fromEntries(decodeLegacyRegion(steps[2]).groups.map(g => [g.name, g.legNames]));
    expect(groups).toEqual({ Running: ['Running'], Cycling: ['Cycling'], Trilho: ['Trilho'], 'Natação': ['Natação'] });
    expect(names(steps[3])).toEqual(['Cycling', 'Trilho', 'Natação']);
    expect(names(steps[5])).toEqual(['Running', 'Cycling', 'Ski']);
    const nat = decodeLegacyRegion(steps[1]).modes[4];
    expect([nat.useHw, nat.altiBaroMode, nat.modeId]).toEqual([0, 0, 2]);
  });

  it('keeps SuuntoLink\'s rules', () => {
    const a2 = synthetic(90);
    expect(legacyUsage(a2)).toEqual({ used: 3, max: 10, multisport: 1 });
    expect(() => deleteLegacyModeRegion(a2, 'Cycling', 'Duck')).toThrow(/multisport Duathlon/);
    expect(() => deleteLegacyModeRegion(a2, 'Transition', 'Duck')).toThrow(/multisport Duathlon/);
    expect(() => createLegacyModeRegion(a2, 'Running', 3, 'Duck')).toThrow(/already has/);
    expect(() => createLegacyModeRegion(a2, 'Nope', 7, 'Duck')).toThrow(/not one SuuntoLink offers/);
    expect(() => createLegacyModeRegion(a2, 'Nope', 3, 'Bluebird')).toThrow(/not the Bluebird/);
    expect(() => createLegacyModeRegion(a2, 'Seventeen chars!!', 3, 'Duck')).toThrow(/16 characters/);
    let full = a2;
    for (let i = 0; i < 7; i++) full = createLegacyModeRegion(full, `M${i}`, 3, 'Duck', 1);
    expect(legacyUsage(full).used).toBe(10);
    expect(() => createLegacyModeRegion(full, 'Eleventh', 3, 'Duck')).toThrow(/10\/10/);
    const menu = legacyMenu(a2, 'Duck');
    expect(menu.entries.map(e => [e.name, e.multisport, e.deletable])).toEqual([
      ['Running', false, false], ['Transition', false, false], ['Cycling', false, false], ['Duathlon', true, true]]);
  });

  it('creates a multisport with the Python writer\'s bytes', () => {
    const steps: Uint8Array[] = [];
    steps.push(createLegacyMultisportRegion(synthetic(90), 'Tri', 19, ['Cycling', 'Running', 'Transition', 'Cycling'], 'Duck', 4000));
    steps.push(deleteLegacyMultisportRegion(steps[0], 'Duathlon', 'Duck'));
    steps.push(createLegacyMultisportRegion(steps[1], 'Aventura', 61, ['Running', 'Cycling'], 'Colibri', 5000));
    let h = 0x811c9dc5;
    for (const s of steps) h = fnv1a(s, h);
    expect(h.toString(16)).toBe('20d031b7');
    const groups = Object.fromEntries(decodeLegacyRegion(steps[2]).groups.map(g => [g.name, g.legNames]));
    expect(groups).toEqual({ Running: ['Running'], Tri: ['Cycling', 'Running', 'Transition', 'Cycling'],
      Cycling: ['Cycling'], Aventura: ['Running', 'Cycling'] });
  });

  it('keeps SuuntoLink\'s multisport rules', () => {
    const a2 = synthetic(90);
    // The Transition only the Duathlon uses is offered after the modes with an entry of their own.
    expect(legacyMultisportLegChoices(a2)).toEqual(['Running', 'Cycling', 'Transition']);
    expect(legacyMenu(a2, 'Duck').legChoices).toEqual(['Running', 'Cycling', 'Transition']);
    const two = createLegacyMultisportRegion(a2, 'Tri', 19, ['Running', 'Cycling'], 'Duck', 1);
    expect(legacyUsage(two).multisport).toBe(2);
    expect(() => createLegacyMultisportRegion(two, 'Three', 2, ['Running', 'Cycling'], 'Duck')).toThrow(/2\/2 multisport/);
    expect(() => createLegacyMultisportRegion(a2, 'X', 19, ['Running', 'Cycling'], 'Greentit')).toThrow(/no multisport/);
    expect(() => createLegacyMultisportRegion(synthetic(76), 'X', 19, ['Running', 'Cycling'], 'Bluebird')).toThrow(/no multisport/);
    expect(() => createLegacyMultisportRegion(a2, 'X', 19, ['Running'], 'Duck')).toThrow(/2 to 6/);
    expect(() => createLegacyMultisportRegion(a2, 'X', 19, Array(7).fill('Running'), 'Duck')).toThrow(/2 to 6/);
    expect(() => createLegacyMultisportRegion(a2, 'X', 19, ['Running', 'Nope'], 'Duck')).toThrow(/offers as a leg/);
    expect(() => createLegacyMultisportRegion(a2, 'X', 3, ['Running', 'Cycling'], 'Duck')).toThrow(/cannot hold/);
    expect(() => createLegacyMultisportRegion(a2, 'Duathlon', 2, ['Running', 'Cycling'], 'Duck')).toThrow(/already has/);
    expect(() => createLegacyMultisportRegion(a2, 'Seventeen chars!!', 2, ['Running', 'Cycling'], 'Duck')).toThrow(/16 characters/);
  });

  it('writes a multisport once with the commit tail', async () => {
    mockState.flash = synthetic(90);
    mockState.writes = [];
    const after = await createLegacyMultisport('Tri', 19, ['Cycling', 'Running'], 'Duck');
    expect(after.groups.find(g => g.name === 'Tri')?.legNames).toEqual(['Cycling', 'Running']);
    expect(mockState.writes).toEqual([{ addr: 0x2000, len: 10240, extra: 0xffffffff }]);
  });

  it('uses SuuntoLink\'s defaults with values these watches hold', () => {
    for (const variant of Object.keys(LEGACY_VARIANTS)) {
      for (const act of LEGACY_SELECTABLE_ACTIVITIES) {
        const f = legacyNewModeFields(variant, act);
        expect([0, 1, 60]).toContain(f.gps);
        expect([1, 10]).toContain(f.rec);
        expect([0, 1, 2]).toContain(f.alti);
      }
    }
    expect(legacyNewModeFields('Bluebird', 10)).toEqual({ pods: 3, alti: 1, gps: 0, rec: 10 });
    expect(legacyNewModeFields('Duck', 61)).toEqual({ pods: 3, alti: 0, gps: 1, rec: 10 });
    expect(legacyNewModeFields('Duck', 3).pods).toBe(0x0107);
  });

  it('writes the region once with the commit tail, a copy first', async () => {
    mockState.flash = synthetic(90);
    mockState.writes = [];
    const after = await createLegacySportMode('Trilho', 82, 'Duck');
    expect(after.modes.map(m => m.name)).toContain('Trilho');
    expect(mockState.writes).toEqual([{ addr: 0x2000, len: 10240, extra: 0xffffffff }]);
    await deleteLegacySportMode('Trilho', 'Duck');
    await deleteLegacySportMode('Duathlon', 'Duck', true);
    expect(names(mockState.flash)).toEqual(['Running', 'Cycling']);
    expect(mockState.writes.length).toBe(3);
  });
});
