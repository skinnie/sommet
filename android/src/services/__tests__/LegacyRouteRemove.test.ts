// Removing a route from an Ambit1/2 (AmbitLegacyNav.removeLegacyRoute,
// LegacyRoute.removeLegacyRouteFromRegion): the same bytes as the desktop's tools/legacy_route.py
// remove(). tools/test_legacy_route_remove.py checks the Python side against the region and
// waypoint list SuuntoLink wrote with three routes (zzambit1full.pcap) and asserts the same FNV-1a
// as below for this fixture. No capture shows SuuntoLink removing a route; not seen on a watch.

import { Buffer } from 'buffer';

const mockState: {
  region: Uint8Array; list: any[]; writes: string[]; keepOldList: boolean;
} = { region: new Uint8Array(0), list: [], writes: [], keepOldList: false };

jest.mock('../../native/AmbitUsbModule', () => {
  const { Buffer: B } = require('buffer');
  return {
    isBleTransportActive: () => false,
    connect: jest.fn(async () => {}),
    disconnect: jest.fn(async () => {}),
    readLegacyRegion: jest.fn(async (_addr: number, len: number) =>
      B.from(mockState.region.subarray(0, len)).toString('base64')),
    readLegacyNav: jest.fn(async () => JSON.stringify({ waypoints: mockState.list })),
    writeLegacyRegion: jest.fn(async (_addr: number, b64: string) => {
      mockState.writes.push('region');
      mockState.region = Uint8Array.from(B.from(b64, 'base64'));
      return true;
    }),
    writeLegacyWaypoints: jest.fn(async (b64: string) => {
      mockState.writes.push('list');
      const n = B.from(b64, 'base64').length / 48;
      if (!mockState.keepOldList) mockState.list = mockState.list.slice(0, n);
      return n;
    }),
  };
});

import { appendLegacyRoutes, parseRoutes, removeLegacyRouteFromRegion, POINTS_OFFSET } from '../LegacyRoute';
import { legacyWaypointsWithoutRoute, removeLegacyRoute, LegacyWaypoint } from '../AmbitLegacyNav';

// The fixture of LegacyRoute.test.ts (and of tools/test_legacy_route_remove.py test_android_parity).
const a = { name: 'Cap Roux loop', distanceM: 5210,
  points: Array.from({ length: 40 }, (_, i) => ({ lat: 43.4521 + 0.0011 * Math.sin(i / 3), lon: 6.8803 + 0.0013 * i })) };
const b = { name: 'Dramont', distanceM: 2400,
  points: Array.from({ length: 25 }, (_, i) => ({ lat: 43.41 + 0.0007 * i, lon: 6.85 - 0.0009 * Math.cos(i / 2) })) };
const fnv = (d: Uint8Array) => { let h = 0x811c9dc5; for (const x of d) h = Math.imul(h ^ x, 0x01000193) >>> 0; return h; };

const wp = (name: string, routeName = ''): LegacyWaypoint =>
  ({ name, routeName, lat_e7: 434500000, lon_e7: 68800000, type: routeName ? 17 : 0 });

describe('removeLegacyRouteFromRegion (same bytes as tools/legacy_route.py remove)', () => {
  const both = appendLegacyRoutes(appendLegacyRoutes(null, [a]), [b]);

  it('matches the Python bytes and keeps the other route byte for byte', () => {
    const r = removeLegacyRouteFromRegion(both, 'Cap Roux loop ');
    expect(r.bytes.length).toBe(2632);
    expect(fnv(r.bytes).toString(16)).toBe((0x09df91c2).toString(16));
    expect(r.removed).toEqual({ name: 'Cap Roux loop', pointCount: 40 });
    expect(r.left).toEqual(['Dramont']);
    // Dramont's 25 points were stored after Cap Roux's 40: same bytes, now at the start.
    expect(Buffer.from(r.bytes.subarray(POINTS_OFFSET))).toEqual(Buffer.from(both.subarray(POINTS_OFFSET + 8 * 40)));
    expect(parseRoutes(Buffer.from(r.bytes).toString('base64')).map(x => x.name)).toEqual(['Dramont']);
  });

  it('removing what was just appended gives the region back', () => {
    expect(Buffer.from(removeLegacyRouteFromRegion(both, 'Dramont').bytes))
      .toEqual(Buffer.from(appendLegacyRoutes(null, [a])));
  });

  it('refuses the last route, an unknown name and an empty region', () => {
    const one = appendLegacyRoutes(null, [a]);
    expect(() => removeLegacyRouteFromRegion(one, 'Cap Roux loop')).toThrow(/only route/);
    expect(() => removeLegacyRouteFromRegion(both, 'Nope')).toThrow(/No route named/);
    expect(() => removeLegacyRouteFromRegion(null, 'Dramont')).toThrow(/no routes/);
  });
});

describe('legacyWaypointsWithoutRoute', () => {
  const list = [wp('Home'), wp('Start', 'Dramont'), wp('Left 1.2', 'Dramont'), wp('Start', 'Cap Roux loop')];
  it("drops the route's own waypoints and keeps the POIs and other routes'", () => {
    const r = legacyWaypointsWithoutRoute(list, 'Dramont ', ['Cap Roux loop']);
    expect(r.dropped).toBe(2);
    expect(r.keep.map(w => `${w.name}/${w.routeName}`)).toEqual(['Home/', 'Start/Cap Roux loop']);
  });
  it('keeps them when another route left has the same name', () => {
    expect(legacyWaypointsWithoutRoute(list, 'Dramont', ['Dramont'])).toEqual({ keep: list, dropped: 0 });
  });
});

describe('removeLegacyRoute', () => {
  const both = appendLegacyRoutes(appendLegacyRoutes(null, [a]), [b]);
  const list = [wp('Home'), wp('Start', 'Cap Roux loop'), wp('End', 'Cap Roux loop'), wp('Start', 'Dramont')];
  beforeEach(() => {
    mockState.region = both; mockState.list = list; mockState.writes = []; mockState.keepOldList = false;
  });

  it('saves both copies, writes the region then the list', async () => {
    const copies: [string, string][] = [];
    const r = await removeLegacyRoute('Cap Roux loop', async (rb, j) => { copies.push([rb, j]); return '/copy'; });
    expect(r).toEqual({ routesLeft: 1, waypointsRemoved: 2, backupPath: '/copy' });
    expect(mockState.writes).toEqual(['region', 'list']);
    expect(Buffer.from(copies[0][0], 'base64')).toEqual(Buffer.from(both));
    expect(JSON.parse(copies[0][1]).length).toBe(4);
    expect(parseRoutes(Buffer.from(mockState.region).toString('base64')).map(x => x.name)).toEqual(['Dramont']);
  });

  it('writes both copies back when the watch keeps the old list', async () => {
    mockState.keepOldList = true;
    await expect(removeLegacyRoute('Cap Roux loop', async () => '/copy')).rejects.toThrow(/written back/);
    expect(mockState.writes).toEqual(['region', 'list', 'region', 'list']);
    expect(Buffer.from(mockState.region)).toEqual(Buffer.from(both));
  });
});
