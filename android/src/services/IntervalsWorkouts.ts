import { getIntervalsIcuCredentials } from './ApiIntervalsIcu';
import { Workout, WorkoutStep } from './WorkoutSource';

// Pull PLANNED workouts from intervals.icu and turn each into this project's Workout schema, so
// the Workout Calendar can be auto-populated from the athlete's training plan instead of hand-built
// one workout at a time (André, 2026-08-27). Exact port of the desktop's tools/intervals_workout.py
// (fetch_intervals_workouts + convert + the zone helpers) and server.py's /api/intervals/workouts.
//
// intervals.icu's events API gives each workout's `workout_doc` with the SAME nested step structure
// as the file export, but it carries `hr.value` (a zone INDEX) instead of the resolved bpm band. We
// reconstruct that band from the athlete's own zone boundaries (sportSettings.hr_zones), so the rest
// of the pipeline (WorkoutSource.generateSource) runs unchanged. Optional watch-Karvonen resolution
// (watchMaxHr/watchRestHr) re-expresses the bands in the watch's own zone model, exactly like the
// desktop; when omitted, intervals.icu's own resolved bpm is used as-is.
//
// Android still compiles each workout via the community compiler site (no shipped compiler), same as
// the rest of the Workout Calendar - this only removes the manual "type every interval in by hand".

const API_BASE = 'https://intervals.icu/api/v1';
const RUN_TYPES = ['Run', 'VirtualRun', 'TrailRun'];

const TEXT_TYPE_HINTS: [string[], string][] = [
  [['warm', 'wu'], 'warmup'],
  [['cool', 'cd'], 'cooldown'],
  [['walk', 'rest', 'recover', 'easy', 'rec'], 'recovery'],
  [['jog', 'run', 'fast', 'hard', 'work', 'interval', 'tempo', 'effort'], 'interval'],
];
const RESOLVED_TARGETS: [string, string][] = [['_hr', 'hr'], ['_power', 'power'], ['_pace', 'pace']];

// eventId / externalId let the plan recognise events it already has (its own carry external_id
// "sommet:<uid>" - IntervalsEvents.ts), so re-importing never duplicates.
export interface PlannedEntry { date: string; mode: string; name: string; workout: Workout; sport?: string; eventId?: number; externalId?: string | null }
export interface SkippedEntry { date: string; name: string; reason: string }
export interface ImportResult { entries: PlannedEntry[]; skipped: SkippedEntry[] }

interface IcuStep {
  steps?: IcuStep[]; reps?: number;
  warmup?: boolean; cooldown?: boolean; text?: string;
  duration?: number; distance?: number;
  hr?: { units?: string; value?: number };
  _hr?: { start: number; end: number; startIsWatchRest?: boolean };
  _power?: { start: number; end: number };
  _pace?: { start: number; end: number };
}

// ── zone reconstruction (port of resolve_zone_band / resolve_zones_into_hr / athlete_hr_zones) ──

/** intervals.icu zone INDEX (1-based) -> [low, high, lowIsWatchRest] bpm from the athlete's per-zone
 *  upper bounds. Zone 1 (Recovery) has no floor on intervals.icu - deliberately: a walk has no "too
 *  slow" (André, 2026-09-26) - so its low is the watch's own resting HR when known, flagged so the
 *  Karvonen rescale leaves it alone; else the old ~68%-of-LTHR estimate. Port of resolve_zone_band. */
export function resolveZoneBand(
  zoneIndex: number | undefined, hrZones: number[], lthr?: number | null, maxHr?: number | null,
  watchRestHr?: number | null,
): [number, number, boolean] | null {
  const n = hrZones.length;
  if (!n) return null;
  const idx = Math.max(1, Math.min(Math.trunc(zoneIndex ?? 1), n));
  const high = Math.trunc(hrZones[idx - 1]);
  let low: number;
  let lowIsWatchRest = false;
  if (idx >= 2) low = Math.trunc(hrZones[idx - 2]) + 1;
  else if (watchRestHr) { low = watchRestHr; lowIsWatchRest = true; }
  else if (lthr) low = Math.round(0.68 * lthr);
  else if (maxHr) low = Math.round(0.6 * maxHr);
  else low = Math.max(0, high - 30);
  return [low, high, lowIsWatchRest];
}

export function resolveZonesIntoHr(steps: IcuStep[], hrZones: number[], lthr?: number | null, maxHr?: number | null,
  watchRestHr?: number | null): void {
  for (const step of steps) {
    if (step.steps) { resolveZonesIntoHr(step.steps, hrZones, lthr, maxHr, watchRestHr); continue; }
    const hr = step.hr;
    if (step._hr || !hr || typeof hr !== 'object' || hr.units !== 'hr_zone') continue;
    const band = resolveZoneBand(hr.value, hrZones, lthr, maxHr, watchRestHr);
    if (band) step._hr = { start: band[0], end: band[1], startIsWatchRest: band[2] };
  }
}

