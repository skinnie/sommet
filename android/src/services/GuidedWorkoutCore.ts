import { DecodedRegion } from './SportModeCodec';
import type { Workout, WorkoutStep } from './WorkoutSource';
import { addLights, LightChoices } from './IamruleCode';

// Pure (native-free) core of the native GUIDED WORKOUT install - the [Next]-3s WORKOUT menu
// mechanism, distinct from AppInstallCore's older field-shortcut mechanism. Exact port of
// tools/guided_workout.py's build_regions/guidance_display (hardware-confirmed 2026-08-19 on
// desktop): the Apps-region entry gets header byte 0 = 1 (guidance, not 0/generic -
// BinaryAreaAppsConverter::typeMapping in Movescount Android's libkomposti), and the mode gets
// a guidance DISPLAY (Template 295) with NO rule in its RULES list - a rule there is an ACTIVE
// engine slot, so the workout would run on every recording and beep even unselected; no rule
// keeps it DORMANT until picked from the menu. Same reasoning as AppInstallCore, split out
// because this is a genuinely different wiring mechanism, not a variant of the old one.

export const GUIDANCE_TEMPLATE = 295;   // 0x127, PID_RUNNER_GPS_TEMPLATE_GUIDANCE
export const GUIDANCE_TYPE = 15;        // 0x0f
export const GUIDANCE_ENTRY_TYPE = 1;   // Apps-entry byte0: 1=guidance, 0=generic

const clone = <T>(x: T): T => JSON.parse(JSON.stringify(x));

/** Case-insensitive sport-mode lookup by name, same as guided_workout.py's find_mode_index. */
export function findModeIndex(decoded: DecodedRegion, name: string): number {
  const idx = decoded.exercise_modes.findIndex(
    (m) => (m.Settings.Name || '').toLowerCase() === name.toLowerCase());
  if (idx < 0) {
    const names = decoded.exercise_modes.map((m) => m.Settings.Name);
    throw new Error(`no sport mode named ${JSON.stringify(name)}; this watch has: ${names.join(', ')}`);
  }
  return idx;
}

/** Adds the guidance display to `modeIndex` if it isn't already there (idempotent - a mode
 * that already has one is reused, matching guided_workout.py's append-mode behavior; several
 * guided workouts can share one mode's WORKOUT menu). Returns the new decoded region (does
 * NOT mutate the input) and whether a display was actually added. */
export function ensureGuidanceDisplay(decoded: DecodedRegion, modeIndex: number):
    { decoded: DecodedRegion; added: boolean } {
  const out = clone(decoded);
  const mode = out.exercise_modes[modeIndex];
  if (!mode) throw new Error(`mode ${modeIndex} does not exist`);
  const hasIt = mode.Displays.some((d) => d.Template === GUIDANCE_TEMPLATE);
  if (!hasIt) mode.Displays.push({ Template: GUIDANCE_TEMPLATE, Type: GUIDANCE_TYPE, Fields: [] });
  return { decoded: out, added: !hasIt };
}

// ── Guided-workout JSON helpers (ports of tools/guided_workout.py) ─────────────────────────────

/** The workout's real steps in the order the watch runs them, repeat blocks expanded - the same
 *  numbering the compiled program's step counter uses (guided_workout.step_sequence). */
export function stepSequence(workout: Workout): WorkoutStep[] {
  const seq: WorkoutStep[] = [];
  let block: WorkoutStep[] | null = null;
  let count = 1;
  for (const s of workout.steps || []) {
    const t = s.type?.typeName;
    if (t === 'repeatStart') { block = []; count = Number(s.type.value ?? 1); }
    else if (t === 'repeatEnd') { for (let r = 0; r < count; r++) seq.push(...(block ?? [])); block = null; }
    else (block ?? seq).push(s);
  }
  return seq;
}

/** Per-step backlight choices from each step's `notify`: `light` = flash when the step starts
 *  (default on), `limitLight` = flash with the out-of-limits alarm (default off, needs a target).
 *  The finish screen flashes if any step start does. The watch's beeps aren't configurable. */
export function lightChoices(workout: Workout): LightChoices {
  const seq = stepSequence(workout);
  const onStepStart = seq.flatMap((s, i) => (s.notify?.light !== false ? [i] : []));
  const onLimits = seq.flatMap((s, i) => (s.notify?.limitLight && (s.target?.targetName ?? 'none') !== 'none' ? [i] : []));
  return { onStepStart, onLimits, onFinish: onStepStart.length > 0, expectedTotal: seq.length };
}

/** Copy with every "hr" target range divided by 60 (bpm -> beats per second): the compiled
 *  program compares SUUNTO_HR * (1/60) against it (read from its bytecode, 2026-09-26). Apply to
 *  the JSON handed to the compiler, never to the stored plan (guided_workout.with_hr_target_as_bps). */
export function withHrTargetAsBps(workout: Workout): Workout {
  const wk = clone(workout);
  for (const s of wk.steps || []) {
    if (s.target?.targetName === 'hr' && s.target.valueRange) {
      s.target.valueRange = { min: s.target.valueRange.min / 60, max: s.target.valueRange.max / 60 };
    }
  }
  return wk;
}

/** The compiled guidance binary with the backlight flashes the workout asks for; unchanged (0) if it
 *  isn't the template IamruleCode knows - never an unvalidated patch. */
export function withLights(binary: Uint8Array, workout: Workout): [Uint8Array, number] {
  try {
    return addLights(binary, lightChoices(workout));
  } catch {
    return [binary, 0];
  }
}
