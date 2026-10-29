import { routeElevationM } from '../RouteClimb';

// Same cases as tools/ambit_format.py route_elevation_m, which matches SuuntoLink.
describe('routeElevationM', () => {
  it('sums every rise and drop, rounded down', () => {
    expect(routeElevationM([100, 110.6, 105, 120.9])).toEqual({ ascentM: 26, descentM: 5 });
  });
  it('bridges a point without altitude to the last one that had one', () => {
    expect(routeElevationM([100, null, 130, undefined, 120])).toEqual({ ascentM: 30, descentM: 10 });
  });
  it('is 0/0 without altitudes', () => {
    expect(routeElevationM([null, null])).toEqual({ ascentM: 0, descentM: 0 });
  });
});
