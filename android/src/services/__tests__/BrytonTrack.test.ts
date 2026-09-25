// Parity with tools/bryton_track.py (hardware-confirmed Follow Track format); fixture generated from it.
jest.mock('react-native', () => ({ NativeModules: {} }));
import fx from './bryton_track.fixture.json';
import { parseGpx, decimate, encodeTrack, sanitizeName } from '../BrytonTrack';

const hex = (b: Uint8Array) => Array.from(b, x => x.toString(16).padStart(2, '0')).join('');

describe('Bryton Follow Track matches tools/bryton_track.py', () => {
  it('track + smy bytes', () => {
    const pts = decimate(parseGpx(fx.gpx), 2500);
    expect(pts.length).toBe(fx.n);
    const { track, smy } = encodeTrack(pts);
    expect(hex(track)).toBe(fx.track);
    expect(hex(smy)).toBe(fx.smy);
  });
  it('file names', () => {
    for (const [inp, out] of Object.entries(fx.names)) expect(sanitizeName(inp)).toBe(out);
  });
});
