# Sommet — Everything the App Can Do

*A plain-language tour of every feature built into Sommet, written for anyone — no
technical background needed.*

**Version 0.2.10** · Works on **Linux, Windows, macOS** (desktop) and **Android** (phone/tablet).
Connects to your watch by **USB cable** and, on Android, also by **Bluetooth**.

---

## What is Sommet?

Sommet (French for "Peak") is a free app that lets you manage your older Suunto and Garmin
watches on your own computer or phone — **no account, no login, and almost entirely offline.**

When Suunto shut down its old "Movescount" website, owners of these watches lost the ability
to send routes, points of interest, workouts and settings to their watch easily. Sommet
brings those abilities back, and adds a lot more on top. It's for people who love their old
watch and don't want to throw it away.

**Watches it works with:** Suunto Ambit 1, Ambit 2, Ambit 3, Traverse, Traverse Alpha and
Kailash — plus Garmin eTrex 10/20/30 handheld GPS units.

---

## The main features

### 🏠 Home & device connection
- Plug in your watch and Sommet **recognises it automatically** — shows the model name,
  serial number, firmware version and **live battery level**.
- If you own several watches, a **watch picker** lets you choose which one you're working with.
- A **demo mode** lets you explore the whole app with sample data even without a watch plugged in.

### 🕒 Sync the time
- Set your watch's clock from your computer/phone's current time — or from **any other time
  zone** if you're travelling.

### 🛰️ Update GPS ("orbital data")
- Downloads the latest satellite-position data so your watch finds GPS **faster** when you go
  outside. Works for both GPS and GLONASS satellites on the watches that support them.

### 🗺️ Routes
- **Send a route to your watch** from a GPX file so you can follow it on your wrist.
- **Read routes back** off the watch and save them as GPX files.
- A **preview map** shows you exactly what the route will look like before you send it.

### 📍 Points of Interest (POIs / waypoints)
- Add places to your watch — by **searching a map**, or by typing in **coordinates**.
- **Export** the POIs already on your watch to a file.
- Live map preview when adding a place.

### 🏃 Activities (your recorded workouts)
- **Download** the workouts stored on your watch.
- **View them on a map**, with an **elevation profile** and a **replay** of the route.
- **Export** them as GPX or FIT files.
- **Upload** them to popular services: **Strava, Runalyze, Livelox and intervals.icu.**
- Save them locally or to the cloud.

### 📅 Calendar
- A visual calendar highlighting the days you recorded an activity — an at-a-glance training diary.

### 📊 Totals
- See lifetime **totals** for a given activity type (distance, time, and so on), with some fun facts.

### 🎿 Sport Modes
- A full **editor** for the watch's sport modes: rename them, set auto-lap, heart-rate limits,
  which sensors/pods to use, and **customise which data fields show on each screen** — just like
  the official software did.
- Create new sport modes and delete ones you don't want.

### ⚙️ Watch Settings
- **Read** your watch's settings (units, personal info, etc.).
- **Change settings** on the watches that support it (Ambit 3 and Kailash).

### 🔁 Copy one watch to another (Sync)
- A separate **Sync** tool for moving your setup from one watch to another — handy when you
  upgrade or replace a watch and want your settings, places and sport modes to come along.
- Because these watches only talk over the cable **one at a time**, it works in steps:
  1. Plug in the **first watch** → the app takes a snapshot of it.
  2. **Unplug it and plug in the second watch** → take a snapshot of that one.
  3. The app shows you a **preview of the differences** before anything is written.
  4. Confirm, and it copies your chosen items onto the target watch.
- You **never need both watches plugged in at the same time** — you swap the cable between steps.
- *(Today, the **Settings** category is the fully working one; Places, Routes and Sport Modes
  are still being finished.)*

> **Note:** "Sync" means two different things in the app. This watch-to-watch **Sync** page is
> one of them. The other is the everyday sending of routes/places/activities to a single watch,
> which is done on each of those pages — there is no single "sync everything" button.

### 💾 Backup
- Make a **safe backup** of your watch's routes and POIs to a folder, and restore them later.
- Backups are saved on your computer, by default in a **`AmbitAppBackups` folder in your home
  folder**, each one stamped with the date and time. When you back up, the app lets you **choose
  the folder** — point it at a Dropbox / OneDrive / Google Drive folder and your backups get
  copied to the cloud for free, with no login needed.
