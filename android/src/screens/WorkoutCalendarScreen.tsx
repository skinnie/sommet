import React, { useEffect, useState } from 'react';
import {
  View, Text, ScrollView, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert, Linking,
} from 'react-native';
import { Card } from '../components/ui/Card';
import { useV3Theme } from '../theme/v3';
import { t } from '../i18n';
import { Workout } from '../services/WorkoutSource';
import { parseCompiledApp, CompiledApp, COMPILE_SITE_URL } from '../services/IntervalsService';
import { pickFile } from '../services/CatalogService';
import { readCustomModes } from '../services/CustomModesService';
import { ExerciseMode } from '../services/CustomModesReader';
import { syncCalendar, CalendarPlanEntry, SyncState, SyncResult } from '../services/TrainingCalendar';
import { fetchIntervalsWorkouts } from '../services/IntervalsWorkouts';

// Workout Calendar - André's locked design (2026-08-21): dated native guided workouts named
// "dd/mm_name" in the WORKOUT menu, sidestepping the unreachable native TrainingProgram flash
// region entirely (assets/Firmware/re-out/training_program_CONCLUSION.md on desktop has the
// full RE writeup). Same manual compile-and-import policy as IntervalsScreen (no compiler key
// shipped, no automated API call - TrainingCalendar.ts's own header comment has the "why").
//
// Flow: build a workout -> "Generate & open compiler" shows its JSON and opens the community
// compiler site -> paste the JSON there, compile, download the result -> "Import compiled
// workout" -> pick a date + sport mode -> "Add to Calendar". The Plan below is this screen's
// own state only (not persisted across app restarts - a real gap vs the desktop tool's
// localStorage-backed plan, acceptable for a first pass). "Sync to Watch" reads what's
// actually on the watch, erases anything dated before today, and installs whatever's next.
export default function WorkoutCalendarScreen() {
  const theme = useV3Theme();
  const s = styles(theme);

  const [date, setDate] = useState(todayIso());
  const [modes, setModes] = useState<ExerciseMode[] | null>(null);
  const [modesLoading, setModesLoading] = useState(false);
  const [mode, setMode] = useState<string | null>(null);

  const [name, setName] = useState('My workout');
  const [warmup, setWarmup] = useState('10');
  const [reps, setReps] = useState('5');
  const [work, setWork] = useState('3');
  const [rest, setRest] = useState('2');
  const [cooldown, setCooldown] = useState('5');

  const [generatedJson, setGeneratedJson] = useState<string | null>(null);
  const [compiledPending, setCompiledPending] = useState<CompiledApp | null>(null);

  // intervals.icu import (a date range -> planned workouts) + which pending plan row a compile is for
  const [importStart, setImportStart] = useState(todayIso());
  const [importEnd, setImportEnd] = useState(plusDaysIso(14));
  const [importing, setImporting] = useState(false);
  const [compileTarget, setCompileTarget] = useState<number | null>(null);

  const [plan, setPlan] = useState<CalendarPlanEntry[]>([]);

  const [syncState, setSyncState] = useState<SyncState | null>(null);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [lastSyncWasWrite, setLastSyncWasWrite] = useState(false);
  const syncBusy = syncState != null && syncState.phase !== 'done' && syncState.phase !== 'error' && syncState.phase !== 'idle';

  useEffect(() => {
    setModesLoading(true);
    readCustomModes(st => { if (st.modes) setModes(st.modes); if (st.phase === 'error') setModesLoading(false); })
      .finally(() => setModesLoading(false));
  }, []);

  function num(v: string): number { const n = parseInt(v, 10); return Number.isFinite(n) ? n : 0; }

  function buildWorkout(): Workout {
    const w = num(warmup), r = num(reps), wk = num(work), rs = num(rest), cd = num(cooldown);
    const steps: Workout['steps'] = [];
    if (w > 0) steps.push({ type: { typeName: 'warmup' }, duration: { durationName: 'time', value: w * 60 }, target: { targetName: 'none' } });
    steps.push({ type: { typeName: 'repeatStart', value: Math.max(1, r) } });
    steps.push({ type: { typeName: 'interval' }, duration: { durationName: 'time', value: Math.max(1, wk) * 60 }, target: { targetName: 'none' } });
    if (rs > 0) steps.push({ type: { typeName: 'recovery' }, duration: { durationName: 'time', value: rs * 60 }, target: { targetName: 'none' } });
    steps.push({ type: { typeName: 'repeatEnd' } });
    if (cd > 0) steps.push({ type: { typeName: 'cooldown' }, duration: { durationName: 'time', value: cd * 60 }, target: { targetName: 'none' } });
    return { name: name.trim() || 'Workout', steps };
  }

  // The compiler site's editor just POSTs whatever text is in it as-is (same request shape
  // guided_workout.py's compile_workout uses) - pasting raw workout JSON there compiles it
  // through the real JSON->native-guidance path, not just the older App-Zone-source path
  // IntervalsScreen uses. Confirmed by reading the site's own bundled main.js, 2026-08-21.
  function handleGenerateAndOpen() {
    try {
      setGeneratedJson(JSON.stringify(buildWorkout(), null, 2));
      Linking.openURL(COMPILE_SITE_URL);
      Alert.alert(t.experimentalWorkoutCalendar, t.intervalsSourceCopiedMsg);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? String(e));
    }
  }

  // Pull the athlete's planned workouts from intervals.icu for the date range and drop them into
  // the plan as pending entries (each carries its structured workout so it can be compiled below).
  async function handleImportFromIntervals() {
    if (!mode) { Alert.alert(t.error, t.workoutCalendarPickModeFirst); return; }
    setImporting(true);
    try {
      const { entries, skipped } = await fetchIntervalsWorkouts(importStart, importEnd, mode);
      if (entries.length === 0) {
        Alert.alert(t.experimentalWorkoutCalendar,
          skipped.length ? `${t.workoutCalendarImportNone} (${skipped.length} skipped)` : t.workoutCalendarImportNone);
      } else {
        setPlan(p => [...p, ...entries.map(e => ({ date: e.date, mode: e.mode, workoutName: e.name, workout: e.workout }))]);
        Alert.alert(t.experimentalWorkoutCalendar,
          `${t.workoutCalendarImportedPrefix} ${entries.length}${skipped.length ? ` (+${skipped.length} skipped)` : ''}. ${t.workoutCalendarImportCompileHint}`);
      }
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? String(e));
    } finally {
      setImporting(false);
    }
  }

  // Compile one pending imported entry: show its JSON, open the community compiler, and remember
  // which plan row the next "Import compiled workout" should attach to.
  function handleCompileEntry(i: number) {
    const e = plan[i];
    if (!e.workout) return;
    setGeneratedJson(JSON.stringify(e.workout, null, 2));
    setCompileTarget(i);
    Linking.openURL(COMPILE_SITE_URL);
    Alert.alert(t.experimentalWorkoutCalendar, t.intervalsSourceCopiedMsg);
  }

  async function handleImportCompiled() {
    try {
      const picked = await pickFile();
      const forName = compileTarget != null ? plan[compileTarget].workoutName : (name.trim() || 'Workout');
      const compiled = parseCompiledApp(picked.base64, forName);
      if (compiled.binary.length === 0) { Alert.alert(t.error, 'Empty compiled app.'); return; }
      if (compileTarget != null) {
        // Attach to the imported plan row being compiled, rather than the manual add flow.
        setPlan(p => p.map((e, idx) => (idx === compileTarget ? { ...e, compiled } : e)));
        setCompileTarget(null);
        setGeneratedJson(null);
        Alert.alert(t.experimentalWorkoutCalendar, t.workoutCalendarAddedMsg);
      } else {
        setCompiledPending(compiled);
      }
    } catch (e: any) {
      if (e?.message !== 'CANCELLED' && e?.code !== 'CANCELLED') Alert.alert(t.error, e?.message ?? String(e));
    }
  }

  function handleAddToPlan() {
    if (!mode) { Alert.alert(t.error, t.workoutCalendarPickModeFirst); return; }
    if (!compiledPending) { Alert.alert(t.error, t.intervalsImportBtn); return; }
    const workoutName = name.trim() || 'Workout';
    setPlan(p => [...p, { date, mode, workoutName, compiled: compiledPending }]);
    Alert.alert(t.experimentalWorkoutCalendar, t.workoutCalendarAddedMsg);
    setCompiledPending(null);
    setGeneratedJson(null);
    setName('My workout');
  }

  function removeFromPlan(i: number) {
    setPlan(p => p.filter((_, idx) => idx !== i));
  }

  async function doSync(write: boolean) {
    if (plan.length === 0) { Alert.alert(t.error, t.workoutCalendarEmptyPlanMsg); return; }
    setLastSyncWasWrite(write);
    setSyncResult(null);
    const result = await syncCalendar(plan, new Date(), write, setSyncState);
    if (result) setSyncResult(result);
  }

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      <Card style={{ width: '100%' }}>
        <Text style={[s.desc, { color: theme.warning }]}>{t.workoutCalendarWarning}</Text>
      </Card>

      {/* ── Import from intervals.icu ── */}
      <Card style={{ width: '100%' }}>
        <Text style={s.title}>{t.workoutCalendarImportTitle}</Text>
        <Text style={s.desc}>{t.workoutCalendarImportDesc}</Text>
        <Row>
          <Field label={t.workoutCalendarImportFrom} value={importStart} onChangeText={setImportStart} s={s} theme={theme} />
          <Field label={t.workoutCalendarImportTo} value={importEnd} onChangeText={setImportEnd} s={s} theme={theme} />
        </Row>
        <Text style={[s.desc, { marginTop: 8 }]}>
          {mode ? `${t.workoutCalendarModeLabel}: ${mode}` : t.workoutCalendarPickModeFirst}
        </Text>
        <TouchableOpacity
          style={[s.btn, s.primaryBtn, (importing || !mode) && { opacity: 0.5 }]}
          disabled={importing || !mode}
          onPress={handleImportFromIntervals}
        >
          {importing
            ? <ActivityIndicator size="small" color={theme.background} />
            : <Text style={s.primaryBtnText}>{t.workoutCalendarImportBtn}</Text>}
        </TouchableOpacity>
      </Card>

      {/* ── New calendar entry ── */}
      <Card style={{ width: '100%' }}>
        <Row>
          <Field label={t.workoutCalendarDateLabel} value={date} onChangeText={setDate} s={s} theme={theme} />
          <Field label={t.intervalsName} value={name} onChangeText={setName} s={s} theme={theme} />
        </Row>

        <Text style={[s.fieldLabel, { marginTop: 10 }]}>{t.workoutCalendarModeLabel}</Text>
        {modesLoading && <ActivityIndicator size="small" color={theme.primary} style={{ marginTop: 6, alignSelf: 'flex-start' }} />}
        {!modesLoading && (
          <View style={s.chipRow}>
            {(modes ?? []).map((m, i) => (
              <TouchableOpacity key={i} style={[s.chip, mode === m.settings.name && s.chipActive]} onPress={() => setMode(m.settings.name)}>
                <Text style={[s.chipText, mode === m.settings.name && s.chipTextActive]}>{m.settings.name}</Text>
              </TouchableOpacity>
            ))}
          </View>
        )}

        <Row>
          <Field label={t.intervalsWarmup} value={warmup} onChangeText={setWarmup} numeric s={s} theme={theme} />
          <Field label={t.intervalsCooldown} value={cooldown} onChangeText={setCooldown} numeric s={s} theme={theme} />
        </Row>
        <Row>
          <Field label={t.intervalsReps} value={reps} onChangeText={setReps} numeric s={s} theme={theme} />
          <Field label={t.intervalsWork} value={work} onChangeText={setWork} numeric s={s} theme={theme} />
          <Field label={t.intervalsRest} value={rest} onChangeText={setRest} numeric s={s} theme={theme} />
        </Row>

        <Text style={[s.desc, { marginTop: 12 }]}>{t.intervalsCompilerNote}</Text>
        <TouchableOpacity style={s.btn} onPress={handleGenerateAndOpen}>
          <Text style={s.btnText}>{t.intervalsGenerateBtn}</Text>
        </TouchableOpacity>
        {generatedJson != null && (
          <View style={{ marginTop: 10 }}>
            <Text style={s.fieldLabel}>{t.intervalsSourceLabel}</Text>
            <TextInput
              style={[s.input, { minHeight: 120, fontFamily: 'monospace', fontSize: 11 }]}
              value={generatedJson}
              editable={false}
              multiline
              selectTextOnFocus
            />
          </View>
        )}
        <TouchableOpacity style={[s.btn, { marginTop: 8 }]} onPress={handleImportCompiled}>
          <Text style={s.btnText}>{t.intervalsImportBtn}</Text>
        </TouchableOpacity>
        {compiledPending != null && (
          <Text style={[s.desc, { color: theme.primary, marginTop: 6 }]}>
            {compiledPending.binary.length} B - {t.workoutCalendarAddBtn.toLowerCase()}?
          </Text>
        )}
        <TouchableOpacity
          style={[s.btn, s.primaryBtn, compiledPending == null && { opacity: 0.5 }]}
          disabled={compiledPending == null}
          onPress={handleAddToPlan}
        >
          <Text style={s.primaryBtnText}>{t.workoutCalendarAddBtn}</Text>
        </TouchableOpacity>
      </Card>

      {/* ── Plan ── */}
      <Card style={{ width: '100%' }}>
        <Text style={s.title}>{t.workoutCalendarPlanTitle}</Text>
        {plan.length === 0 && <Text style={s.desc}>{t.workoutCalendarPlanEmpty}</Text>}
        {[...plan]
          .map((e, i) => ({ e, i }))
          .sort((a, b) => a.e.date.localeCompare(b.e.date))
          .map(({ e, i }) => {
            const isPast = e.date < todayIso();
            return (
              <View key={i} style={s.planRow}>
                <Text style={[s.planDate, isPast && { color: theme.error }]}>{e.date}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={s.planName}>{e.workoutName}</Text>
                  <Text style={s.desc}>{e.mode}{!e.compiled ? ` - ${t.workoutCalendarPending}` : ''}</Text>
                </View>
                {/* Pending imported entries carry their structured workout, so offer to compile it */}
                {!e.compiled && e.workout && (
                  <TouchableOpacity onPress={() => handleCompileEntry(i)}>
                    <Text style={[s.desc, { color: theme.primary, fontWeight: '700' }]}>
                      {compileTarget === i ? t.workoutCalendarCompilingRow : t.intervalsGenerateBtn}
                    </Text>
                  </TouchableOpacity>
                )}
                <TouchableOpacity onPress={() => removeFromPlan(i)}>
                  <Text style={[s.desc, { color: theme.error }]}>{t.deleteBtn}</Text>
                </TouchableOpacity>
              </View>
            );
          })}

        {syncBusy && (
          <View style={s.rowCenter}><ActivityIndicator size="small" color={theme.primary} />
            <Text style={[s.desc, { marginLeft: 8 }]}>{t.workoutCalendarSyncing}</Text></View>
        )}
        {syncState?.phase === 'error' && <Text style={[s.desc, { color: theme.error }]}>{syncState.error}</Text>}
        {syncResult != null && (
          <View style={{ marginTop: 8 }}>
            <Text style={s.desc}>Erase: {syncResult.removed.length ? syncResult.removed.join(', ') : '-'}</Text>
            <Text style={s.desc}>Install: {syncResult.added.length ? syncResult.added.join(', ') : '-'}</Text>
            {syncResult.pendingCompile.length > 0 && (
              <Text style={[s.desc, { color: theme.warning }]}>{t.workoutCalendarPending}: {syncResult.pendingCompile.join(', ')}</Text>
            )}
            {lastSyncWasWrite && <Text style={[s.desc, { color: theme.primary }]}>{t.workoutCalendarSyncedMsg}</Text>}
          </View>
        )}

        {/* #6 (André, 2026-09-02): single Sync button - dropped the separate Preview step for
            desktop parity. */}
        <Row>
          <TouchableOpacity style={[s.btn, s.primaryBtn, { flex: 1 }, syncBusy && { opacity: 0.5 }]} disabled={syncBusy} onPress={() => doSync(true)}>
            <Text style={s.primaryBtnText}>{t.workoutCalendarSyncBtn}</Text>
          </TouchableOpacity>
        </Row>
      </Card>
    </ScrollView>
  );
}

