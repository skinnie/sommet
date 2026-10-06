import { buildRoutePins } from '../RoutePins';
import { buildEtrexGpx } from '../EtrexExport';

// corners in local metres (x east, y north) -> GPX track around (46N, 6E); same shapes as the
// Python selftest in tools/route_pins.py
function gpx(corners: [number, number][], step = 10): string {
  const kx = Math.cos((46 * Math.PI) / 180) * 111320;
  const xy: [number, number][] = [corners[0]];
  for (let k = 1; k < corners.length; k++) {
    const [x0, y0] = corners[k - 1]; const [x1, y1] = corners[k];
    const n = Math.max(1, Math.floor(Math.hypot(x1 - x0, y1 - y0) / step));
    for (let j = 1; j <= n; j++) xy.push([x0 + ((x1 - x0) * j) / n, y0 + ((y1 - y0) * j) / n]);
  }
  const pts = xy.map(([x, y]) => `<trkpt lat="${(46 + y / 110540).toFixed(6)}" lon="${(6 + x / kx).toFixed(6)}"><ele>100.0</ele></trkpt>`);
  return `<?xml version="1.0"?><gpx version="1.1"><trk><name>test</name><trkseg>${pts.join('')}</trkseg></trk></gpx>`;
}
const WALK: [number, number][] = [[0, 0], [0, 300], [300, 300], [300, 700], [-300, 700], [-300, 300], [0, 300], [0, 0], [600, 0],
  [600, 500], [600, 0], [1500, 0], [1800, 0], [1800, 300], [1500, 300], [1500, 0], [1500, -300],
  [1200, -300], [1200, -900], [1500, -900], [1500, -1200], [1200, -1200], [1200, -900], [600, -900]];
const names = (g: string, target: 'etrex' | 'ambit' = 'etrex') =>
  buildRoutePins(g, { target }).pins.filter(p => ['decision', 'turn', 'merged'].includes(p.kind)).map(p => p.name);

describe('buildRoutePins', () => {
  test('the approved test walk: lollipop, out-and-back, crossroads, figure-8 centre', () => {
    const r = buildRoutePins(gpx(WALK), { target: 'etrex' });
    expect(names(gpx(WALK))).toEqual(['Right 0.3', 'Right 2.3', 'Left 3.2', 'Turn back 3.7', 'Left 4.2',
      'Straight 5.1', 'Straight 6.3', 'Left 7.5', 'Left 8.7']);
    expect(r.pins.some(p => p.name === 'OK 0.4')).toBe(true);
    expect((r.gpx.match(/<trkpt/g) ?? []).length).toBe(r.stats.pointsIn); // the track is untouched
  });

  test('nothing to decide: straight line, L, closed loop, hairpin', () => {
    const shapes: [number, number][][] = [[[0, 0], [0, 2000]], [[0, 0], [0, 500], [500, 500]],
      [[0, 0], [0, 500], [500, 500], [500, 0], [0, 0]], [[0, 0], [0, 300], [6, 310], [12, 300], [12, 0]]];
    for (const s of shapes) expect(names(gpx(s))).toEqual([]);
  });

  test('a 20 m viewpoint spur merges its turn-back and way out into one pin', () => {
    expect(names(gpx([[0, 0], [0, 300], [20, 300], [0, 300], [0, 600]], 5))).toEqual(['Right 0.3', 'Back, right 0.3']);
  });

  test('a 7 m poke off the path is a drawing slip: no pin', () => {
    const r = buildRoutePins(gpx([[0, 0], [0, 300], [7, 300], [0, 300], [0, 600]], 1), { target: 'etrex' });
    expect(r.pins.filter(p => p.kind !== 'ok')).toEqual([]);
    expect(r.stats.ignoredSpikes).toBe(1);
  });

  test('Ambit: names fit 15 bytes, waypoints sit on route points in order, Start first', () => {
    const r = buildRoutePins(gpx(WALK), { target: 'ambit', name: 'Test' });
    const wp = [...r.gpx.matchAll(/<wpt lat="([-\d.]+)" lon="([-\d.]+)"><name>([^<]*)<\/name>/g)].map(m => [m[1], m[2], m[3]]);
    const rte = [...r.gpx.matchAll(/<rtept lat="([-\d.]+)" lon="([-\d.]+)"/g)].map(m => `${m[1]},${m[2]}`);
    expect(wp[0][2]).toBe('Start');
    expect(wp[wp.length - 1][2]).toBe('End');
    expect(wp.every(w => w[2].length <= 15)).toBe(true);
    const idx = wp.slice(0, -1).map(w => rte.indexOf(`${w[0]},${w[1]}`));
    expect(idx.every(i => i >= 0)).toBe(true);
    expect([...idx].sort((a, b) => a - b)).toEqual(idx);
    expect(`${wp[wp.length - 1][0]},${wp[wp.length - 1][1]}`).toBe(rte[rte.length - 1]);
  });

  test('a loop that starts into a junction: "Start, ..." and no End', () => {
    const r = buildRoutePins(gpx([[0, 0], [0, 20], [200, 20], [200, 220], [-200, 220], [-200, 20], [0, 20], [0, 0]], 5), { target: 'ambit' });
    expect(r.pins[0].name.startsWith('Start, ')).toBe(true);
    expect(r.pins.some(p => p.kind === 'end')).toBe(false);
  });
});

describe('buildEtrexGpx', () => {
  test('track mode carries the pins and leaves the track untouched', () => {
    const r = buildEtrexGpx(gpx(WALK), { mode: 'track' });
    expect(r.stats.decisions).toBe(8);
    expect(r.stats.turnarounds).toBe(1);
    expect(r.gpx).toContain('<name>Right 0.3</name>');
    expect(r.gpx).toContain('<name>Turn back 3.7</name>');
    expect((r.gpx.match(/<trkpt/g) ?? []).length).toBe(r.stats.pointsIn);
  });
  test('track mode thins only past maxTrack', () => {
    expect((buildEtrexGpx(gpx(WALK), { mode: 'track', maxTrack: 60 }).gpx.match(/<trkpt/g) ?? []).length).toBeLessThanOrEqual(60);
  });
  test('route mode respects the via cap and keeps the decision spots', () => {
    const r = buildEtrexGpx(gpx(WALK), { mode: 'route', maxVia: 20 });
    const n = (r.gpx.match(/<rtept/g) ?? []).length;
    expect(n).toBeGreaterThanOrEqual(12);
    expect(n).toBeLessThanOrEqual(20);
    expect(r.gpx).toContain('<name>Left 7.5</name>');
  });
});
