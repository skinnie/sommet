// ─── ActivityMetrics ────────────────────────────────────────────────────────
// Catalogue of metrics an activity can show, for the configurable Activities columns on
// Android (port of desktop/qml/ActivityMetrics.qml; André, 2026-08-16). One place the column
// dropdowns and the rows both read, so a header and its value always agree on label/format.
//
// Units are metric here - the Android app shows metric everywhere today (no watch-unit read is
// wired to display yet); matching that keeps it consistent. (Desktop reads the watch's unit
// setting; a units port is a separate parity item.)

// The unified numeric shape ActivityMetrics reads. Built from an ActivityRecord (DB core) plus
// the richer GpxMetadata fields (re-parsed from the move's GPX). All SI / watch-native; 0 = not
// recorded.
export interface MetricValues {
  distanceM: number;
  durationS: number;
  ascentM: number;
  descentM: number;
  energyKcal: number;
  avgHr: number;
  maxHr: number;
  avgCadence: number;
  maxCadence: number;
  avgSpeedMh: number;
  maxSpeedMh: number;
  recoveryS: number;
  peakTe: number;
  poolLengths: number;
  maxAltM: number;
  paceSecPerKm: number;
  shots?: number;      // Traverse Alpha: shots counted while hunting
}

export interface MetricDef {
  key: string;
  label: string;
}

// Ordered catalogue - same order as desktop.
export const ALL_METRICS: MetricDef[] = [
  { key: 'distance',    label: 'Distance' },
  { key: 'duration',    label: 'Duration' },
  { key: 'pace',        label: 'Pace' },
  { key: 'avgSpeed',    label: 'Avg speed' },
  { key: 'maxSpeed',    label: 'Max speed' },
  { key: 'ascent',      label: 'Ascent' },
  { key: 'descent',     label: 'Descent' },
  { key: 'calories',    label: 'Calories' },
  { key: 'avgHr',       label: 'Avg HR' },
  { key: 'maxHr',       label: 'Max HR' },
  { key: 'avgCadence',  label: 'Avg cadence' },
  { key: 'maxCadence',  label: 'Max cadence' },
  { key: 'recovery',    label: 'Recovery' },
  { key: 'peakTe',      label: 'Peak TE' },
  { key: 'maxAltitude', label: 'Max alt.' },
  { key: 'poolLengths', label: 'Lengths' },
  { key: 'shots',       label: 'Shots' },
];

export function metricLabel(key: string): string {
  return ALL_METRICS.find(m => m.key === key)?.label ?? key;
}

/** Raw numeric value (for sorting + the "is it recorded" test). 0 = not recorded. */
export function metricRaw(m: MetricValues, key: string): number {
  switch (key) {
    case 'distance':    return m.distanceM || 0;
    case 'duration':    return m.durationS || 0;
    case 'pace':        return m.paceSecPerKm || 0;
    case 'avgSpeed':    return m.avgSpeedMh || 0;
    case 'maxSpeed':    return m.maxSpeedMh || 0;
    case 'ascent':      return m.ascentM || 0;
    case 'descent':     return m.descentM || 0;
    case 'calories':    return m.energyKcal || 0;
    case 'avgHr':       return m.avgHr || 0;
    case 'maxHr':       return m.maxHr || 0;
    case 'avgCadence':  return m.avgCadence || 0;
    case 'maxCadence':  return m.maxCadence || 0;
    case 'recovery':    return m.recoveryS || 0;
    case 'peakTe':      return m.peakTe || 0;
    case 'maxAltitude': return m.maxAltM || 0;
    case 'poolLengths': return m.poolLengths || 0;
    case 'shots':       return m.shots || 0;
    default:            return 0;
  }
}

function fmtDuration(seconds: number): string {
  if (!seconds) return '--';
  const h = Math.floor(seconds / 3600);
  const mm = Math.floor((seconds % 3600) / 60);
  return h > 0 ? `${h}h ${mm}m` : `${mm}m`;
}

function fmtPace(secPerKm: number): string {
  if (secPerKm <= 0) return '';
  const mm = Math.floor(secPerKm / 60);
  const ss = Math.round(secPerKm % 60);
  const m = ss === 60 ? mm + 1 : mm;
  const s = ss === 60 ? 0 : ss;
  return `${m}:${String(s).padStart(2, '0')} /km`;
}

/** Display string in metric units, or "" when not recorded. */
export function metricValue(m: MetricValues, key: string): string {
  if (metricRaw(m, key) <= 0 && key !== 'duration') return '';
  switch (key) {
    case 'distance':    return `${(m.distanceM / 1000).toFixed(m.distanceM >= 100000 ? 0 : 1)} km`;
    case 'duration':    return fmtDuration(m.durationS);
    case 'pace':        return fmtPace(m.paceSecPerKm);
    case 'avgSpeed':    return `${(m.avgSpeedMh / 1000).toFixed(1)} km/h`;
    case 'maxSpeed':    return `${(m.maxSpeedMh / 1000).toFixed(1)} km/h`;
    case 'ascent':      return `${Math.round(m.ascentM)} m`;
    case 'descent':     return `${Math.round(m.descentM)} m`;
    case 'calories':    return `${Math.round(m.energyKcal)} kcal`;
    case 'avgHr':       return `${m.avgHr} bpm`;
    case 'maxHr':       return `${m.maxHr} bpm`;
    case 'avgCadence':  return `${m.avgCadence} rpm`;
    case 'maxCadence':  return `${m.maxCadence} rpm`;
    case 'recovery':    return fmtDuration(m.recoveryS);
    case 'peakTe':      return (m.peakTe / 10).toFixed(1);
    case 'maxAltitude': return `${Math.round(m.maxAltM)} m`;
    case 'poolLengths': return String(m.poolLengths);
    case 'shots':       return String(m.shots);
    default:            return '';
  }
}

/** Metrics used by other columns (for the no-duplicate picker). */
export function metricsAvailableFor(columns: string[], idx: number): MetricDef[] {
  const usedElsewhere = columns.filter((_, i) => i !== idx);
  return ALL_METRICS.filter(m => usedElsewhere.indexOf(m.key) === -1);
}
