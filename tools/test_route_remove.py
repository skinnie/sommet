#!/usr/bin/env python3
"""Removing a route on the desktop (write_nav.remove_route): the database is rebuilt from every
other route, compared by the name the watch stores (15 UTF-8 bytes), and an unknown name writes
nothing. Same rule as the Android app's removeRouteFromWatch(). No watch, no captures."""

import io
import contextlib
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import write_nav as W  # noqa: E402
from test_route_same_name import gpx  # noqa: E402


def flash_of(paths):
    with contextlib.redirect_stdout(io.StringIO()):
        flash, _ = W.build_routes(paths, None)
    return flash


def kept_names(flash, name):
    with tempfile.TemporaryDirectory() as d:
        out = []
        for i, text in enumerate(W.routes_to_keep_as_gpx(flash, name)):
            path = pathlib.Path(d) / f"{i}.gpx"
            path.write_text(text, encoding="utf-8")
            out.append(path)
        with contextlib.redirect_stdout(io.StringIO()):
            return [W.route_from_gpx(p, 0, 0, 0, (1, 1, 0, 0, 0, 0)).name for p in out]


def main():
    with tempfile.TemporaryDirectory() as d:
        a = gpx(d, "a", "SommetTest", 3.06)
        b = gpx(d, "b", "Paris-Brest-Paris 2027", 3.10)
        c = gpx(d, "c", "Arras - Amiens", 3.20)
        flash = flash_of([a, b, c])
        assert kept_names(flash, "SommetTest") == ["Paris-Brest-Par", "Arras - Amiens"]
        # the name as the watch shows it, cut to 15 bytes, finds the route too
        assert kept_names(flash, "Paris-Brest-Par") == ["SommetTest", "Arras - Amiens"]
        # the last route: nothing left, the database is then reset
        assert kept_names(flash_of([a]), "SommetTest") == []
        try:
            W.routes_to_keep_as_gpx(flash, "Not there")
        except LookupError as e:
            assert "Not there" in str(e) and "no longer on the watch" in str(e)
        else:
            raise AssertionError("an unknown route must not be 'removed'")
        # a dry-run link never reaches the watch
        link = W.Link(dry_run=True)
        assert not W.remove_route(link, "SommetTest")["ok"] and link.sent == []
    print("ok  a removed route leaves every other one; an unknown name writes nothing")


if __name__ == "__main__":
    main()