- Back up your watch's **firmware** too.
- Every backup can be "rehearsed" (previewed) before anything is written.

### 🔧 Firmware flasher
- **Installs watch firmware** over USB — **tested and working on Linux, Android and macOS**,
  and even used to recover a "dead" Ambit 3. On Android it flashes over a USB-OTG cable, so
  make sure everything is charged and don't interrupt it mid-flash.

### ❤️ Suunto Heart-Rate Sensor
- Connect to a Suunto HR chest-strap sensor, check its **battery level and firmware version**.

### 🏋️ Interval Workout Builder
- A **visual workout builder** for creating structured interval workouts, which can be turned
  into an app on the watch (Ambit 3). It opens in your web browser and can also be used
  completely on its own.

### 📖 Device manuals
- Download the official **PDF manual** for your watch right from the app.

---

## Features for specific watches

### Kailash (the travel watch)
- Pair and sync over **Bluetooth and USB**.
- Read your **travel history** and the **countries/places you've visited**.
- Export a recorded activity to GPX.
- Set your **Home Location**.
- *(Note: this watch was originally iPhone-only with very limited options — Sommet gives it far more.)*

### Garmin eTrex (handheld GPS)
- **Download activities** from the device.
- **Import and export POIs.**
- **Import and export routes.**

### Experimental (no test hardware yet)
- **Suunto GPS Pod** support.
- **Suunto T6** heart-rate watch support.

---

## The "extra" features Sommet adds (beyond the original watch tools)

These go further than what Movescount or SuuntoLink ever offered:

- **🧭 Training Coach** — a readiness "traffic light" plus a **chat coach** that reads your own
  training data (and can optionally use an AI assistant if you provide your own key). Works
  offline for the basics.
- **📆 Training calendar / planned workouts** — pull planned workouts from intervals.icu and
  see them in a calendar.
- **⚖️ Weight tracking** — reads your weight history from intervals.icu (and Garmin, where set up).
- **🩺 Health & wellness** — reads wellness data (resting HR, HRV, etc.) from intervals.icu.
- **💓 HRV (heart-rate variability)** — computed from your watch's raw heartbeat data.
- **☁️ Cloud backup** — back up your data to **Dropbox, Google Drive or OneDrive.**
- **🔗 intervals.icu two-way sync** — import and export planned workouts.
- **🚴 Gear tracker** — track your bikes, shoes and parts, with maintenance reminders.
- **🔥 Ember** — a companion personal wellness/fasting tracker view.
- **🌤️ Weather** — local weather (from Open-Meteo) shown in the app.
- **🗺️ Built-in maps** — several map styles (OpenStreetMap, CyclOSM, France's IGN) with no
  Google account needed.

---

## What makes it special

- **Free, no account, no login** — and works almost entirely **offline**. (Only the GPS-update
  data, firmware checks and the online workout builder need internet.)
- **Same features on your computer and your phone** — the Android and desktop apps are built
  to match each other.
- **Safety first** — every change to the watch is *previewed* before it's written, addresses are
  double-checked, and backups are always restorable.
- **Revive old hardware** — the whole point is to keep these classic watches (and old
  computers/phones) out of the landfill and in use.

---

## A few honest limitations

- **Bluetooth** works fully on **Android**; on the desktop app it's cable-only for now.
- **Firmware updates** — Sommet's firmware flasher is **tested and working on Linux, Android
  and macOS**. (On Android, do it with a charged phone and a reliable USB-OTG cable.)
- **GPS-update data and firmware images** still come from Suunto's own servers (Sommet just
  knows how to put them on your watch).
- A couple of old Movescount features — **guided interval workouts** and **training plans that
  show up in the watch's menu** — could not be fully brought back yet, because the tools that
  created them no longer exist to study.
- The **Ambit 3 Peak** is the most thoroughly tested watch; the others share the same
  internals and are expected to work, but haven't all been verified on real hardware.

---

*Sommet is an independent, unofficial project — not affiliated with or endorsed by Suunto or
Garmin. It's provided as-is, for use on your own hardware at your own risk. Licensed under
the GNU GPLv3.*