interface SportSettings { types?: string[]; hr_zones?: number[]; lthr?: number; max_hr?: number }

function athleteHrZones(athlete: { sportSettings?: SportSettings[] }, activityTypes = RUN_TYPES):
  { hrZones: number[] | null; lthr: number | null; maxHr: number | null } {
  const all = athlete.sportSettings ?? [];
  let best = all.find(ss => (ss.types ?? []).some(t => activityTypes.includes(t)) && ss.hr_zones?.length);
  if (!best) best = all.find(ss => ss.hr_zones?.length);
  return { hrZones: best?.hr_zones ?? null, lthr: best?.lthr ?? null, maxHr: best?.max_hr ?? null };
}

// ── step conversion (port of guess_type / convert_duration / convert_target / convert_step[s]) ──

function guessType(step: IcuStep): string {
  if (step.warmup) return 'warmup';
  if (step.cooldown) return 'cooldown';
  const text = (step.text ?? '').trim().toLowerCase();
  for (const [needles, typeName] of TEXT_TYPE_HINTS) if (needles.some(nn => text.includes(nn))) return typeName;
  return 'interval';
}

function convertDuration(step: IcuStep): { durationName: string; value: number } {
  const seconds = step.duration;
  if (seconds) return { durationName: 'time', value: Math.round(seconds) };
  const metres = step.distance;
  if (metres) return { durationName: 'distance', value: Math.round(metres) };
  throw new Error(`step has neither a duration nor a distance`);
}

/** Re-express one bpm target from intervals.icu's model into the watch's Karvonen reserve. */
export function karvonenRescale(bpm: number, icuMax: number, watchMax: number, watchRest: number): number {
  const denom = icuMax - watchRest;
  if (denom <= 0) return Math.round(bpm);
  const frac = (bpm - watchRest) / denom;
  return Math.round(watchRest + frac * (watchMax - watchRest));
}

function convertTarget(step: IcuStep, hrResolve?: (bpm: number) => number): WorkoutStep['target'] {
  for (const [key, targetName] of RESOLVED_TARGETS) {
    const band = (step as any)[key];
    if (!band || typeof band !== 'object') continue;
    let { start: lo, end: hi } = band as { start: number; end: number };
    if (lo == null || hi == null) continue;
    lo = Number(lo); hi = Number(hi);
    if (targetName === 'hr' && hrResolve) {
      // startIsWatchRest: lo is already the watch's own resting HR, not an intervals.icu-model
      // bpm - rescaling it again would be wrong (tools/intervals_workout.convert_target).
      hi = hrResolve(hi);
      if (!band.startIsWatchRest) lo = hrResolve(lo);
    }
    if (targetName === 'pace') {
      // intervals.icu resolves pace to m/s; our workout JSON (like Suunto's SUUNTO_PACE) is decimal
      // min/km - convert and keep the decimals (6.5 = 6:30); the swap below fixes the order.
      if (lo <= 0 || hi <= 0) continue;
      lo = Math.round((1000 / 60 / lo) * 100) / 100; hi = Math.round((1000 / 60 / hi) * 100) / 100;
    } else { lo = Math.round(lo); hi = Math.round(hi); }
    if (lo > hi) [lo, hi] = [hi, lo];
    return { targetName, valueRange: { min: lo, max: hi } };
  }
  // Already in absolute units (written by Sommet itself, or typed as W/bpm/rpm): take as is -
  // tools/intervals_workout.convert_target.
  for (const [key, targetName, units] of [['power', 'power', 'w'], ['hr', 'hr', 'bpm'], ['cadence', 'cadence', 'rpm']] as const) {
    const band = (step as any)[key];
    if (!band || typeof band !== 'object' || band.units !== units || band.start == null) continue;
    let lo = Number(band.start), hi = band.end != null ? Number(band.end) : lo;
    if (targetName === 'hr' && hrResolve) { lo = hrResolve(lo); hi = hrResolve(hi); }
    lo = Math.round(lo); hi = Math.round(hi);
    if (lo > hi) [lo, hi] = [hi, lo];
    return { targetName, valueRange: { min: lo, max: hi } };
  }
  return { targetName: 'none' };
}

function convertStep(step: IcuStep, hrResolve?: (bpm: number) => number): WorkoutStep {
  const out: WorkoutStep = {
    type: { typeName: guessType(step) },
    duration: convertDuration(step),
    target: convertTarget(step, hrResolve),
  };
  const text = (step.text ?? '').trim();
  if (text) (out as any).text = text;
  return out;
}

