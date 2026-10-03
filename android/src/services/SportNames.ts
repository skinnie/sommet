// One sport-name table for every source (André, 2026-10-03: the list showed "EMountainBikeRide"
// and "HighIntensityIntervalTraining" - "it may be a mix of activities from all types of devices
// ... Any way to unify this? Suunto ambit should remain the main product"). The names are
// Suunto's own (ActivityColors.ts); the table is shared/sport_names.json, the same one the
// desktop compiles in, so a move reads the same in both apps whatever recorded it.
import { ACTIVITY_TYPES } from './ActivityColors';

const TABLE: {
  intervals: Record<string, string>;
  garmin: Record<string, string>;
  aliases: Record<string, string>;
  foot: string[];
} = require('../config/sport_names.json');

const FOOT = new Set(TABLE.foot.map(s => s.toLowerCase()));

/** A readable form of a type no table knows: "EMountainBikeRide" -> "E mountain bike ride". */
export function humanizeSportType(type: string): string {
  const s = String(type || '')
    .replace(/_/g, ' ')
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .replace(/([A-Z])([A-Z][a-z])/g, '$1 $2')
    .replace(/\s+/g, ' ')
    .trim()
    .toLowerCase();
  return s ? s.charAt(0).toUpperCase() + s.slice(1) : 'Unspecified sport';
}

/** intervals.icu activity `type` -> Suunto's sport name. */
export function sportNameForIntervalsType(type: string): string {
  return TABLE.intervals[type] || humanizeSportType(type);
}

/** Garmin activity typeKey -> Suunto's sport name. */
export function sportNameForGarminType(typeKey: string): string {
  return TABLE.garmin[typeKey] || humanizeSportType(typeKey);
}

const SUUNTO = new Map<string, string>(
  Object.values(ACTIVITY_TYPES).map(a => [a.name.toLowerCase(), a.name] as [string, string]));

/**
 * Whatever an import path hands over (a Suunto name, an older spelling this app stored, a raw
 * intervals.icu or Garmin type) -> Suunto's sport name. A real Suunto name always wins, so a
 * watch "Rowing" move is never taken for intervals.icu's "Rowing" (= indoor rowing).
 */
export function canonicalSportName(name: string): string {
  const n = String(name || '').trim();
  if (!n) return '';
  const suunto = SUUNTO.get(n.toLowerCase());
  if (suunto) return suunto;
  return TABLE.aliases[n] || TABLE.intervals[n] || TABLE.garmin[n]
    || (/\s/.test(n) ? n : humanizeSportType(n));
}

/** Junk/test entry: under a minute AND under 100 m. Same rule as the desktop's import (André,
 *  2026-08-24: "it was tests for our app"; 2026-10-03: they still showed on Android). */
export function isJunkActivity(durationS: number, distanceM: number): boolean {
  return (Number(durationS) || 0) < 60 && (Number(distanceM) || 0) < 100;
}

/** On foot, pace (min/km) means something; on a bike or in a boat it does not - speed does. */
export function isFootSport(name: string): boolean {
  return FOOT.has(String(name || '').trim().toLowerCase());
}
