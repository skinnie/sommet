#!/usr/bin/env python3
"""Checks for ambit_steps.py and the long-id SBEM entries it relies on. No watch needed.

The reply below has the shape of a real Ambit3 Peak DeviceHistory reply (fw 2.4.17, read
2026-10-18): short-id ActivityTracking entries, then the steps record and the timestamp in
the long-id form `[ff][u16 id][len]`. The numbers are made up.
"""
import struct
import sys

import ambit_steps as S
import sbem_schema


def entry(eid, data):
    head = bytes([eid]) if eid < 0xFF else b"\xff" + struct.pack("<H", eid)
    return head + bytes([len(data)]) + data


def reply(steps, stamp=b"2026-10-18T13:06:02+03:00\x00", cursor=0):
    body = (entry(0x92, struct.pack("<3H", 77, 76, 75))
            + entry(0x94, struct.pack("<2H", 2933, 680))
            + entry(0x12E, struct.pack("<30I", *steps))
            + entry(0x130, stamp))
    return struct.pack("<IH", cursor, 1) + sbem_schema.MAGIC + body


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

    # 1. the walker reads long ids and keeps short ones
    ids = [eid for eid, _ in sbem_schema.entries(reply([1] * 30)[6:])]
    check("long ids walked", ids == [0x92, 0x94, 0x12E, 0x130])

    # 2. request bytes = the shape of the logbook query, with the DeviceHistory id
    check("request", S.request(0x97) == bytes.fromhex("00000000" "0100" "0a00")
          + b"SBEM0102" + b"\x97\x00")

    # 3. decode: oldest first, no-count slots dropped, last slot dated by the timestamp
    counts = [S.NO_COUNT] * 26 + [17194, 6759, 5798, 5]
    link = FakeLink([reply(counts)])
    out = S.decode(S.read_raw(link, 0x97), S.REFERENCE_IDS)
    check("one request", len(link.sent) == 1 and link.sent[0][0] == 0x1200)
    check("days", [(d["date"], d["steps"]) for d in out["days"]] == [
        ("2026-10-15", 17194), ("2026-10-16", 6759), ("2026-10-17", 5798), ("2026-10-18", 5)])
    check("today flag", [d["today"] for d in out["days"]] == [False, False, False, True])
    check("raw activity", out["activity_tracking"]["recovery"] == [77, 76, 75]
          and out["activity_tracking"]["weekly_activity"] == [2933, 680])

    # 3b. activity and recovery: quarter-hour samples ending at the last quarter hour before
    #     the timestamp (13:06 -> 13:00), weekly days ending today, recovery in 22.5-min units
    act, rec = out["activity"], out["recovery"]
    check("recovery now", rec["hours"] == 28.12 and [s["time"] for s in rec["samples"]] == [
        "2026-10-18T12:30", "2026-10-18T12:45", "2026-10-18T13:00"])
    check("week days", act["week"] == [
        {"date": "2026-10-17", "kcal": 2933, "today": False},
        {"date": "2026-10-18", "kcal": 680, "today": True}] and act["today_kcal"] == 680)
    check("no daily samples", act["samples"] == [])

    # 4. paging: a first page with a cursor is followed by a second request at that cursor
    link = FakeLink([struct.pack("<IH", 0x18, 0) + sbem_schema.MAGIC + entry(0x92, b"\1\0"),
                     reply(counts)])
    S.read_raw(link, 0x97)
    check("paged", len(link.sent) == 2 and link.sent[1][1][:4] == struct.pack("<I", 0x18))

    # 5. a reply with another layout is refused, not decoded
    bad = struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + entry(0x12E, b"\0" * 64)
    try:
        S.decode(S.walk(bad), S.REFERENCE_IDS)
        check("wrong layout refused", False)
    except ValueError:
        pass

    # 6. each model's ids by name from its own descriptor (assets/, skipped without it), and
    #    Android's table (AmbitSteps.ts STEPS_IDS) the same as that lookup
    import glob
    import pathlib
    import re
    here = pathlib.Path(__file__).resolve().parent
    assets = here.parent / "assets"
    expected = {"emu": ("descr+*+2.4.17", dict(S.REFERENCE_IDS)),
                "kaka": ("descr+FROM-FIRMWARE-Kaka+1.1.22",
                         {"query": 0x96, "steps": 0x13A, "timestamp": 0x13C, "recovery": 0x8F,
                          "daily_activity": 0x90, "weekly_activity": 0x91,
                          "total_weekly_activity": 0x92}),
                "jabiru": ("descr+*+2.0.22",
                           {"query": 0x8B, "steps": 0x105, "timestamp": 0x108, "recovery": None,
                            "daily_activity": None, "weekly_activity": None,
                            "total_weekly_activity": None})}
    ts = (here.parent / "android" / "src" / "services" / "AmbitSteps.ts").read_text()
    android = {}
    consts = dict(re.findall(r"const (\w+_IDS): StepsIds = (\{[^}]*\})", ts))
    table = ts[ts.index("export const STEPS_IDS"):]
    table = table[:table.index("\n};")]
    rows = re.split(r"\n  (?=\w+: )", table)[1:]          # one chunk per model, nested braces kept
    for row in rows:
        code, value = row.split(":", 1)
        ref = re.match(r"\s*(\w+_IDS)", value)
        body = consts[ref.group(1)] if ref else value.split("ascent:")[0]
        android[code] = {k: int(v, 16) for k, v in re.findall(r"(\w+): (0x[0-9a-f]+)", body)}
    check("android table names all six", sorted(android) == sorted(
        ["emu", "finch", "ibisbill", "kaka", "jabiru", "loon"]))
    for code, (pattern, want) in expected.items():
        found = sorted(glob.glob(str(assets / "**" / pattern), recursive=True))
        if found:
            ids = S.ids_from_schema(sbem_schema.load(found[0]))
            check(f"{code} ids from its descriptor", {k: ids[k] for k in want} == want)
            ascent = {k: v for k, v in ids.items() if k.startswith("ascent")}
            check(f"{code} ascent ids", ascent == ({
                "ascent_total_time": 0x12D, "ascent_total": 0x12E, "ascent_days": 0x131,
                "ascent_year": 0x132, "ascent_since": 0x133, "ascent_since_total": 0x134,
                "ascent_since_days": 0x137, "ascent_since_months": 0x138} if code == "kaka"
                else {k: None for k in ascent}))
        mine = android.get(code, {})
        check(f"{code} android ids", mine.get("query") == want["query"]
              and mine.get("steps") == want["steps"] and mine.get("timestamp") == want["timestamp"]
              and mine.get("recovery") == want["recovery"]
              and mine.get("weekly") == want["weekly_activity"])
    check("loon = jabiru on android", android.get("loon") == android.get("jabiru"))
    check("sport and run = peak on android",
          android.get("finch") == android.get("emu") == android.get("ibisbill"))

    # 7. a Vertical and a Traverse reply decode with their own ids; the Traverse has no rings
    def reply_with(ids, counts, rings=True):
        body = b""
        if rings:
            body += entry(ids["recovery"], struct.pack("<2H", 40, 41))
            body += entry(ids["weekly_activity"], struct.pack("<2H", 900, 120))
        body += entry(ids["steps"], struct.pack("<30I", *counts))
        body += entry(ids["timestamp"], b"2026-11-26T09:20:00+03:00\x00")
        return struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + body
    counts = [S.NO_COUNT] * 28 + [9100, 412]
    for code, rings in (("kaka", True), ("jabiru", False)):
        ids = expected[code][1]
        out = S.decode(S.walk(reply_with(ids, counts, rings)), ids)
        check(f"{code} days", [(d["date"], d["steps"]) for d in out["days"]] == [
            ("2026-11-25", 9100), ("2026-11-26", 412)])
        check(f"{code} rings", (out["activity"]["today_kcal"] == 120) if rings
              else (out["activity"]["today_kcal"] is None and out["recovery"]["hours"] is None))

    # 8. the Vertical's ascent history from the same reply (vertical-ascent-re/FINDINGS.md):
    #    sums as its views show them, dated from the timestamp, 0 kept as a value
    v = dict(expected["kaka"][1])
    v.update({"ascent_total_time": 0x12D, "ascent_total": 0x12E, "ascent_days": 0x131,
              "ascent_year": 0x132, "ascent_since": 0x133, "ascent_since_total": 0x134,
              "ascent_since_days": 0x137, "ascent_since_months": 0x138})
    week = [0, 120, 0, 450, 30, 0, 210]
    year = [1000, 2000, 0, 0, 0, 0, 0, 0, 0, 0, 4100, 810]
    since_days = [300, 0, 510]                       # reset 3 days ago, today last
    since_months = [810]
    body = (entry(0x12D, struct.pack("<I", 7 * 3600 + 1800)) + entry(0x12E, struct.pack("<I", 52345))
            + entry(0x131, struct.pack(f"<{len(week)}H", *week))
            + entry(0x132, struct.pack("<12I", *year))
            + entry(0x133, b"2026-11-24T00:00:00\x00") + entry(0x134, struct.pack("<I", 810))
            + entry(0x137, struct.pack("<3H", *since_days)) + entry(0x138, struct.pack("<I", 810))
            + entry(v["steps"], struct.pack("<30I", *([0] * 29 + [412])))
            + entry(v["timestamp"], b"2026-11-26T09:20:00+03:00\x00"))
    out = S.decode(S.walk(struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + body), v)
    a = out.get("ascent") or {}
    check("ascent week", a.get("week_m") == 810 and a["week"][-1] == {"date": "2026-11-26", "m": 210}
          and a["week"][0]["date"] == "2026-11-20")
    check("ascent year", a.get("year_m") == 7910 and a["year"][10] == {"month": "2026-11", "m": 4100})
    check("ascent total", a.get("total_m") == 52345 and a.get("total_hours") == 7
          and a.get("total_seconds") == 7 * 3600 + 1800)
    check("ascent since", a.get("since", {}).get("date") == "2026-11-24"
          and a["since"]["total_m"] == 810
          and [d["date"] for d in a["since"]["days"]] == ["2026-11-24", "2026-11-25", "2026-11-26"]
          and a["since"]["months"] == [{"month": "2026-11", "m": 810}])
    check("steps 0 kept on a Vertical", out["days"][0] == {"date": "2026-10-28", "steps": 0, "today": False})
    # the reset day doesn't match the first daily value (clock moved back): totals only
    body2 = body.replace(b"2026-11-24T00:00:00", b"2026-11-23T00:00:00")
    a2 = S.decode(S.walk(struct.pack("<IH", 0, 1) + sbem_schema.MAGIC + body2), v)["ascent"]
    check("ascent since days dropped when misplaced", a2["since"]["days"] is None
          and a2["since"]["total_m"] == 810)
    check("no ascent on a Peak reply", "ascent" not in S.decode(S.walk(reply(counts)), S.REFERENCE_IDS))

    if fails:
        print("FAILED:", ", ".join(fails))
        return 1
    print("ambit_steps: all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
