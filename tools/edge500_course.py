#!/usr/bin/env python3
"""Send a GPX route to a mass-storage Garmin Edge (Edge 500 & co.) as a TCX course, stdlib only.

    ./tools/edge500_course.py convert route.gpx out.tcx --name "Serra"   # GPX -> TCX, no device
    ./tools/edge500_course.py send route.gpx /media/<user>/GARMIN --name "Serra"

The Edge 500's own Garmin/GarminDevice.xml lists "FitnessCourses" as TCX (TrainingCenterDatabase
v2), InputToUnit, in Garmin/NewFiles - the path Garmin Training Center used. The Edge turns the
TCX into Garmin/Courses/<name>_course.fit when it leaves USB mode, and it then shows under
Training > Courses. Limits from the same XML: 50 courses, 10000 course points, 500000 track
points in total; we thin one course to MAX_POINTS so a long brevet still loads quickly.

A course needs a time per point (for the Virtual Partner); like Garmin's own converters we lay
the points out at a steady SPEED_KMH from a fixed start, so the course is the same each time.
"""

import argparse
import math
import os
import re
import sys
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone

NAME_MAX = 15            # the Edge 500 cuts course names on screen; TCX Name is <= 15 chars
MAX_POINTS = 3000
SPEED_KMH = 25.0
START = datetime(2010, 1, 1, tzinfo=timezone.utc)
NEWFILES_REL = os.path.join("Garmin", "NewFiles")


def parse_gpx(text):
    """[(lat, lon, ele|None)] from track points, else route points."""
    root = ET.fromstring(text)
    pts = []
    for tag in ("trkpt", "rtept"):
        for el in root.iter():
            if el.tag.rsplit("}", 1)[-1] != tag:
                continue
            ele = None
            for ch in el:
                if ch.tag.rsplit("}", 1)[-1] == "ele" and (ch.text or "").strip():
                    try:
                        ele = float(ch.text)
                    except ValueError:
                        ele = None
            pts.append((float(el.get("lat")), float(el.get("lon")), ele))
        if pts:
            break
    return pts


def haversine(a, b):
    r = 6371008.8
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(min(1.0, math.sqrt(h)))


def thin(pts, n=MAX_POINTS):
    """Keep at most n points, evenly spread, always keeping the first and last."""
    if len(pts) <= n:
        return list(pts)
    step = (len(pts) - 1) / (n - 1)
    return [pts[round(i * step)] for i in range(n)]


def course_name(name):
    clean = re.sub(r"[^\w \-]", "", name or "", flags=re.UNICODE).strip() or "Sommet"
    return clean[:NAME_MAX].strip()


def _ts(t):
    return t.strftime("%Y-%m-%dT%H:%M:%SZ")


def to_tcx(pts, name):
    if len(pts) < 2:
        raise ValueError("a route needs at least 2 points")
    pts = thin(pts)
    name = course_name(name)
    dist = [0.0]
    for a, b in zip(pts, pts[1:]):
        dist.append(dist[-1] + haversine(a, b))
    mps = SPEED_KMH / 3.6
    total_s = dist[-1] / mps
    x = ['<?xml version="1.0" encoding="UTF-8"?>',
         '<TrainingCenterDatabase xmlns="http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2">',
         '<Folders><Courses><CourseFolder Name="Courses"><CourseNameRef><Id>%s</Id></CourseNameRef>'
         '</CourseFolder></Courses></Folders>' % _esc(name),
         '<Courses><Course><Name>%s</Name>' % _esc(name),
         '<Lap><TotalTimeSeconds>%.1f</TotalTimeSeconds><DistanceMeters>%.1f</DistanceMeters>'
         % (total_s, dist[-1]),
         '<BeginPosition><LatitudeDegrees>%.6f</LatitudeDegrees><LongitudeDegrees>%.6f'
         '</LongitudeDegrees></BeginPosition>' % pts[0][:2],
         '<EndPosition><LatitudeDegrees>%.6f</LatitudeDegrees><LongitudeDegrees>%.6f'
         '</LongitudeDegrees></EndPosition>' % pts[-1][:2],
         '<Intensity>Active</Intensity></Lap>', '<Track>']
    for (lat, lon, ele), d in zip(pts, dist):
        t = START + timedelta(seconds=d / mps)
        x.append('<Trackpoint><Time>%s</Time><Position><LatitudeDegrees>%.6f</LatitudeDegrees>'
                 '<LongitudeDegrees>%.6f</LongitudeDegrees></Position>%s<DistanceMeters>%.1f'
                 '</DistanceMeters></Trackpoint>'
                 % (_ts(t), lat, lon,
                    ('<AltitudeMeters>%.1f</AltitudeMeters>' % ele) if ele is not None else "", d))
    x += ['</Track></Course></Courses></TrainingCenterDatabase>', '']
    return "\n".join(x)


def _esc(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def send(gpx_text, mount, name):
    nf = os.path.join(mount, NEWFILES_REL)
    if not os.path.isdir(nf):
        raise FileNotFoundError(f"{nf} missing - is this an Edge 500 drive?")
    tcx = to_tcx(parse_gpx(gpx_text), name)
    fname = re.sub(r"[^\w\-]", "_", course_name(name)) + ".tcx"
    dst = os.path.join(nf, fname)
    data = tcx.encode("utf-8")
    with open(dst, "wb") as fh:
        fh.write(data)
        fh.flush()
        os.fsync(fh.fileno())
    if open(dst, "rb").read() != data:
        raise RuntimeError("verify after write failed")
    return {"ok": True, "written": dst, "name": course_name(name),
            "note": "Unplug the Edge: the course appears under Training > Courses."}


def main(argv=None):
    import json
    ap = argparse.ArgumentParser(description="GPX -> Edge 500 TCX course")
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("convert"); c.add_argument("gpx"); c.add_argument("out")
    c.add_argument("--name", default="")
    s = sub.add_parser("send"); s.add_argument("gpx"); s.add_argument("mount")
    s.add_argument("--name", default="")
    a = ap.parse_args(argv)
    try:
        text = open(a.gpx, encoding="utf-8").read()
        name = a.name or os.path.splitext(os.path.basename(a.gpx))[0]
        if a.cmd == "convert":
            open(a.out, "w", encoding="utf-8").write(to_tcx(parse_gpx(text), name))
            print(json.dumps({"ok": True, "out": a.out}))
        else:
            print(json.dumps(send(text, a.mount, name)))
        return 0
    except (ValueError, OSError, RuntimeError, ET.ParseError) as exc:
        print(json.dumps({"ok": False, "error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
