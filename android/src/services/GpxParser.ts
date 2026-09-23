import { XMLParser } from 'fast-xml-parser';
import { ACTIVITY_TYPES } from './ActivityColors';

// ─── Types ────────────────────────────────────────────────────────────────────

export interface TrackPoint {
  latitude: number;
  longitude: number;
  elevation: number;   // mètres
  timestamp: number;   // Unix ms
  cumDist: number;     // mètres cumulés depuis le départ
  cumTime: number;     // secondes cumulées depuis le départ
}

export interface ElevationStats {
  dPlus: number;       // dénivelé positif cumulé (m)
  dMinus: number;      // dénivelé négatif cumulé (m)
  altMin: number;
  altMax: number;
  totalDistance: number; // mètres
}

export interface GpxMetadata {
  date: string;        // ISO 8601 du premier point
  durationS: number;
  distanceM: number;
  dPlus: number;
  activityType: string; // ex: "Orienteering", "Running", "Cycling"…
  // Richer summary metrics from the watch log header, carried through as GPX extensions by
  // exercise_log.py (shared with desktop). 0 = not recorded. For the configurable Activities
  // columns (ActivityMetrics.ts). Units: hr = bpm, cadence = rpm, speed = m/h, descent = m,
  // recovery = s, peakTe = value*10, poolLengths = count, maxAlt = m, energy = kcal.
  energyKcal: number;
  avgHr: number;
  maxHr: number;
  avgCadence: number;
  maxCadence: number;
  avgSpeedMh: number;
  maxSpeedMh: number;
  descentM: number;
  recoveryS: number;
  peakTe: number;
  poolLengths: number;
  maxAltM: number;
  paceSecPerKm: number;   // derived
}

/** Read a numeric <tag>value</tag> summary extension from the raw GPX. Regex (not the XML
 * parser) for the same reason activityType uses it - more reliable for the flat extensions
 * block. Returns 0 when absent. */
function extTag(gpxXml: string, tag: string): number {
  const m = gpxXml.match(new RegExp(`<${tag}>(-?\\d+(?:\\.\\d+)?)</${tag}>`));
  return m ? parseFloat(m[1]) : 0;
}

// The log header's <sport_type> is the watch's Suunto activity id (the sport mode's id from
// assets/activity_types.json - 17 = Indoor cycling, 93 = Treadmill, ...). Name it from that same
// authoritative table (ACTIVITY_TYPES), like the desktop does. The previous hand-rolled map here
// was a different, older scheme that was off by one on the ski sports and had no indoor cycling,
// so those moves were mislabelled or blank (found 2026-09-23 alongside the FIT-sport fix).
function sportTypeName(code: number): string {
  return ACTIVITY_TYPES[code]?.name ?? '';
}

// ─── Parser ───────────────────────────────────────────────────────────────────

const parser = new XMLParser({ ignoreAttributes: false, attributeNamePrefix: '@_' });

/** Extrait les points GPS d'une string GPX. */
export function parseTrackPoints(gpxXml: string): TrackPoint[] {
  const obj = parser.parse(gpxXml);
  const trkseg = obj?.gpx?.trk?.trkseg;
  if (!trkseg) return [];

  const rawPoints = Array.isArray(trkseg.trkpt) ? trkseg.trkpt : [trkseg.trkpt];

  const points: TrackPoint[] = [];
  let firstTimestamp = 0;
  let lastValidPoint: TrackPoint | null = null;

  for (const p of rawPoints) {
    if (p?.['@_lat'] === undefined || p?.['@_lon'] === undefined) continue;

    const latitude = parseFloat(p['@_lat']);
    const longitude = parseFloat(p['@_lon']);
    const elevation = parseFloat(p.ele ?? '0');
    const timestamp = p.time ? new Date(p.time).getTime() : 0;

    if (points.length === 0) {
      firstTimestamp = timestamp;
      const pt: TrackPoint = { latitude, longitude, elevation, timestamp, cumDist: 0, cumTime: 0 };
      points.push(pt);
      lastValidPoint = pt;
      continue;
    }

    if (lastValidPoint) {
      const dist = haversineM(lastValidPoint.latitude, lastValidPoint.longitude, latitude, longitude);
      const newCumDist = lastValidPoint.cumDist + dist;
      let newCumTime = lastValidPoint.cumTime;

      if (timestamp > 0 && firstTimestamp > 0) {
        newCumTime = (timestamp - firstTimestamp) / 1000;
        // Filtrage des sauts temporels en arrière ou doublons temporels
        if (newCumTime <= lastValidPoint.cumTime) {
          continue; 
        }
      }

      const pt: TrackPoint = { latitude, longitude, elevation, timestamp, cumDist: newCumDist, cumTime: newCumTime };
      points.push(pt);
      lastValidPoint = pt;
    }
  }

  return points;
}

