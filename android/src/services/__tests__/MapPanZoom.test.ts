import { centeredPointMosaic, fitBoundsMosaic } from '../MapTile';
import { layoutView, panView, viewFromMosaic, zoomView, MAX_ZOOM, MIN_ZOOM } from '../MapPanZoom';

const FARO = { lat: 37.0194, lon: -7.9322 };
const close = (a: number, b: number, eps = 0.5) => expect(Math.abs(a - b)).toBeLessThan(eps);

describe('MapPanZoom', () => {
  it('starts where the fitted picture was (same point, same pixel)', () => {
    const track = [{ lat: 37.01, lon: -7.95 }, { lat: 37.05, lon: -7.90 }, { lat: 37.03, lon: -7.85 }];
    const m = fitBoundsMosaic(track, 600, { targetHeight: 200 });
    const lay = layoutView(viewFromMosaic(m), m.contentWidth, m.contentHeight);
    for (const p of track) {
      const a = m.project(p.lat, p.lon), b = lay.project(p.lat, p.lon);
      close(a.x, b.x); close(a.y, b.y);
    }
  });

  it('a drag moves every point by the finger distance', () => {
    const m = centeredPointMosaic(FARO.lat, FARO.lon, 400, 220, 15);
    const v = viewFromMosaic(m);
    const before = layoutView(v, 400, 220).project(FARO.lat, FARO.lon);
    const after = layoutView(panView(v, 30, -12), 400, 220).project(FARO.lat, FARO.lon);
    close(after.x - before.x, 30); close(after.y - before.y, -12);
  });

  it('latLonAt is the inverse of project (double-tap puts the POI under the finger)', () => {
    const lay = layoutView(viewFromMosaic(centeredPointMosaic(FARO.lat, FARO.lon, 400, 220, 15)), 400, 220);
    const at = lay.latLonAt(123, 45);
    const back = lay.project(at.lat, at.lon);
    close(back.x, 123, 0.01); close(back.y, 45, 0.01);
    const mid = lay.latLonAt(200, 110);
    close(mid.lat, FARO.lat, 1e-5); close(mid.lon, FARO.lon, 1e-5);
  });

  it('pinch doubles the scale per doubling of the finger spread, within limits', () => {
    const v = viewFromMosaic(centeredPointMosaic(FARO.lat, FARO.lon, 400, 220, 12));
    expect(zoomView(v, v.zf, 2).zf).toBeCloseTo(v.zf + 1, 6);
    expect(zoomView(v, v.zf, 1e6).zf).toBe(MAX_ZOOM);
    expect(zoomView(v, v.zf, 1e-6).zf).toBe(MIN_ZOOM);
  });

  it('tiles cover the whole box at any zoom', () => {
    const v = { ...viewFromMosaic(centeredPointMosaic(FARO.lat, FARO.lon, 400, 220, 13)), zf: 13.6 };
    const lay = layoutView(v, 400, 220);
    const minL = Math.min(...lay.tiles.map(t => t.left)), minT = Math.min(...lay.tiles.map(t => t.top));
    const maxR = Math.max(...lay.tiles.map(t => t.left + t.size)), maxB = Math.max(...lay.tiles.map(t => t.top + t.size));
    expect(minL).toBeLessThanOrEqual(0); expect(minT).toBeLessThanOrEqual(0);
    expect(maxR).toBeGreaterThanOrEqual(400); expect(maxB).toBeGreaterThanOrEqual(220);
    expect(new Set(lay.tiles.map(t => t.key)).size).toBe(lay.tiles.length);
  });
});
