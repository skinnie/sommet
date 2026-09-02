#!/usr/bin/env python3
"""Slice a GPX to a distance range and print the sub-route as GPX. Used by the Plan page's
multi-day planner to export one day's portion of the route (André, 2026-09-02).

    ./tools/route_slice.py route.gpx --start-km 74 --end-km 148 [--reverse] [--name "Day 2"]

Stdlib only (via geo_util), so it works in the frozen backend like every other tool.
"""
import argparse
import sys

import geo_util


def slice_points(points, start_m, end_m):
    """Return the points whose cumulative distance falls in [start_m, end_m]. Keeps at least the
    two nearest points so a degenerate range still yields a drawable stub."""
    latlon = [(p["lat"], p["lon"]) for p in points]
    cum = geo_util.cumulative_distances(latlon)
    out = [p for i, p in enumerate(points) if cum[i] >= start_m - 1.0 and cum[i] <= end_m + 1.0]
    if len(out) < 2 and points:
        mid = min(range(len(points)), key=lambda k: abs(cum[k] - (start_m + end_m) / 2.0))
        out = points[mid:mid + 2] if mid + 2 <= len(points) else points[-2:]
    return out


def main(argv=None):
    ap = argparse.ArgumentParser(description="Slice a GPX to a distance range (offline).")
    ap.add_argument("track", help="GPX file")
    ap.add_argument("--start-km", type=float, default=0.0)
    ap.add_argument("--end-km", type=float, required=True)
    ap.add_argument("--reverse", action="store_true", help="reverse the route before slicing")
    ap.add_argument("--name", default="Day", help="track name for the sliced GPX")
    args = ap.parse_args(argv)

    raw = open(args.track, "r", encoding="utf-8", errors="replace").read()
    points = geo_util.parse_gpx_points(raw)
    if not points:
        sys.stderr.write("no points found in track\n")
        return 2
    if args.reverse:
        points = list(reversed(points))
    sub = slice_points(points, args.start_km * 1000.0, args.end_km * 1000.0)
    sys.stdout.write(geo_util.points_to_gpx(sub, name=args.name))
    return 0


if __name__ == "__main__":
    sys.exit(main())
