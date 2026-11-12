"""Fills a scratch activities.db with made-up activities for the README screenshots.

Nothing comes from real data: loops are random smooth curves around Chamonix (water sports on
Lac d'Annecy), 12 sports, June to November 2026, recorded by Testing mode's sample watch.

    # 1. a scratch home, so nothing touches your own data
    export HOME=/path/to/scratch-home
    # 2. start the app once (creates the empty database), quit it, then:
    python3 tools/make_screenshot_activities.py ~/.local/share/Sommet/Sommet/activities.db
    # 3. backend in Testing mode: POST /api/demo {"enabled": true, "variant": "Emu"}

Same seed every run, so the screenshots can be retaken with the same activities.
"""
import datetime as dt
import json
import math
import random
import sqlite3
import sys

random.seed(7)
DB = sys.argv[1]
DEVICE = "DEMO000000000"
BASE = [(45.9237, 6.8694), (45.9050, 6.8350), (45.9480, 6.9050), (45.8950, 6.9000)]
# name, sport_type_raw, km range, speed km/h, hr mean, climb factor
LAKE = [(45.8560, 6.1720), (45.8350, 6.2050)]          # Lac d'Annecy, for water sports
# name, sport id (ActivityTypes.qml), km range, speed km/h, hr mean, climb factor, where
#   where: "mtn" = around Chamonix, "lake" = Lac d'Annecy, None = no GPS (pool, indoor)
SPORTS = [
    ("Running", 3, (6, 14), 11.5, 148, 0.6, "mtn"),
    ("Trail running", 82, (10, 24), 8.0, 152, 2.2, "mtn"),
    ("Cycling", 4, (35, 90), 25.0, 138, 1.0, "mtn"),
    ("Mountain biking", 5, (18, 40), 14.0, 145, 1.8, "mtn"),
    ("Hiking", 96, (8, 18), 4.2, 118, 2.8, "mtn"),
    ("Walking", 12, (3, 7), 5.0, 100, 0.4, "mtn"),
    ("Alpine skiing", 20, (15, 35), 12.0, 125, 3.0, "mtn"),
    ("Ski touring", 78, (8, 16), 3.5, 140, 4.0, "mtn"),
    ("Openwater swimming", 83, (1.5, 3.5), 2.6, 135, 0.0, "lake"),
    ("Standup paddling", 89, (5, 10), 6.0, 115, 0.0, "lake"),
    ("Pool swimming", 6, (1.5, 3.0), 2.8, 130, 0.0, None),
    ("Indoor cycling", 17, (20, 35), 30.0, 145, 0.0, None),
]
WEIGHTS = [6, 4, 4, 2, 3, 2, 2, 2, 1, 1, 2, 1]


def terrain(lat, lon):
    """A smooth made-up valley: low along the Arve, rising to both sides."""
    d = abs((lat - 45.92) * 1.0 - (lon - 6.87) * 0.55)
    return 1035 + 9000 * d * d * 40 + 80 * math.sin(lat * 900) * math.cos(lon * 700)


def loop(c, km):
    n = max(120, int(km * 60))
    harm = [(random.uniform(0.2, 0.6), random.uniform(0, 6.28)) for _ in range(4)]
    r0 = km / (2 * math.pi) / 111.0
    pts = []
    for i in range(n + 1):
        t = 2 * math.pi * i / n
        r = r0 * (1 + sum(a * math.sin((k + 2) * t + p) for k, (a, p) in enumerate(harm)) * 0.35)
        pts.append((c[0] + r * math.sin(t), c[1] + r * math.cos(t) / math.cos(math.radians(c[0]))))
    return pts


def hav(a, b):
    R = 6371000
    p1, p2 = math.radians(a[0]), math.radians(b[0])
    dp, dl = p2 - p1, math.radians(b[1] - a[1])
    h = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * R * math.asin(math.sqrt(h))


