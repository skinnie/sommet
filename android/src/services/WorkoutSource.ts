// EXPERIMENTAL - App-Zone source generator for a structured interval workout. Exact port of
// tools/workout.py's generate_source / expand_steps / build_compile_request. This ONLY
// generates the source text; the user compiles it themselves on a third-party community site
// in their own browser (link-out, no key, no API call - see IntervalsService.ts) and imports
// the result. This generator is deterministic and proven to match the Python one byte-for-byte
// (WorkoutSource.test.ts); the compile step and the on-watch result are outside our control.

const DURATION_VARS: Record<string, string> = {
  time: 'SUUNTO_DURATION', distance: 'SUUNTO_DISTANCE', ascent: 'SUUNTO_ASCENT',
};
const DURATION_POSTFIX: Record<string, string> = { time: 's', distance: 'm', ascent: 'm+', lap: '' };
const TARGET_VARS: Record<string, string> = {
  hr: 'SUUNTO_HR', pace: 'SUUNTO_PACE', speed: 'SUUNTO_SPEED',
  vertical_speed: 'SUUNTO_VERTICAL_SPD', power: 'SUUNTO_BIKE_POWER',
};
const TYPE_LABELS: Record<string, string> = { warmup: 'Warm', interval: 'Fast', recovery: 'Rec', cooldown: 'Cool' };

export interface WorkoutStep {
  type: { typeName: string; value?: number };
  duration?: { durationName: string; value: number };
  target?: { targetName: string; valueRange?: { min: number; max: number } };
  notify?: { beep?: boolean; light?: boolean; limitLight?: boolean };
}
export interface Workout { name?: string; steps: WorkoutStep[] }

/** Flatten repeatStart/repeatEnd blocks into a plain step list (no nested repeats). */
export function expandSteps(workout: Workout): WorkoutStep[] {
  const flat: WorkoutStep[] = [];
  const steps = workout.steps;
  let i = 0;
  while (i < steps.length) {
    const step = steps[i];
    const typeName = step.type.typeName;
    if (typeName === 'repeatStart') {
      const count = step.type.value ?? 0;
      const block: WorkoutStep[] = [];
      i += 1;
      while (steps[i] && steps[i].type.typeName !== 'repeatEnd') {
        if (steps[i].type.typeName === 'repeatStart') throw new Error("nested repeat blocks aren't supported");
        block.push(steps[i]); i += 1;
      }
      for (let r = 0; r < count; r++) flat.push(...block);
    } else if (typeName === 'repeatEnd') {
      throw new Error('repeatEnd without a matching repeatStart');
    } else {
      flat.push(step);
    }
    i += 1;
  }
  return flat;
}

function phaseCondition(step: WorkoutStep): string {
  const d = step.duration!;
  if (d.durationName === 'lap') return 'SUUNTO_LAP_NUMBER > CURRENT_LAP_NUMBER';
  return `(${DURATION_VARS[d.durationName]} - START_COUNTER) >= ${d.value}`;
}

function phaseLabel(step: WorkoutStep): string {
  return TYPE_LABELS[step.type.typeName]
    ?? (step.type.typeName.slice(0, 4).charAt(0).toUpperCase() + step.type.typeName.slice(1, 4));
}

