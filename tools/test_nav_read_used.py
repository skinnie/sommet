#!/usr/bin/env python3
"""Over Bluetooth the routes database is read only as far as it is used
(write_nav.read_nav_flash_used): the result decodes to exactly what the whole 146 KB read gives,
in a fraction of the bytes, and a read that does not add up falls back to the whole read.
The watch is a fake built by the app's own route builder. No watch, no captures."""

import io
import contextlib
import pathlib
import struct
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ambit_format as F  # noqa: E402
import write_nav as W  # noqa: E402
from test_route_same_name import gpx  # noqa: E402


class FakeWatch:
    """Answers 0x0b17 from a flash image; unwritten flash reads 0xFF, as erased flash does."""

    def __init__(self, flash, slow):
        self.flash, self.slow_link, self.dry_run, self.read = flash, slow, False, 0

    def command(self, cmd, payload=b"", quiet=False):
        assert cmd == W.CMD_FLASH_READ, hex(cmd)
        address, size = struct.unpack("<II", payload)
        self.read += size
        return payload + self.flash.read(address, size, fill=0xFF)


def read(flash, slow):
    watch = FakeWatch(flash, slow)
    with contextlib.redirect_stdout(io.StringIO()):
        got = W.read_nav_flash(watch)
        gpx_texts = []
        header = F.RouteHeader.parse(got.read(F.ROUTE_BASE, 32))
        if header.magic == F.ROUTE_HEADER_MAGIC:
            gpx_texts = [W.route_to_gpx(got, i) for i in range(header.route_count)]
    return W.nav_summary_json(got), gpx_texts, watch.read


def main():
    whole = F.REGIONS[F.WAYPOINT_BASE][1] + F.REGIONS[F.ROUTE_BASE][1]
    with tempfile.TemporaryDirectory() as d:
        paths = [gpx(d, "a", "SommetTest", 3.06), gpx(d, "b", "Paris-Brest-Paris 2027", 3.10),
                 gpx(d, "c", "Arras - Amiens", 3.20)]
        with contextlib.redirect_stdout(io.StringIO()):
            flash, _ = W.build_routes(paths, None)
            empty, _ = W.build_reset()

    full = read(flash, slow=False)
    used = read(flash, slow=True)
    assert full[2] == whole, (full[2], whole)
    assert len(full[0]["routes"]) == 3
    assert used[:2] == full[:2], "the short read must decode to the same routes"
    assert used[2] < whole // 10, f"read {used[2]} of {whole} bytes"

    # no routes at all: two headers, nothing else
    assert read(empty, slow=True)[:2] == read(empty, slow=False)[:2] == ({"routes": []}, [])
    assert read(empty, slow=True)[2] < 100

    # a database that does not add up (one point changed, the CRC no longer matches): read whole
    odd = W.FlashImage()
    odd.bytes = dict(flash.bytes)
    odd.write(F.ROUTE_POINTS, b"\\x01")
    assert read(odd, slow=True)[2] > whole, "a CRC mismatch must read everything again"
    assert read(odd, slow=True)[:2] == read(odd, slow=False)[:2]

    # a never-written region (all 0xFF): unknown header, read whole, as before
    assert read(W.FlashImage(), slow=True)[2] > whole
    print(f"ok  the routes database over Bluetooth: {used[2]} bytes read instead of {whole}, "
          "same routes; a mismatch reads it all")

    # The databases SuuntoLink itself wrote, from the captures (skipped without assets/).
    from ambit_pcap import FlashImage  # noqa: E402
    captures = pathlib.Path(__file__).resolve().parent.parent / "assets" / "ambit3 pcap"
    seen = 0
    for name in ("route12km", "route128km", "routedelete", "ambit3full"):
        if not (captures / name).exists():
            continue
        real = FlashImage.from_pcap(captures / name)
        short, long_ = read(real, slow=True), read(real, slow=False)
        assert short[:2] == long_[:2], f"{name}: the short read differs from the whole read"
        assert short[2] < long_[2] // 5, (name, short[2])
        seen += 1
    print(f"ok  same on {seen} databases written by SuuntoLink" if seen
          else "ok  (SuuntoLink captures absent, that part skipped)")


if __name__ == "__main__":
    main()