db = sqlite3.connect(DB)
db.execute("DELETE FROM activities")
day = dt.datetime(2026, 11, 11, 7, 30, tzinfo=dt.timezone.utc)
idx = 0
while day > dt.datetime(2026, 6, 1, tzinfo=dt.timezone.utc):
    name, raw, (lo, hi), speed, hrm, climb, where = random.choices(SPORTS, WEIGHTS)[0]
    km = random.uniform(lo, hi)
    c = random.choice(LAKE if where == "lake" else BASE)
    c = (c[0] + random.uniform(-0.01, 0.01), c[1] + random.uniform(-0.015, 0.015))
    pts = loop(c, km)
    dist = sum(hav(pts[i], pts[i + 1]) for i in range(len(pts) - 1))
    dur = dist / 1000 / speed * 3600 * random.uniform(0.9, 1.1)
    start = day.replace(hour=random.choice([6, 7, 8, 12, 17, 18]), minute=random.randint(0, 59))
    # Smooth made-up profile (two slow hills along the loop), scaled to a believable climb.
    raw_e = [math.sin(2 * math.pi * i / len(pts)) + 0.4 * math.sin(6 * math.pi * i / len(pts) + 1)
             for i in range(len(pts))]
    raw_up = sum(max(0, raw_e[i + 1] - raw_e[i]) for i in range(len(raw_e) - 1)) or 1
    target = km * climb * 14 * random.uniform(0.8, 1.2)
    base = 447 if where == "lake" else 1035 + random.uniform(0, 300)
    eles = [base + (e - min(raw_e)) * target / raw_up for e in raw_e]
    ascent = sum(max(0, eles[i + 1] - eles[i]) for i in range(len(eles) - 1))
    track, trkpts = [], []
    for i, (p, e) in enumerate(zip(pts, eles)):
        t = start + dt.timedelta(seconds=dur * i / (len(pts) - 1))
        hr = int(hrm + 10 * math.sin(i / 25) + random.uniform(-3, 3) + (eles[i] - eles[i - 1]) * 0.8 if i else hrm - 20)
        track.append({"ele": round(e, 1), "lat": round(p[0], 7), "lon": round(p[1], 7)})
        trkpts.append(f'<trkpt lat="{p[0]:.7f}" lon="{p[1]:.7f}"><ele>{e:.1f}</ele><time>{t:%Y-%m-%dT%H:%M:%SZ}</time>'
                      f'<extensions><gpxtpx:TrackPointExtension><gpxtpx:hr>{hr}</gpxtpx:hr>'
                      f'</gpxtpx:TrackPointExtension></extensions></trkpt>')
    gpx = ('<?xml version="1.0" encoding="UTF-8"?>\n<gpx version="1.1" creator="Sommet demo" '
           'xmlns="http://www.topografix.com/GPX/1/1" '
           'xmlns:gpxtpx="http://www.garmin.com/xmlschemas/TrackPointExtension/v1">'
           f'<trk><name>{name}</name><type>{name}</type><trkseg>' + "".join(trkpts) + '</trkseg></trk></gpx>')
    kcal = int(dur / 60 * random.uniform(6, 12))
    if where is None:
        dist = km * 1000
        dur = km / speed * 3600 * random.uniform(0.9, 1.1)
        ascent, track, gpx = 0, [], None
    db.execute("INSERT INTO activities (idx, name, duration_s, distance_m, ascent_m, energy_kcal, sport_type_raw,"
               " start_time, track_json, gpx_text, source, device, updated_at, start_utc)"
               " VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
               (idx, name, int(dur), round(dist, 1), round(ascent), kcal, raw,
                start.strftime("%Y-%m-%dT%H:%M:%S.000Z"), (json.dumps(track, separators=(",", ":")) if track else None), gpx,
                "watch", DEVICE, int(start.timestamp() * 1000), int(start.timestamp())))
    idx += 1
    day -= dt.timedelta(days=random.choice([1, 1, 2, 2, 3]))
db.commit()
print(idx, "activities")
