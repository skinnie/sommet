# Sommet

Sommet reads, shows and writes the data of sport watches and bike computers without the maker's
cloud. It began as an app for Suunto Ambit watches; those stay the main product. There are two
apps that must look and behave the same: the desktop app and the Android/iOS app.

## Language

### The two apps

**Desktop app**:
The Qt/QML program for Linux, macOS and Windows, in `desktop/`.
_Avoid_: PC app, v2, ambitapp

**Mobile app**:
The React Native program for Android phones and tablets and for iPhone, in `android/`.
_Avoid_: the APK, Android app (when iPhone is meant too)

**Parity**:
The rule that a screen or feature looks and behaves the same in the desktop app and the mobile app, unless the device cannot do it.

**Twin**:
One rule written twice, once for each app, in two programming languages. Twins must give the same answer for the same input.
_Avoid_: port, copy, mirror

**Backend**:
The Python program the desktop app starts in the background to talk to devices (`desktop/backend/server.py`).
_Avoid_: server, API, daemon

**Tool**:
A Python script in `tools/` that does one job with a device or a file. The backend runs tools; a tool also runs alone from a terminal.

### Devices

**Watch**:
A Suunto watch that Sommet reads or writes (Ambit1, Ambit2, Ambit3, Traverse, Kailash).
_Avoid_: device (when a Suunto watch is meant), unit

**Legacy watch**:
An Ambit1 or Ambit2: USB only, no Bluetooth, older protocol.
_Avoid_: Bluebird, old Ambit

**Older Suunto device**:
A Suunto T6, T6c, T6d, X6HR or the GPS Track Pod, read over serial or USB. Experimental.
_Avoid_: legacy (that word is for the Ambit1/2)

**GPS (handheld)**:
A Garmin eTrex 10, 20, 20x, 30 or 30x.

**Sensor**:
A Bluetooth heart-rate or sleep sensor (Suunto Smart Sensor, Polar Verity Sense).

**Bike computer**:
A cycling head unit from another maker (Garmin Edge, Bryton, Wahoo ELEMNT, Magene, Karoo, Coros Dura).
_Avoid_: head unit, GPS, watch

**Source**:
Where an Activity or a Weigh-in came from: a watch, a bike computer, a file, a Scale, Garmin Connect, intervals.icu, or typed by hand (Manual).
_Avoid_: origin, provider, device (when intervals.icu or a file can be meant)

**Scale**:
A Bluetooth body scale that Sommet listens to directly, with no account and no pairing. Today the Etekcity FIT 8S, which broadcasts its reading while someone stands on it.
_Avoid_: smart scale, balance

**Weigh-in**:
One reading of body weight from a Source, for one day. When a day has several, the Scale or a Manual one wins; among cloud sources the one with the most detail wins.
_Avoid_: measurement, weighing, entry

**Estimated**:
A body-fat or body-water number worked out from the impedance of a Scale (Sun et al. 2003), not measured. It always carries this word on screen; follow the trend, not one reading.
_Avoid_: calculated, accurate, real

### What the user sees

**Activity**:
One recorded session of sport, from any source.
_Avoid_: Move, workout, log, exercise, session

**Sport**:
The kind of Activity, always shown with Suunto's name for it (see ADR-0001).
_Avoid_: activity type, discipline

**Foot sport**:
A sport done on foot, the only kind that shows pace (min/km); every other sport shows speed (km/h).

**Junk activity**:
An Activity shorter than 2 minutes and shorter than 100 m: a test recording. Only the developer's personal build hides these; the public apps keep every recording.
_Avoid_: test activity, short activity

**Duplicate**:
The same session recorded twice (by two devices, or once from a watch and once from intervals.icu). It is shown and counted once.

**Sport mode**:
A set of screens and settings on a watch for one sport, edited on the Sport Modes screen.
_Avoid_: custom mode, exercise mode, profile, activity profile

**Route**:
A line to follow, sent to a watch or bike computer.
_Avoid_: track (a track is what was recorded), course

**POI**:
One saved point on a watch, with a name.
_Avoid_: waypoint (the word for a point inside a Route), favourite, location

**POI type**:
The kind of a POI, which decides the icon the watch draws for it (Camp, Water, Big game ...). Each watch family has its own list and its own numbers: 18 on an Ambit3, 48 on a Traverse.
_Avoid_: POI icon (the icon follows from the type), category

**Shot**:
A gunshot a Suunto Traverse Alpha counted from the recoil during a Hunting Activity. It is part of the Activity, with the place where the watch was. A POI of type "Shot" is something else: a point the hunter saves by hand.
_Avoid_: shot POI, shot detection (the watch's setting, not the counted event)

**Direction pin**:
A point Sommet adds to a Route at a turn or crossing, so that a watch or an eTrex announces it.
_Avoid_: turn waypoint, cue, direction waypoint

**Workout**:
A planned, structured session (warm-up, intervals, rest) sent to a watch before it is done. Once done, the recording is an Activity.
_Avoid_: guided workout (as a general word), training, plan

**Training Program**:
A calendar of Workouts sent to a watch or bike computer.
_Avoid_: training plan, schedule

**Race Plan**:
The screen that estimates arrival times along a Route for a long ride and plans stops.
_Avoid_: race planner, ETA planner

**Gear**:
A bike, a pair of shoes or a part, with the distance it has done.
_Avoid_: equipment, components

**Ember**:
The food and energy log inside Sommet.
_Avoid_: nutrition, food diary

**Coach**:
The screen that shows training load, form and readiness, with an optional chat (your own API key).

### Working on the project

**Capture**:
A recording of what the maker's own program and a device said to each other over USB or Bluetooth, also called a sniffing. Captures are the source of truth for every protocol question.
_Avoid_: pcap, dump, trace

**Ticket**:
One task or bug in the private task list (see `docs/agents/issue-tracker.md`).
_Avoid_: issue, todo, task

**Checks**:
The automatic tests that need no device, run with `tools/check.sh`.
_Avoid_: test suite, CI

**Hardware-confirmed**:
Seen working on a real device by the maintainer, with the date and the device written down.
_Avoid_: tested, verified (when no device was used)

**Public repository**:
The GitHub repository of Sommet. Anyone can read it, so private notes, serial numbers and the task list are kept outside it.
