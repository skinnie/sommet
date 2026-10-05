// Device-aware workout step editor - the Android twin of the desktop Training Program's per-day
// editor (TrainingProgramPage.qml `editor`). Rows of {type, duration, target} in the project
// workout schema (workout.py / WorkoutSource.Workout); the duration and target choices are
// limited to what the chosen device can take (capsFor), like the desktop's root.capsFor().

import React from 'react';
import { View, Text, TextInput, Pressable } from 'react-native';
import { useV3Theme, v3Radius, v3Type } from '../theme/v3';
import type { Workout, WorkoutStep } from '../services/WorkoutSource';
import Icon from './ui/Icon';
import { PHASE_WORD } from '../services/GuidedWorkoutCore';

// Swimming in the builders - shared with the desktop (shared/swim_workout.js).
const SW = require('../config/swimWorkout');
export const swimKind = (activityId?: number): string => SW.swimKind(activityId);
export const fitRows = (rows: StepRow[], device: PlanDevice, swim: string, pool: number): StepRow[] =>
  rows.map(r => SW.fitRow(r, capsFor(device, swim), pool));

export type PlanDevice = 'suunto' | 'bryton' | 'magene' | '';
export const DEVICE_LABELS: Record<Exclude<PlanDevice, ''>, string> = {
  suunto: 'Suunto watch', bryton: 'Bryton', magene: 'Magene C406',
};

// Same table as the desktop: Magene = time + power/cadence (magene_workout.py); Bryton =
// time/distance + power/HR/speed/cadence (bryton_from_intervals.py); Suunto = everything.
export function capsFor(device: PlanDevice, swim = ''): { durations: string[]; targets: string[] } {
  // In the water (a Suunto swim mode): no targets; steps end on a distance, a time, the Lap
  // button or pool lengths (shared/swim_workout.js).
  if ((device === '' || device === 'suunto') && SW.swimCaps(swim)) return SW.swimCaps(swim);
  if (device === 'magene') return { durations: ['time_min', 'time_s'], targets: ['none', 'power', 'cadence'] };
  if (device === 'bryton') return { durations: ['time_min', 'time_s', 'distance_km', 'distance_m'], targets: ['none', 'power', 'hr', 'speed', 'cadence'] };
  // Suunto = native guided workout: its compiler rejects ascent steps and vertical-speed targets
  // (2026-09-26) - app workouts (Intervals screen) still have them.
  return { durations: ['time_min', 'time_s', 'distance_km', 'distance_m', 'lap', 'energy_kcal', 'hr_above', 'hr_below'], targets: ['none', 'hr', 'pace', 'speed', 'power', 'cadence'] };
}

const TYPES = ['warmup', 'interval', 'recovery', 'cooldown', 'repeatStart', 'repeatEnd'];
const TYPE_LABEL: Record<string, string> = {
  warmup: 'Warm up', interval: 'Interval', recovery: 'Recovery', cooldown: 'Cool down',
  repeatStart: 'Repeat ×', repeatEnd: 'Repeat end',
};
const DUR_LABEL: Record<string, string> = {
  time_min: 'min', time_s: 's', distance_km: 'km', distance_m: 'm', ascent_m: 'm+', lap: 'lap',
  lengths: 'lengths', energy_kcal: 'kcal', hr_above: 'until HR above (bpm)', hr_below: 'until HR below (bpm)',
};
const TGT_LABEL: Record<string, string> = {
  none: 'No target', hr: 'HR', pace: 'Pace min/km', speed: 'Speed km/h', vertical_speed: 'V-speed', power: 'Power W', cadence: 'Cadence',
};

export interface StepRow {
  stepType: string; durationKind: string; durationValue: number;
  targetKind: string; targetMin: number; targetMax: number; repeatCount: number;
  // Suunto guided workout only: backlight flash at this step's start / with its limits alarm
  // (step.notify.light / notify.limitLight - spliced in at install, GuidedWorkoutCore.withLights).
  lightStart: boolean; lightLimits: boolean;
  stepText: string; // shown on the watch when the step starts (native: long text + digits OK)
}

