# Credits, licenses and data

## Legal & licenses
Independent, unofficial software — **not affiliated with, endorsed by, or supported by Suunto or
Garmin.** Suunto, Ambit, Traverse, Kailash, T6 Garmin and eTrex are trademarks of their respective
owners, used here only to describe compatibility.

This software is provided **as is, without warranty of any kind** — you are responsible for
anything that happens to your device.

Licensed under the **[GNU GPLv3](LICENSE)** — the same license as
[openambit](https://github.com/openambitproject/openambit), whose `libambit` this project's
protocol work is checked against throughout. Because it is GPLv3, the full source is this
repository; you may use, study, modify and redistribute it under the same license.

- **Desktop** links **Qt 6** under the **LGPLv3** (dynamically linked — you may relink it against
  your own build of Qt).
- **Android** uses **React Native** and its ecosystem (**MIT**).

## Credits

Built on the real work of others:

- **[openambit](https://github.com/openambitproject/openambit)** — `libambit`, the reference the
  USB/BLE protocol work is checked against (GPLv3).
- **opensportsync** — the starting point for the Android app.
- **marguslt** — firmware-download recipe, gists, Movescount knowledge.
- **Pavel Samokha** — the community App Zone / workout compiler every scheduled workout is compiled on, and the SuuntoLink app-catalogue method (Suunto forum).
- **Emil Ljungdahl** — original author of `libambit`.
- **[openScale](https://github.com/oliexdev/openScale)** (GPLv3) and
  **[etekcity_esf551_ble](https://github.com/ronnnnnnnnnnnnn/etekcity_esf551_ble)** — Etekcity
  scale broadcast layout, and a cross-check of the published Sun et al. (2003) body-fat equations.
- **sebchastang** — published Suunto App Zone interval-training scripts.
- **[bwaldvogel/openmoves](https://github.com/bwaldvogel/openmoves)** — Openmoves.
- **[iwanders/gps_track_pod](https://github.com/iwanders/gps_track_pod)** — Suunto GPS Track Pod
  support (MIT), vendored in `tools/vendor/gpspod/`.
- **[evelbulgroz/suunto-t6-sync](https://github.com/evelbulgroz/suunto-t6-sync)** — Suunto
  T6/T6c/T6d read support (MIT), vendored in `tools/vendor/suunto_t6_sync/`; basis for the
  experimental T6 heart-rate export and GPS-Track-Pod merge.
- **App Zone workout examples** — real published App-Zone interval scripts the workout findings
  were checked against: [claha/suunto](https://github.com/claha/suunto) (its `Suunto.mod(STEP,N)`
  repeat encoding), [follesoe/suunto-ambit-intervals](https://github.com/follesoe/suunto-ambit-intervals),
  [hefler/SuuntoApps](https://github.com/hefler/SuuntoApps),
  [AdamHodgson/Suunto-Interval-Training](https://github.com/AdamHodgson/Suunto-Interval-Training),
  [Httqm/Suunto](https://github.com/Httqm/Suunto).
- **[ruvido/goambit](https://github.com/ruvido/goambit)** & **[AlexLBraits/ambit2gpx](https://github.com/AlexLBraits/ambit2gpx)**
  — independent implementations of the same cloud-free USB paths (route upload / activity read),
  confirming they're real.
- **tomoya kamata ([nabeka/x6hr-python](https://github.com/nabeka/x6hr-python))** — Suunto X6HR protocol (GPLv3), with
  [larshesel/suunto_x6hr_erl](https://github.com/larshesel/suunto_x6hr_erl) and the terre-adelie wiki.
- **[Taxom/OpenBikeCompanion](https://github.com/Taxom/OpenBikeCompanion)** — Magene C406 BLE protocol notes (GPLv3);
  **[WSTRN/C406pro_Hack](https://github.com/WSTRN/C406pro_Hack)** — C406 Pro hardware reference.
- **[fullcarbonbike/Activity-Profile-Editor](https://github.com/fullcarbonbike/Activity-Profile-Editor)** (MIT)
  — the Garmin Edge built-in data-field id→name map and the data-screen arrangement (A/B) survey behind the
  Edge data-screen editor (`tools/garmin_edge_screens.py`); cross-verified on a real Edge 1040 Solar.
- **[googlarz/suunto-mcp](https://github.com/googlarz/suunto-mcp)** — workout-guide format for the Coach's Suunto sink.
- **Eric Banister, Dr. Andrew Coggan, Hunter Allen** — the CTL / ATL / TSB training-load model behind the Coach.
- **[openathleteorg/openathlete](https://github.com/openathleteorg/openathlete)** (AGPLv3) — the idea of an
  on-device HR load (Banister TRIMP) when no cloud load is available, and weather history on each
  activity; ideas and formula only, no code copied.
- **[rfivesix/train-libre](https://github.com/rfivesix/train-libre)** (GPLv3) — its Sleep Health Score engine,
  ported for the sleep score.
- **Suunto Apps Developer Manual** (2015) — watch variables and activity ids, checked against.
- **[mihaildemidoff/suunto-sml-model](https://github.com/mihaildemidoff/suunto-sml-model)** —
  a JAXB model of Suunto's SML format, a reference for the exercise-log work.
- the **Suunto forum community** and **wanarun.net**.

Services and libraries the app talks to at runtime:

- **[cyberjunky/python-garminconnect](https://github.com/cyberjunky/python-garminconnect)** (MIT) —
  the Garmin Connect client behind activity, weight and health sync (`tools/garmin_sync.py`,
  `tools/garmin_weight.py`).
- **[intervals.icu](https://intervals.icu/)** — its public API backs activity/gear import and
  export, the wellness and weight feeds, and the profile/activity-level writes to the watch.
- **[joaodrp/wahoo-systm-mcp](https://github.com/joaodrp/wahoo-systm-mcp)** — the Wahoo SYSTM
  workout catalogue the Coach can read from (its offline sample ships as `desktop/assets/coach/systm-sample.json`).
- **[Open Food Facts](https://world.openfoodfacts.org/)** (database under ODbL) and **[USDA FoodData Central](https://fdc.nal.usda.gov/)**
  (public domain) — the food search behind Ember's meal logging. The idea came from
  [SparkyFitness](https://github.com/CodeWithCJ/SparkyFitness); none of its code is used (its
  licence is non-commercial, incompatible with GPL-3.0).
- **[ZXing-C++](https://github.com/zxing-cpp/zxing-cpp)** (Apache-2.0) — reads food barcodes from the
  webcam in Ember (desktop); built from source, its licence ships inside the app. Camera capture
  via **Qt Multimedia** (LGPLv3).
- **[react-native-camera-kit](https://github.com/teslamotors/react-native-camera-kit)** (MIT) — the phone camera
  barcode scan in Ember (Android: CameraX + Google ML Kit barcode scanning, bundled model; iOS: AVFoundation).
- **[Anthropic Claude API](https://www.anthropic.com/api)** — optional, powers the Coach chat
  when you supply your own API key. The app works fully without it.
- **[Qt](https://www.qt.io/)** (LGPLv3) — the desktop app's UI framework; **React Native** for
  the Android app.

## Data & assets:

- Map data © **OpenStreetMap** contributors, under the **Open Database License (ODbL)**. Tiles:
  **CyclOSM** / OpenStreetMap France and standard OSM.
- Weather by **[Open-Meteo](https://open-meteo.com/)** (CC BY 4.0).
- Approximate position (from the internet address, and by Wi-Fi when switched on):
  **[BeaconDB](https://beacondb.net/)**; its IP data is from **DB-IP** (IP to City Lite, CC BY 4.0).
- Icons: **Google Material Symbols** (Apache License 2.0); 7 activity icons are Suunto's own (see credits).
- Mobile maps: **[Leaflet](https://leafletjs.com/)** (BSD-2-Clause).
- POIs along a route: exported from **[PitStopper](https://pitstopper.net)**.

Full list with details: [`docs/reference/credits.md`](docs/reference/credits.md).
