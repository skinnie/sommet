// A route's climb and descent, the rule the desktop (tools/ambit_format.py route_elevation_m)
// and SuuntoLink use: every rise and every drop between points that have an altitude, a gap
// bridged to the last point that had one, in whole metres rounded down. The watch shows what
// the route write puts in its descriptor, so Android must count the same way: it rounded to
// nearest and lost the rise across a point without altitude (desktop audit 2026-10-29).
export function routeElevationM(elevations: (number | null | undefined)[]): { ascentM: number; descentM: number } {
  let up = 0;
  let down = 0;
  let prev: number | null = null;
  for (const alt of elevations) {
    if (alt === null || alt === undefined || !Number.isFinite(alt)) continue;
    if (prev !== null) {
      if (alt > prev) up += alt - prev;
      else down += prev - alt;
    }
    prev = alt;
  }
  return { ascentM: Math.min(Math.floor(up), 0xffff), descentM: Math.min(Math.floor(down), 0xffff) };
}