function todayIso(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function plusDaysIso(days: number): string {
  const d = new Date();
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function Row({ children }: { children: React.ReactNode }) {
  return <View style={{ flexDirection: 'row', gap: 10 }}>{children}</View>;
}
function Field({ label, value, onChangeText, numeric, s, theme }: {
  label: string; value: string; onChangeText: (v: string) => void; numeric?: boolean;
  s: any; theme: ReturnType<typeof useV3Theme>;
}) {
  return (
    <View style={{ flex: 1, marginTop: 10 }}>
      <Text style={s.fieldLabel}>{label}</Text>
      <TextInput
        style={s.input}
        value={value}
        onChangeText={onChangeText}
        keyboardType={numeric ? 'numeric' : 'default'}
        placeholderTextColor={theme.mutedText}
      />
    </View>
  );
}

const styles = (th: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: th.background },
  content: { padding: 16, gap: 14 },
  title: { fontSize: 16, fontWeight: '800', color: th.text },
  desc: { fontSize: 12.5, color: th.mutedText, marginTop: 6, lineHeight: 18 },
  fieldLabel: { fontSize: 12, color: th.mutedText, marginBottom: 4 },
  input: {
    backgroundColor: th.background, borderRadius: 8, borderWidth: 1, borderColor: th.mutedText + '33',
    paddingHorizontal: 12, paddingVertical: 9, color: th.text, fontSize: 14,
  },
  btn: {
    marginTop: 16, paddingVertical: 11, borderRadius: 10, alignItems: 'center',
    backgroundColor: th.primary + '1F', borderWidth: 1, borderColor: th.primary,
  },
  btnText: { color: th.primary, fontWeight: '700', fontSize: 13 },
  primaryBtn: { backgroundColor: th.primary },
  primaryBtnText: { color: th.background, fontWeight: '700', fontSize: 13 },
  rowCenter: { flexDirection: 'row', alignItems: 'center', marginTop: 10 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 6 },
  chip: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999,
    backgroundColor: th.card, borderWidth: 1, borderColor: th.mutedText + '33',
  },
  chipActive: { backgroundColor: th.primary + '1F', borderColor: th.primary },
  chipText: { fontSize: 13, color: th.mutedText },
  chipTextActive: { color: th.primary, fontWeight: '700' },
  planRow: {
    flexDirection: 'row', alignItems: 'center', gap: 10,
    paddingVertical: 10, borderTopWidth: 1, borderTopColor: th.mutedText + '22',
  },
  planDate: { fontSize: 13, fontWeight: '700', color: th.text, minWidth: 44 },
  planName: { fontSize: 14, color: th.text, fontWeight: '600' },
});
