# Sommet mobile app

The Sommet app for Android phones and tablets and for iPhone and iPad, written in React Native.
It reads and writes Suunto Ambit, Traverse and Kailash watches (USB OTG on Android, Bluetooth),
Garmin eTrex units and bike computers, the same way as the desktop app in `../desktop/`.

## Where it comes from

This app was forked from [opensportsync](https://github.com/guiguoz/opensportsync) in August
2026 and has changed a lot since. The upstream history was imported with `git subtree`. See
[credits](../docs/reference/credits.md) for opensportsync and the other projects this app uses.

## Licence

GPL-3.0, like the rest of Sommet (see [`LICENSE`](../LICENSE)). The app links openambit's
`libambit` (GPLv3), vendored in `android/app/src/main/cpp/libambit/`.

## Build

See [Build and run](../docs/how-to/build-and-run.md).

## Origin (opensportsync's README at fork time)

Kept as it was, for history. It describes opensportsync, not Sommet: the name, the download
links, the features (Livelox and others) and "Android only" no longer apply here.

> # OpenSportsSync
>
> [![Build APK](https://github.com/guiguoz/opensportsync/actions/workflows/build.yml/badge.svg)](https://github.com/guiguoz/opensportsync/actions/workflows/build.yml)
>
> > 🇫🇷 [Lire en français](#français) · 🇬🇧 English below
>
> ---
>
> ## English
>
> Android app to sync GPS activity logs from Suunto Ambit watches via USB OTG, display them on a map, and export them to training platforms.
>
> ### Installation / Download
>
> You don't need to build the app yourself.
> 1. Go to the [Releases page](https://github.com/guiguoz/opensportsync/releases/latest)
> 2. Download the latest `app-debug.apk` file to your Android device
> 3. Install it (you may need to allow "Install from unknown sources")
>
> ### Features
>
> - Sync activity logs directly from the watch via USB OTG (no Bluetooth required)
> - Display GPS tracks on an interactive map (IGN tiles via WebView + Leaflet)
> - Elevation profile with D+ / D- stats, pace (min/km) and average speed (km/h)
> - Interactive GPX replay — relive your activity point by point on the map (play/pause, speed x1/x10/x60/x120, seek)
> - Activity type display (Orienteering, Trail running, MTB, Cycling…)
> - Activity filters by type in the log list
> - Local SQLite database — automatic rebuild from GPX files on device
> - Permanent deletion (deleted activities are never re-imported on next sync)
> - Export to **Strava** (OAuth2 — one-tap upload)
> - Export to **Livelox** (OAuth2 — orienteering route analysis)
> - Export to **Runalyze** (FIT format — training analytics + push to Suunto app)
> - Share GPX / Save to Downloads
>
> ### Getting activities into the Suunto app
>
> Since the Ambit 1 has no BLE and Movescount is closed, the path to the official Suunto app is:
>
> **OpenSportsSync → Runalyze (FIT) → Suunto app**
>
> 1. In OpenSportsSync: open an activity → export → Upload to Runalyze
> 2. On [runalyze.com](https://runalyze.com): Settings → Connections → link your Suunto account
> 3. On the activity page in Runalyze: Share → Suunto
>
> ### Hardware support
>
> | Device | Protocol | Status |
> |--------|----------|--------|
> | Suunto Ambit 1 / 2 / 2S / 2R / 3 | USB OTG (libambit NDK) | ✅ Working |
> | Suunto Traverse / Traverse Alpha | USB OTG (libambit NDK) | ✅ Supported |
>
> ### Tech stack
>
> - **React Native 0.84** — Bare Workflow, Android only
> - **Kotlin** — USB OTG, Android UsbManager, FileDescriptor
> - **C++ / NDK** — JNI bridge + [libambit](https://github.com/openambitproject/openambit)
> - **react-native-sqlite-storage** — local activity database
> - **react-native-fs** — GPX/FIT file read/write
> - **fast-xml-parser** — GPX parsing
> - **FIT encoder** — pure TypeScript, no dependencies (`src/services/FitExport.ts`)
>
> ### Local Build (Development)
>
> If you want to modify the source code:
>
> ```bash
> # Clean install
> npm ci
>
> # Start the bundler (Metro) and install on connected device (ADB)
> npx react-native run-android
> ```
>
> *Note: The native code (libambit) handles USB OTG communication via JNI. Building requires NDK (r26+) and CMake, which Gradle automatically downloads.*
>
> ### Requirements
>
> - Android 9+ (API 28+) — required for iconv (libambit)
> - USB OTG cable
> - NDK r26+ (set in `android/app/build.gradle`)
>
> ### Configuration
>
> Create `src/config/secrets.ts` (not committed — never share this file):
>
> ```typescript
> export const LIVELOX_CLIENT_ID     = 'your_livelox_client_id';
> export const STRAVA_CLIENT_ID      = 'your_strava_client_id';
> export const STRAVA_CLIENT_SECRET  = 'your_strava_client_secret';
> ```
>
> - **Strava**: OAuth2 flow handled in-app. Add your `client_id` and `client_secret` from [strava.com/settings/api](https://www.strava.com/settings/api). Set **Authorization Callback Domain to `oauth`** (the host part of `opensportsync://oauth/strava`).
> - **Runalyze API key**: enter your personal key in the app Settings screen. Generate it at [runalyze.com/settings/config/account](https://runalyze.com/settings/config/account).
> - **Livelox**: OAuth2 flow handled in-app. Requires a registered `client_id` from [livelox.com](https://livelox.com).
>
> ### Credits
>
> - [libambit](https://github.com/openambitproject/openambit) — open-source Ambit communication library
> - [AmbitSync](https://github.com/starryalley/AmbitSync) — Android USB OTG patch reference
>
> ### License
>
> MIT (upstream opensportsync's own licence, as stated in its README; it applies to upstream, not to Sommet)
>
> ---
>
> ## Français
>
> Application Android pour synchroniser les activités GPS d'une montre Suunto Ambit via USB OTG, les afficher sur une carte et les exporter vers des plateformes d'entraînement.
>
> ### Installation / Téléchargement
>
> Pas besoin de compiler l'application vous-même.
> 1. Allez sur la [page des Releases GitHub](https://github.com/guiguoz/opensportsync/releases/latest)
> 2. Téléchargez le dernier fichier `app-debug.apk` sur votre téléphone
> 3. Installez-le (il faudra autoriser "l'installation d'applications de sources inconnues")
>
> ### Fonctionnalités
>
> - Synchronisation des activités directement depuis la montre via USB OTG (sans Bluetooth)
> - Affichage des tracés GPS sur une carte interactive (tuiles IGN via WebView + Leaflet)
> - Profil altimétrique avec statistiques D+ / D-, allure (min/km) et vitesse moyenne (km/h)
> - Replay interactif : revivez votre activité point par point sur la carte (play/pause, vitesse x1/x10/x60/x120, scrub)
> - Affichage du type d'activité (Orientation, Trail, VTT, Cyclisme…)
> - Filtres par type d'activité dans la liste
> - Base de données SQLite locale — reconstruction automatique depuis les fichiers GPX présents sur l'appareil
> - Suppression permanente (les activités supprimées ne sont jamais réimportées lors des synchronisations suivantes)
> - Export vers **Strava** (OAuth2 — upload en un tap)
> - Export vers **Livelox** (OAuth2 — analyse de parcours d'orientation)
> - Export vers **Runalyze** (format FIT — analyse d'entraînement + envoi vers l'app Suunto)
> - Partage GPX / Enregistrement dans les Téléchargements
>
> ### Faire apparaître ses activités dans l'app Suunto
>
> Comme l'Ambit 1 n'a pas de BLE et que Movescount est fermé, le chemin vers l'app Suunto officielle est :
>
> **OpenSportsSync → Runalyze (FIT) → App Suunto**
>
> 1. Dans OpenSportsSync : ouvrir une activité → exporter → Upload Runalyze
> 2. Sur [runalyze.com](https://runalyze.com) : Paramètres → Connexions → lier votre compte Suunto
> 3. Sur la page de l'activité dans Runalyze : Partager → Suunto
>
> ### Montres supportées
>
> | Montre | Protocole | Statut |
> |--------|-----------|--------|
> | Suunto Ambit 1 / 2 / 2S / 2R / 3 | USB OTG (libambit NDK) | ✅ Fonctionnel |
> | Suunto Traverse / Traverse Alpha | USB OTG (libambit NDK) | ✅ Supporté |
>
> ### Stack technique
>
> - **React Native 0.84** — Bare Workflow, Android uniquement
> - **Kotlin** — USB OTG, Android UsbManager, FileDescriptor
> - **C++ / NDK** — Pont JNI + [libambit](https://github.com/openambitproject/openambit)
> - **react-native-sqlite-storage** — base de données locale
> - **react-native-fs** — lecture/écriture GPX et FIT
> - **fast-xml-parser** — parsing GPX
> - **Encodeur FIT** — TypeScript pur, sans dépendances (`src/services/FitExport.ts`)
>
> ### Build local (Développement)
>
> Si vous souhaitez modifier le code source :
>
> ```bash
> # Installation propre
> npm ci
>
> # Lancer le serveur Metro et installer sur l'appareil connecté via ADB
> npx react-native run-android
> ```
>
> *Note : le code natif (libambit) communique avec le port USB via le NDK (JNI). La compilation nécessite le NDK (r26+) et CMake, que Gradle télécharge automatiquement.*
>
> ### Prérequis
>
> - Android 9+ (API 28+) — requis pour iconv (libambit)
> - Câble USB OTG
> - NDK r26+ (configuré dans `android/app/build.gradle`)
>
> ### Configuration
>
> Créer `src/config/secrets.ts` (non versionné — ne jamais partager ce fichier) :
>
> ```typescript
> export const LIVELOX_CLIENT_ID     = 'votre_livelox_client_id';
> export const STRAVA_CLIENT_ID      = 'votre_strava_client_id';
> export const STRAVA_CLIENT_SECRET  = 'votre_strava_client_secret';
> ```
>
> - **Strava** : flux OAuth2 géré dans l'app. Ajouter `client_id` et `client_secret` depuis [strava.com/settings/api](https://www.strava.com/settings/api). **"Domaine de rappel d'autorisation" : `oauth`** (partie host de `opensportsync://oauth/strava`).
> - **Clé API Runalyze** : à saisir dans l'écran Paramètres de l'app. À générer sur [runalyze.com/settings/config/account](https://runalyze.com/settings/config/account).
> - **Livelox** : flux OAuth2 géré dans l'app. Nécessite un `client_id` enregistré auprès de [livelox.com](https://livelox.com).
>
> ### Crédits
>
> - [libambit](https://github.com/openambitproject/openambit) — bibliothèque de communication Ambit open source
> - [AmbitSync](https://github.com/starryalley/AmbitSync) — référence pour le patch USB OTG Android
>
> ### Licence
>
> MIT (licence propre à opensportsync en amont, selon son README ; elle s'applique à l'amont, pas à Sommet)