function convertSteps(icuSteps: IcuStep[], hrResolve?: (bpm: number) => number): WorkoutStep[] {
  const out: WorkoutStep[] = [];
  for (const step of icuSteps) {
    const children = step.steps;
    if (children) {
      const count = Math.trunc(step.reps ?? 1);
      if (children.some(c => c.steps)) throw new Error('nested repeat inside a repeat is not supported');
      if (count > 1) {
        out.push({ type: { typeName: 'repeatStart', value: count } });
        for (const c of children) out.push(convertStep(c, hrResolve));
        out.push({ type: { typeName: 'repeatEnd' } });
      } else {
        for (const c of children) out.push(convertStep(c, hrResolve));
      }
    } else {
      out.push(convertStep(step, hrResolve));
    }
  }
  return out;
}

/** intervals.icu workout_doc -> this project's Workout (port of convert()). */
export function convertWorkout(
  doc: { steps?: IcuStep[]; sportSettings?: { max_hr?: number } }, name: string,
  watchMaxHr?: number | null, watchRestHr?: number | null,
): Workout {
  const icuSteps = doc.steps;
  if (!icuSteps || !icuSteps.length) throw new Error('no steps in this workout');
  let hrResolve: ((bpm: number) => number) | undefined;
  if (watchMaxHr != null && watchRestHr != null) {
    const icuMax = doc.sportSettings?.max_hr;
    if (!icuMax) throw new Error('cannot resolve HR against the watch zones: no source max HR');
    hrResolve = (bpm: number) => karvonenRescale(bpm, icuMax, watchMaxHr, watchRestHr);
  }
  return { name, steps: convertSteps(icuSteps, hrResolve) };
}

// ── the fetch (port of fetch_intervals_workouts + /api/intervals/workouts) ──

async function icuGet(path: string, athleteId: string, apiKey: string, query = ''): Promise<any> {
  const url = `${API_BASE}/athlete/${encodeURIComponent(athleteId)}${path}${query ? `?${query}` : ''}`;
  const resp = await fetch(url, {
    headers: { Authorization: 'Basic ' + btoa(`API_KEY:${apiKey}`), 'User-Agent': 'Sommet/1.0' },
  });
  if (!resp.ok) throw new Error(`intervals.icu ${path || '/athlete'}: HTTP ${resp.status}`);
  return resp.json();
}

/**
 * Pull planned workouts from intervals.icu in [start, end] (ISO dates) and convert each into a
 * calendar plan entry { date, mode, name, workout }. Only WORKOUT-category events of a Run activity
 * type with real steps are returned; everything else lands in `skipped` with a reason. HR zones are
 * reconstructed from the athlete's own zone table; pass watchMaxHr/watchRestHr to Karvonen-resolve
 * the bands into the watch's model.
 */
export async function fetchIntervalsWorkouts(
  start: string, end: string, mode: string,
  watchMaxHr?: number | null, watchRestHr?: number | null,
): Promise<ImportResult> {
  const creds = await getIntervalsIcuCredentials();
  if (!creds) throw new Error('intervals.icu is not connected');
  const { athleteId, apiKey } = creds;

  let athlete = await icuGet('', athleteId, apiKey);
  if (Array.isArray(athlete)) athlete = athlete[0];
  // Every sport (André, 2026-09-25): the calendar plans for the watch AND the bike computers.
  // HR zones come per event from that sport's own settings (tools/intervals_workout.py).

  const events = await icuGet('/events', athleteId, apiKey,
    `oldest=${start}&newest=${end}&category=WORKOUT`);

  const entries: PlannedEntry[] = [];
  const skipped: SkippedEntry[] = [];
  for (const ev of (Array.isArray(events) ? events : [])) {
    const date = (ev.start_date_local ?? '').slice(0, 10);
    const name = ev.name || 'Workout';
    const doc = ev.workout_doc ?? {};
    const steps: IcuStep[] | undefined = doc.steps;
    if (!steps || !steps.length) { skipped.push({ date, name, reason: 'no steps' }); continue; }
    const { hrZones, lthr, maxHr } = athleteHrZones(athlete ?? {}, ev.type ? [ev.type] : RUN_TYPES);
    if (hrZones) resolveZonesIntoHr(steps, hrZones, lthr, maxHr, watchRestHr);
    try {
      const workout = convertWorkout({ steps, sportSettings: { max_hr: maxHr ?? undefined } },
        name, watchMaxHr, watchRestHr);
      entries.push({ date, mode, name, workout, sport: ev.type, eventId: ev.id, externalId: ev.external_id ?? null });
    } catch (e: any) {
      skipped.push({ date, name, reason: e?.message ?? String(e) });
    }
  }
  return { entries, skipped };
}
