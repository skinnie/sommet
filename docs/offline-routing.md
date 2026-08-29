# Offline routing, climb colouring & POI search

An OruxMaps-style, fully-offline route planner for biking and trekking, built into Sommet:
plan a route with no internet, see it painted by climb steepness, search points of interest
locally, and push the result to the watch through the GPX export Sommet already has. Nothing
here calls a cloud service; once the region data is downloaded it works on a plane.

It is built entirely on open source and the *same* OpenStreetMap data that Garmin `.img`
maps are compiled from, so a planned route stays consistent with what an eTrex shows. No
open-source engine routes on `.img` directly (it's a closed, device-side render format) — so
we route on OSM, the source `.img` itself is built from, and keep `.img` for the device.

## The pieces

| Concern | What we use | Where |
|---|---|---|
| Routing engine | **BRouter** (MIT) — offline, elevation-aware, bike/hike profiles, `.rd5` segments from OSM | local server on :17777 |
| Route planning | `tools/route_plan.py` — queries BRouter, normalises to lat/lon/ele | backend `/api/router/*` |
| Climb colouring | `tools/track_color.py` — resample + smooth + classify by gradient | backend `/api/router/color` |
| POI search | `tools/poi_search.py` — SQLite (FTS5 + R*Tree) built from an OSM extract | backend `/api/poi/search` |
| Map display | mapsforge `.map` vector tiles + `.poi` (download-per-region, OruxMaps-style) | frontend (see below) |
| Elevation fill | SRTM `.hgt` tiles (same data BRouter uses) — only for GPX with no `<ele>` | `geo_util.sample_hgt` |

The three tools are stdlib-only and pure computation — no pip installs, no framework, and
they run offline with a self-test (`--selftest`) and a full suite
(`tools/test_offline_routing.py`, 15 tests). The backend calls them via `run_tool` like every
other endpoint, so the frozen (`--tool`) desktop build works unchanged.

## One-time setup per region

### 1. BRouter server + segments

```sh
# get BRouter (MIT), a small Java jar server
git clone https://github.com/abrensch/brouter && cd brouter && ./gradlew clean build
# download the .rd5 segment tile(s) covering your area (5x5 deg squares)
#   from https://brouter.de/brouter/segments4/   e.g. E5_N45.rd5 for the western Alps
# run the server (defaults to :17777)
./misc/scripts/standalone/server.sh   # or the docker image: saesh/brouter-server
```

Point Sommet at it with `SOMMET_BROUTER_URL` if it isn't the default `http://127.0.0.1:17777`.
Profiles worth exposing in the UI: `trekking`, `fastbike`, `gravel`, `mtb`, `hiking-mountain`.

### 2. POI database

```sh
# extract the POI kinds that matter for bike/trek from a Geofabrik OSM extract
osmium tags-filter region.osm.pbf \
  n/amenity=drinking_water,shelter,cafe,bicycle_repair_station \
  n/tourism=alpine_hut,wilderness_hut,viewpoint,camp_site \
  n/natural=peak,spring,saddle -o pois.osm.pbf
osmium export pois.osm.pbf -f geojson -o pois.geojson
python3 tools/poi_search.py build --db region.poi.sqlite --from pois.geojson
```

(`build --from` also accepts a plain `name,category,lat,lon` CSV.)

### 3. Map tiles (display — separate from routing)

Download a mapsforge `.map` for the region (e.g. from the OpenAndroMaps project) and its
matching `.poi` file. This is the OruxMaps "it asks you to download the area" model. The
frontend renders the `.map`; routing and POI search above are independent of it.

## Backend API

All read-only — none of these touch the watch.

- `GET  /api/router/health` → `{ok, reachable}` — is a BRouter server up?
- `POST /api/router/route` — body `{"via": [[lon,lat],...], "profile": "trekking",
  "color": true, "gpx": true}` → `{ok, points:[{lat,lon,ele}], summary, colored?, gpx?}`.
  `gpx` is ready to hand straight to `POST /api/routes` to send to the watch.
- `POST /api/router/color` — body `{"points": [{lat,lon,ele?}], "step":30, "window":100,
  "hgt":"~/srtm"}` → colour any track (e.g. an imported GPX) by climb.
- `POST /api/poi/search` — body `{"db": "region.poi.sqlite", one of "name" |
  "near":[lon,lat] | "along":[{lat,lon}], "radius", "buffer", "category", "limit"}`.

### `colored` / `/api/router/color` response

```jsonc
{
  "ok": true,
  "segments": [ { "bucket": "steep", "color": "#f46d43",
                  "coords": [[lat,lon],...], "from_m": 550, "to_m": 950,
                  "avg_gradient_pct": 10.0 }, ... ],   // draw one polyline per segment
  "legend":  [ { "key": "steep", "label": "9-12 % steep", "color": "#f46d43",
                 "distance_m": 425, "ascent_m": 42 }, ... ],
  "profile": [ { "dist_m": 0, "ele_m": 400, "grad_pct": 0, "color": "#66bd63" }, ... ],
  "summary": { "distance_m": 1994, "ascent_m": 50, "descent_m": 50,
               "max_gradient_pct": 10, "has_elevation": true }
}
```

`segments` draw the coloured route on the map; `profile` draws the elevation graph in the
**same** colours so the red pitch on the map and the red pitch on the graph are obviously the
same climb.

## Climb colour scale

Cool→hot ramp; descents blue/green, climbs yellow→orange→red→purple. The climb side is a warm
luminance ramp (not red-vs-green) so it stays legible for the common colour blindness. Edit
`DEFAULT_BUCKETS` in `track_color.py` to retune per sport.

| Gradient | Band | Colour |
|---|---|---|
| < −3 % | descent | `#4575b4` |
| −3 – 3 % | flat | `#66bd63` |
| 3 – 6 % | gentle | `#fee08b` |
| 6 – 9 % | moderate | `#fdae61` |
| 9 – 12 % | steep | `#f46d43` |
| 12 – 15 % | very steep | `#d73027` |
| > 15 % | brutal | `#7a0177` |

The same "classify a value along a polyline" machinery re-skins to colour by **surface**
(BRouter returns waytype/surface tags) or, for a synced activity, by **speed/HR** — build the
coloured-polyline renderer once, reuse it everywhere.

## What's left (the frontend)

The data/logic layer above is done and tested. The remaining work is the interactive map
*canvas* in the Qt/QML desktop app and the React Native app: tap start/end, drag to reroute
(re-call `/api/router/route`), draw the coloured segments + elevation profile, and a POI
search box calling `/api/poi/search`. That's UI wiring on top of these endpoints — the engine,
colouring and search are in place.
