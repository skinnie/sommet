#!/usr/bin/env python3
"""A route write reports where it is (write_nav.send_plan's `progress`), so the Routes page can
show it over Bluetooth, where a long route takes minutes. The packs sent are the same with and
without it. No watch, no captures."""

import base64
import io
import contextlib
import json
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ambit_format as F  # noqa: E402
import write_nav as W  # noqa: E402
from test_route_same_name import gpx  # noqa: E402


def sent_by(flash, layout, progress=None):
    link = W.Link(dry_run=True)
    with contextlib.redirect_stdout(io.StringIO()):
        W.send_plan(link, flash, layout, progress=progress)
    return link.sent


def main():
    with tempfile.TemporaryDirectory() as d:
        with contextlib.redirect_stdout(io.StringIO()):
            flash, layout = W.build_routes([gpx(d, "a", "SommetTest", 3.06)], None)
    seen = []
    with_progress = sent_by(flash, layout, lambda done, total: seen.append((done, total)))
    assert with_progress == sent_by(flash, layout), "progress must not change what is sent"
    data = sum(len(blob) for _name, _addr, blob in layout if blob)
    assert seen and seen[-1] == (data, data), (seen[-1], data)
    assert [done for done, _ in seen] == sorted(done for done, _ in seen), "never goes back"
    assert all(total == data for _, total in seen)
    print(f"ok  a route write reports its progress ({len(seen)} steps, {data} bytes), same packs")

    # A route below sea level keeps its altitudes: the field is an int16 (30000 = none).
    # Found 2026-11-22 with a 207 km route that never left the computer: one point at -1 m
    # made the builder fail. The Android and iOS writer always wrote these bits.
    assert F.route_point_altitude(None) == F.ALTITUDE_NONE
    # whole metres, rounded half away from zero as SuuntoLink does; never under -500 m
    assert [F.route_point_altitude(e) for e in (-100, -1, -0.4, -0.5, 0, 12.4, 12.5, 12.9, 8848,
                                                40000, -9000)] == \
        [-100, -1, 0, -1, 0, 12, 13, 13, 8848, F.ALTITUDE_NONE - 1, -500]
    assert F.RoutePoint(0, 0, -100, 0).build()[8:10] == bytes.fromhex("9cff")
    region, eles = below_sea_region()
    fixture = pathlib.Path(__file__).resolve().parent.parent / "android" / "src" / "services" \
        / "__tests__" / "routeBelowSea.fixture.json"
    wanted = json.dumps({"routes": base64.b64encode(region).decode(), "altitudes": eles}, indent=1) + "\n"
    if "--write-fixture" in sys.argv:
        fixture.write_text(wanted, encoding="utf-8")
    # The same bytes are decoded by the Android reader (RouteReader.test.ts): one file, two apps.
    assert fixture.read_text(encoding="utf-8") == wanted, \
        "the desktop builder no longer gives the bytes Android's test decodes: " \
        "run tools/test_route_send_progress.py --write-fixture and look at the difference"
    print("ok  a route below sea level keeps its altitudes (-100 m), same bytes Android decodes")


BELOW_SEA = (-100, -1, 0, 12)


def below_sea_region():
    """(the routes region as far as it is used, the altitudes read back) of a 4-point route
    that starts at -100 m, built by the desktop and read back by the desktop."""
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(d) / "low.gpx"
        path.write_text(
            '<?xml version="1.0"?><gpx version="1.1" creator="t" '
            'xmlns="http://www.topografix.com/GPX/1/1"><metadata><name>Dead Sea</name></metadata><rte>'
            + "".join(f'<rtept lat="{31.50 + 0.005 * i:.3f}" lon="{35.40 + 0.005 * i:.3f}">'
                      f"<ele>{ele}</ele></rtept>" for i, ele in enumerate(BELOW_SEA))
            + "</rte></gpx>", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            flash, _ = W.build_routes([path], None)
    track = W.nav_summary_json(flash)["routes"][0]["track"]
    eles = [p["ele"] for p in track]
    assert eles == list(BELOW_SEA), eles
    assert "<ele>-100.0</ele>" in W.route_to_gpx(flash, 0)
    used = (F.ROUTE_POINTS - F.ROUTE_BASE) + 12 * len(track)
    return flash.read(F.ROUTE_BASE, used), eles


if __name__ == "__main__":
    main()