export const defaultStep = (type: string, minutes = 10): StepRow =>
  ({ stepType: type, durationKind: 'time_min', durationValue: minutes, targetKind: 'none', targetMin: 0, targetMax: 0, repeatCount: 3,
     lightStart: true, lightLimits: false, stepText: '' });

export const defaultSteps = (): StepRow[] => [
  defaultStep('warmup', 10), defaultStep('repeatStart'), defaultStep('interval', 4), defaultStep('recovery', 2),
  defaultStep('repeatEnd'), defaultStep('cooldown', 5),
];

export function fromSchema(s: WorkoutStep, swim = '', pool = 25): StepRow {
  const type = s.type.typeName;
  if (type === 'repeatStart') return { ...defaultStep(type), repeatCount: s.type.value || 2 };
  if (type === 'repeatEnd') return defaultStep(type);
  let kind = 'time_s', value = s.duration?.value ?? 0;
  const dn = s.duration?.durationName;
  if (dn === 'time' && value % 60 === 0) { kind = 'time_min'; value = value / 60; }
  else if (dn === 'distance') {
    const lengths = swim === 'pool' ? SW.lengthsFor(value, pool) : null;
    if (lengths !== null) { kind = 'lengths'; value = lengths; }
    else if (swim === 'pool') kind = 'distance_m';
    else if (value % 1000 === 0) { kind = 'distance_km'; value = value / 1000; }
    else kind = 'distance_m';
  }
  else if (dn === 'ascent') kind = 'ascent_m';
  else if (dn === 'lap') kind = 'lap';
  else if (dn === 'energy') kind = 'energy_kcal';
  else if (dn === 'hr_above' || dn === 'hr_below') kind = dn;
  const tn = s.target?.targetName ?? 'none';
  return {
    stepType: type, durationKind: kind, durationValue: value, targetKind: tn,
    targetMin: s.target?.valueRange?.min ?? 0, targetMax: s.target?.valueRange?.max ?? 0, repeatCount: 0,
    lightStart: s.notify?.light !== false, lightLimits: !!s.notify?.limitLight, stepText: s.text ?? '',
  };
}

export function toSchema(r: StepRow, pool = 25): WorkoutStep {
  if (r.stepType === 'repeatStart') return { type: { typeName: 'repeatStart', value: Math.max(1, Math.round(r.repeatCount)) } };
  if (r.stepType === 'repeatEnd') return { type: { typeName: 'repeatEnd' } };
  const v = Number(r.durationValue) || 0;
  const duration =
    r.durationKind === 'lap' ? { durationName: 'lap', value: 1 }
    : r.durationKind === 'time_min' ? { durationName: 'time', value: Math.round(v * 60) }
    : r.durationKind === 'time_s' ? { durationName: 'time', value: Math.round(v) }
    : r.durationKind === 'distance_km' ? { durationName: 'distance', value: Math.round(v * 1000) }
    : r.durationKind === 'distance_m' ? { durationName: 'distance', value: Math.round(v) }
    : r.durationKind === 'lengths' ? { durationName: 'distance', value: Math.round(v * pool) }
    : r.durationKind === 'energy_kcal' ? { durationName: 'energy', value: Math.round(v) }
    : r.durationKind === 'hr_above' || r.durationKind === 'hr_below' ? { durationName: r.durationKind, value: Math.round(v) }
    : { durationName: 'ascent', value: Math.round(v) };
  const step: WorkoutStep = { type: { typeName: r.stepType }, duration };
  step.target = r.targetKind === 'none'
    ? { targetName: 'none' }
    : { targetName: r.targetKind, valueRange: { min: Number(r.targetMin), max: Number(r.targetMax) } };
  if (r.stepText) step.text = r.stepText;
  step.notify = { light: r.lightStart !== false, limitLight: r.targetKind !== 'none' && !!r.lightLimits };
  return step;
}

export const toWorkout = (name: string, rows: StepRow[], pool = 25): Workout =>
  ({ name: name.trim() || 'Workout', steps: rows.map(r => toSchema(r, pool)) });

