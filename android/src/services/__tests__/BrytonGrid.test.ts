// Parity with tools/bryton_grid.py (fixture generated from it, over a real Aero 60 Grid.ini).
jest.mock('react-native', () => ({ NativeModules: {} }));
import fx from './bryton_grid.fixture.json';
import { parseGrid, patchGrid, GRID_GROUPS } from '../BrytonGrid';

const bytes = (h: string) => new Uint8Array((h.match(/../g) ?? []).map(x => parseInt(x, 16)));
const hex = (b: Uint8Array) => Array.from(b, x => x.toString(16).padStart(2, '0')).join('');

describe('Bryton Grid.ini matches tools/bryton_grid.py', () => {
  it('parses the same screens', () => { expect(parseGrid(bytes(fx.original))).toEqual(fx.pages); });
  it('patches byte-for-byte the same', () => { expect(hex(patchGrid(bytes(fx.original), fx.change as any))).toBe(fx.patched); });
  it('same field catalogue', () => { expect(GRID_GROUPS).toEqual(fx.groups); });
  it('refuses what the device can’t take', () => {
    const o = bytes(fx.original);
    expect(() => patchGrid(o, [{ page: 1, enabled: 0 }])).toThrow();
    expect(() => patchGrid(o, [{ page: 8, count: 4 }])).toThrow();
    expect(() => patchGrid(o, [{ page: 2, count: 2, fields: [2, 999] }])).toThrow();
  });
});
