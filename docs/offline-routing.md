# Route weather + climb colouring (from a GPX)

The Plan page takes a route you've already drawn (in any online planner, Basecamp, Komoot,
RideWithGPS, ...) as a **GPX** and adds the two things those tools don't:

- **Climb colouring** — the route painted by gradient steepness, with an elevation profile and
  a legend, so a hard pitch jumps out before you ride/walk it.
- **Weather along the route** — set a start time + pace, and each point is stamped with its ETA
  and the forecast there: temperature, rain, head/cross/tail wind, plus a sun/moon summary and
  a plain-language verdict.

Then it can send the route straight to the watch (the same GPX route write as the Routes page).

This is the same feature on all three platforms — desktop Plan page, and the Android/iOS
**Route weather** screen, which imports a GPX the same way.

## The pieces

| Concern | What we use | Where |
|---|---|---|
| Climb colouring | `tools/track_color.py` — resample + smooth + classify by gradient (parses GPX directly) | backend `/api/router/color` |
| Weather along route | `tools/weather_route.py` + `tools/astro.py` — ETA per point, Open-Meteo forecast, sun/moon | backend `/api/weather/route` |
| Map display | XYZ slippy tiles (see `desktop/qml/MapService.qml`); offline tiles via the Offline Maps page | frontend |

`track_color.py` and `astro.py` are stdlib-only, pure computation, and run fully offline
(`--selftest`). `weather_route.py` needs network only for the forecast fetch. Tests:
`tools/test_offline_routing.py`.

## History — the offline BRouter planner (removed 2026-08-31)

Plan started as a *fully-offline route planner* on a bundled **BRouter** engine (tap points on
the map, auto-route on downloaded OSM `.rd5` segments, OruxMaps-style). It was removed because:

- Route **drawing** is already well covered by online planners and Basecamp on every platform
  André uses; the real need was **weather along a route**.
- Shipping it usably would have meant bundling a Java runtime + `brouter.jar` per platform,
  hosting in-app region (`.rd5`) downloads, and managing a subprocess on three OSes — a lot of
  machinery to duplicate route-drawing that already exists.

Climb colouring and weather never needed the router (they run on any coordinate list), so the
valuable half stayed and the engine went. The removed tools were `tools/route_plan.py`
(BRouter client) and `tools/poi_search.py` (offline POI-DB search), plus the backend
`/api/router/route`, `/api/router/health` and `/api/poi/search` endpoints.
