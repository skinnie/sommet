import { buildEtrexGpx } from '../EtrexExport';

// corners in local metres (x east, y north) -> dense GPX track around (46N, 6E)
function gpx(corners: [number, number][], step = 5): string {
  const kx = Math.cos((46 * Math.PI) / 180) * 111320;
  const xy: [number, number][] = [corners[0]];
  for (let k = 1; k < corners.length; k++) {
    const [x0, y0] = corners[k - 1]; const [x1, y1] = corners[k];
    const n = Math.max(1, Math.floor(Math.hypot(x1 - x0, y1 - y0) / step));
    for (let j = 1; j <= n; j++) xy.push([x0 + ((x1 - x0) * j) / n, y0 + ((y1 - y0) * j) / n]);
  }
  const pts = xy.map(([x, y]) => `<trkpt lat="${46 + y / 110540}" lon="${6 + x / kx}"><ele>100</ele></trkpt>`);
  return `<?xml version="1.0"?><gpx version="1.1"><trk><name>t</name><trkseg>${pts.join('')}</trkseg></trk></gpx>`;
}

describe('buildEtrexGpx', () => {
  test('an L-shaped track gets one right-turn waypoint near 0.5 km', () => {
    const r = buildEtrexGpx(gpx([[0, 0], [0, 500], [500, 500]]), { mode: 'track' });
    expect(r.stats.turns).toBe(1);
    const m = /<name>Right (\d+\.\d)<\/name>/.exec(r.gpx);
    expect(m).not.toBeNull();
    expect(Math.abs(parseFloat(m![1]) - 0.5)).toBeLessThan(0.05);
  });

  test('mirror image is a left turn', () => {
    expect(buildEtrexGpx(gpx([[0, 0], [0, 500], [-500, 500]]), { mode: 'track' }).gpx).toMatch(/<name>Left \d/);
  });

  test('a figure of eight is one crossing, crossed straight', () => {
    const r = buildEtrexGpx(gpx([[-300, -300], [300, 300], [300, 600], [-300, 600], [-300, 300], [300, -300]]), { mode: 'track' });
    expect(r.stats.crossings).toBe(1);
    expect(r.gpx).toMatch(/Cross (Straight|Bear)/);
  });

  test('route mode respects the via-point cap', () => {
    const big = gpx([[0, 0], [0, 400], [400, 400], [400, 0], [800, 0], [800, 400], [1200, 400], [1200, 0]]);
    const r = buildEtrexGpx(big, { mode: 'route', maxVia: 12 });
    const n = (r.gpx.match(/<rtept/g) ?? []).length;
    expect(n).toBeLessThanOrEqual(12);
    expect(n).toBeGreaterThanOrEqual(8);
  });

  test('track mode caps the number of track points', () => {
    const big = gpx([[0, 0], [0, 400], [400, 400], [400, 0], [800, 0]]);
    expect((buildEtrexGpx(big, { mode: 'track', maxTrack: 60 }).gpx.match(/<trkpt/g) ?? []).length).toBeLessThanOrEqual(60);
  });

  test('a straight line has no turns', () => {
    expect(buildEtrexGpx(gpx([[0, 0], [0, 2000]]), { mode: 'track' }).stats.turns).toBe(0);
  });
});
