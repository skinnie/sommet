#!/usr/bin/env python3
"""Checks for kailash_steps.py. No watch needed.

The replies are built here in the shape the Kailash 2.0.5 program gives (one page of 944 bytes,
or 256-byte pages whose continuation pages carry no "SBEM0102"), with made-up counts. When the
emulation outputs of the firmware research are on this computer
(sommet-builds/kailash-steps-re/emu/), the real bytes the watch program produced are checked too.
"""
import os
import pathlib
import struct
import sys

import kailash_steps as K
import sbem_schema

HERE = pathlib.Path(__file__).resolve().parent
RESEARCH = pathlib.Path(os.environ.get(
    "SOMMET_KAILASH_STEPS_RE", HERE.parent.parent / "sommet-builds" / "kailash-steps-re"))


def sample(text, n):
    return text.encode() + b"\0" + struct.pack("<I", n)


def element_list(counts, today_text, today):
    out = [sample(f"2026-10-{6 + i:02d}T23:59:59+00:00", n) for i, n in enumerate(counts)]
    return out + [sample(today_text, today)]


def group(eid, data):
    if len(data) < 0xFF:
        return bytes([eid, len(data)]) + data
    return bytes([eid, 0xFF]) + struct.pack("<I", len(data)) + data


class FakeLink:
    def __init__(self, pages):
        self.pages, self.sent = list(pages), []

    def command(self, command, payload=b"", expect_reply=True, quiet=False):
        self.sent.append((command, payload))
        return self.pages.pop(0)


def main():
    fails = []

    def check(name, cond):
        if not cond:
            fails.append(name)

    counts = [K.NO_COUNT] * 25 + [9100, 12034, 4210, 0, 7650]
    elems = element_list(counts, "2026-11-26T09:20:00+00:00", 3120)

    # 1. one page, as the watch sends it (944 bytes after the 6-byte cursor header)
    one = struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + group(0x8E, b"".join(elems))
    check("one page is 944 bytes", len(one) - 6 == 944)
    link = FakeLink([one])
    out = K.decode(K.read_raw(link, 0x91), 0x8E)
    check("one request", len(link.sent) == 1 and link.sent[0] == (0x1200, K.request(0x91)))
    check("today", out["today"] == {"time": "2026-11-26T09:20:00+00:00", "date": "2026-11-26",
                                    "steps": 3120})
    check("history raw", out["history_raw"] == [None] * 25 + [9100, 12034, 4210, 0, 7650])

    # 2. 256-byte pages: 8 + 8 + 8 + 7 samples, cursors 0x2000.., continuation without magic
    chunks = [elems[0:8], elems[8:16], elems[16:24], elems[24:]]
    cursors = [0x2000, 0x4000, 0x6000, 0]
    pages = []
    for i, (chunk, cur) in enumerate(zip(chunks, cursors)):
        body = group(0x8E, b"".join(chunk))
        pages.append(struct.pack("<IH", cur, 1 if cur == 0 else 0)
                     + (sbem_schema.MAGIC if i == 0 else b"") + body)
    link = FakeLink(pages)
    paged = K.decode(K.read_raw(link, 0x91), 0x8E)
    check("paged = one page", paged == out)
    check("paged cursors asked", [struct.unpack_from("<I", p, 0)[0] for _c, p in link.sent]
          == [0, 0x2000, 0x4000, 0x6000])

    # 3. today alone: André's Kailash right after a GPS fix set its clock from 2015 to 2026
    #    (USB, 2026-11-26 21:03; the watch showed about 15 steps). The values of that real reply.
    alone = struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + group(
        0x8E, sample("2026-11-26T21:03:09+00:00", 15))
    got = K.decode(K.read_raw(FakeLink([alone]), 0x91), 0x8E)
    check("today alone", got["today"] == {"time": "2026-11-26T21:03:09+00:00",
                                          "date": "2026-11-26", "steps": 15}
          and got["history_raw"] == [])
    #    more than 30 days before today, or no sample at all, is refused, not guessed
    too_many = struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + group(
        0x8E, b"".join([elems[0]] + elems))
    empty = struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + group(0x8E, b"")
    for name, reply in (("32 samples refused", too_many), ("no sample refused", empty)):
        try:
            K.decode(K.read_raw(FakeLink([reply]), 0x91), 0x8E)
            check(name, False)
        except ValueError:
            pass

    # 4. the tool only ever asks 0x1200 (no 0x1101, no 0x1201)
    check("read only", all(c == 0x1200 for c, _p in link.sent))

    # 5. ids by name from the Kailash's own descriptor (assets/, skipped without it); the
    #    Peak's TimelinePart has no StepCount, so it gives none
    import glob
    for pattern, want in (("descr+*+2.0.5", {"query": 0x91, "samples": 0x8E}),
                          ("descr+*+2.4.17", None)):
        found = sorted(glob.glob(str(HERE.parent / "assets" / "**" / pattern), recursive=True))
        if found:
            check(f"ids {pattern[-6:]}", K.ids_from_schema(sbem_schema.load(found[0])) == want)

    # 6. the bytes the Kailash program itself produced in the emulation (35 midnights)
    def hex_lines(name):
        p = RESEARCH / "emu" / name
        return [bytes.fromhex(l.split()[0]) for l in p.read_text().splitlines()
                if l.startswith("   ")] if p.is_file() else None
    single = hex_lines("out_query91_35.txt")
    paged_hex = hex_lines("out_query91_35_pages256.txt")
    if single and paged_hex:
        real = K.decode(K.page_entries(struct.pack("<IH", 0, 1) + single[0], True), 0x8E)
        curs = [0x2000, 0x4000, 0x6000, 0]
        real_pages = [struct.pack("<IH", c, 1 if c == 0 else 0) + b for c, b in zip(curs, paged_hex)]
        from_pages = K.decode(K.read_raw(FakeLink(real_pages), 0x91), 0x8E)
        check("emulated: 31 samples", len(real["history_raw"]) == 30)
        check("emulated: pages = one page", from_pages == real)

    if fails:
        print("FAILED:", ", ".join(fails))
        return 1
    print("kailash_steps: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
