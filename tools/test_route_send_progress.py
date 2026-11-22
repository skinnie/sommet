#!/usr/bin/env python3
"""A route write reports where it is (write_nav.send_plan's `progress`), so the Routes page can
show it over Bluetooth, where a long route takes minutes. The packs sent are the same with and
without it. No watch, no captures."""

import io
import contextlib
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
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

    # A point below sea level (a coast road at -1 m) no longer breaks the route: it goes as
    # 0 m. Found 2026-11-22 with a 207 km route that never left the computer.
    import ambit_format as F  # noqa: E402
    assert F.route_point_altitude(None) == F.ALTITUDE_NONE
    assert [F.route_point_altitude(e) for e in (-1, -0.4, 0, 12.9, 8848, 40000)] == \
        [0, 0, 0, 12, 8848, F.ALTITUDE_NONE - 1]
    with tempfile.TemporaryDirectory() as d:
        path = pathlib.Path(gpx(d, "low", "Coast", 3.06))
        text = path.read_text(encoding="utf-8")
        import re  # noqa: E402
        low, n = re.subn(r"(<rtept\b[^>]*)/>", r"\1><ele>-1</ele></rtept>", text, count=1)
        if not n:
            low, n = re.subn(r"(<rtept\b[^>]*>)", r"\1<ele>-1</ele>", text, count=1)
        assert n == 1, "the helper GPX has no <rtept> to lower"
        path.write_text(low, encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            W.build_routes([path], None)
    print("ok  a route with a point below sea level builds (0 m)")


if __name__ == "__main__":
    main()
