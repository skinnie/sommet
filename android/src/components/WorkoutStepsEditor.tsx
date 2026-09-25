// Device-aware workout step editor - the Android twin of the desktop Training Program's per-day
// editor (TrainingProgramPage.qml `editor`). Rows of {type, duration, target} in the project
// workout schema (workout.py / WorkoutSource.Workout); the duration and target choices are
// limited to what the chosen device can take (capsFor), like the desktop's root.capsFor().

import React from 'react';
import { View, Text, TextInput, Pressable } from 'react-native';
import { useV3Theme, v3Radius, v3Type } from '../theme/v3';
import type { Workout, WorkoutStep } from '../services/WorkoutSource';

export type PlanDevice = 'suunto' | 'bryton' | 'magene' | '';
export const DEVICE_LABELS: Record<Exclude<PlanDevice, ''>, string> = {
  suunto: 'Suunto watch', bryton: 'Bryton', magene: 'Magene C406',
};

// Same table as the desktop: Magene = time + power/cadence (magene_workout.py); Bryton =
// time/distance + power/HR/speed/cadence (bryton_from_intervals.py); Suunto = everything.
export function capsFor(device: PlanDevice) {
  if (device === 'magene') return { durations: ['time_min', 'time_s'], targets: ['none', 'power', 'cadence'] };
  if (device === 'bryton') return { durations: ['time_min', 'time_s', 'distance_km', 'distance_m'], targets: ['none', 'power', 'hr', 'speed', 'cadence'] };
  return { durations: ['time_min', 'time_s', 'distance_km', 'distance_m', 'ascent_m', 'lap'], targets: ['none', 'hr', 'pace', 'speed', 'vertical_speed', 'power', 'cadence'] };
}

const TYPES = ['warmup', 'interval', 'recovery', 'cooldown', 'repeatStart', 'repeatEnd'];
const TYPE_LABEL: Record<string, string> = {
  warmup: 'Warm up', interval: 'Interval', recovery: 'Recovery', cooldown: 'Cool down',
  repeatStart: 'Repeat ×', repeatEnd: 'Repeat end',
};
const DUR_LABEL: Record<string, string> = {
  time_min: 'min', time_s: 's', distance_km: 'km', distance_m: 'm', ascent_m: 'm+', lap: 'lap',
};
const TGT_LABEL: Record<string, string> = {
  none: 'No target', hr: 'HR', pace: 'Pace', speed: 'Speed', vertical_speed: 'V-speed', power: 'Power W', cadence: 'Cadence',
};

export interface StepRow {
  stepType: string; durationKind: string; durationValue: number;
  targetKind: string; targetMin: number; targetMax: number; repeatCount: number;
}

export const defaultStep = (type: string, minutes = 10): StepRow =>
  ({ stepType: type, durationKind: 'time_min', durationValue: minutes, targetKind: 'none', targetMin: 0, targetMax: 0, repeatCount: 3 });

export const defaultSteps = (): StepRow[] => [
  defaultStep('warmup', 10), defaultStep('repeatStart'), defaultStep('interval', 4), defaultStep('recovery', 2),
  defaultStep('repeatEnd'), defaultStep('cooldown', 5),
];

export function fromSchema(s: WorkoutStep): StepRow {
  const type = s.type.typeName;
  if (type === 'repeatStart') return { ...defaultStep(type), repeatCount: s.type.value || 2 };
  if (type === 'repeatEnd') return defaultStep(type);
  let kind = 'time_s', value = s.duration?.value ?? 0;
  const dn = s.duration?.durationName;
  if (dn === 'time' && value % 60 === 0) { kind = 'time_min'; value = value / 60; }
  else if (dn === 'distance') { if (value % 1000 === 0) { kind = 'distance_km'; value = value / 1000; } else kind = 'distance_m'; }
  else if (dn === 'ascent') kind = 'ascent_m';
  else if (dn === 'lap') kind = 'lap';
  const tn = s.target?.targetName ?? 'none';
  return {
    stepType: type, durationKind: kind, durationValue: value, targetKind: tn,
    targetMin: s.target?.valueRange?.min ?? 0, targetMax: s.target?.valueRange?.max ?? 0, repeatCount: 0,
  };
}

export function toSchema(r: StepRow): WorkoutStep {
  if (r.stepType === 'repeatStart') return { type: { typeName: 'repeatStart', value: Math.max(1, Math.round(r.repeatCount)) } };
  if (r.stepType === 'repeatEnd') return { type: { typeName: 'repeatEnd' } };
  const v = Number(r.durationValue) || 0;
  const duration =
    r.durationKind === 'lap' ? { durationName: 'lap', value: 0 }
    : r.durationKind === 'time_min' ? { durationName: 'time', value: Math.round(v * 60) }
    : r.durationKind === 'time_s' ? { durationName: 'time', value: Math.round(v) }
    : r.durationKind === 'distance_km' ? { durationName: 'distance', value: Math.round(v * 1000) }
    : r.durationKind === 'distance_m' ? { durationName: 'distance', value: Math.round(v) }
    : { durationName: 'ascent', value: Math.round(v) };
  const step: WorkoutStep = { type: { typeName: r.stepType }, duration };
  step.target = r.targetKind === 'none'
    ? { targetName: 'none' }
    : { targetName: r.targetKind, valueRange: { min: Number(r.targetMin), max: Number(r.targetMax) } };
  return step;
}

export const toWorkout = (name: string, rows: StepRow[]): Workout => ({ name: name.trim() || 'Workout', steps: rows.map(toSchema) });

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
export function fitsDevice(rows: StepRow[], device: PlanDevice): boolean {
  const c = capsFor(device);
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

const cycle = (list: string[], cur: string) => list[(Math.max(0, list.indexOf(cur)) + 1) % list.length];

/** Tap a pill to cycle its value (type / duration unit / target) - compact on a phone. */
export function WorkoutStepsEditor({ rows, device, onChange }: {
  rows: StepRow[]; device: PlanDevice; onChange: (rows: StepRow[]) => void;
}) {
  const t = useV3Theme();
  const caps = capsFor(device);
  const set = (i: number, patch: Partial<StepRow>) => onChange(rows.map((r, j) => (j === i ? { ...r, ...patch } : r)));
  return (
    <View style={{ gap: 8, marginTop: 10 }}>
      {rows.map((r, i) => {
        const repeat = r.stepType === 'repeatStart' || r.stepType === 'repeatEnd';
        const badDur = !repeat && !caps.durations.includes(r.durationKind);
        const badTgt = !repeat && !caps.targets.includes(r.targetKind);
        return (
          <View key={i} style={{ flexDirection: 'row', flexWrap: 'wrap', alignItems: 'center', gap: 6,
            paddingLeft: rows.slice(0, i).reduce((d, x) => d + (x.stepType === 'repeatStart' ? 1 : x.stepType === 'repeatEnd' ? -1 : 0), 0) > 0 && r.stepType !== 'repeatEnd' ? 14 : 0 }}>
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