/** Returns [source, ownVars]. Exact port of generate_source. */
export function generateSource(workout: Workout): [string, string[]] {
  const steps = expandSteps(workout);
  if (steps.length === 0) throw new Error('workout has no real steps');

  const lines: string[] = [
    'if (PHASE <= 0) {', '\tPHASE = 1;',
    `\tSTART_COUNTER = ${DURATION_VARS[steps[0].duration!.durationName] ?? 'SUUNTO_DURATION'};`,
    '\tCURRENT_LAP_NUMBER = SUUNTO_LAP_NUMBER;', '} else {',
  ];

  steps.forEach((step, idx) => {
    const phaseNum = idx + 1;
    const keyword = idx === 0 ? '\tif' : '\t} else if';
    lines.push(`${keyword} (PHASE == ${phaseNum} && ${phaseCondition(step)}) {`);
    lines.push(`\t\tPHASE = ${phaseNum + 1};`);
    if (idx + 1 < steps.length) {
      const nextKind = steps[idx + 1].duration!.durationName;
      const nextVar = DURATION_VARS[nextKind] ?? 'SUUNTO_DISTANCE';
      if (nextKind !== 'lap') lines.push(`\t\tSTART_COUNTER = ${nextVar};`);
    }
    lines.push('\t\tCURRENT_LAP_NUMBER = SUUNTO_LAP_NUMBER;');
    const entering = idx + 1 < steps.length ? steps[idx + 1] : null;
    const notify = (entering && entering.notify) || { beep: true, light: true };
    if (notify.beep !== false) lines.push('\t\tSuunto.alarmBeep();');
    if (notify.light !== false) lines.push('\t\tSuunto.light();');
  });
  lines.push('\t}');
  lines.push('}');
  lines.push('');

  lines.push('if (PHASE < 1) {');
  lines.push('\tprefix = "";');
  lines.push('\tRESULT = 0;');
  steps.forEach((step, idx) => {
    const phaseNum = idx + 1;
    const d = step.duration!;
    const kind = d.durationName;
    lines.push(`} else if (PHASE == ${phaseNum}) {`);
    lines.push(`\tprefix = "${phaseLabel(step)}";`);
    lines.push(`\tpostfix = "${DURATION_POSTFIX[kind] ?? ''}";`);
    if (kind === 'lap') lines.push('\tRESULT = SUUNTO_LAP_NUMBER - CURRENT_LAP_NUMBER;');
    else lines.push(`\tRESULT = ${d.value} - (${DURATION_VARS[kind]} - START_COUNTER);`);
  });
  lines.push('} else {');
  lines.push('\tprefix = "Done";');
  lines.push('\tpostfix = "";');
  lines.push('\tRESULT = 0;');
  lines.push('}');

  const ownVars = ['PHASE', 'START_COUNTER', 'CURRENT_LAP_NUMBER'];

  const hasTargets = steps.some(s => (s.target?.targetName ?? 'none') in TARGET_VARS);
  if (hasTargets) {
    lines.push('');
    lines.push('if (PHASE < 1) {');
    lines.push('\tOUT_OF_RANGE = 0;');
    steps.forEach((step, idx) => {
      const phaseNum = idx + 1;
      const target = step.target ?? { targetName: 'none' };
      const targetName = target.targetName ?? 'none';
      lines.push(`} else if (PHASE == ${phaseNum}) {`);
      if (!(targetName in TARGET_VARS)) { lines.push('\tOUT_OF_RANGE = 0;'); return; }
      const v = TARGET_VARS[targetName];
      const rng = target.valueRange!;
      lines.push(`\tif (${v} < ${rng.min} || ${v} > ${rng.max}) {`);
      lines.push('\t\tif (OUT_OF_RANGE == 0) {');
      lines.push('\t\t\tSuunto.alarmBeep();');
      lines.push('\t\t\tOUT_OF_RANGE = 1;');
      lines.push('\t\t}');
      lines.push('\t} else {');
      lines.push('\t\tOUT_OF_RANGE = 0;');
      lines.push('\t}');
    });
    lines.push('} else {');
    lines.push('\tOUT_OF_RANGE = 0;');
    lines.push('}');
    ownVars.push('OUT_OF_RANGE');
  }

  return [lines.join('\n') + '\n', ownVars];
}

// Wrap generated source in the compiler's HEADER block (the literal /***HEADER***/ ...
// /***ENDHEADER***/ markers). Exact port of build_compile_request.
export function buildCompileRequest(source: string, ownVars: string[], _name?: string): string {
  const header = '/***HEADER***/\n' + ownVars.map(v => `${v} = 0`).join('\n') + '\n/***ENDHEADER***/\n';
  return header + source;
}
