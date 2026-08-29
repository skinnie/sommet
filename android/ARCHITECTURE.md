# OpenSportsSync — Architecture & État du projet

## Objectif
Application Android personnelle (React Native Bare Workflow) pour connecter une montre Suunto Ambit via câble USB OTG, extraire les logs d'activité (NDK/C++/Kotlin), les afficher sur carte IGN, et les exporter vers Livelox.

**GitHub :** [guiguoz/opensportsync](https://github.com/guiguoz/opensportsync)

---

## État d'avancement

| Phase | Description | État |
|-------|-------------|------|
| 0 | Init projet React Native Bare | ✅ |
| 1 | Pont USB Kotlin + JNI | ✅ |
| 2 | Extraction logs + SQLite + GPX | ✅ |
| 3 | UI + cartographie IGN + profil altimétrique | ✅ |
| 4 | Partage GPX (FileProvider) + enregistrement Téléchargements | ✅ |
| 5 | Intégration libambit NDK (vrai hardware) | ✅ |
| 6 | Fix crash double-free + architecture hardware-agnostique | ✅ |
| 7 | Partage GPX via share sheet Android | ✅ |
| 8 | Icône personnalisée « Summit sync » (sommet enneigé + anneau de sync sur fond #14181c) | ✅ |
| 9 | Export Runalyze (FIT) + SettingsScreen | ✅ |
| 10 | i18n EN/FR | ✅ |
| 11 | Sécurité : Keychain credentials, PKCE S256, WebView hardening | ✅ |
| 12 | OAuth2 Livelox : connect/disconnect dans Settings, SHA-256 pure JS (Hermes) | ✅ |
| 13 | Intégration Strava : OAuth2, upload GPX, polling, connect/disconnect Settings | ✅ |
| 14 | MapScreen : allure, vitesse moyenne, replay interactif du tracé GPX | ✅ |

---

## Stack Technique

- **React Native** 0.84.1 Bare Workflow, Android uniquement, New Architecture
- **Navigation** : `@react-navigation/native` + `native-stack` (pas `stack` — gesture-handler non autoliable)
- **Cartographie** : `react-native-maps` avec `UrlTile` pour tuiles IGN
- **Base de données** : `react-native-sqlite-storage`
- **Fichiers** : `react-native-fs`
- **Parser GPX** : `fast-xml-parser`
- **Stockage clé-valeur** : `@react-native-async-storage/async-storage`
- **Sécurité** : `react-native-keychain` (credentials OAuth2), PKCE S256, WebView hardening
- **i18n** : EN/FR intégré (`src/i18n/index.ts`)
- **Couche Native** : Kotlin (UsbManager, FileDescriptor, FileProvider, MediaStore) + NDK/C++ (JNI + libambit + libusb)

---

## Structure des dossiers

```
├── android/app/src/main/
│   ├── cpp/
│   │   ├── CMakeLists.txt          # Build NDK (project="appmodules")
│   │   ├── jni_bridge.cpp          # JNI → libambit
│   │   └── libambit/               # Sources libambit (openambitproject)
│   ├── java/com/ambitsyncmodern/
│   │   ├── MainApplication.kt      # SoLoader + OpenSourceMergedSoMapping + load()
│   │   ├── MainActivity.kt         # getMainComponentName = "OpenSportsSync"
│   │   └── usb/
│   │       ├── AmbitUsbModule.kt   # shareFile(), saveToDownloads(), USB init
│   │       └── AmbitUsbPackage.kt
│   ├── res/
│   │   ├── xml/device_filter.xml   # VID/PID Ambit (10 PIDs)
│   │   └── xml/file_paths.xml      # FileProvider paths
│   └── AndroidManifest.xml         # USB OTG + FileProvider + WRITE_EXTERNAL_STORAGE maxSdk=28
├── src/
│   ├── screens/
│   │   ├── HomeScreen.tsx          # Dashboard sportif : bouton SYNC circulaire + tracé GPS
│   │   ├── LogListScreen.tsx       # Liste activités SQLite (filtres par type)
│   │   ├── MapScreen.tsx           # Carte IGN + profil altimétrique + export
│   │   └── SettingsScreen.tsx      # Clé API Runalyze + connexion/déconnexion Strava & Livelox
│   ├── components/
│   │   └── ElevationChart.tsx      # Profil altimétrique SVG-like
│   ├── services/
│   │   ├── SyncService.ts          # Orchestration sync montre → SQLite
│   │   ├── GpxService.ts           # Écriture fichiers GPX
│   │   ├── GpxParser.ts            # Parsing GPX (coordonnées, dénivelé, type activité)
│   │   ├── ApiStrava.ts            # OAuth2 + upload GPX + polling + token refresh (Keychain)
│   │   ├── ApiLivelox.ts           # OAuth2 PKCE S256 + upload GPX + SHA-256 pure JS
│   │   ├── ApiRunalyze.ts          # Upload FIT vers Runalyze (clé API via Keychain)
│   │   ├── FitExport.ts            # Encodeur FIT TypeScript pur (sans dépendance)
│   │   ├── SgeeService.ts          # Export format SGEE (orienteering)
│   │   └── VikazimutExport.ts      # Export format Vikazimut (orienteering)
│   ├── native/
│   │   ├── AmbitUsbModule.ts       # Bridge TS→Kotlin (connect, shareFile, saveToDownloads)
│   │   └── DeviceConnector.ts      # Interface DeviceProvider (architecture agnostique)
│   ├── database/
│   │   └── db.ts                   # SQLite helper (activities, gpx_path, sport_type)
│   ├── i18n/
│   │   └── index.ts                # Traductions EN/FR + hook t()
│   ├── config/
│   │   └── secrets.ts              # Credentials Livelox + Strava — GITIGNORE
│   └── specs/                      # Vide — codegenConfig.jsSrcsDir (évite symboles dupliqués)
├── scripts/
│   └── generate_icons.py           # Génère icônes mipmap (Pillow)
├── App.tsx                          # createNativeStackNavigator (Home/LogList/Map)
├── index.js                         # AppRegistry "OpenSportsSync"
└── metro.config.js                  # fs.realpathSync.native (fix Windows path casing)
```

---

## Fonctionnalités implémentées

### Sync USB
- Détection Suunto Ambit 1/2/3 (VID=0x1493, 10 PIDs supportés)
- Permission USB Android → FileDescriptor → libambit NDK
- Extraction logs → conversion GPX en C++ (`<sport_type>` custom tag)
- Déduplication SQLite (pas de doublon si sync relancée)

### Affichage
- Liste activités avec date, distance, dénivelé, type (Orientation, Course, VTT…)
- Carte IGN avec tracé GPX (`<Polyline>`)
- Profil altimétrique
- Statistiques avancées : allure (min/km), vitesse moyenne (km/h)
- Replay interactif : bouton Play pour revivre le tracé point par point sur la carte
- Reconstruction DB depuis GPX orphelins (si DB effacée)

### Export / Partage
- **Partager GPX** : share sheet Android via FileProvider (content:// URI) — compatible Strava, Files, email…
- **Enregistrer dans Téléchargements** : MediaStore (API 29+) / copie directe (API 28)
- **Strava** : OAuth2 + upload GPX + polling statut — bouton connect/disconnect dans Settings (nécessite `STRAVA_CLIENT_ID` + `STRAVA_CLIENT_SECRET` dans `secrets.ts`)
- **Runalyze** : upload FIT (format natif) via clé API personnelle stockée dans Keychain
- **Livelox** : OAuth2 PKCE S256 + upload GPX — bouton connect/disconnect dans Settings (nécessite `LIVELOX_CLIENT_ID` dans `secrets.ts`)
- **SGEE / Vikazimut** : formats export orienteering

### Sécurité
- Credentials OAuth2 stockés dans Keychain (react-native-keychain), jamais en AsyncStorage
- PKCE S256 : SHA-256 implémenté en TypeScript pur (crypto.subtle absent dans Hermes)
- WebView durci (no JS injection, no file access)

### i18n
- EN/FR intégré (`src/i18n/index.ts`), détection locale via `Intl.DateTimeFormat`

### UI
- HomeScreen : dashboard sportif, bouton SYNC circulaire, anneau pulsant, tracé GPS en filigrane
- Thème sombre cohérent (`#16213e` / `#00e5ff` cyan)
- Icône personnalisée « Summit sync » générée par script Python (`android/scripts/generate_icons.py`, artwork partagé dans `tools/packaging/sommet_icon.py`) : launcher carré + rond + foreground adaptatif, 5 densités mipmap

---

## Build

```bash
# 1. Bundler le JS (OBLIGATOIRE avant assembleDebug)
npx react-native bundle --platform android --dev false --entry-file index.js \
  --bundle-output android/app/src/main/assets/index.android.bundle \
  --assets-dest android/app/src/main/res

# 2. Build APK
cd android && ./gradlew assembleDebug

# 3. Installer
adb install -r app/build/outputs/apk/debug/app-debug.apk
```

> **IMPORTANT :** `assembleDebug` n'embarque pas le JS automatiquement. Toute modification TS/TSX nécessite de relancer l'étape 1.

---

## Points techniques critiques

- `CMakeLists.txt` : `project("appmodules")` → crée `libappmodules.so` requis par SoLoader
- `codegenConfig.jsSrcsDir: "src/specs"` (dossier vide) → évite symboles dupliqués avec react-native-maps
- USBDEVFS_INTERRUPT défini manuellement (non disponible dans NDK headers)
- `minSdkVersion=28` (iconv Bionic disponible depuis API 28)
- libambit corrigé : suppression du double-free dans `log_push_callback`
- SHA-256 implémenté en TypeScript pur dans `ApiLivelox.ts` : `crypto.subtle` absent dans Hermes (moteur JS RN)
- `assembleDebug` n'embarque pas le JS automatiquement → toujours lancer `react-native bundle` en premier

---

## Prochaines étapes

1. **Livelox** : obtenir `LIVELOX_CLIENT_ID` auprès de Mats pour finaliser l'export (OAuth2 côté app est prêt)
2. **Play Store** : clé de signature, fiche store, captures d'écran, politique de confidentialité

## Export / Intégrations

| Service | Auth | Format | Statut |
|---------|------|--------|--------|
| Strava | OAuth2 (client_secret) | GPX multipart | ✅ |
| Livelox | OAuth2 PKCE S256 | GPX JSON | ✅ (en attente client_id) |
| Runalyze | Clé API | FIT binaire | ✅ |
| GPX direct | — | GPX | ✅ |
