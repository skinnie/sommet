import { NativeModules, Platform } from 'react-native';

// ─── Détection locale ─────────────────────────────────────────────────────────

function deviceLocale(): string {
  // Intl est disponible dans Hermes (RN 0.70+) et respecte la locale système
  try {
    const intlLocale = Intl.DateTimeFormat().resolvedOptions().locale;
    if (intlLocale) return intlLocale;
  } catch {}
  // Fallback NativeModules (Old Architecture)
  if (Platform.OS === 'android') {
    return NativeModules.I18nManager?.localeIdentifier ?? 'en';
  }
  return (
    NativeModules.SettingsManager?.settings?.AppleLocale ??
    NativeModules.SettingsManager?.settings?.AppleLanguages?.[0] ??
    'en'
  );
}

export const locale   = deviceLocale();
export const isFrench = locale.toLowerCase().startsWith('fr');
// Kept in sync with the forced English `t` above (see note there).
export const dateLocale = 'en-GB';

// ─── Traductions ──────────────────────────────────────────────────────────────

const fr = {
  // HomeScreen
  sync:         'SYNC',
  synced:       'SYNC OK',
  retry:        'RÉESSAYER',
  conn:         'CONN…',
  read:         'LECTURE…',
  save:         'ENREG…',
  idle:         'En attente',
  connecting:   'Connexion à la montre…',
  fetching:     'Lecture des logs…',
  writing:      'Enregistrement…',
  done:         (n: number) => `${n} log${n !== 1 ? 's' : ''} importé${n !== 1 ? 's' : ''}`,
  error:        'Erreur',
  unknownError: 'Erreur inconnue',
  viewActivities: 'Voir les activités',
  homeNavHome: 'Accueil',
  homeConnVia: (via: string) => `via ${via}`,
  homeConnViaUsb: 'USB',
  homeConnViaBle: 'Bluetooth',

  // WeatherCard (v3.0 UI port, real feature port from desktop's WeatherService/
  // WeatherViewModel/WeatherCard.qml - open-meteo.com WMO weather codes)
  weatherClear: 'Ciel dégagé',
  weatherMainlyClear: 'Généralement dégagé',
  weatherPartlyCloudy: 'Partiellement nuageux',
  weatherOvercast: 'Couvert',
  weatherFog: 'Brouillard',
  weatherDrizzle: 'Bruine',
  weatherRain: 'Pluie',
  weatherRainShowers: 'Averses',
  weatherSnow: 'Neige',
  weatherSnowShowers: 'Averses de neige',
  weatherThunderstorm: 'Orage',
  weatherUnknown: 'Inconnu',
  weatherOffline: "Vous êtes hors ligne, sortez pour vérifier la météo !",
  weatherWind: (speed: number) => `Vent ${speed} km/h`,
  weatherHighLow: (high: number, low: number) => `H:${high}°  B:${low}°`,
  weatherHighLowShort: (high: number, low: number) => `${high}°/${low}°`,
  weatherToday: "Aujourd'hui",

  // HomeScreen — connecting flow (v2.3.2 beta)
  homeSearchingTitle: 'Recherche de votre appareil, veuillez patienter…',
  // Real, 2026-08-10 ("under the icon put AmbitApp and a funny quote")
  homeTagline: "L'aventure commence dehors.",
  homeNoDeviceTitle:
    "Aucun appareil détecté. Vérifiez le câble et l'appareil, ou utilisez l'app sans appareil.",
  homeNoDeviceSub: 'Branchez-la en USB, ou recherchez-la en Bluetooth ci-dessous.',
  homeConnectingAmbit: 'Connexion à la montre…',
  homeConnectRetryBtn: 'Réessayer',
  homeBleConnectBtn: 'Associer en Bluetooth',
  homeBleConnectWatchBtn: (name: string) => `Connecter ${name} en Bluetooth`,
  // Real bug, 2026-08-21: a live BLE link can drop on its own (watch-driven, short-lived
  // session) with no user action — this message + the existing connect-error retry UI is
  // what the user now sees instead of a silently stale "Connected" label.
  homeBleDisconnectedError: 'La montre s\'est déconnectée. Réessayez « Associer en Bluetooth ».',
  homePairWatchPill: 'Associer',
  homeBleReadyTitle: 'Prêt à associer',
  homeBleReadyMsg:
    "Sur la montre : menu « Pair Mobile App » (première association) ou « Sync now » " +
    "(déjà associée) maintenant — la fenêtre Bluetooth de la montre ne reste " +
    "active que quelques secondes. Ambit3, Traverse et Kailash, expérimental.",
  homeBleReadyBtn: 'Prêt',
  homeConnectingBle: 'Connexion Bluetooth…',
  homeActivitiesBtn: 'ACTIVITÉS',
  homeRoutesBtn:     'ROUTES',
  homePoisBtn:       'POI',
  homeSyncActivitiesBtn: 'SYNC ACTIVITÉS',
  homeBatteryLabel: 'batterie',
  homeHwLabel:      'matériel',
  homeSerialLabel:  'Nº de série',
  homeDeviceConnectedStatus: 'Connecté',

  // Kailash travel history (2026-08-08)
  homeKailashTravelTitle:     'Historique de voyage',
  homeKailashCitiesLabel:     'villes visitées',
  homeKailashCountriesLabel:  'pays visités',
  homeKailashTravelledLabel:  'distance parcourue',
  homeKailashFurthestLabel:   'plus loin de la maison',
  homeKailashLogbookLabel:    'sessions enregistrées',
  homeKailashPlacesTitle:     (n: number) => `Lieux visités (${n})`,
  homeSyncTimeButton:         'Synchroniser l\'heure',
  homeTimeSyncOk:             'Heure synchronisée',
  homeTimeSyncFailed:         (err: string) => `Échec : ${err}`,
  homeManualLink:             'Voir le manuel (EN)',
  sortBy: 'Trier :',
  colSortAsc: 'Tri croissant',
  colSortDesc: 'Tri décroissant',
  colShow: 'Afficher',
  colRemove: 'Retirer la colonne',
  addColumn: 'Ajouter une colonne',
  sortUploaded: 'Dernier ajout',
  sortName: 'Nom',
  sortDistance: 'Distance',
  sortAscent: 'Dénivelé',
  viewMap: 'Carte',
  viewList: 'Liste',
  testingSection: 'Mode test',
  testingDesc: "Simuler un appareil connecté pour explorer l'app sans montre. Les changements sont sur un appareil d'exemple, oubliés à la fermeture - rien n'est écrit sur une vraie montre.",
  testingOnShowing: (name: string)=>`Activé - ${name}`,
  testingChange: "Changer d'appareil",
  demoPickTitle: 'Choisir un appareil à simuler',
  listViewsSection: 'Vues en liste',
  routesViewLabel: 'Vue des itinéraires',
  poisViewLabel: 'Vue des POI',
  activitiesViewLabel: 'Vue des activités',
  listViewSettingDesc: 'Carte : chaque élément avec son tracé. Liste : plus léger, plus d’éléments à la fois.',
  homeThisYearTitle: 'Cette année',
  homeOpenTotals: 'Ouvrir les totaux ›',
  homeStatDistance: 'Distance',
  homeStatTime: 'Temps',
  homeStatActivities: 'Activités',
  homeLastActivityTitle: 'Dernière activité',
  homeOpenActivities: 'Ouvrir les activités ›',
  homeUntitledActivity: 'Activité sans titre',
  homeKailashTrackTitle:      'Activité GPS',
  homeKailashTrackPoints:     'points GPS',
  homeKailashTrackExport:     'Exporter la trace (GPX)',
  homeKailashExportDone:      'Trace exportée (%d points) vers Téléchargements',
  homeKailashExportEmpty:     'Aucune trace GPS à exporter',

  // LogListScreen
  all:          'Toutes',
  loadError:    'Erreur chargement',
  deleteTitle:  'Supprimer',
  deleteMsg:    (date: string) =>
    `Supprimer l'activité du ${date} ?\n\nElle ne sera pas rechargée lors des prochaines synchronisations.`,
  cancel:       'Annuler',
  delete:       'Supprimer',
  noActivities: 'Rien à synchroniser.',
  connectHint:  'Connectez la montre et lancez une synchronisation',
  noFilter:     'Aucune activité pour ce filtre',
  deleteHint:   'Appui long sur une activité pour la supprimer',
  unknownDate:  'Date inconnue',

  // MapScreen
  loading:        'Chargement du parcours…',
  noGps:          'Aucun point GPS dans ce fichier GPX',
  readError:      'Impossible de lire le fichier GPX\n',
  connect:        'Se connecter',
  close:          'Fermer',
  noApiKey:       'Clé API manquante',
  noApiKeyMsg:    'Configurez votre clé API Runalyze dans les Paramètres.',
  settings:       'Paramètres',
  runalyzeOk:     (id: string | number) =>
    `Activité importée ! (ID : ${id})\n\nPour l'envoyer vers Suunto : runalyze.com → activité → Partager → Suunto`,
  runalyzeError:  'Erreur Runalyze',
  savedOk:        'Enregistré',
  savedMsg:       (name: string) => `Fichier copié dans Téléchargements :\n${name}`,
  saveError:      "Impossible d'enregistrer\n",
  shareError:     'Impossible de partager le fichier\n',
  offlineMapTitle: 'Carte hors-ligne',
  offlineMapDone: (total: number) => `${total} tuile(s) téléchargée(s) - cette zone est maintenant disponible hors-ligne.`,
  offlineMapPartial: (ok: number, total: number) => `${ok}/${total} tuiles téléchargées - certaines ont échoué (vérifiez la connexion et réessayez).`,
  shareGpx:       'Partager GPX',
  saveDownloads:  'Enregistrer (Téléchargements)',
  shareFit:       'Partager FIT',
  saveFitDownloads: 'Enregistrer FIT (Téléchargements)',
  uploadRunalyze: 'Upload Runalyze',
  uploadStrava:   'Upload Strava',
  distance:       'Distance',
  duration:       'Durée',
  avgSpeed:       'Vitesse',
  pace:           'Allure',
  departure:      'Départ',
  arrival:        'Arrivée',
  replayTime:     'Replay (temps)',
  replayDist:     'Replay (distance)',

  // SettingsScreen — Apparence
  appearanceSection:  'Apparence',
  appearanceDesc:     'Choisissez le thème clair, sombre, ou suivez le réglage du système.',
  themeLight:         'Clair',
  themeDark:          'Sombre',
  themeSystem:        'Système',

  // SettingsScreen — Connections card (v3.0 UI port, compact tap-to-open rows)
  connectionsSection: 'Connexions',
  closeBtn: 'Fermer',

  // SettingsScreen — Strava
  stravaSection:         'Strava',
  stravaSettingsDesc:    'Connectez votre compte Strava pour exporter vos activités. La connexion utilise OAuth2.',
  stravaConnectedStatus: 'Compte Strava connecté',
  stravaDisconnectBtn:   'Se déconnecter de Strava',
  stravaDisconnected:    'Déconnecté de Strava.',
  stravaConnected:       'Connexion réussie ! Vous pouvez maintenant exporter vers Strava.',
  stravaError:           'Erreur Strava',
  stravaNotConnected:    'Connectez d\'abord Strava dans les Paramètres.',
  viewOnStrava:          'Voir sur Strava',
  stravaSuccess:         'Activité uploadée sur Strava !',

  // SettingsScreen / BackupScreen — Dropbox / Google Drive / OneDrive (added 2026-08-12,
  // "implement the ones that the user can set up easily by itself" — Backup & Restore cloud
  // destinations). Shared strings across all three providers, unlike Strava's own dedicated
  // set above, since the dialogs/status text are otherwise identical.
  cloudConnectedStatus:    'Connecté',
  cloudDisconnectBtn:      'Se déconnecter',
  cloudDisconnected:       'Déconnecté.',
  cloudConnected:          'Connexion réussie !',
  cloudError:              'Erreur de connexion',
  cloudAppKeyPlaceholder:    'Clé d\'application',
  cloudAppSecretPlaceholder: 'Secret d\'application',
  cloudClientIdPlaceholder:     'ID client',
  cloudClientSecretPlaceholder: 'Secret client',
  cloudDropboxDesc: 'Créez votre propre application gratuite sur dropbox.com/developers/apps ' +
    '(accès "App folder"). Ajoutez ces deux Redirect URI : http://localhost (pour l\'app ' +
    'ordinateur) et opensportsync://oauth/dropbox (pour cette app) - la même application ' +
    'sert les deux. Puis collez sa clé et son secret ci-dessous. Seul votre dossier ' +
    '"AmbitApp Backups" dans Dropbox est utilisé.',
  cloudGoogleDriveDesc: 'Créez votre propre application gratuite sur console.cloud.google.com ' +
    '(activez l\'API Google Drive, créez un ID client OAuth de type "Desktop app"), puis ' +
    'collez son ID et son secret ci-dessous. Seuls les fichiers créés par cette application ' +
    'sont visibles (portée drive.file).',
  cloudOneDriveDesc: 'Créez votre propre application gratuite sur entra.microsoft.com ' +
    '(plateforme "Mobile and desktop applications"). Ajoutez ces deux Redirect URI : ' +
    'http://localhost (pour l\'app ordinateur) et opensportsync://oauth/onedrive (pour ' +
    'cette app) - la même application sert les deux. Puis collez son ID d\'application ' +
    'ci-dessous — pas de secret nécessaire ici (PKCE). Seul le dossier d\'application ' +
    'OneDrive de cette app est utilisé.',

  // BackupScreen — Cloud backup card
  cloudBackupSection:  'Sauvegarde cloud',
  cloudBackupDesc:     'Optionnel. Uploadez une sauvegarde ci-dessus vers votre propre ' +
    'stockage cloud connecté, ou téléchargez-en une — elle apparaît alors dans la liste ' +
    'locale ci-dessus. Connectez d\'abord un service dans Paramètres → Connexions.',
  cloudNone:           'Aucun',
  cloudNoneConnected:  'Aucun service connecté pour l\'instant — configurez-en un dans ' +
    'Paramètres → Connexions.',
  cloudEmpty:          'Aucune sauvegarde cloud trouvée — utilisez "Uploader" sur une ' +
    'sauvegarde ci-dessus.',
  cloudUploadBtn:      'Uploader',
  cloudDownloadBtn:    'Télécharger',
  cloudRefreshBtn:     'Rafraîchir',
  cloudUploaded:       'Uploadée vers le cloud.',
  cloudDownloaded:     'Téléchargée — elle apparaît maintenant dans la liste locale ci-dessus.',


  // SettingsScreen — Runalyze
  emptyKey:       'Clé vide',
  emptyKeyMsg:    'Entrez votre clé API Runalyze.',
  keySaved:       'Clé API Runalyze sauvegardée.',
  keyDeleted:     'Clé API Runalyze supprimée.',
  deleted:        'Supprimé',
  runalyzeSection: 'Runalyze',
  runalyzeDesc:   "Runalyze est une plateforme d'analyse d'entraînement open source et gratuite. Vos activités seront importées dans votre compte Runalyze.",
  runalyzeApiHint: 'Générez votre clé API sur ',
  runalyzeApiLink: 'runalyze.com → Account → API access',
  apiKey:          'Clé API',
  apiKeyPlaceholder: 'Collez votre clé API ici',
  saveBtn:         'Enregistrer',
  deleteBtn:       'Supprimer',
  keyStored:       'Clé enregistrée',

  // SettingsScreen — Intervals.icu
  intervalsSection:    'Intervals.icu',
  intervalsDesc:       "Connectez votre compte Intervals.icu pour y importer vos activités. Authentification par clé API personnelle (pas d'OAuth).",
  intervalsApiHint:    'Générez votre clé API sur ',
  intervalsApiLink:    'intervals.icu → Settings → Developer Settings',
  athleteId:           'ID Athlète',
  athleteIdPlaceholder: 'ex : i123456',
  emptyCreds:          'Champs manquants',
  emptyCredsMsg:       "Entrez votre ID athlète et votre clé API Intervals.icu.",
  credsSaved:          'Identifiants Intervals.icu sauvegardés.',
  credsDeleted:        'Identifiants Intervals.icu supprimés.',
  credsStored:         'Identifiants enregistrés',

  // MapScreen — Intervals.icu
  uploadIntervals:     'Upload Intervals.icu',
  intervalsError:      'Erreur Intervals.icu',
  intervalsSuccess:    'Activité importée sur Intervals.icu !',
  viewOnIntervals:     'Voir sur Intervals.icu',
  noCreds:             'Identifiants manquants',
  noCredsMsg:          "Configurez votre ID athlète et votre clé API Intervals.icu dans les Paramètres.",


  // App.tsx
  logListTitle:  'Activités',
  mapFallback:   'Parcours',
  settingsTitle: 'Paramètres',
  oauthMissingCode: 'Code OAuth manquant dans le callback',

  // HomeScreen — bouton données GPS (SGEE/AGPS)
  gpsUpdate:          'GPS',
  gpsDone:            'GPS OK',
  gpsDownloading:      'DL…',
  gpsIdle:            'Données GPS',
  gpsDownloadingMsg:  'Téléchargement des données GPS…',
  gpsDoneMsg:         'Données GPS à jour',

  // HomeScreen — bouton envoyer une route
  sendRoute:          'ROUTE',
  routeDone:          'ENVOYÉ',
  routePicking:       'FICHIER…',
  routeParsing:       'LECTURE…',
  routeIdle:          'Envoyer une route',
  routePickingMsg:    'Choisissez un fichier GPX…',
  routeParsingMsg:    'Analyse du GPX…',
  routeWritingMsg:    'Écriture sur la montre…',
  routeDoneMsg: (name: string, points: number, waypoints: number) =>
    `« ${name} » envoyée (${points} points, ${waypoints} waypoint${waypoints !== 1 ? 's' : ''})`,
  sendRouteConfirmTitle: 'Envoyer une route',
  sendRouteConfirmMsg:
    "Ceci va remplacer toute route déjà présente sur la montre. Vos POIs sont préservés " +
    "automatiquement. Attention : cette route n'est pas permanente — la prochaine " +
    "synchronisation SuuntoLink, ou même la simple proximité Bluetooth de l'app Suunto, " +
    "la remplacera. C'est fait pour charger une route juste avant de partir, pas pour " +
    "la stocker durablement.",
  sendRouteConfirmBtn: 'Envoyer',
  routeScreenTitle:   'Route',
  routeSendSection:   'Envoyer une route',
  routePlannerTitle:  'Planifier une route',
  routeWatchNote: 'Les routes envoyées à la montre seront effacées par SuuntoLink et ne sont pas transmises à l’app Suunto.',
  poiWatchNote: 'Les POI envoyés à la montre sont transmis à l’app Suunto par SuuntoLink.',
  poiInfoTitle: 'Points d’intérêt',
  routePlannerIntro:  'Vous pouvez exporter vos routes depuis l\'app Suunto en gpx et les importer ici, ou utiliser l\'un de ceux-ci ou d\'autres :',
  routePlannerOnline: '(en ligne)',
  routePlannerOfflineWinMac: '(hors ligne, Win/Mac)',
  routePlannerOfflineAll:    '(hors ligne, Linux/Win/Mac)',
  routeExportSection: 'Exporter depuis la montre',
  routeExportDesc:    'Lit les routes et waypoints présents sur la montre et les enregistre dans un fichier GPX (Téléchargements).',
  routeExportBtn:     'Exporter depuis la montre',
  routeExportReading: 'Lecture de la navigation…',
  navExportedTitle:   'Export terminé',
  navExportedMsg: (routes: number, waypoints: number) =>
    `${routes} route${routes !== 1 ? 's' : ''}, ${waypoints} waypoint${waypoints !== 1 ? 's' : ''} enregistrés dans Téléchargements.`,

  // RouteScreen — Bluetooth (expérimental, v0.3.0, Ambit3/Traverse uniquement)
  bleExperimentalBadge: 'EXPÉRIMENTAL',
  bleExperimentalDisclaimer:
    "Le transfert par Bluetooth est expérimental et n'a pas encore été vérifié sur du matériel réel — " +
    "utilisez le câble si possible. Réservé aux montres Ambit3 et Traverse. Vous devrez déclencher " +
    '"Sync now" sur la montre au bon moment (voir la fenêtre suivante).',
  sendRouteBleBtn:    'Envoyer (Bluetooth)',
  routeExportBleBtn:  'Exporter (Bluetooth)',
  // v3.0 UI port - real "On the watch" list (RoutesPage.qml parity, 2026-08-09)
  routeOnWatchSection: 'Sur la montre',
  routeOnWatchReading: 'Lecture des routes sur la montre…',
  routeOnWatchEmpty: 'Rien à synchroniser.',
  routeOnWatchError: (msg: string) => `Impossible de lire les routes : ${msg}`,
  routeStats: (dist: string, points: number, ascent: number, descent: number) =>
    `${dist} · ${points} points · D+ ${ascent} m · D- ${descent} m`,
  routeItemExportBtn: 'Exporter',
  routeUploadBtn: 'Envoyer sur la montre',
  routeRehearseBtn: 'Simuler (sans écrire)',
  routeDiscardBtn: 'Annuler',
  bleScanning:        'Recherche de la montre…',
  bleConnecting:      'Connexion Bluetooth…',
  bleSyncNowTitle:    'Prêt à synchroniser',
  bleSyncNowMsg:
    'Sur la montre, déclenchez "Sync now" MAINTENANT, puis appuyez immédiatement sur "Prêt" ' +
    'ci-dessous — la fenêtre Bluetooth de la montre ne reste active que quelques secondes.',
  bleSyncNowReady:    'Prêt',

  // HomeScreen / PoiScreen
  poiButton:        'POI',
  poiScreenTitle:   'POI',
  poiImportSection: 'Importer depuis un GPX',
  poiImportDesc:    "Choisissez un fichier GPX : chaque <wpt> qu'il contient sera envoyé comme POI, en préservant ceux déjà sur la montre.",
  poiExportSection: 'Exporter vers un GPX',
  poiExportDesc:    'Lit tous les POI présents sur la montre et les enregistre dans un fichier GPX (Téléchargements).',
  poiExportBtn:     'Exporter depuis la montre',
  poiExportReading: 'Lecture des POI…',
  poiExportedTitle: 'POI exportés',
  poiExportedMsg: (n: number) => `${n} POI${n !== 1 ? 's' : ''} enregistré${n !== 1 ? 's' : ''} dans Téléchargements.`,

  // SettingsScreen — Ajouter un POI
  poiSection:    'Ajouter un POI',
  poiDesc:       "Envoie un point d'intérêt à la montre par câble, en préservant ceux déjà présents.",
  poiName:       'Nom',
  poiNamePlaceholder: 'ex : Sommet',
  poiLat:        'Latitude',
  poiType:       'Type',
  poiLon:        'Longitude',
  poiAddBtn:     'Envoyer à la montre',
  poiWriting:    'Envoi du POI…',
  poiInvalid:    'Entrée invalide',
  poiNameRequired: 'Entrez un nom pour le POI.',
  poiCoordsInvalid: 'Latitude/longitude invalide (latitude -90 à 90, longitude -180 à 180).',
  poiAddedTitle: 'POI ajouté',
  poiAddedMsg: (name: string) => `« ${name} » a été envoyé à la montre.`,
  poiImportBtn:     'Importer depuis un GPX',
  poiImportPicking: 'Choisissez un fichier GPX…',
  poiImportParsing: 'Analyse du GPX…',
  poiImportWriting: (done: number, total: number) => `Envoi des POI… (${done}/${total})`,
  poiImportedTitle: 'POI importés',
  poiImportedMsg: (n: number) => `${n} POI${n !== 1 ? 's' : ''} envoyé${n !== 1 ? 's' : ''} à la montre.`,
  // v3.0 UI port - real "On the watch" list + real import preview (PoisPage.qml parity)
  poiOnWatchSection: 'Sur la montre',
  poiOnWatchReading: 'Lecture des POI sur la montre…',
  poiOnWatchEmpty: 'Rien à synchroniser.',
  poiOnWatchError: (msg: string) => `Impossible de lire les POI : ${msg}`,
  poiItemExportBtn: 'Exporter',
  poiItemAddBtn: 'Ajouter',
  poiCoords: (lat: number, lon: number) => `${lat.toFixed(5)}, ${lon.toFixed(5)}`,

  // SettingsScreen — Ambit3 Settings (2026-08-08)
  ambitSettingsSection: 'Réglages de la montre',
  kailashSettingsSection: 'Réglages Kailash',
  ambitSettingsTitle: (name: string) => `Réglages ${name}`,
  ambitSettingsDesc:
    'Réglages réels de la montre (langue, formats, luminosité, etc.), lus et modifiés par ' +
    'câble USB — confirmé sur du matériel réel le 8 août 2026.',
  ambitSettingsReadBtn: 'Lire les réglages',
  ambitSettingsRefreshBtn: 'Actualiser',
  ambitSettingsReading: 'Lecture des réglages…',
  ambitSettingsReadOnly: 'Modifiables via le câble USB — vérifié sur du matériel réel. Quelques réglages sans offset d\'écriture connu restent en lecture seule.',
  orbitalDataTitle: 'Données orbitales',
  ephemerisGpsOnly: 'Éphémérides GPS uniquement',
  ephemerisGpsOnlyInfo: 'Cette montre peut aussi utiliser les satellites GLONASS et dispose de sa propre mémoire pour leurs données orbitales. Les logiciels Suunto ne les lui envoient jamais, donc ces satellites démarrent « à froid » à chaque fois. Sommet envoie les données orbitales GPS et GLONASS, ce qui peut accélérer l\'acquisition d\'une position. Cochez pour n\'envoyer que le GPS.',

  // SportModesScreen — Ambit3 CustomModes (2026-08-08), Ambit3-only, pas disponible sur Kailash
  sportModesButton:      'MODES SPORT',
  sportModesScreenTitle: 'Modes sport',
  sportModesDesc:
    'Modifie les modes sport de la montre : noms, autolap, limites FC, capteurs et les écrans.',
  sportModesReadBtn: 'Lire les modes sport',
  sportModesInstallApp: 'Installer une app Suunto…',
  sportModesReading: 'Lecture des modes sport…',
  sportModesCheckConnection: 'Vérifiez la connexion de la montre.',
  sportModesRefreshBtn: 'Actualiser',
  // Création / suppression / multisport (2026-08-14, portage de tools/sport_mode_manage.py)
  sportModesManageTitle: 'Modes sport sur la montre',
  sportModesCounts: (used: number, max: number, multi: number, maxMulti: number) =>
    `${used}/${max} modes · ${multi}/${maxMulti} multisport`,
  sportModesCreateBtn: '＋ Mode sport',
  sportModesCreateMultiBtn: '＋ Multisport',
  sportModesMultiBadge: 'multisport',
  sportModesUsedByBadge: (names: string) => `utilisé par ${names}`,
  sportModesDeleteBtn: 'Supprimer',
  sportModesDeleteTitle: 'Supprimer le mode',
  sportModesDeleteMsg: (name: string) => `Supprimer « ${name} » de la montre ?`,
  sportModesCreateTitle: 'Nouveau mode sport',
  sportModesCreateMultiTitle: 'Nouveau mode multisport',
  sportModesNamePlaceholder: 'Nom',
  sportModesActivityLabel: 'Activité',
  sportModesLegsLabel: 'Étapes (dans l’ordre)',
  sportModesLegsHint: 'Touchez les modes dans l’ordre où la montre doit les enchaîner. Répétitions autorisées.',
  sportModesLegsChosen: (legs: string) => `Ordre : ${legs}`,
  sportModesNoLegsYet: 'Aucune étape choisie',
  sportModesCreateConfirm: 'Créer',
  sportModesWriteWarning:
    'Les changements sont écrits directement sur la montre. Faites une sauvegarde d’abord par sécurité.',
  sportModesWritingStep: (step: number, total: number) => total > 1 ? `Écriture ${step}/${total}…` : 'Écriture…',
  sportModesVerifying: 'Vérification…',
  sportModesManageReadError: 'Impossible de lire la structure des modes sport.',
  sportModesRenameBtn: 'Renommer',
  sportModesExpandBtn: 'Détails',
  sportModesCollapseBtn: 'Masquer',
  // v3.0 UI port - List<->Detail rework (real desktop parity, 2026-08-09)
  sportModesBackBtn: 'Modes sport',
  sportModesNameLabel: 'Nom',
  sportModesDisplaysCount: (n: number, max: number) => `Affichages (${n}/${max})`,
  sportModesBuiltInShort: '•',
  sportModesBuiltInMsg: (template: string) => template ? `Écran système intégré (${template}) - non modifiable.` : 'Écran système intégré - non modifiable.',
  sportModesScreenLabel: (n: number) => `Écran ${n}`,
  sportModesAutolapLabel: 'Autolap (m)',
  sportModesSetBtn: 'Appliquer',
  sportModesHrLimitsLabel: 'Limites FC',
  sportModesHrLowLabel: 'Basse',
  sportModesHrHighLabel: 'Haute',
  sportModesPodsLabel: 'Capteurs externes',
  sportModesDisplaysLabel: 'Affichages',
  sportModesChangeBtn: 'Changer',
  sportModesPickerTitle: 'Choisir le type de champ',
  sportModesCloseBtn: 'Fermer',
  sportModesWriteSentNotConfirmed: 'Écriture envoyée mais non confirmée par relecture.',

  // TrackPreview — pas de données GPS (2026-08-10, "for data without gps data, please do a
  // nice mappyish image saying no data")
  trackPreviewNoData: 'Pas de données GPS',

  // SettingsScreen — Cartes (2026-08-09, "no button to change provider, nor in the
  // settings like the desktop version") - même carte que desktop/qml/pages/SettingsPage.qml,
  // avec IGN en option Android supplémentaire (voir MapProviderService.ts)
  mapsSection: 'Cartes',
  mapsProviderDesc: (name: string) => `Fournisseur : tuiles de ${name}`,
  mapProviderIgnLabel: 'IGN (France)',
  mapProviderOsmLabel: 'OpenStreetMap (standard)',
  mapProviderCyclosmLabel: 'CyclOSM (axé cyclisme)',
  offlineMapCacheSize: (size: string) => `Cache de cartes hors-ligne : ${size}`,
  offlineMapClearCache: 'Vider le cache',

  // SettingsScreen — À propos / mentions légales
  aboutSection: 'À propos',
  aboutVersion: (v: string) => `Sommet v${v}`,
  aboutDisclaimer:
    "Sommet est un projet personnel, indépendant et open source. Il n'est ni affilié à, " +
    "ni approuvé, ni sponsorisé par Suunto Oy ou Garmin Ltd. Suunto, Ambit, Traverse, " +
    "Kailash, Garmin, eTrex, ainsi que tout autre nom de produit ou marque mentionné dans " +
    "l'application, sont des marques déposées ou non déposées de leurs détenteurs " +
    "respectifs (Suunto Oy et Garmin Ltd.) ; elles ne sont utilisées ici que pour décrire " +
    "la compatibilité avec ces appareils. Tous droits réservés à leurs propriétaires " +
    "respectifs. Fourni « tel quel », sans aucune garantie. Sous licence GNU GPLv3 ; " +
    "développé avec React Native (MIT). Données cartographiques © contributeurs " +
    "OpenStreetMap (ODbL) ; météo par Open-Meteo (CC BY 4.0) ; icônes Google Material " +
    "Symbols (Apache 2.0).",
  aboutCreditsSection: 'Remerciements',
  aboutCreditsIntro:
    "Ce projet s'appuie sur le travail réel d'autres personnes, sans qui la " +
    "rétro-ingénierie des protocoles utilisés ici aurait pris bien plus de temps :",

  // Garmin — shared (v2.3 beta, updated v2.3.2)
  garminButton:      'GARMIN',
  garminWaitingForMount: (secondsLeft: number) =>
    `En attente du montage de l'appareil… (jusqu'à 40s, ${secondsLeft}s restantes)`,
  garminUnknownModel: 'Modèle inconnu',
  garminFirmwareLabel: 'firmware',
  garminSdCardPresent: 'Carte SD détectée',
  garminSdCardAbsent:  'Aucune carte SD détectée',
  garminInternalMemoryWarning:
    "⚠️ Par sécurité, cette fonction n'écrit JAMAIS sur la mémoire interne de l'appareil. " +
    "Une carte SD doit être présente ; le fichier sera envoyé uniquement sur celle-ci " +
    "(SDCARD\\Garmin\\GPX).",
  garminNoSdCardMsg: "Fonction indisponible : aucune carte SD détectée dans l'appareil.",

  // Home — inline activity sync for Garmin (v2.3.2 beta, no sub-screen — see
  // GarminActivityService.ts)
  homeGarminSyncReading: 'Lecture des activités…',
  homeGarminSyncWriting: (current: number, total: number) => `Import… (${current}/${total})`,
  homeGarminSyncDone: (count: number) =>
    count === 0
      ? 'Aucune nouvelle activité à importer.'
      : `${count} activité${count !== 1 ? 's' : ''} importée${count !== 1 ? 's' : ''}.`,

  // GarminRouteScreen (v2.3.2 beta)
  garminRouteScreenTitle: 'Routes Garmin',
  garminRouteSendSection: 'Envoyer une route',
  garminRouteSendDesc: 'Envoie un fichier GPX (route) sur la carte SD de l\'appareil.',
  garminRouteSendBtn:  'Choisir un fichier GPX',
  garminRouteSendDone: 'Fichier envoyé sur la carte SD.',
  garminRouteExportSection: 'Exporter les routes',
  garminRouteExportDesc:
    "Lit les fichiers GPX enregistrés sur l'appareil (mémoire interne et carte SD) et les " +
    "enregistre dans Téléchargements.",
  garminRouteExportBtn: 'Exporter',
  garminRouteExportDone: (count: number) =>
    count === 0 ? 'Aucun fichier de route trouvé.' : `${count} fichier${count !== 1 ? 's' : ''} exporté${count !== 1 ? 's' : ''}.`,
  garminShareBtn: 'Partager…',
  // Real, 2026-08-10 ("Garmin: POIs and routes, please follow the same logic as suunto,
  // showing them on the maps") - même carte "Sur l'appareil" que routeOnWatchSection, avec
  // un aperçu carte par élément (TrackPreview), pas seulement un export en masse.
  garminRouteOnDeviceSection: "Sur l'appareil",
  garminRouteOnDeviceReading: "Lecture des routes sur l'appareil…",
  garminRouteOnDeviceEmpty: "Aucune route sur l'appareil.",

  // GarminPoiScreen (v2.3.2 beta)
  garminPoiScreenTitle: 'POI Garmin',
  garminPoiSendSection: 'Envoyer un POI',
  garminPoiSendDesc: 'Envoie un fichier GPX (waypoints) sur la carte SD de l\'appareil.',
  garminPoiSendBtn:  'Choisir un fichier GPX',
  garminPoiSendDone: 'Fichier envoyé sur la carte SD.',
  garminPoiRetrieveSection: 'Récupérer les POI',
  garminPoiRetrieveDesc:
    "Lit les fichiers Waypoints (créés par Garmin BaseCamp) sur l'appareil (mémoire interne " +
    "et carte SD) et les enregistre dans Téléchargements.",
  garminPoiRetrieveBtn: 'Récupérer',
  garminPoiRetrieveDone: (count: number) =>
    count === 0 ? 'Aucun fichier de POI trouvé.' : `${count} fichier${count !== 1 ? 's' : ''} récupéré${count !== 1 ? 's' : ''}.`,
  garminPoiOnDeviceSection: "Sur l'appareil",
  garminPoiOnDeviceReading: "Lecture des POI sur l'appareil…",
  garminPoiOnDeviceEmpty: "Aucun POI sur l'appareil.",

  // BackupScreen (v2.3.2 beta) — Ambit firmware backup
  backupButton:      'Backup',
  backupScreenTitle: 'Backup',
  // v3.0 UI port - real "Backup & Restore" card (BackupPage.qml parity, 2026-08-09).
  // Restore itself is deferred - see NavBackupService.ts's own header comment on why.
  backupNavSection: 'Sauvegarde de la navigation',
  backupNavDesc: 'Couvre les routes et les POI ensemble (toute la base de navigation de la montre).',
  backupNavCreateBtn: 'Créer une sauvegarde',
  backupNavWorking: 'Sauvegarde en cours…',
  backupFolderSection: 'Sauvegarder la base dans un dossier',
  backupFolderInfo: 'Vous pouvez l\'enregistrer dans votre dossier cloud préféré, pour qu\'elle soit synchronisée si vous le souhaitez.',
  backupFolderBtn: 'Enregistrer une sauvegarde dans un dossier…',
  backupNavDone: 'Sauvegarde créée.',
  backupExistingSection: 'Sauvegardes existantes',
  backupExistingEmpty: 'Aucune pour le moment.',
  backupShareBtn: 'Partager',
  backupRestoreUnavailable:
    'La restauration nécessite une fonction native pas encore construite sur Android - ' +
    'ces sauvegardes sont pour l\'instant en lecture seule (utilisez le bureau pour restaurer).',
  backupWarning:
    "⚠️ Sauvegarde uniquement : ce fichier ne peut PAS être réinstallé sur la montre depuis " +
    "cette app. Pour mettre à jour le firmware, utilisez l'app officielle Suunto ou SuuntoLink.",
  backupCheckSection: 'Vérifier le firmware disponible',
  backupCheckDesc: "Interroge les serveurs Suunto pour connaître la dernière version de firmware disponible pour votre montre.",
  backupCheckBtn:  'Vérifier',
  backupReading:   'Lecture des informations de la montre…',
  backupChecking:  'Vérification auprès de Suunto…',
  backupLatestVersion: (v: string) => `Dernière version disponible : ${v}`,
  backupUploadDate: (d: string) => `Publiée le ${d}`,
  backupNoUpdateInfo: 'Aucune information de firmware disponible pour ce modèle/cette version matérielle.',
  backupDownloadSection: 'Télécharger une sauvegarde',
  backupDownloadDesc:
    "Télécharge le fichier de firmware tel quel, sans le modifier ni le décoder. Vous " +
    "pourrez choisir où l'enregistrer (Téléchargements par défaut).",
  backupDownloadBtn: 'Télécharger',
  backupDownloading: (pct: number) => `Téléchargement… ${pct}%`,
  backupDownloadDone: 'Sauvegarde enregistrée.',

  // Totals / Calendar (2026-08-13, portage des écrans TotalsPage.qml / CalendarPage.qml)
  totalsScreenTitle: 'Totaux',
  totalsTitle: 'Totaux',
  totalsEmptyNoData: "Rien à additionner pour l'instant - lisez d'abord vos activités depuis la montre et ceci se remplira.",
  totalsEmptyYear: 'Aucune activité cette année.',
  totalsHoursTitle: 'Heures dehors',
  totalsHoursSubtitle: (n: number) => `Sur ${n} activité${n !== 1 ? 's' : ''} avec une trace GPS`,
  totalsDistanceTitle: 'Distance',
  totalsDistanceDesc: 'Les activités sont regroupées par sport automatiquement. Touchez-en une pour la mettre en avant.',
  totalsActivitiesCount: (n: number) => `${n} activité${n !== 1 ? 's' : ''}`,
  totalsEnergyTitle: 'Énergie dépensée',
  totalsEnergyUnavailable: "Les calories ne sont pas encore lues depuis la montre sur Android (elles n'apparaissent pas dans le GPX). Utilisez l'app de bureau pour ce total.",
  totalsMore: 'Bientôt plus !',
  calendarScreenTitle: 'Calendrier',
  calendarTitle: 'Calendrier',
  calendarActivities: (n: number) => `${n} activité${n !== 1 ? 's' : ''}`,
  calendarToday: "Aujourd'hui",
  calendarLegendRest: 'Jour de repos',
  calendarLegendActivity: 'Activité',

  // Fonctionnalités expérimentales (2026-08-14) - App Zone, Intervalles, Smart Sensor
  experimentalSection: 'Fonctionnalités expérimentales',
  experimentalToggleLabel: 'Activer les fonctionnalités expérimentales',
  experimentalToggleDesc:
    'Affiche des outils encore en test sur Android : App Zone (installer des apps Suunto), ' +
    'entraînements par intervalles et la ceinture cardio Smart Sensor. Certains écrivent sur ' +
    'la montre, faites d’abord une sauvegarde.',
  markSyncedLabel: 'Marquer les activités comme synchronisées pour l’app Suunto et SuuntoLink',
  markSyncedDesc:
    'Une fois une activité lue ici, indiquer à la montre qu’elle est déjà synchronisée. Cela ' +
    'évite les doublons dans l’app Suunto et SuuntoLink — mais l’activité ne pourra plus être ' +
    'récupérée depuis la montre si l’app Suunto échoue à la conserver. Laissez désactivé si ' +
    'vous n’êtes pas sûr.',
  experimentalWarningBanner:
    '⚠️ Expérimental — non testé sur matériel réel. Connectez la montre par câble et faites ' +
    'une sauvegarde avant d’écrire quoi que ce soit.',
  experimentalAppZone: 'App Zone (Suunto Apps)',
  experimentalAppZoneDesc: 'Installer des Suunto Apps sur la montre (Ambit3).',
  experimentalIntervals: 'Séance d’intervalles',
  experimentalIntervalsDesc: 'Créer une séance d’intervalles (Suunto App ou séance planifiée).',
  experimentalSmartSensor: 'Smart Sensor',
  experimentalSmartSensorDesc: 'Ceinture cardio Suunto Smart Sensor via Bluetooth.',
  experimentalWorkoutCalendar: 'Calendrier d’entraînement',
  experimentalWorkoutCalendarDesc: 'Séances datées dans le menu WORKOUT, nommées « jj/mm_nom ».',
  smartSensorScreenTitle: 'Smart Sensor',
  appZoneScreenTitle: 'App Zone',
  intervalsScreenTitle: 'Intervalles',
  experimentalComingNote:
    'En construction. L’écran et le pipeline de données se mettent en place ; l’installation ' +
    'sur la montre nécessite la prochaine build native. Rien n’écrit encore sur votre montre.',
  smartSensorScanBtn: 'Chercher la ceinture',
  smartSensorScanning: 'Recherche…',
  smartSensorNotFound: 'Aucun Smart Sensor trouvé. Portez la ceinture (contact peau requis) puis réessayez.',
  smartSensorForgetBtn: 'Oublier',
  smartSensorNativeMissing: 'Le module Bluetooth Smart Sensor n’est pas dans cette build — recompilez l’app pour l’activer.',
  smartSensorBattery: 'Batterie',
  smartSensorHeartRate: 'Fréquence cardiaque',
  smartSensorNoReading: 'aucune lecture',
  // App Zone import (2026-08-14)
  appZoneNativeMissing: 'Le module App Zone n’est pas dans cette build — recompilez l’app.',
  appZoneNoCatalogTitle: 'Aucun catalogue importé',
  appZoneInstructions:
    'Sommet ne distribue aucune app Suunto (contenu propriétaire). Importez votre propre ' +
    'catalogue depuis SuuntoLink : sur l’ordinateur où SuuntoLink est installé, trouvez le ' +
    'dossier « suunto-apps » et son fichier « index.json » (~29 Mo), copiez-le sur cet ' +
    'appareil, puis touchez Importer.',
  appZoneImportBtn: 'Importer depuis SuuntoLink',
  appZoneReimportBtn: 'Ré-importer',
  appZoneImporting: 'Importation… (cela peut prendre un moment)',
  appZoneImported: (n: number) => `${n} apps importées`,
  appZoneImportFailed: 'Échec de l’importation',
  appZoneSearchPlaceholder: 'Rechercher des apps…',
  appZoneAppsCount: (n: number) => `${n} apps`,
  appZoneInstallNote: 'Touchez une app pour l’installer sur un écran de mode sport.',
  appZoneInstallTitle: (name: string) => `Installer « ${name} »`,
  appZonePickMode: 'Choisir un mode sport',
  appZonePickScreen: 'Choisir un écran',
  appZonePickField: 'Choisir le champ à partager avec l’app',
  appZoneReadingModes: 'Lecture des modes sport…',
  appZoneNoRealScreens: 'Ce mode n’a aucun écran modifiable.',
  appZoneInstallBtn: 'Installer',
  appZoneInstalling: 'Installation…',
  appZoneInstalledMsg: 'App installée.',
  appZoneScreenLabel: (n: number) => `Écran ${n}`,
  // Intervals (2026-08-14)
  intervalsWarning:
    '⚠️ Expérimental. La compilation utilise un compilateur communautaire tiers (pas Suunto). ' +
    'L’installation sur la montre n’est pas confirmée sur du matériel. Faites une sauvegarde.',
  intervalsAppSection: 'Séance d’intervalles (Suunto App)',
  intervalsAppDesc: 'Construire → compiler (en ligne) → installer sur un écran.',
  intervalsWarmup: 'Échauffement (min)',
  intervalsReps: 'Répétitions',
  intervalsWork: 'Effort (min)',
  intervalsRest: 'Récup (min)',
  intervalsCooldown: 'Retour au calme (min)',
  intervalsCompileInstall: 'Compiler et installer',
  intervalsCompiling: 'Compilation…',
  intervalsGenerateBtn: 'Copier la source et ouvrir le compilateur',
  intervalsImportBtn: 'Importer l’app compilée',
  intervalsSourceCopiedMsg: 'Le site du compilateur s’est ouvert et la source est affichée ci-dessous. Sélectionnez-la, copiez-la sur le site, compilez, téléchargez le résultat, puis touchez « Importer l’app compilée ».',
  intervalsSourceLabel: 'Source de la séance — sélectionnez tout, copiez, collez sur le site du compilateur.',
  intervalsCompilerNote: 'La compilation se fait sur un site communautaire tiers (ni Suunto, ni nous). Sommet génère seulement la source et ouvre le site ; vous compilez là-bas et importez le résultat.',
  intervalsPlannedSection: 'Séance planifiée (natif)',
  intervalsPlannedDesc: 'Format non confirmé — peut ne pas apparaître sur la montre.',
  intervalsName: 'Nom',
  intervalsDuration: 'Durée (min)',
  intervalsIntensity: 'Intensité (1-5)',
  intervalsWriteBtn: 'Écrire sur la montre',
  intervalsWritten: 'Écrit sur la montre.',
  intervalsWriting: 'Écriture…',
  // Workout Calendar (2026-08-21) - dated native guided workouts named "dd/mm_nom" dans le
  // menu WORKOUT, contournant la zone TrainingProgram native (inaccessible). Compilation
  // manuelle, comme Intervals - voir intervalsCompilerNote.
  workoutCalendarWarning:
    '⚠️ Expérimental, montres Ambit3 uniquement. Chaque séance se compile sur le site ' +
    'communautaire tiers (pas Suunto), comme les séances d’intervalles ci-dessus. À la ' +
    'synchronisation, tout ce qui est daté avant aujourd’hui est effacé de la montre et ' +
    'remplacé par ce qui vient ensuite.',
  workoutCalendarDateLabel: 'Date',
  workoutCalendarModeLabel: 'Mode sportif',
  workoutCalendarImportTitle: 'Importer depuis intervals.icu',
  workoutCalendarImportDesc:
    'Récupère tes séances planifiées sur une plage de dates et les ajoute au plan (cibles FC ' +
    'reconstruites depuis tes zones). Choisis d\'abord un mode sportif ci-dessous, puis compile chaque séance en attente.',
  workoutCalendarImportFrom: 'Du',
  workoutCalendarImportTo: 'Au',
  workoutCalendarImportBtn: 'Importer les séances planifiées',
  workoutCalendarImportNone: 'Aucune séance planifiée sur cette plage',
  workoutCalendarImportedPrefix: 'Importées',
  workoutCalendarImportCompileHint: 'Compile chaque séance en attente ci-dessous.',
  workoutCalendarCompilingRow: 'Compilation…',
  workoutCalendarAddBtn: 'Ajouter au calendrier',
  workoutCalendarAddedMsg: 'Ajouté au calendrier.',
  workoutCalendarPlanTitle: 'Calendrier',
  workoutCalendarPlanEmpty: 'Rien de prévu pour l’instant.',
  workoutCalendarPending: 'en attente de compilation',
  workoutCalendarPreviewBtn: 'Aperçu de la synchro',
  workoutCalendarSyncBtn: 'Synchroniser avec la montre',
  workoutCalendarSyncing: 'Synchronisation…',
  workoutCalendarSyncedMsg: 'Montre synchronisée.',
  workoutCalendarEmptyPlanMsg: 'Le calendrier est vide.',
  workoutCalendarPickModeFirst: 'Choisissez un mode sportif.',
  // ── Gear tracker (v3) ──
  gearButton: 'Matériel',
  gearScreenTitle: 'Matériel',
  gearBikes: 'Vélos',
  gearShoes: 'Chaussures',
  gearParts: 'Composants',
  gearReminders: 'Rappels d’entretien',
  gearAddBike: 'Ajouter un vélo',
  gearAddShoes: 'Ajouter des chaussures',
  gearAddPart: 'Ajouter un composant',
  gearAddReminder: 'Ajouter un rappel',
  gearName: 'Nom',
  gearRetired: 'Retiré',
  gearPrimary: 'Principal',
  gearPrimaryShort: 'Principal',
  gearRetire: 'Retirer',
  gearUnretire: 'Réactiver',
  gearDelete: 'Supprimer',
  gearImportBtn: 'Importer depuis Intervals.icu',
  gearImporting: 'Importation…',
  gearImportDone: (n: number) => `Importé — ${n} équipements reçus.`,
  gearImportHint: 'Récupère vélos, composants et rappels dans l’app. Aucune modification envoyée.',
  gearSyncBtn: 'Synchro bidirectionnelle',
  gearSyncing: 'Synchronisation…',
  gearSyncDone: (p: number, u: number) => `Synchronisé — ${p} reçus, ${u} envoyés.`,
  gearNoConnection: 'Connectez Intervals.icu dans les Réglages pour synchroniser le matériel.',
  gearDefaultFor: 'Matériel par défaut par sport',
  gearNoDefault: 'Aucun',
  gearAssignedTo: (name: string) => `Assigné à ${name}.`,
  gearTrackedHere: (km: string, n: number) => `${km} km suivis ici (${n})`,
  gearReminderDistance: 'Distance (km)',
  gearReminderTime: 'Temps (h)',
  gearReminderDate: 'Date',
  gearReminderDays: 'Jours',
  gearReminderActivities: 'Séances',
  gearReminderKind: 'Type',
  gearDue: 'À faire',
  gearDueSoon: 'Bientôt',
  gearSnooze: 'Reporter',
  gearConflictTitle: 'Conflit de synchronisation',
  gearConflictBody: (name: string) => `« ${name} » a été modifié ici et sur Intervals.icu. Que garder ?`,
  gearConflictKeepLocal: 'Garder la version locale',
  gearConflictKeepRemote: 'Garder Intervals.icu',
  gearEmpty: 'Aucun matériel pour l’instant. Synchronisez ou ajoutez-en un.',
  gearSetForActivity: 'Matériel utilisé',
  gearPickTitle: 'Matériel utilisé pour cette activité',
  gearPickClear: 'Retirer',
  gearDueCount: (n: number) => n === 1 ? '1 entretien à faire' : `${n} entretiens à faire`,
  gearSoonCount: (n: number) => n === 1 ? '1 entretien bientôt dû' : `${n} entretiens bientôt dus`,
};