/** Calcule les statistiques altimétriques et la distance totale. */
export function computeElevationStats(points: TrackPoint[]): ElevationStats {
  if (points.length === 0) {
    return { dPlus: 0, dMinus: 0, altMin: 0, altMax: 0, totalDistance: 0 };
  }

  let dPlus = 0;
  let dMinus = 0;
  let altMin = points[0].elevation;
  let altMax = points[0].elevation;

  for (let i = 1; i < points.length; i++) {
    const prev = points[i - 1];
    const curr = points[i];

    // Dénivelé
    const dEle = curr.elevation - prev.elevation;
    if (dEle > 0) dPlus += dEle;
    else dMinus += Math.abs(dEle);

    if (curr.elevation < altMin) altMin = curr.elevation;
    if (curr.elevation > altMax) altMax = curr.elevation;
  }

  return {
    dPlus: Math.round(dPlus),
    dMinus: Math.round(dMinus),
    altMin: Math.round(altMin),
    altMax: Math.round(altMax),
    totalDistance: Math.round(points[points.length - 1].cumDist),
  };
}

/** Extrait les métadonnées principales d'un GPX (date, durée, distance, D+).
 *  Utilise <metadata><time> comme date de référence (= header.date_time de la montre)
 *  pour que l'ID généré corresponde à celui du skip_callback C (formatLogId). */
export function extractGpxMetadata(gpxXml: string): GpxMetadata {
  const obj = parser.parse(gpxXml);
  const metaTime: string | undefined = obj?.gpx?.metadata?.time;

  const points = parseTrackPoints(gpxXml);
  const stats = points.length > 0 ? computeElevationStats(points) : null;
  const first = points[0];
  const last  = points[points.length - 1];

  return {
    // Préférer metadata/time (stable, timezone-free) plutôt que premier trkpt
    date: metaTime
      ? new Date(metaTime).toISOString()
      : (first?.timestamp ? new Date(first.timestamp).toISOString() : ''),
    durationS: (() => {
      // The watch's own move duration (a <duration> summary extension - what the native GPX
      // emits and what desktop activityservice.cpp reads) is authoritative; the trkpt time
      // span is only the fallback when there's no such extension.
      const summary = extTag(gpxXml, 'duration');
      return summary > 0
        ? Math.round(summary)
        : (last?.timestamp && first?.timestamp ? Math.round((last.timestamp - first.timestamp) / 1000) : 0);
    })(),
    distanceM: (() => {
      // The watch's own reported distance (a <distance> summary extension - exactly what the
      // native GPX emits and desktop activityservice.cpp reads) is authoritative; use it
      // whenever present. Only sum the track points when there's no such extension: fine for
      // dense 1 Hz Ambit3 tracks, but the Kailash's sparse passive TrackLog badly
      // under-reports otherwise (a 5.44 km walk logged shows as ~1 km of straight
      // point-to-point). Matches desktop, which never computed distance from the points.
      const summary = extTag(gpxXml, 'distance');
      return summary > 0 ? summary : (stats?.totalDistance ?? 0);
    })(),
    dPlus:     stats?.dPlus ?? 0,
    activityType: (() => {
      // 1. Essayer activity_name (peut être vide sur Ambit 1)
      const rawName = obj?.gpx?.trk?.name;
      const name = rawName && rawName !== true ? String(rawName).trim() : '';
      if (name && name !== 'Activité') return name;
      // 2. Regex sur la string brute — plus fiable que le parser pour les extensions
      const m = gpxXml.match(/<sport_type>(\d+)<\/sport_type>/);
      const code = m ? parseInt(m[1], 10) : -1;
      return sportTypeName(code);
    })(),
    energyKcal:  extTag(gpxXml, 'energy'),
    avgHr:       extTag(gpxXml, 'avg_hr'),
    maxHr:       extTag(gpxXml, 'max_hr'),
    avgCadence:  extTag(gpxXml, 'avg_cadence'),
    maxCadence:  extTag(gpxXml, 'max_cadence'),
    avgSpeedMh:  (() => {
      const s = extTag(gpxXml, 'avg_speed');
      const distM = stats?.totalDistance ?? 0;
      const durS = last?.timestamp && first?.timestamp ? (last.timestamp - first.timestamp) / 1000 : 0;
      // Derive from distance/duration when the watch didn't record it (older GPX / some sports).
      return s > 0 ? s : (distM > 0 && durS > 0 ? distM / (durS / 3600) : 0);
    })(),
    maxSpeedMh:  extTag(gpxXml, 'max_speed'),
    descentM:    extTag(gpxXml, 'descent'),
    recoveryS:   extTag(gpxXml, 'recovery_time'),
    peakTe:      extTag(gpxXml, 'peak_training_effect'),
    poolLengths: extTag(gpxXml, 'pool_lengths'),
    maxAltM:     extTag(gpxXml, 'max_altitude'),
    paceSecPerKm: (() => {
      const distM = stats?.totalDistance ?? 0;
      const durS = last?.timestamp && first?.timestamp ? (last.timestamp - first.timestamp) / 1000 : 0;
      const s = extTag(gpxXml, 'avg_speed');
      if (s > 0) return 3600 * 1000 / s;
      return distM > 0 && durS > 0 ? durS / (distM / 1000) : 0;
    })(),
  };
}

// ─── Haversine ────────────────────────────────────────────────────────────────

function haversineM(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const R = 6371000;
  const toRad = (d: number) => (d * Math.PI) / 180;
  const dLat = toRad(lat2 - lat1);
  const dLon = toRad(lon2 - lon1);
  const a =
    Math.sin(dLat / 2) ** 2 +
    Math.cos(toRad(lat1)) * Math.cos(toRad(lat2)) * Math.sin(dLon / 2) ** 2;
  return R * 2 * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a));
}
