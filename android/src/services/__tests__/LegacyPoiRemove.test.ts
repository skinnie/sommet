jest.mock('../../native/AmbitUsbModule', () => ({}));
import { legacyWaypointsWithout, LegacyWaypoint } from '../AmbitLegacyNav';

const wp = (name: string, lat: number, lon: number, routeName = ''): LegacyWaypoint =>
  ({ name, routeName, lat_e7: Math.round(lat * 1e7), lon_e7: Math.round(lon * 1e7), type: 0 });

describe('legacyWaypointsWithout (Ambit1/2 POI removal)', () => {
  const list = [wp('Home', 37.0194, -7.9322), wp('Start', 37.1, -7.9, 'Loop'), wp('Home', 37.1, -7.9, 'Loop'),
                wp('Spring', 37.2, -8.0), wp('Solo', 37.3, -8.1, 'Solo')];
  it('takes the standalone POI and keeps the order of the rest', () => {
    const r = legacyWaypointsWithout(list, [{ name: 'Spring', latitude: 37.2, longitude: -8.0 }]);
    expect(r.removed).toBe(1);
    expect(r.keep.map(w => w.name)).toEqual(['Home', 'Start', 'Home', 'Solo']);
  });
  it('never takes a route turn-point, even with the same name and place', () => {
    const r = legacyWaypointsWithout(list, [{ name: 'Home', latitude: 37.1, longitude: -7.9 }]);
    expect(r.removed).toBe(0);
    expect(r.keep).toEqual(list);
  });
  it('a route name only one waypoint carries is a POI (the POI screen lists it)', () => {
    const r = legacyWaypointsWithout(list, [{ name: 'Solo', latitude: 37.3, longitude: -8.1 }]);
    expect(r.removed).toBe(1);
  });
});
