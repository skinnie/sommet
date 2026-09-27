// Mirrors /home/skinnie/ambit-app/CREDITS.md — kept in sync by hand since that
// file lives in a separate research-project repo, not this one. This project's
// own protocol reverse-engineering leans directly on the real, working prior
// art listed here; see CREDITS.md itself for the full prose version.

export interface CreditEntry {
  name: string;
  url?: string;
  description: string;
}

export const CREDITS: CreditEntry[] = [
  {
    name: 'openambit',
    url: 'https://github.com/openambitproject/openambit',
    description:
      "And its contributors, especially Emil Ljungdahl (libambit's original author) — the " +
      'real, working reference implementation this project checks its own findings against ' +
      "throughout. libambit's design is the foundation this app's own native code is written " +
      'to sit alongside.',
  },
  {
    name: 'opensportsync',
    url: 'https://github.com/guiguoz/opensportsync',
    description: "And its author — the React Native base this app was forked from.",
  },
  {
    name: 'marguslt',
    url: 'https://github.com/marguslt',
    description:
      'Several independent, real contributions: the firmware-download-link recipe, the ' +
      'workout/App-Zone gists, and openmoves.',
  },
  {
    name: 'sebchastang',
    url: 'https://forum.suunto.com/user/sebchastang',
    description:
      'Author of a complete, published set of real interval-training Suunto Apps, ' +
      "maintained through Movescount's actual 2022 shutdown.",
  },
  {
    name: 'iwanders/gps_track_pod',
    url: 'https://github.com/iwanders/gps_track_pod',
    description:
      'Suunto GPS Track Pod support (MIT), the basis for this app’s GPS Track Pod ' +
      'integration.',
  },
  {
    name: 'evelbulgroz/suunto-t6-sync',
    url: 'https://github.com/evelbulgroz/suunto-t6-sync',
    description:
      'Suunto T6/T6c/T6d read support (MIT), the basis for this app’s experimental T6 ' +
      'heart-rate export and GPS-Track-Pod merge.',
  },
  {
    name: 'App Zone workout examples',
    url: 'https://github.com/claha/suunto',
    description:
      'Real published App-Zone interval scripts the structured-workout findings were checked ' +
      'against — claha/suunto, follesoe/suunto-ambit-intervals, hefler/SuuntoApps, ' +
      'AdamHodgson/Suunto-Interval-Training and Httqm/Suunto.',
  },
  {
    name: 'ruvido/goambit & AlexLBraits/ambit2gpx',
    url: 'https://github.com/ruvido/goambit',
    description:
      'Independent implementations of the same cloud-free USB paths (route upload / activity ' +
      'read), confirming those paths are real.',
  },
  {
    name: 'mihaildemidoff/suunto-sml-model',
    url: 'https://github.com/mihaildemidoff/suunto-sml-model',
    description:
      "A JAXB model of Suunto's SML activity format, a reference for the exercise-log work.",
  },
  {
    name: 'Pavel Samokha and the Suunto forum community',
    url: 'https://forum.suunto.com/topic/7592',
    description:
      'The documented, confirmed-real mechanism for adding a compiled Suunto App to ' +
      "SuuntoLink's own catalog, and much shared knowledge besides.",
  },
  {
    name: 'wanarun.net',
    url: 'https://wanarun.net',
    description:
      'Independent confirmation of the structured-workout JSON schema this project targets.',
  },
  {
    name: 'OpenAthlete',
    url: 'https://github.com/openathleteorg/openathlete',
    description:
      "The on-device HR load (Banister TRIMP) the Coach falls back to when intervals.icu isn't " +
      'connected, and the weather line on each activity. Ideas and formula only, no code copied ' +
      '(AGPL-3.0).',
  },
  {
    name: 'Train Libre',
    url: 'https://github.com/rfivesix/train-libre',
    description: 'The sleep score is a port of its Sleep Health Score engine (GPL-3.0).',
  },
  {
    name: 'Open Food Facts',
    url: 'https://world.openfoodfacts.org/',
    description: "Ember's meal search by name and barcode. Database © Open Food Facts contributors (ODbL).",
  },
  {
    name: 'USDA FoodData Central',
    url: 'https://fdc.nal.usda.gov/',
    description: "Generic foods for Ember's meal search (public domain).",
  },
  {
    name: 'react-native-camera-kit',
    url: 'https://github.com/teslamotors/react-native-camera-kit',
    description: "Camera barcode scan in Ember (MIT). Android reads codes with Google ML Kit's on-device model.",
  },
  {
    name: 'OpenStreetMap',
    url: 'https://www.openstreetmap.org/copyright',
    description:
      'Map data © OpenStreetMap contributors, under the Open Database License (ODbL). ' +
      'Tiles from CyclOSM / OpenStreetMap France, standard OSM, and IGN Géoplateforme.',
  },
  {
    name: 'Open-Meteo',
    url: 'https://open-meteo.com/',
    description: 'Weather data (CC BY 4.0).',
  },
  {
    name: 'Google Material Symbols',
    url: 'https://github.com/google/material-design-icons',
    description: 'The icon set (Apache License 2.0).',
  },
];