/** Repeat markers pair up, no nesting (the generator's own expand_steps rule). */
export function repeatsBalanced(rows: StepRow[]): boolean {
  let depth = 0;
  for (const r of rows) {
    if (r.stepType === 'repeatStart' && ++depth > 1) return false;
    if (r.stepType === 'repeatEnd' && --depth < 0) return false;
  }
  return depth === 0;
}

/** Steps the device can't take (e.g. an HR step created for the Magene). */
export function fitsDevice(rows: StepRow[], device: PlanDevice, swim = ''): boolean {
  const c = capsFor(device, swim);
  return rows.every(r => r.stepType === 'repeatStart' || r.stepType === 'repeatEnd'
    || (c.durations.includes(r.durationKind) && c.targets.includes(r.targetKind)));
}

function Pill({ label, onPress, active }: { label: string; onPress: () => void; active?: boolean }) {
  const t = useV3Theme();
  return (
    <Pressable onPress={onPress} style={{
      paddingVertical: 6, paddingHorizontal: 10, borderRadius: v3Radius.small, borderWidth: 1,
      borderColor: active ? t.primary : t.border, backgroundColor: active ? t.primary + '1F' : t.card,
    }}>
      <Text style={{ color: active ? t.primary : t.text, fontSize: v3Type.caption, fontWeight: '600' }}>{label}</Text>
    </Pressable>
  );
}

function Num({ value, onChange, w = 56 }: { value: number; onChange: (n: number) => void; w?: number }) {
  const t = useV3Theme();
  // Controlled, but keep the typed text while it still parses to the same number ("1." / "1,5").
  const [text, setText] = React.useState(String(value));
  React.useEffect(() => {
    setText(cur => (parseFloat(cur.replace(',', '.')) === value ? cur : String(value)));
  }, [value]);
  return (
    <TextInput value={text} keyboardType="numeric"
      onChangeText={v => { setText(v); const n = parseFloat(v.replace(',', '.')); if (Number.isFinite(n)) onChange(n); }}
      style={{
        width: w, borderWidth: 1, borderColor: t.border, borderRadius: v3Radius.small, color: t.text,
        backgroundColor: t.surface, paddingHorizontal: 8, paddingVertical: 4, fontSize: v3Type.body,
      }} />
  );
}

// Same wording as the desktop editor / Workout Builder. The watch's beeps aren't configurable.
const LIMIT_WORD: Record<string, string> = { hr: 'HR', pace: 'pace', speed: 'speed', power: 'power', cadence: 'cadence' };
// One explanation per tick (desktop TrainingProgramPage stepLightInfo / limitLightInfo).
const LIGHT_INFO = {
  step: 'The backlight flashes when this step begins, together with the watch\'s step melody - handy with '
    + 'headphones on. The workout-finished screen flashes too if any step has this ticked.',
  limit: 'The backlight flashes together with the two quick beeps the watch gives once you\'ve been outside '
    + 'this step\'s target for 5 s, then every 15 s while you stay outside.',
};

/** Rounded tick - the Android twin of desktop's RoundedCheckBox (22px, primary fill when on). */
function Tick({ label, value, onChange }: { label: string; value: boolean; onChange: (v: boolean) => void }) {
  const t = useV3Theme();
  return (
    <Pressable onPress={() => onChange(!value)} hitSlop={6} style={{ flexDirection: 'row', alignItems: 'center', gap: 6 }}>
      <View style={{
        width: 22, height: 22, borderRadius: v3Radius.small, borderWidth: 1, alignItems: 'center', justifyContent: 'center',
        borderColor: value ? t.primary : t.mutedText, backgroundColor: value ? t.primary : t.card,
      }}>
        {value && <Icon name="check" size={16} color={t.card} />}
      </View>
      <Text style={{ color: t.text, fontSize: v3Type.caption }}>{label}</Text>
    </Pressable>
  );
}

const cycle = (list: string[], cur: string) => list[(Math.max(0, list.indexOf(cur)) + 1) % list.length];

