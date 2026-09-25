// Byte-exact parity with the desktop's hardware-verified Python tools (tools/magene_*.py): the
// fixture is generated from them, so a drift in either port fails here.
jest.mock('react-native', () => ({ NativeModules: {}, NativeEventEmitter: jest.fn() }));

import fx from './magene.fixture.json';
import { buildRouteFile } from '../MageneRoute';
import { toIntervals, encodeWorkout, tss, workoutInfo } from '../MageneWorkout';
import { packets } from '../MageneTransfer';
import { fitCrc16 } from '../MageneBle';
import { decodeSettings, patchSettings, reassembleFit } from '../MageneDevice';
import { encodePages, decodePages, FIELD_GROUPS } from '../MagenePages';

const hex = (b: Uint8Array) => Array.from(b, x => x.toString(16).padStart(2, '0')).join('');
const bytes = (h: string) => new Uint8Array((h.match(/../g) ?? []).map(x => parseInt(x, 16)));

describe('Magene C406 encoders match the Python tools', () => {
  it('route file', () => {
    const r = buildRouteFile(fx.points as [number, number][]);
    expect(hex(r.file)).toBe(fx.route);
    expect([r.steps, r.previewOffset, r.previewCount]).toEqual([fx.steps, fx.po, fx.pc]);
    expect(fitCrc16(r.file)).toBe(fx.routeCrc);
  });
  it('transfer packets', () => {
    expect(packets(bytes(fx.route), 185).map(hex)).toEqual(fx.packets);
  });
  it('workout file, TSS and info frame', () => {
    const iv = toIntervals(fx.workout);
    expect(iv).toEqual(fx.intervals);
    const f = encodeWorkout(iv);
    expect(hex(f)).toBe(fx.workoutFile);
    expect(tss(iv, 225)).toBeCloseTo(fx.tss, 9);
    const total = iv.reduce((a, i) => a + i.seconds, 0);
    expect(hex(workoutInfo(123456, tss(iv, 225), total, fitCrc16(f), 'Sommet 5x4 ✓'))).toBe(fx.info);
  });
  it('settings decode + patch', () => {
    const reply = bytes(fx.settingsReply);
    expect(decodeSettings(reply)).toEqual(fx.settings);
    expect(hex(patchSettings(reply, { autoPause: 3, powerAlert: 300, keyTone: 1 }))).toBe(fx.patched);
    expect(() => patchSettings(reply, { hrAlert: 50 })).toThrow();
    expect(() => patchSettings(reply, { timezoneOffset: 0 })).toThrow();
  });
  it('data screens (pages) block + field catalogue', () => {
    expect(hex(encodePages(fx.pages))).toBe(fx.pagesBlock);
    expect(decodePages(bytes(fx.pagesBlock))).toEqual(fx.pages);
    expect(FIELD_GROUPS).toEqual(fx.fieldGroups);
    expect(() => encodePages([[0x10]])).toThrow();
    expect(() => encodePages([[0x10, 0x99]])).toThrow();
  });
  it('ride reassembly rejects a bad stream', () => {
    expect(reassembleFit([new Uint8Array(20)])).toBeNull();
  });
});
