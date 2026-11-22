import { Buffer } from 'buffer';
import { decodeNavigation, navUsedSizes } from '../RouteReader';

// Route waypoints are 52 bytes on the Ambit3 (header magic 0x0334) and 54 on the Traverse /
// Traverse Alpha (0x0336: an int16 altitude after the longitude, before the names).
// Same layouts as tools/ambit_format.py; tools/test_route_waypoints.py checks the writer.

function waypointRegion(size: 52 | 54, name: string, routeName: string): string {
  const region = new Uint8Array(0x20 + size).fill(0);
  const dv = new DataView(region.buffer);
  dv.setUint16(0, 0x300 + size, true);
  dv.setUint16(2, 1, true);
  const off = 0x20;
  dv.setInt32(off, 436000000, true);   // 43.6
  dv.setInt32(off + 4, 69000000, true); // 6.9
  const shift = size - 52;
  if (shift) dv.setInt16(off + 8, 512, true);
  region.set(Buffer.from(name, 'utf8'), off + 8 + shift);
  region.set(Buffer.from(routeName, 'utf8'), off + 24 + shift);
  return Buffer.from(region).toString('base64');
}

const noRoutes = Buffer.from(new Uint8Array(64)).toString('base64');

describe('RouteReader route waypoints', () => {
  it.each([52, 54] as const)('decodes the %i-byte layout', (size) => {
    const nav = decodeNavigation(waypointRegion(size, 'Turn back 3.7', 'Cap Roux'), noRoutes);
    expect(nav.waypoints).toHaveLength(1);
    expect(nav.waypoints[0]).toMatchObject({ name: 'Turn back 3.7', routeName: 'Cap Roux' });
    expect(nav.waypoints[0].latitude).toBeCloseTo(43.6, 6);
    expect(nav.waypoints[0].longitude).toBeCloseTo(6.9, 6);
  });

  it('sizes the used waypoint extent by the record size', () => {
    const header = (magic: number, n: number) => {
      const h = new Uint8Array(6); const dv = new DataView(h.buffer);
      dv.setUint16(0, magic, true); dv.setUint16(2, n, true);
      return Buffer.from(h).toString('base64');
    };
    expect(navUsedSizes(header(0x0334, 10), noRoutes, 16384, 130000).waypointUsed).toBe(0x20 + 520 + 4);
    expect(navUsedSizes(header(0x0336, 10), noRoutes, 16384, 130000).waypointUsed).toBe(0x20 + 540 + 4);
  });
});

// A route that starts at -100 m, as the DESKTOP builder writes it (tools/test_route_send_progress.py
// keeps this file equal to its own output): the altitude of a route point is an int16, and this
// reader gave 65436 m for such a point until 2026-11-22.
describe('RouteReader route point altitudes', () => {
  it('reads altitudes below sea level from what the desktop wrote', () => {
    // eslint-disable-next-line @typescript-eslint/no-var-requires
    const fixture = require('./routeBelowSea.fixture.json');
    const noWaypoints = Buffer.from(new Uint8Array(0x20)).toString('base64');
    const nav = decodeNavigation(noWaypoints, fixture.routes);
    expect(nav.routes).toHaveLength(1);
    expect(nav.routes[0].points.map((p: { altitude: number | null }) => p.altitude)).toEqual(fixture.altitudes);
    expect(fixture.altitudes[0]).toBe(-100);
  });
});