const en: typeof fr = {
  sync:         'SYNC',
  synced:       'SYNCED',
  retry:        'RETRY',
  conn:         'CONN…',
  read:         'READ…',
  save:         'SAVE…',
  idle:         'Idle',
  connecting:   'Connecting to watch…',
  fetching:     'Reading logs…',
  writing:      'Saving…',
  done:         (n: number) => `${n} log${n !== 1 ? 's' : ''} imported`,
  error:        'Error',
  unknownError: 'Unknown error',
  viewActivities: 'View activities',
  homeNavHome: 'Home',
  homeConnVia: (via: string) => `via ${via}`,
  homeConnViaUsb: 'USB',
  homeConnViaBle: 'Bluetooth',

  // WeatherCard (v3.0 UI port, real feature port from desktop's WeatherService/
  // WeatherViewModel/WeatherCard.qml - open-meteo.com WMO weather codes)
  weatherClear: 'Clear sky',
  weatherMainlyClear: 'Mainly clear',
  weatherPartlyCloudy: 'Partly cloudy',
  weatherOvercast: 'Overcast',
  weatherFog: 'Fog',
  weatherDrizzle: 'Drizzle',
  weatherRain: 'Rain',
  weatherRainShowers: 'Rain showers',
  weatherSnow: 'Snow',
  weatherSnowShowers: 'Snow showers',
  weatherThunderstorm: 'Thunderstorm',
  weatherUnknown: 'Unknown',
  weatherOffline: "You're offline, go outside to check the weather!",
  weatherWind: (speed: number) => `Wind ${speed} km/h`,
  weatherHighLow: (high: number, low: number) => `H:${high}°  L:${low}°`,
  weatherHighLowShort: (high: number, low: number) => `${high}°/${low}°`,
  weatherToday: 'Today',

  // HomeScreen — connecting flow (v2.3.2 beta)
  homeSearchingTitle: 'Searching for your device, please wait…',
  // Real, 2026-08-10 ("under the icon put AmbitApp and a funny quote like 'Adventure
  // starts outside' or suggest me something") - a few other real options considered,
  // easy one-line swaps if a different tone is wanted: "Track it. Trust it." /
  // "Your next summit is calling." / "Built for the trail, not the couch."
  homeTagline: 'Adventure starts outside.',
  homeNoDeviceTitle:
    'No device detected, please check your cable and device or use app without it.',
  homeNoDeviceSub: 'Plug it in via USB, or search for it over Bluetooth below.',
  homeConnectingAmbit: 'Connecting to watch…',
  homeConnectRetryBtn: 'Retry',
  homeBleConnectBtn: 'Pair via Bluetooth',
  homeBleConnectWatchBtn: (name: string) => `Connect ${name} via Bluetooth`,
  homeBleDisconnectedError: 'The watch disconnected. Tap "Pair via Bluetooth" to reconnect.',
  homePairWatchPill: 'Pair',
  homeBleReadyTitle: 'Ready to pair',
  homeBleReadyMsg:
    "On the watch: menu \"Pair Mobile App\" (first pairing) or \"Sync now\" (already " +
    "paired) now — the watch's Bluetooth window only stays open for a few " +
    "seconds. Ambit3, Traverse, and Kailash, experimental.",
  homeBleReadyBtn: 'Ready',
  homeConnectingBle: 'Connecting via Bluetooth…',
  homeActivitiesBtn: 'ACTIVITIES',
  homeRoutesBtn:     'ROUTES',
  homePoisBtn:       'POIS',
  homeSyncActivitiesBtn: 'SYNC ACTIVITIES',
  homeBatteryLabel: 'battery',
  homeHwLabel:      'hardware',
  homeSerialLabel:  'Serial',
  homeDeviceConnectedStatus: 'Connected',

  // Kailash travel history (2026-08-08)
  homeKailashTravelTitle:     'Travel History',
  homeKailashCitiesLabel:     'cities visited',
  homeKailashCountriesLabel:  'countries visited',
  homeKailashTravelledLabel:  'travelled',
  homeKailashFurthestLabel:   'furthest from home',
  homeKailashLogbookLabel:    'recorded sessions',
  homeKailashPlacesTitle:     (n: number) => `Places visited (${n})`,
  homeSyncTimeButton:         'Sync time',
  homeTimeSyncOk:             'Time synced',
  homeTimeSyncFailed:         (err: string) => `Failed: ${err}`,
  homeManualLink:             'View manual (EN)',
  sortBy: 'Sort:',
  colSortAsc: 'Sort ascending',
  colSortDesc: 'Sort descending',
  colShow: 'Show',
  colRemove: 'Remove column',
  addColumn: 'Add column',
  sortUploaded: 'Last uploaded',
  sortName: 'Name',
  sortDistance: 'Distance',
  sortAscent: 'Ascent',
  viewMap: 'Map',
  viewList: 'List',
  testingSection: 'Testing mode',
  testingDesc: "Pretend a device is connected, so you can look around the app without one. Changes are made to a sample device and forgotten when you close the app - nothing is written to a real one.",
  testingOnShowing: (name)=>`On - ${name}`,
  testingChange: 'Change device',
  demoPickTitle: 'Choose a device to simulate',
  listViewsSection: 'List views',
  routesViewLabel: 'Routes view',
  poisViewLabel: 'POIs view',
  activitiesViewLabel: 'Activities view',
  listViewSettingDesc: 'Map shows each item with its track. List is lighter and shows more at once.',
  homeThisYearTitle: 'This year',
  homeOpenTotals: 'Open Totals ›',
  homeStatDistance: 'Distance',
  homeStatTime: 'Time',
  homeStatActivities: 'Activities',
  homeLastActivityTitle: 'Last Activity',
  homeOpenActivities: 'Open Activities ›',
  homeUntitledActivity: 'Untitled activity',
  homeKailashTrackTitle:      'GPS activity',
  homeKailashTrackPoints:     'GPS points',
  homeKailashTrackExport:     'Export track (GPX)',
  homeKailashExportDone:      'Track exported (%d points) to Downloads',
  homeKailashExportEmpty:     'No GPS track to export',

  all:          'All',
  loadError:    'Load error',
  deleteTitle:  'Delete',
  deleteMsg:    (date: string) =>
    `Delete activity from ${date}?\n\nIt won't be re-imported on next sync.`,
  cancel:       'Cancel',
  delete:       'Delete',
  noActivities: 'Nothing to sync.',
  connectHint:  'Connect the watch and start a sync',
  noFilter:     'No activities for this filter',
  deleteHint:   'Long press on an activity to delete it',
  unknownDate:  'Unknown date',

  loading:        'Loading track…',
  noGps:          'No GPS points in this GPX file',
  readError:      'Cannot read GPX file\n',
  connect:        'Log in',
  close:          'Close',
  noApiKey:       'API key missing',
  noApiKeyMsg:    'Configure your Runalyze API key in Settings.',
  settings:       'Settings',
  runalyzeOk:     (id: string | number) =>
    `Activity imported! (ID: ${id})\n\nTo send to Suunto: runalyze.com → activity → Share → Suunto`,
  runalyzeError:  'Runalyze Error',
  savedOk:        'Saved',
  savedMsg:       (name: string) => `File saved to Downloads:\n${name}`,
  saveError:      'Cannot save file\n',
  shareError:     'Cannot share file\n',
  offlineMapTitle: 'Offline map',
  offlineMapDone: (total: number) => `${total} tile(s) downloaded - this area is now available offline.`,
  offlineMapPartial: (ok: number, total: number) => `${ok}/${total} tiles downloaded - some failed (check your connection and try again).`,
  shareGpx:       'Share GPX',
  saveDownloads:  'Save to Downloads',
  shareFit:       'Share FIT',
  saveFitDownloads: 'Save FIT to Downloads',
  uploadRunalyze: 'Upload to Runalyze',
  uploadStrava:   'Upload to Strava',
  distance:       'Distance',
  duration:       'Duration',
  avgSpeed:       'Speed',
  pace:           'Pace',
  departure:      'Start',
  arrival:        'Finish',
  replayTime:     'Replay (time)',
  replayDist:     'Replay (distance)',


  emptyKey:       'Empty key',
  emptyKeyMsg:    'Enter your Runalyze API key.',
  keySaved:       'Runalyze API key saved.',
  keyDeleted:     'Runalyze API key deleted.',
  deleted:        'Deleted',
  runalyzeSection: 'Runalyze',
  runalyzeDesc:   'Runalyze is a free, open-source training analysis platform. Your activities will be imported into your Runalyze account.',
  runalyzeApiHint: 'Generate your API key at ',
  runalyzeApiLink: 'runalyze.com → Account → API access',
  apiKey:          'API key',
  apiKeyPlaceholder: 'Paste your API key here',
  saveBtn:         'Save',
  deleteBtn:       'Delete',
  keyStored:       'Key saved',

  intervalsSection:    'Intervals.icu',
  intervalsDesc:       'Connect your Intervals.icu account to import your activities there. Authenticated with a personal API key (no OAuth).',
  intervalsApiHint:    'Generate your API key at ',
  intervalsApiLink:    'intervals.icu → Settings → Developer Settings',
  athleteId:           'Athlete ID',
  athleteIdPlaceholder: 'e.g. i123456',
  emptyCreds:          'Missing fields',
  emptyCredsMsg:       'Enter your Intervals.icu athlete ID and API key.',
  credsSaved:          'Intervals.icu credentials saved.',
  credsDeleted:        'Intervals.icu credentials deleted.',
  credsStored:         'Credentials saved',

  uploadIntervals:     'Upload to Intervals.icu',
  intervalsError:      'Intervals.icu Error',
  intervalsSuccess:    'Activity imported to Intervals.icu!',
  viewOnIntervals:     'View on Intervals.icu',
  noCreds:             'Missing credentials',
  noCredsMsg:          'Configure your Intervals.icu athlete ID and API key in Settings.',


  logListTitle:  'Activities',
  mapFallback:   'Track',
  settingsTitle: 'Settings',
  oauthMissingCode: 'OAuth code missing in callback',

  // SettingsScreen — Appearance
  appearanceSection:  'Appearance',
  appearanceDesc:     'Choose light or dark, or follow your system setting.',
  themeLight:         'Light',
  themeDark:          'Dark',
  themeSystem:        'System',

  connectionsSection: 'Connections',
  closeBtn: 'Close',

  stravaSection:         'Strava',
  stravaSettingsDesc:    'Connect your Strava account to export your activities. The connection uses OAuth2.',
  stravaConnectedStatus: 'Strava account connected',
  stravaDisconnectBtn:   'Disconnect from Strava',
  stravaDisconnected:    'Disconnected from Strava.',
  stravaConnected:       'Connected! You can now export activities to Strava.',
  stravaError:           'Strava Error',
  stravaNotConnected:    'Connect Strava first in Settings.',
  viewOnStrava:          'View on Strava',
  stravaSuccess:         'Activity uploaded to Strava!',

  // Dropbox / Google Drive / OneDrive cloud-backup destinations (added 2026-08-12). Shared
  // strings across all three providers — see the fr block above for the rationale.
  cloudConnectedStatus:    'Connected',
  cloudDisconnectBtn:      'Disconnect',
  cloudDisconnected:       'Disconnected.',
  cloudConnected:          'Connected!',
  cloudError:              'Connection error',
  cloudAppKeyPlaceholder:    'App key',
  cloudAppSecretPlaceholder: 'App secret',
  cloudClientIdPlaceholder:     'Client ID',
  cloudClientSecretPlaceholder: 'Client Secret',
  cloudDropboxDesc: 'Register your own free app at dropbox.com/developers/apps (App folder ' +
    'access). Add both Redirect URIs: http://localhost (for the desktop app) and ' +
    'opensportsync://oauth/dropbox (for this app) - the same app serves both. Then paste ' +
    'its App key and App secret below. Only your own "AmbitApp Backups" folder in Dropbox ' +
    'is ever touched.',
  cloudGoogleDriveDesc: 'Register your own free app at console.cloud.google.com (enable the ' +
    'Google Drive API, create an OAuth Client ID of type "Desktop app"), then paste its ' +
    'Client ID and Client Secret below. Only files this app itself creates are ever visible ' +
    'to it (drive.file scope).',
  cloudOneDriveDesc: 'Register your own free app at entra.microsoft.com (platform "Mobile ' +
    'and desktop applications"). Add both Redirect URIs: http://localhost (for the desktop ' +
    'app) and opensportsync://oauth/onedrive (for this app) - the same app serves both. ' +
    'Then paste its Application (client) ID below — no secret needed here, this uses PKCE. ' +
    'Only this app\'s own OneDrive app folder is ever touched.',

  cloudBackupSection:  'Cloud backup',
  cloudBackupDesc:     'Optional. Upload a backup above to your own connected cloud storage, ' +
    'or download one back down — it then appears in the local list above. Connect a provider ' +
    'first in Settings → Connections.',
  cloudNone:           'None',
  cloudNoneConnected:  'Nothing connected yet — set one up in Settings → Connections first.',
  cloudEmpty:          'No cloud backups found yet — use "Upload" on a backup above.',
  cloudUploadBtn:      'Upload',
  cloudDownloadBtn:    'Download',
  cloudRefreshBtn:     'Refresh',
  cloudUploaded:       'Uploaded to the cloud.',
  cloudDownloaded:     'Downloaded — it now appears in the local list above.',

  gpsUpdate:          'GPS',
  gpsDone:            'GPS OK',
  gpsDownloading:      'DL…',
  gpsIdle:            'GPS data',
  gpsDownloadingMsg:  'Downloading GPS data…',
  gpsDoneMsg:         'GPS data up to date',

  sendRoute:          'ROUTE',
  routeDone:          'SENT',
  routePicking:       'FILE…',
  routeParsing:       'READING…',
  routeIdle:          'Send a route',
  routePickingMsg:    'Choose a GPX file…',
  routeParsingMsg:    'Parsing the GPX…',
  routeWritingMsg:    'Writing to the watch…',
  routeDoneMsg: (name: string, points: number, waypoints: number) =>
    `"${name}" sent (${points} points, ${waypoints} waypoint${waypoints !== 1 ? 's' : ''})`,
  sendRouteConfirmTitle: 'Send a route',
  sendRouteConfirmMsg:
    "This will replace any route already on the watch. Your POIs are preserved " +
    "automatically. Note: this route isn't permanent — the next SuuntoLink sync, or " +
    "even just the Suunto phone app coming into Bluetooth range, will replace it. " +
    "This is for loading a route right before you go, not for permanent storage.",
  sendRouteConfirmBtn: 'Send',
  routeScreenTitle:   'Route',
  routeSendSection:   'Send a route',
  routePlannerTitle:  'Plan a route',
  routeWatchNote: 'Routes that are sent to the watch will be erased by SuuntoLink and not pushed to the Suunto app.',
  poiWatchNote: 'POIs that are sent to the watch will be pushed to the Suunto app by SuuntoLink.',
  poiInfoTitle: 'Points of interest',
  routePlannerIntro:  'You can export your routes from Suunto App to gpx and import them here, or you can use any of these or others:',
  routePlannerOnline: '(online)',
  routePlannerOfflineWinMac: '(offline, Win/Mac)',
  routePlannerOfflineAll:    '(offline, Linux/Win/Mac)',
  routeExportSection: 'Export from watch',
  routeExportDesc:    'Reads the routes and waypoints on the watch and saves them to a GPX file (Downloads).',
  routeExportBtn:     'Export from watch',
  routeExportReading: 'Reading navigation data…',
  navExportedTitle:   'Export complete',
  navExportedMsg: (routes: number, waypoints: number) =>
    `${routes} route${routes !== 1 ? 's' : ''}, ${waypoints} waypoint${waypoints !== 1 ? 's' : ''} saved to Downloads.`,

  // RouteScreen — Bluetooth (experimental, v0.3.0, Ambit3/Traverse only)
  bleExperimentalBadge: 'EXPERIMENTAL',
  bleExperimentalDisclaimer:
    'Bluetooth transfer is experimental and has not yet been verified on real hardware — use the cable ' +
    'if you can. Ambit3 and Traverse watches only. You\'ll need to trigger "Sync now" on the watch at ' +
    'the right moment (see the next prompt).',
  sendRouteBleBtn:    'Send (Bluetooth)',
  routeExportBleBtn:  'Export (Bluetooth)',
  // v3.0 UI port - real "On the watch" list (RoutesPage.qml parity, 2026-08-09)
  routeOnWatchSection: 'On the watch',
  routeOnWatchReading: 'Reading routes off the watch...',
  routeOnWatchEmpty: 'Nothing to sync.',
  routeOnWatchError: (msg: string) => `Couldn't read routes: ${msg}`,
  routeStats: (dist: string, points: number, ascent: number, descent: number) =>
    `${dist} · ${points} points · ascent ${ascent} m · descent ${descent} m`,
  routeItemExportBtn: 'Export',
  routeUploadBtn: 'Upload to watch',
  routeRehearseBtn: 'Rehearse (no write)',
  routeDiscardBtn: 'Discard',
  bleScanning:        'Scanning for the watch…',
  bleConnecting:      'Connecting over Bluetooth…',
  bleSyncNowTitle:    'Ready to sync',
  bleSyncNowMsg:
    'On the watch, trigger "Sync now" NOW, then tap "Ready" below immediately — the watch\'s ' +
    'Bluetooth window only stays open for a few seconds.',
  bleSyncNowReady:    'Ready',

  // HomeScreen / PoiScreen
  poiButton:        'POI',
  poiScreenTitle:   'POI',
  poiImportSection: 'Import from GPX',
  poiImportDesc:    'Choose a GPX file: every <wpt> in it is sent as a POI, preserving any already on the watch.',
  poiExportSection: 'Export to GPX',
  poiExportDesc:    'Reads every POI on the watch and saves them to a GPX file (Downloads).',
  poiExportBtn:     'Export from watch',
  poiExportReading: 'Reading POIs…',
  poiExportedTitle: 'POIs exported',
  poiExportedMsg: (n: number) => `${n} POI${n !== 1 ? 's' : ''} saved to Downloads.`,

  // SettingsScreen — Add POI
  poiSection:    'Add POI',
  poiDesc:       'Sends a point of interest to the watch over cable, preserving any already there.',
  poiName:       'Name',
  poiNamePlaceholder: 'e.g. Summit',
  poiLat:        'Latitude',
  poiType:       'Type',
  poiLon:        'Longitude',
  poiAddBtn:     'Send to watch',
  poiWriting:    'Sending POI…',
  poiInvalid:    'Invalid input',
  poiNameRequired: 'Enter a name for the POI.',
  poiCoordsInvalid: 'Invalid latitude/longitude (latitude -90 to 90, longitude -180 to 180).',
  poiAddedTitle: 'POI added',
  poiAddedMsg: (name: string) => `"${name}" was sent to the watch.`,
  poiImportBtn:     'Import from GPX',
  poiImportPicking: 'Choose a GPX file…',
  poiImportParsing: 'Parsing the GPX…',
  poiImportWriting: (done: number, total: number) => `Sending POIs… (${done}/${total})`,
  poiImportedTitle: 'POIs imported',
  poiImportedMsg: (n: number) => `${n} POI${n !== 1 ? 's' : ''} sent to the watch.`,
  // v3.0 UI port - real "On the watch" list + real import preview (PoisPage.qml parity)
  poiOnWatchSection: 'On the watch',
  poiOnWatchReading: 'Reading POIs off the watch...',
  poiOnWatchEmpty: 'Nothing to sync.',
  poiOnWatchError: (msg: string) => `Couldn't read POIs: ${msg}`,
  poiItemExportBtn: 'Export',
  poiItemAddBtn: 'Add',
  poiCoords: (lat: number, lon: number) => `${lat.toFixed(5)}, ${lon.toFixed(5)}`,

  // SettingsScreen — Ambit3 Settings (2026-08-08)
  ambitSettingsSection: 'Watch settings',
  kailashSettingsSection: 'Kailash Settings',
  ambitSettingsTitle: (name: string) => `${name} Settings`,
  ambitSettingsDesc:
    'Real watch settings (language, formats, brightness, etc.), read and written over ' +
    'USB cable - confirmed working against real hardware 2026-08-08.',
  ambitSettingsReadBtn: 'Read Settings',
  ambitSettingsRefreshBtn: 'Refresh',
  ambitSettingsReading: 'Reading settings...',
  ambitSettingsReadOnly: 'Editable over the USB cable — verified on real hardware. A few fields with no known write offset stay read-only.',
  orbitalDataTitle: 'Orbital data',
  ephemerisGpsOnly: 'Ephemeris GPS only',
  ephemerisGpsOnlyInfo: 'This watch can also use GLONASS satellites, and has its own storage for their orbital data. Suunto\'s software never sends it to this model, so those satellites start cold every time. Sommet sends both GPS and GLONASS orbital data, which can speed up getting a fix. Tick this to send GPS only.',

  // SportModesScreen — Ambit3 CustomModes (2026-08-08), Ambit3-only, not available on Kailash
  sportModesButton:      'SPORT MODES',
  sportModesScreenTitle: 'Sport Modes',
  sportModesDesc:
    'Edit the watch\'s sport modes: names, autolap, HR limits, sensors and the display screens.',
  sportModesReadBtn: 'Read Sport Modes',
  sportModesInstallApp: 'Install a Suunto App…',
  sportModesReading: 'Reading sport modes...',
  sportModesCheckConnection: 'Check your watch connection.',
  sportModesRefreshBtn: 'Refresh',
  // Create / delete / multisport (2026-08-14, port of tools/sport_mode_manage.py)
  sportModesManageTitle: 'Sport modes on the watch',
  sportModesCounts: (used: number, max: number, multi: number, maxMulti: number) =>
    `${used}/${max} modes · ${multi}/${maxMulti} multisport`,
  sportModesCreateBtn: '＋ Sport mode',
  sportModesCreateMultiBtn: '＋ Multisport',
  sportModesMultiBadge: 'multisport',
  sportModesUsedByBadge: (names: string) => `used by ${names}`,
  sportModesDeleteBtn: 'Delete',
  sportModesDeleteTitle: 'Delete sport mode',
  sportModesDeleteMsg: (name: string) => `Delete “${name}” from the watch?`,
  sportModesCreateTitle: 'New sport mode',
  sportModesCreateMultiTitle: 'New multisport mode',
  sportModesNamePlaceholder: 'Name',
  sportModesActivityLabel: 'Activity',
  sportModesLegsLabel: 'Legs (in order)',
  sportModesLegsHint: 'Tap modes in the order the watch should step through them. Repeats allowed.',
  sportModesLegsChosen: (legs: string) => `Order: ${legs}`,
  sportModesNoLegsYet: 'No legs chosen yet',
  sportModesCreateConfirm: 'Create',
  sportModesWriteWarning:
    'Changes are written straight to the watch. Take a backup first if you want to be safe.',
  sportModesWritingStep: (step: number, total: number) => total > 1 ? `Writing ${step}/${total}...` : 'Writing...',
  sportModesVerifying: 'Verifying...',
  sportModesManageReadError: 'Could not read the sport-mode structure.',
  sportModesRenameBtn: 'Rename',
  sportModesExpandBtn: 'Details',
  sportModesCollapseBtn: 'Hide',
  // v3.0 UI port - List<->Detail rework (real desktop parity, 2026-08-09)
  sportModesBackBtn: 'Sport Modes',
  sportModesNameLabel: 'Name',
  sportModesDisplaysCount: (n: number, max: number) => `Displays (${n}/${max})`,
  sportModesBuiltInShort: '•',
  sportModesBuiltInMsg: (template: string) => template ? `Built-in system screen (${template}) - not editable.` : 'Built-in system screen - not editable.',
  sportModesScreenLabel: (n: number) => `Screen ${n}`,
  sportModesAutolapLabel: 'Autolap (m)',
  sportModesSetBtn: 'Set',
  sportModesHrLimitsLabel: 'HR limits',
  sportModesHrLowLabel: 'Low',
  sportModesHrHighLabel: 'High',
  sportModesPodsLabel: 'External sensors',
  sportModesDisplaysLabel: 'Displays',
  sportModesChangeBtn: 'Change',
  sportModesPickerTitle: 'Choose field type',
  sportModesCloseBtn: 'Close',
  sportModesWriteSentNotConfirmed: 'Write sent but not confirmed by re-read.',

  // TrackPreview — no GPS data (2026-08-10, "for data without gps data, please do a nice
  // mappyish image saying no data")
  trackPreviewNoData: 'No GPS data',

  // SettingsScreen — Maps (2026-08-09, "no button to change provider, nor in the settings
  // like the desktop version") - same real card as desktop/qml/pages/SettingsPage.qml, plus
  // IGN as a genuine Android-only extra option (see MapProviderService.ts)
  mapsSection: 'Maps',
  mapsProviderDesc: (name: string) => `Provider: tiles from ${name}`,
  mapProviderIgnLabel: 'IGN (France)',
  mapProviderOsmLabel: 'OpenStreetMap (standard)',
  mapProviderCyclosmLabel: 'CyclOSM (cycling-focused)',
  offlineMapCacheSize: (size: string) => `Offline map cache: ${size}`,
  offlineMapClearCache: 'Clear cache',

  // SettingsScreen — About / legal
  aboutSection: 'About',
  aboutVersion: (v: string) => `Sommet v${v}`,
  aboutDisclaimer:
    "Sommet is an independent, open-source personal project. It is not affiliated " +
    "with, endorsed by, or sponsored by Suunto Oy or Garmin Ltd. Suunto, Ambit, Traverse, " +
    "Kailash, Garmin, eTrex, and any other product name or trademark referenced in this app " +
    "are registered or unregistered trademarks of their respective owners (Suunto Oy and " +
    "Garmin Ltd.), used here only to describe compatibility with those devices. All " +
    "rights reserved to their respective owners. Provided as is, without warranty of any " +
    "kind. Licensed under the GNU GPLv3; built with React Native (MIT). Map data © " +
    "OpenStreetMap contributors (ODbL); weather by Open-Meteo (CC BY 4.0); icons by Google " +
    "Material Symbols (Apache 2.0).",
  aboutCreditsSection: 'Credits',
  aboutCreditsIntro:
    "This project stands on real prior work by other people, without which the protocol " +
    "reverse-engineering behind it would have taken far longer:",

  // Garmin — shared (v2.3 beta, updated v2.3.2)
  garminButton:      'GARMIN',
  garminWaitingForMount: (secondsLeft: number) =>
    `Waiting for the device to finish mounting… (can take up to 40s, ${secondsLeft}s left)`,
  garminUnknownModel: 'Unknown model',
  garminFirmwareLabel: 'firmware',
  garminSdCardPresent: 'SD card detected',
  garminSdCardAbsent:  'No SD card detected',
  garminInternalMemoryWarning:
    '⚠️ For safety, this feature NEVER writes to the device\'s internal memory. An SD ' +
    'card must be present; the file will only be sent there (SDCARD\\Garmin\\GPX).',
  garminNoSdCardMsg: 'Feature unavailable: no SD card detected in the device.',

  // Home — inline activity sync for Garmin (v2.3.2 beta, no sub-screen — see
  // GarminActivityService.ts)
  homeGarminSyncReading: 'Reading activities…',
  homeGarminSyncWriting: (current: number, total: number) => `Importing… (${current}/${total})`,
  homeGarminSyncDone: (count: number) =>
    count === 0
      ? 'No new activities to import.'
      : `${count} activit${count !== 1 ? 'ies' : 'y'} imported.`,

  // GarminRouteScreen (v2.3.2 beta)
  garminRouteScreenTitle: 'Garmin routes',
  garminRouteSendSection: 'Send a route',
  garminRouteSendDesc: "Sends a GPX file (route) to the device's SD card.",
  garminRouteSendBtn:  'Choose a GPX file',
  garminRouteSendDone: 'File sent to the SD card.',
  garminRouteExportSection: 'Export routes',
  garminRouteExportDesc:
    'Reads GPX files saved on the device (internal memory and SD card) and saves them to Downloads.',
  garminRouteExportBtn: 'Export',
  garminRouteExportDone: (count: number) =>
    count === 0 ? 'No route files found.' : `${count} file${count !== 1 ? 's' : ''} exported.`,
  garminShareBtn: 'Share…',
  // Real, 2026-08-10 ("Garmin: POIs and routes, please follow the same logic as suunto,
  // showing them on the maps") - same "On the device" card as routeOnWatchSection, with a
  // real per-item map preview (TrackPreview), not only a bulk export.
  garminRouteOnDeviceSection: 'On the device',
  garminRouteOnDeviceReading: 'Reading routes off the device...',
  garminRouteOnDeviceEmpty: 'No routes on the device.',

  // GarminPoiScreen (v2.3.2 beta)
  garminPoiScreenTitle: 'Garmin POI',
  garminPoiSendSection: 'Send a POI',
  garminPoiSendDesc: "Sends a GPX file (waypoints) to the device's SD card.",
  garminPoiSendBtn:  'Choose a GPX file',
  garminPoiSendDone: 'File sent to the SD card.',
  garminPoiRetrieveSection: 'Retrieve POIs',
  garminPoiRetrieveDesc:
    'Reads Waypoints files (created by Garmin BaseCamp) off the device (internal memory ' +
    'and SD card) and saves them to Downloads.',
  garminPoiRetrieveBtn: 'Retrieve',
  garminPoiRetrieveDone: (count: number) =>
    count === 0 ? 'No POI files found.' : `${count} file${count !== 1 ? 's' : ''} retrieved.`,
  garminPoiOnDeviceSection: 'On the device',
  garminPoiOnDeviceReading: 'Reading POIs off the device...',
  garminPoiOnDeviceEmpty: 'No POIs on the device.',

  // BackupScreen (v2.3.2 beta) — Ambit firmware backup
  backupButton:      'Backup',
  backupScreenTitle: 'Backup',
  // v3.0 UI port - real "Backup & Restore" card (BackupPage.qml parity, 2026-08-09).
  // Restore itself is deferred - see NavBackupService.ts's own header comment on why.
  backupNavSection: 'Navigation backup',
  backupNavDesc: 'Covers Routes and POIs together (the watch\'s whole navigation database).',
  backupNavCreateBtn: 'Create backup now',
  backupNavWorking: 'Working…',
  backupFolderSection: 'Backup database to folder',
  backupFolderInfo: 'You can save it to your favourite cloud folder, so it can be synced if you wish.',
  backupFolderBtn: 'Save a backup to a folder…',
  backupNavDone: 'Backup created.',
  backupExistingSection: 'Existing backups',
  backupExistingEmpty: 'None yet.',
  backupShareBtn: 'Share',
  backupRestoreUnavailable:
    'Restore needs a native capability not built on Android yet - these backups are ' +
    'read-only for now (use the desktop app to restore).',
  backupWarning:
    "⚠️ Backup only: this file CANNOT be flashed back onto the watch from this app. " +
    "To update the firmware, use the official Suunto app or SuuntoLink.",
  backupCheckSection: 'Check available firmware',
  backupCheckDesc: "Asks Suunto's servers for the latest firmware version available for your watch.",
  backupCheckBtn:  'Check',
  backupReading:   'Reading watch info…',
  backupChecking:  'Checking with Suunto…',
  backupLatestVersion: (v: string) => `Latest available version: ${v}`,
  backupUploadDate: (d: string) => `Released ${d}`,
  backupNoUpdateInfo: 'No firmware info available for this model/hardware version.',
  backupDownloadSection: 'Download a backup',
  backupDownloadDesc:
    "Downloads the firmware file as-is, without modifying or decoding it. You'll be asked " +
    "where to save it (Downloads by default).",
  backupDownloadBtn: 'Download',
  backupDownloading: (pct: number) => `Downloading… ${pct}%`,
  backupDownloadDone: 'Backup saved.',

  // Totals / Calendar (2026-08-13, port of TotalsPage.qml / CalendarPage.qml)
  totalsScreenTitle: 'Totals',
  totalsTitle: 'Totals',
  totalsEmptyNoData: 'Nothing to add up yet - read your activities off the watch first and this fills in.',
  totalsEmptyYear: 'No activities in this year.',
  totalsHoursTitle: 'Hours outside',
  totalsHoursSubtitle: (n: number) => `Across ${n} activit${n !== 1 ? 'ies' : 'y'} with a GPS track`,
  totalsDistanceTitle: 'Distance',
  totalsDistanceDesc: 'Activities are grouped by sport automatically. Tap one to feature it.',
  totalsActivitiesCount: (n: number) => `${n} activit${n !== 1 ? 'ies' : 'y'}`,
  totalsEnergyTitle: 'Energy spent',
  totalsEnergyUnavailable: "Calories aren't read off the watch on Android yet (they aren't in the GPX). Use the desktop app for this total.",
  totalsMore: 'More to come!',
  calendarScreenTitle: 'Calendar',
  calendarTitle: 'Calendar',
  calendarActivities: (n: number) => `${n} activit${n !== 1 ? 'ies' : 'y'}`,
  calendarToday: 'Today',
  calendarLegendRest: 'Rest day',
  calendarLegendActivity: 'Activity',

  // Experimental features (2026-08-14) - App Zone, Intervals, Smart Sensor
  experimentalSection: 'Experimental features',
  experimentalToggleLabel: 'Enable experimental features',
  experimentalToggleDesc:
    'Reveals extra tools still being tried out on Android: App Zone (install Suunto Apps), ' +
    'Intervals workouts, and the Smart Sensor HR belt. Some write to the watch, so take a ' +
    'backup first.',
  markSyncedLabel: 'Mark synced workouts as synced for Suunto app and SuuntoLink',
  markSyncedDesc:
    'Once a workout has been read here, tell the watch it is already synced. This avoids ' +
    'duplicated workouts in the Suunto app and SuuntoLink — but it also means the workout can ' +
    'no longer be retrieved again from the watch if the Suunto app fails to keep it. Leave off ' +
    'unless you understand this tradeoff.',
  experimentalWarningBanner:
    '⚠️ Experimental — not hardware-tested. Connect the watch by cable and take a backup ' +
    'before writing anything.',
  experimentalAppZone: 'App Zone (Suunto Apps)',
  experimentalAppZoneDesc: 'Install Suunto Apps onto the watch (Ambit3).',
  experimentalIntervals: 'Intervals workout',
  experimentalIntervalsDesc: 'Build an interval workout (Suunto App or a planned move).',
  experimentalSmartSensor: 'Smart Sensor',
  experimentalSmartSensorDesc: 'Suunto Smart Sensor heart-rate belt over Bluetooth.',
  experimentalWorkoutCalendar: 'Workout Calendar',
  experimentalWorkoutCalendarDesc: 'Dated workouts in the WORKOUT menu, named "dd/mm_name".',
  smartSensorScreenTitle: 'Smart Sensor',
  appZoneScreenTitle: 'App Zone',
  intervalsScreenTitle: 'Intervals',
  experimentalComingNote:
    'Under construction. The screen and data pipeline are being built; installing to the ' +
    'watch needs the next native build. Nothing here writes to your watch yet.',
  smartSensorScanBtn: 'Scan for belt',
  smartSensorScanning: 'Scanning...',
  smartSensorNotFound: 'No Smart Sensor found. Put the belt on (needs skin contact to advertise) and try again.',
  smartSensorForgetBtn: 'Forget',
  smartSensorNativeMissing: "The Smart Sensor Bluetooth module isn't in this build yet - rebuild the app to enable it.",
  smartSensorBattery: 'Battery',
  smartSensorHeartRate: 'Heart rate',
  smartSensorNoReading: 'no reading',
  // App Zone import (2026-08-14)
  appZoneNativeMissing: "The App Zone module isn't in this build yet - rebuild the app.",
  appZoneNoCatalogTitle: 'No catalog imported',
  appZoneInstructions:
    'Sommet ships no Suunto apps (proprietary content). Import your own catalog from ' +
    'SuuntoLink: on the computer where SuuntoLink is installed, find the "suunto-apps" folder ' +
    'and its "index.json" file (~29 MB), copy it to this device, then tap Import.',
  appZoneImportBtn: 'Import from SuuntoLink',
  appZoneReimportBtn: 'Re-import',
  appZoneImporting: 'Importing... (this can take a moment)',
  appZoneImported: (n: number) => `${n} apps imported`,
  appZoneImportFailed: 'Import failed',
  appZoneSearchPlaceholder: 'Search apps...',
  appZoneAppsCount: (n: number) => `${n} apps`,
  appZoneInstallNote: 'Tap an app to install it onto a sport-mode screen.',
  appZoneInstallTitle: (name: string) => `Install "${name}"`,
  appZonePickMode: 'Choose a sport mode',
  appZonePickScreen: 'Choose a screen',
  appZonePickField: 'Choose a field to share with the app',
  appZoneReadingModes: 'Reading sport modes...',
  appZoneNoRealScreens: 'This mode has no editable screens.',
  appZoneInstallBtn: 'Install',
  appZoneInstalling: 'Installing...',
  appZoneInstalledMsg: 'App installed.',
  appZoneScreenLabel: (n: number) => `Screen ${n}`,
  // Intervals (2026-08-14)
  intervalsWarning:
    '⚠️ Experimental. Compiling uses a third-party community compiler (not Suunto). Installing ' +
    'onto the watch is not hardware-confirmed. Take a backup first.',
  intervalsAppSection: 'Interval workout (Suunto App)',
  intervalsAppDesc: 'Build → compile (online) → install onto a screen.',
  intervalsWarmup: 'Warm-up (min)',
  intervalsReps: 'Repeats',
  intervalsWork: 'Work (min)',
  intervalsRest: 'Rest (min)',
  intervalsCooldown: 'Cool-down (min)',
  intervalsCompileInstall: 'Compile & install',
  intervalsCompiling: 'Compiling...',
  intervalsGenerateBtn: 'Copy source & open compiler',
  intervalsImportBtn: 'Import compiled app',
  intervalsSourceCopiedMsg: 'The compiler site opened and the source is shown below. Select it, copy it into the site, compile, download the result, then tap "Import compiled app".',
  intervalsSourceLabel: 'Workout source - select all, copy, and paste into the compiler site.',
  intervalsCompilerNote: 'Compiling runs on a third-party community site (not Suunto, not us). Sommet only generates the source and opens the site; you compile there and import the result.',
  intervalsPlannedSection: 'Planned move (native)',
  intervalsPlannedDesc: 'Format unconfirmed - may not appear on the watch.',
  intervalsName: 'Name',
  intervalsDuration: 'Duration (min)',
  intervalsIntensity: 'Intensity (1-5)',
  intervalsWriteBtn: 'Write to watch',
  intervalsWritten: 'Written to the watch.',
  intervalsWriting: 'Writing...',
  // Workout Calendar (2026-08-21) - dated native guided workouts named "dd/mm_name" in the
  // WORKOUT menu, sidestepping the unreachable native TrainingProgram region. Manual compile,
  // same as Intervals above - see intervalsCompilerNote.
  workoutCalendarWarning:
    '⚠️ Experimental, Ambit3 watches only. Each workout compiles on the same third-party ' +
    'community site as Intervals above (not Suunto). On sync, anything dated before today ' +
    'is erased from the watch and replaced with whatever comes next.',
  workoutCalendarDateLabel: 'Date',
  workoutCalendarModeLabel: 'Sport mode',
  workoutCalendarImportTitle: 'Import from intervals.icu',
  workoutCalendarImportDesc:
    'Pull your planned workouts for a date range and drop them into the plan (HR targets ' +
    'reconstructed from your zones). Pick a sport mode below first; then compile each pending one.',
  workoutCalendarImportFrom: 'From',
  workoutCalendarImportTo: 'To',
  workoutCalendarImportBtn: 'Import planned workouts',
  workoutCalendarImportNone: 'No planned workouts in that range',
  workoutCalendarImportedPrefix: 'Imported',
  workoutCalendarImportCompileHint: 'Compile each pending one below.',
  workoutCalendarCompilingRow: 'Compiling…',
  workoutCalendarAddBtn: 'Add to Calendar',
  workoutCalendarAddedMsg: 'Added to the calendar.',
  workoutCalendarPlanTitle: 'Calendar',
  workoutCalendarPlanEmpty: 'Nothing planned yet.',
  workoutCalendarPending: 'awaiting compile',
  workoutCalendarPreviewBtn: 'Preview sync',
  workoutCalendarSyncBtn: 'Sync to Watch',
  workoutCalendarSyncing: 'Syncing...',
  workoutCalendarSyncedMsg: 'Watch synced.',
  workoutCalendarEmptyPlanMsg: 'The calendar is empty.',
  workoutCalendarPickModeFirst: 'Pick a sport mode.',
  // ── Gear tracker (v3) ──
  gearButton: 'Gear',
  gearScreenTitle: 'Gear',
  gearBikes: 'Bikes',
  gearShoes: 'Shoes',
  gearParts: 'Components',
  gearReminders: 'Service reminders',
  gearAddBike: 'Add bike',
  gearAddShoes: 'Add shoes',
  gearAddPart: 'Add component',
  gearAddReminder: 'Add reminder',
  gearName: 'Name',
  gearRetired: 'Retired',
  gearPrimary: 'Primary',
  gearPrimaryShort: 'Primary',
  gearRetire: 'Retire',
  gearUnretire: 'Un-retire',
  gearDelete: 'Delete',
  gearImportBtn: 'Import from Intervals.icu',
  gearImporting: 'Importing…',
  gearImportDone: (n: number) => `Imported — ${n} gear items pulled in.`,
  gearImportHint: 'Pulls bikes, components and reminders into the app. Nothing is sent back.',
  gearSyncBtn: 'Two-way sync',
  gearSyncing: 'Syncing…',
  gearSyncDone: (p: number, u: number) => `Synced — ${p} pulled, ${u} pushed.`,
  gearNoConnection: 'Connect Intervals.icu in Settings to sync gear.',
  gearDefaultFor: 'Default gear per sport',
  gearNoDefault: 'None',
  gearAssignedTo: (name: string) => `Assigned to ${name}.`,
  gearTrackedHere: (km: string, n: number) => `${km} km tracked here (${n})`,
  gearReminderDistance: 'Distance (km)',
  gearReminderTime: 'Time (h)',
  gearReminderDate: 'Date',
  gearReminderDays: 'Days',
  gearReminderActivities: 'Activities',
  gearReminderKind: 'Type',
  gearDue: 'Due',
  gearDueSoon: 'Soon',
  gearSnooze: 'Snooze',
  gearConflictTitle: 'Sync conflict',
  gearConflictBody: (name: string) => `“${name}” changed here and on Intervals.icu. Which do you want to keep?`,
  gearConflictKeepLocal: 'Keep local version',
  gearConflictKeepRemote: 'Keep Intervals.icu',
  gearEmpty: 'No gear yet. Sync or add one.',
  gearSetForActivity: 'Gear used',
  gearPickTitle: 'Gear used for this activity',
  gearPickClear: 'Clear',
  gearDueCount: (n: number) => n === 1 ? '1 service due' : `${n} services due`,
  gearSoonCount: (n: number) => n === 1 ? '1 service due soon' : `${n} services due soon`,
};

// Forced to English regardless of device locale (was `isFrench ? fr : en`,
// which picked French because the device's system locale resolves to fr-*).
// Revert to `isFrench ? fr : en` to restore automatic locale detection.
export const t = en;