/** Tap a pill to cycle its value (type / duration unit / target) - compact on a phone. */
export function WorkoutStepsEditor({ rows, device, onChange, swim = '', poolLength = 25, onPoolLength, words = 'en', onWords }: {
  rows: StepRow[]; device: PlanDevice; onChange: (rows: StepRow[]) => void;
  // Swimming ("pool" / "open" when the chosen sport mode is a swim sport): the pool's length and
  // the language of the stroke words the Stroke pill writes into a step's text.
  swim?: string; poolLength?: number; onPoolLength?: (m: number) => void; words?: string; onWords?: (lang: string) => void;
}) {
  const t = useV3Theme();
  const caps = capsFor(device, swim);
  // A step text the Stroke pill wrote follows its step ("Crawl 50m" -> "Crawl 100m").
  const set = (i: number, patch: Partial<StepRow>) => onChange(rows.map((r, j) => {
    if (j !== i) return r;
    const next = { ...r, ...patch };
    if (swim && patch.stepText === undefined) next.stepText = SW.retext(r, next, poolLength, poolLength);
    return next;
  }));
  const setPool = (m: number) => {
    if (!(m > 0) || m === poolLength) return;
    onChange(rows.map(r => ({ ...r, stepText: SW.retext(r, r, poolLength, m) })));
    onPoolLength?.(m);
  };
  const strokeIds: string[] = SW.WORDS.map((w: { id: string }) => w.id);
  const suunto = device === 'suunto' || device === '';
  const [infoRow, setInfoRow] = React.useState<string | null>(null);  // "<row>:step" | "<row>:limit"
  const bold = { fontWeight: '700' as const, fontSize: v3Type.caption };
  return (
    <View style={{ gap: 8, marginTop: 10 }}>
      {suunto && (
        <View style={{ borderWidth: 1, borderColor: t.border, borderRadius: v3Radius.small, padding: 8 }}>
          <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>
            <Text style={bold}>Beeps</Text> come from the watch and always sound: a <Text style={bold}>melody</Text> when
            a step starts and when the workout ends; <Text style={bold}>two quick beeps</Text> once you&apos;ve been outside
            a step&apos;s target limits for 5 s, then every 15 s while you stay outside. The <Text style={bold}>Light On</Text> ticks
            add a backlight flash to those moments.
          </Text>
        </View>
      )}
      {!!swim && (
        <View style={{ gap: 6 }}>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
            {swim === 'pool' && (
              <>
                <Text style={{ color: t.text, fontSize: v3Type.caption }}>Pool length (m)</Text>
                <Num value={poolLength} onChange={setPool} />
              </>
            )}
            <Text style={{ color: t.text, fontSize: v3Type.caption }}>Stroke words on the watch</Text>
            <Pill label={(SW.LANGUAGES.find((l: { id: string }) => l.id === words) ?? SW.LANGUAGES[0]).label}
              onPress={() => onWords?.(cycle(SW.LANGUAGES.map((l: { id: string }) => l.id), words))} />
          </View>
          <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>
            In the water a step can end on a distance, a time, the Lap button or pool lengths, and has no target: the
            heart-rate belt cannot reach the watch under water. Tap Stroke on a step to write its text for the watch,
            e.g. &quot;Crawl 50m&quot; - you can still type your own.
          </Text>
        </View>
      )}
      {rows.map((r, i) => {
        const repeat = r.stepType === 'repeatStart' || r.stepType === 'repeatEnd';
        const badDur = !repeat && !caps.durations.includes(r.durationKind);
        const badTgt = !repeat && !caps.targets.includes(r.targetKind);
        const info = (which: 'step' | 'limit') => (
          <Pressable onPress={() => setInfoRow(infoRow === `${i}:${which}` ? null : `${i}:${which}`)} hitSlop={8}>
            <Icon name="info" size={18} color={t.mutedText} />
          </Pressable>
        );
        const openInfo = infoRow?.startsWith(`${i}:`) ? infoRow.split(':')[1] as 'step' | 'limit' : null;
        return (
          <View key={i} style={{ gap: 6,
            paddingLeft: rows.slice(0, i).reduce((d, x) => d + (x.stepType === 'repeatStart' ? 1 : x.stepType === 'repeatEnd' ? -1 : 0), 0) > 0 && r.stepType !== 'repeatEnd' ? 14 : 0 }}>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6 }}>
            <Pill label={TYPE_LABEL[r.stepType]} onPress={() => set(i, { stepType: cycle(TYPES, r.stepType) })} />
            {r.stepType === 'repeatStart' && <Num value={r.repeatCount} onChange={n => set(i, { repeatCount: Math.max(1, Math.round(n)) })} w={44} />}
            {!repeat && r.durationKind !== 'lap' && <Num value={r.durationValue} onChange={n => set(i, { durationValue: n })} />}
            {!repeat && <Pill label={DUR_LABEL[r.durationKind] + (badDur ? ' ⚠' : '')} active={badDur}
              onPress={() => set(i, { durationKind: cycle(caps.durations, r.durationKind) })} />}
            {!repeat && <Pill label={TGT_LABEL[r.targetKind] + (badTgt ? ' ⚠' : '')} active={badTgt}
              onPress={() => set(i, { targetKind: cycle(caps.targets, r.targetKind) })} />}
            {!repeat && r.targetKind !== 'none' && (
              <>
                <Num value={r.targetMin} onChange={n => set(i, { targetMin: n })} w={52} />
                <Text style={{ color: t.mutedText }}>–</Text>
                <Num value={r.targetMax} onChange={n => set(i, { targetMax: n })} w={52} />
              </>
            )}
            <Pressable onPress={() => onChange(rows.filter((_, j) => j !== i))} hitSlop={8}>
              <Text style={{ color: t.mutedText, fontSize: 18, paddingHorizontal: 4 }}>×</Text>
            </Pressable>
          </View>
          {!repeat && (
            <View style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 10 }}>
              {!!swim && (() => {
                const word = SW.wordOf(r.stepText, words);
                return (
                  <Pill label={word ? SW.WORDS.find((w: { id: string }) => w.id === word.id).label : 'Stroke'} active={!!word}
                    onPress={() => set(i, { stepText: SW.stepText(cycle(strokeIds, word ? word.id : strokeIds[strokeIds.length - 1]), words, r, poolLength) })} />
                );
              })()}
              <TextInput value={r.stepText ?? ''} maxLength={29} placeholder={`On watch: ${PHASE_WORD[r.stepType] ?? 'Step'}`}
                placeholderTextColor={t.mutedText} onChangeText={v => set(i, { stepText: v })}
                style={{
                  minWidth: 120, borderWidth: 1, borderColor: t.border, borderRadius: v3Radius.small, color: t.text,
                  backgroundColor: t.surface, paddingHorizontal: 8, paddingVertical: 4, fontSize: v3Type.body,
                }} />
              {suunto && (
                <>
                  <Tick label="Light On at each step" value={r.lightStart !== false} onChange={v => set(i, { lightStart: v })} />
                  {info('step')}
                  {r.targetKind !== 'none' && (
                    <>
                      <Tick label={`Light On for ${LIMIT_WORD[r.targetKind] ?? ''} limits`} value={!!r.lightLimits}
                        onChange={v => set(i, { lightLimits: v })} />
                      {info('limit')}
                    </>
                  )}
                </>
              )}
            </View>
          )}
          {suunto && !repeat && openInfo && (
            <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>{LIGHT_INFO[openInfo]}</Text>
          )}
          </View>
        );
      })}
      <View style={{ flexDirection: 'row', gap: 8 }}>
        <Pill label="+ Step" onPress={() => onChange([...rows, defaultStep('interval', 5)])} />
        <Pill label="+ Repeat" onPress={() => onChange([...rows, defaultStep('repeatStart'), defaultStep('interval', 4), defaultStep('repeatEnd')])} />
      </View>
      {!repeatsBalanced(rows) && <Text style={{ color: t.warning, fontSize: v3Type.caption }}>Repeat start/end markers must pair up (no nesting).</Text>}
      {repeatsBalanced(rows) && !fitsDevice(rows, device) && (
        <Text style={{ color: t.warning, fontSize: v3Type.caption }}>
          Steps marked ⚠ can’t go to the {device ? DEVICE_LABELS[device as Exclude<PlanDevice, ''>] : 'device'} — tap them to change.
        </Text>
      )}
    </View>
  );
}
