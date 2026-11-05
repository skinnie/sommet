#!/usr/bin/env python3
"""Checks tools/edge500_course.py: GPX -> TCX course for the Edge 500. Run: python3 tools/test_edge500_course.py"""

import os
import sys
import tempfile
import xml.etree.ElementTree as ET

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import edge500_course as ec  # noqa: E402

NS = {"t": "http://www.garmin.com/xmlschemas/TrainingCenterDatabase/v2"}


def gpx(n, tag="trkpt"):
    pts = "".join('<%s lat="%.6f" lon="%.6f"><ele>%d</ele></%s>' % (tag, 41.0 + i * 0.001, -8.6, 100 + i, tag)
                  for i in range(n))
    wrap = ("<trk><trkseg>%s</trkseg></trk>" if tag == "trkpt" else "<rte>%s</rte>") % pts
    return '<?xml version="1.0"?><gpx xmlns="http://www.topografix.com/GPX/1/1" version="1.1">%s</gpx>' % wrap


def main():
    tcx = ec.to_tcx(ec.parse_gpx(gpx(10)), "Volta à Serra da Estrela!")
    root = ET.fromstring(tcx)
    name = root.find("t:Courses/t:Course/t:Name", NS).text
    assert name == "Volta à Serra d" and len(name) <= ec.NAME_MAX, name
    assert root.find("t:Folders/t:Courses/t:CourseFolder/t:CourseNameRef/t:Id", NS).text == name
    tps = root.findall("t:Courses/t:Course/t:Track/t:Trackpoint", NS)
    assert len(tps) == 10
    d = [float(tp.find("t:DistanceMeters", NS).text) for tp in tps]
    assert d[0] == 0 and all(b > a for a, b in zip(d, d[1:])), d
    assert abs(d[-1] - 9 * 111.2) < 2, d[-1]           # 0.001 deg lat ~ 111.2 m
    lap = float(root.find("t:Courses/t:Course/t:Lap/t:DistanceMeters", NS).text)
    assert abs(lap - d[-1]) < 0.2
    assert tps[0].find("t:AltitudeMeters", NS).text == "100.0"

    big = ec.parse_gpx(gpx(7001))
    t2 = ET.fromstring(ec.to_tcx(big, "x"))
    assert len(t2.findall(".//t:Trackpoint", NS)) == ec.MAX_POINTS
    assert ec.parse_gpx(gpx(3, "rtept"))[2][0] == 41.002          # route points work too

    m = tempfile.mkdtemp()
    os.makedirs(os.path.join(m, ec.NEWFILES_REL))
    r = ec.send(gpx(5), m, "Test/Route")
    assert os.path.basename(r["written"]) == "TestRoute.tcx", r
    try:
        ec.to_tcx(ec.parse_gpx(gpx(1)), "x")
        raise AssertionError("one point must be refused")
    except ValueError:
        pass
    print("edge500_course: all checks passed")


if __name__ == "__main__":
    main()
