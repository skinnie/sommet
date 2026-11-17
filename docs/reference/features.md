# Features and supported devices

The short version is in the [README](../../README.md).

## Supported devices

- **Suunto watches:** Ambit (1), Ambit2 / 2 S / 2 R, Ambit3 Peak / Sport / Run / Vertical, Traverse, Traverse Alpha, Kailash
- **Suunto legacy (experimental):** T6 / T6c / T6d, X6HR, GPS Track Pod
- **Sensors:** Suunto Smart Sensor (HR belt); Polar Verity Sense (sleep, experimental)
- **GPS:** Garmin eTrex 10 / 20 / 20x / 30 / 30x
- **Bike computers:** Bryton Aero 60 (activities, routes, workouts, profile), Magene C406 Pro (activities, workouts), Garmin Edge (activity import + data-screen editor, over USB) and Hammerhead Karoo 3 (activity import), Wahoo ELEMNT (the original) and ELEMNT BOLT (rides, routes, workouts, data pages, device settings, rider profile and OpenStreetMap maps, over USB or Bluetooth; desktop, rides also on Android)

All features that are visible in the app are available by USB/USB-OTG, and some are also available/being tested by bluetooth.
Bluetooth is still an experimental feature, which can be enabled in settings. On the desktop it is experimental: tested on Linux with mixed results, depending on the computer's Bluetooth hardware, and not tested on macOS or Windows. On Linux (Ambit3 family) it holds: the link reconnects by itself after the watch sleeps, a sync starts when the watch asks for one, and activities, POIs, routes, the clock, sport modes, apps, workouts, the Training Program and the watch profile go over it. On Linux, pairing asks for your password once (see [Linux: pairing a watch over Bluetooth](../how-to/bluetooth-on-linux.md#linux-pairing-a-watch-over-bluetooth-the-password-prompt), and [Keep a Bluetooth watch paired on Linux](../how-to/bluetooth-pairing-on-linux.md)).
Within the features we have:
- Sync time with watch: from the current time of the device or another timezone
- Update orbital data: gps and glonass (for the glonass devices)
- Device manual: download them in pdf format
- Activities: download from the watch, view on a map, export them in gpx, fit, to local, cloud or 3rd party services (strava, runalyze, intervals.icu, others to come)
- Activity details: tiles chosen per sport, a "compared with your usual" line (same sport, similar distance, last 12 months), time in heart-rate and power zones, one chart with the lines you pick (pace, heart rate, altitude...), and the track coloured by zone on the chart and the map
- Routes: Import/send a gpx route to the watch (with its distance, climb and altitude graph), remove a route from the watch, export the routes on the watch to gpx format. Sending or removing a route keeps your POIs; a copy of them is saved first in `~/SommetBackups/POIs/`
- POIs: Import/send a POI to the watch (via a map search, or coordinates), remove a POI, export the POIs on the map to gpx
- Calendar: visual calendar with days where activities were done
- Totals: totals of a certain activity
- Backup: backup routes and POI altogether to a folder. Backup of your watch firmware (these watches firmwares should be matching the hw version)
- Scheduled workouts (Ambit3): the watch's own WORKOUT menu is back - step screen with target band, step text, melody at each step, optional backlight flash at step change / out of limits. Running, cycling, pool swimming... (the watch shows 5 at a time)
- Training Program: calendar of dated workouts, imported from intervals.icu (or made in the app) and rotated onto the watch at each sync; also sent to Bryton / Magene bike computers. On the day, pick the workout from the sport's WORKOUT menu (full beeps and melody, and a normal start stays a free run). The optional "Training day on the watch" switch starts the day's workout by itself, but silently on stock firmware
- Workout Builder: build a workout step by step (time, distance, lap, energy, HR above/below; HR, pace, speed, power targets) and install it straight to the watch
- Apps: Suunto App Zone apps (app-based workouts for Ambit1/2, or more complex ones on Ambit3), with app logging
- Copy to watch: copy sport modes and settings from one watch to another
- Watch settings: edit the watch's settings (Ambit1/2 and Ambit3 families)
- Race Plan: plan a brevet / long ride from a GPX or roadbook: controls, stops, weather, climbs
- Bluetooth scale: Etekcity FIT 8S weigh-ins with a body-fat estimate, sent to intervals.icu if you want (desktop and Android)
- Health: this week's activity, steps per weekday, the watch's own activity calories and recovery time, each compared with the 7 days before; wellness from intervals.icu and HRV from R-R
- Weight, Coach: weigh-ins, training load and readiness, and an optional chat coach (bring your own API key)
- Gear: bikes, shoes and parts with service reminders, synced with intervals.icu
- Cloud backup: Dropbox, Google Drive, OneDrive
- Garmin Connect: activity, weight and health sync
- Sports modes: sports mode editor for the watches
- Firmware: flasher of firmware to the watch
- Suunto HR Sensor: connection of the sensor, check battery level, firmware version, etc
- Kailash specific: places visit, countries visited, export activity (categorized by default as walk)
- Etrex specific: download activities (categorized by default as walk), import/export POIs, import/export routes.
- GPS Track Pod support: experimental, no device to test. Hidden until you switch it on in **Settings → Show in the sidebar → GPS Track Pod**
- Suunto T6 / X6HR support: experimental, no device to test. Hidden until you switch it on in **Settings → Show in the sidebar → T6/X6**
- Offline maps: download the map tiles of an area to use it without internet
- Sommet Sync: one shared app database between your computers and phone, through your own server (NAS)
- Route weather: the route planner shows the weather along a route
- Export all activities: every activity in the list into a folder at once (Activities page, desktop)
- Connect IQ (Garmin Edge): check which Connect IQ apps on the Edge have an update

## Tested on

I am not at all responsible for errors, failures, bricked devices, etc. This software is provided as is.
Currently main features were tested with Ambit 3, Traverse and Kailash in Android and Linux Mint 22, and with an Ambit2 on Windows. Normally most features work with the Ambit 1 too (I don't have one to test). Complications of each feature may require further testing (sports modes, settings, etc)
