# Credits

This project stands on real prior work by other people. None of the protocol
reverse-engineering here would have been possible, or would have taken far longer, without it.

- **[openambit](https://github.com/openambitproject/openambit)** and its contributors,
  especially **Emil Ljungdahl** (`libambit`'s original author) - the real, working reference
  implementation this project checks its own findings against throughout. `libambit`'s design
  (the `device_driver_*`/`pmem20` split, the BXml sport-mode format, the whole USB transport
  layer) is the foundation this project's own C code is written to sit alongside, not inside
  of - see `csrc/`'s own notes on why openambit's GPLv3 code was kept out of this repo.

- **[opensportsync](https://github.com/guiguoz/opensportsync)** and its author - the React
  Native base this project's own Android app (`android/` in this repo - imported via a real
  `git subtree` merge of the upstream history, 2026-08-08, after living as an unversioned
  sibling folder for most of this project's life) was forked from.

- **[marguslt](https://github.com/marguslt)** - several independent, real contributions cited
  throughout this project: the firmware-download-link recipe
  (`gist.github.com/marguslt/8cffaa78152503b29b91920de845e536`), the workout/App-Zone gists,
  and [`openmoves`](https://github.com/marguslt/openmoves).

- **[sebchastang](https://forum.suunto.com/user/sebchastang)** - author of a complete,
  published set of real interval-training Suunto Apps (`IntervalCounter`, `IntervalRun`,
  `IntervalSpeed`, `IntervalSerie`, `IntervalAIO`, and more), maintained through Movescount's
  actual 2022 shutdown. Genuine, sophisticated App Zone code that this project's own
  structured-workout tooling learned from.

- **Pavel Samokha** and the Suunto forum community, especially
  [`forum.suunto.com/topic/7592`](https://forum.suunto.com/topic/7592) - the documented,
  confirmed-real mechanism for adding a compiled Suunto App to SuuntoLink's own catalog
  (`suunto-apps/index.json`), which this project's own installer tooling uses directly rather
  than reinventing a flash-write path.

- **[wanarun.net](https://wanarun.net)** and its developers - independent confirmation of the
  structured-workout JSON schema this project's own workout generator (`tools/workout.py`)
  targets, alongside `openambitproject/openambit#257` and Suunto's own French tutorial.

- **tomoya kamata (T.Kamata)** - author of [`nabeka/x6hr-python`](https://github.com/nabeka/x6hr-python)
  (GPLv3), the reverse-engineered serial protocol and log decoding for the Suunto X6HR wristop,
  developed against a real X6HR. `tools/suunto_x6hr.py` is a Python-3 port of it, cross-checked
  against **[larshesel/suunto_x6hr_erl](https://github.com/larshesel/suunto_x6hr_erl)** and the
  [terre-adelie SuuntoX6HR wiki](http://wiki.terre-adelie.org/SuuntoX6HR). Nobody on this project
  owns an X6HR, so this legacy-device support rides entirely on their real-hardware work.

- **Ulrik ([evelbulgroz/suunto-t6-sync](https://github.com/evelbulgroz/suunto-t6-sync))** -
  the reverse-engineered Suunto T6 / T6c / T6d FTDI-USB protocol and training-log decoding
  (MIT), developed against real T6d units. Vendored unmodified at `tools/vendor/suunto_t6_sync/`
  and wrapped by `tools/suunto_t6.py` - this project owns no T6 either, so the same
  real-hardware-development-is-the-verification reasoning as the X6HR applies.

- **Ivor Wanders ([iwanders/gpspod](https://github.com/iwanders/gpspod))** - the
  reverse-engineered Suunto GPS Track Pod USB protocol (MIT), vendored at `tools/vendor/gpspod/`
  and used by `tools/gps_track_pod.py` to read the pod tracks the T6/X6HR heart-rate logs are
  merged with into a mapped activity.

- **[intervals.icu](https://intervals.icu)** (David Tinker) - the training-analysis platform
  this app integrates with: activity upload and the Gear tracker's two-way sync of
  bikes / shoes / components / service reminders both talk to its API.

- **[OpenStreetMap](https://www.openstreetmap.org/copyright)** - © OpenStreetMap contributors
  (ODbL). The map views render OSM tiles.

- **[Leaflet](https://leafletjs.com/)** - © Vladimir Agafonkin / CloudMade (BSD-2-Clause). The
  interactive map engine behind the mobile map views; bundled inline (`android/src/services/
  leafletInline.ts`, auto-generated from the pinned `leaflet` npm package) so maps render with
  no network on iOS/iPadOS/Android.

- **[Open-Meteo](https://open-meteo.com/)** - weather data (CC BY 4.0), for the weather card.

- **[Google Material Symbols](https://github.com/google/material-design-icons)** - the icon
  set (Apache License 2.0) used throughout the desktop UI.

The **Coach** feature (readiness beacon + chat) stands on further prior work:

- **The CTL / ATL / TSB "Performance Manager" model** - Eric Banister's impulse-response model
  of training load, popularised as the Performance Manager Chart by **Dr. Andrew Coggan** and
  **Hunter Allen** (and TrainingPeaks). The Coach's Fitness / Fatigue / Freshness readiness math
  (`coachservice.cpp`'s exponentially-weighted CTL/ATL pass) is that model - honestly limited
  here to a duration-based load signal, since this device family decodes no power or HR strap.

- **[joaodrp/wahoo-systm-mcp](https://github.com/joaodrp/wahoo-systm-mcp)** - the SYSTM
  catalogue MCP server the Coach's "live" library source is designed to read (via a small HTTP
  bridge in front of its stdio JSON-RPC).

- **[googlarz/suunto-mcp](https://github.com/googlarz/suunto-mcp)** - the modern-Suunto
  (SuuntoPlus Guides) MCP server whose `push_workout_guide` / IntervalPlan format the Coach's
  Suunto-Race device sink maps onto. That sink lives in the local `coach/` scaffold rather than
  the shipped app, but its design and workout-guide shape are taken directly from this project.

- **Wahoo SYSTM** (formerly **The Sufferfest**) - the structured-workout catalogue whose real
  sessions (TSS / IF / 4DP metrics) the Coach's library and its bundled offline sample
  (`systm-sample.json`) are built from. SYSTM and its content are Wahoo's; used here only to
  describe and match workouts, not redistributed.

- **The App-Zone interval/workout example authors** - real, published App-Zone-language
  workout apps this project's structured-workout findings (Findings 8-11) were checked against.
  Chief among them **[claha/suunto](https://github.com/claha/suunto)**, whose Python generator
  encodes a repeat block as one `Suunto.mod(STEP, N)` conditional per step-position - a
  concrete technique dissected in `docs/explanation/training-program.md` - and alongside it
  **[follesoe/suunto-ambit-intervals](https://github.com/follesoe/suunto-ambit-intervals)**,
  **[hefler/SuuntoApps](https://github.com/hefler/SuuntoApps)**,
  **[AdamHodgson/Suunto-Interval-Training](https://github.com/AdamHodgson/Suunto-Interval-Training)**,
  and **[Httqm/Suunto](https://github.com/Httqm/Suunto)** (kept as a reference copy under
  `githubprojects/Suunto`). These examples let this project confirm the App-Zone step/target
  model against real code rather than reverse-engineered material alone.

- **[ruvido/goambit](https://github.com/ruvido/goambit)** and
  **[AlexLBraits/ambit2gpx](https://github.com/AlexLBraits/ambit2gpx)** - independent
  implementations of the exact cloud-free USB paths this project reverse-engineered:
  `goambit` uploads GPX routes to an Ambit3 Peak over direct USB (this project's own route-write
  path), and `ambit2gpx` reads activities off an Ambit over USB and writes GPX (this project's
  activity-read path). This project's tooling was developed independently of them, but their
  existence is real, working confirmation that these paths are genuine and need no Movescount
  cloud.

- **[mihaildemidoff/suunto-sml-model](https://github.com/mihaildemidoff/suunto-sml-model)** -
  a JAXB (XML-binding) model of Suunto's SML activity format, an independent reference point
  for this project's own exercise-log / SML work.

If anyone belongs on this list and isn't here, that's an omission to fix, not a judgment -
say so and it'll be corrected.

## Activity icons

The sport-mode badges are keyed on each mode's own `activityId`, using the activity table in
`assets/activity_types.json` (84 activity ids with their names and Suunto's own category
colours, read out of SuuntoLink's `activity.js` - factual mapping, not artwork).

**77 of the 84 symbols are our own drawings**, made for this app in a 24x24 box and
deliberately not traced from anyone's font.

**7 are taken from Suunto's own icon font** (`suunto_icon.woff`, shipped inside SuuntoLink) -
Boxing, Frisbee, Horseback riding, Indoor rowing, Racquet ball, Scuba diving and Squash.
Those seven are equipment shapes we could not draw legibly at 22px after three attempts, and
André chose to use Suunto's rather than ship icons that did not read. They remain Suunto Oy's
artwork; this project claims no rights over them and is not affiliated with or endorsed by
Suunto Oy. If that ever becomes a problem, they can be swapped for the generic "Unspecified
sport" star with no code change - just edit those seven entries in
`assets/activity_types.json` and re-run `tools/gen_activity_qml.py`.

## Suunto's own published documentation

**Suunto Apps Developer Manual** (Movescount.com, Apr 2, 2015) -
`assets/manuals/SuuntoAppZoneDeveloperManual.pdf`. Suunto's public developer manual for the
Suunto Apps scripting language. Two things in this project are checked against it rather than
only against reverse-engineered material:

- **The activity-id table** (p.29). `tools/verify_activity_types.py` transcribes it verbatim
  and compares it to `assets/activity_types.json`: all 74 published ids are present and none
  contradicts us, with 10 ids and 10 wording changes newer than the 2015 manual because our
  list came from SuuntoLink 4.1.15.
- **Value enumerations** (pp.24-29, WATCH VARIABLES). `custom_modes.FIELD_VALUE_ENUMS` takes
  the pool-length-style codes from it. Note the manual documents the App SCRIPTING namespace
  (`SUUNTO_*` variables), which is NOT the display-field id namespace - it never prints a
  field id, so it can name a quantity but cannot tell us which byte to write for it.

It also independently confirms four of the personal-setting ranges André read off the
SuuntoLink UI: height 89-241 cm, max HR 30-240, rest HR 30-240, activity class 1-10. It gives
weight as 30-200 kg where SuuntoLink's UI allows 30-250; the UI wins, since it is what
actually gates the write.

The manual is Suunto Oy's document, reproduced here only as a reference for interoperability
work; this project is not affiliated with or endorsed by Suunto Oy.
