import React, { useEffect, useState } from 'react';
import {
  View, Text, ScrollView, TextInput, TouchableOpacity, Pressable, StyleSheet, ActivityIndicator, Alert, Linking,
} from 'react-native';
import { useRoute } from '@react-navigation/native';
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
import { connectBryton, sendSchemaWorkout } from '../services/BrytonUsb';
import { sendWorkout as sendMageneWorkout } from '../services/MageneWorkout';
import { ActionMenu } from '../components/ui/ActionMenu';
import { loadPlan, savePlan } from '../services/WorkoutPlanStore';
import {
  WorkoutStepsEditor, StepRow, PlanDevice, DEVICE_LABELS, defaultSteps, fromSchema, toWorkout,
  repeatsBalanced, fitsDevice,
} from '../components/WorkoutStepsEditor';

// One plan for every device (André, 2026-09-25 - desktop Training Program parity): an entry is
// made FOR a device (the step editor offers only what that device takes), and a long press on a
// plan row opens Edit / Send to <connected bike computer> / Compile for watch / Remove. Home
// passes which devices are around; opened without params it behaves as before (watch only).
type PlanEntry = CalendarPlanEntry & { device?: PlanDevice };
// Workout Calendar - André's locked design (2026-08-21): dated native guided workouts named
// "dd/mm_name" in the WORKOUT menu, sidestepping the unreachable native TrainingProgram flash
// region entirely (assets/Firmware/re-out/training_program_CONCLUSION.md on desktop has the
// full RE writeup). Same manual compile-and-import policy as IntervalsScreen (no compiler key
// shipped, no automated API call - TrainingCalendar.ts's own header comment has the "why").
//
// Flow: build a workout -> "Generate & open compiler" shows its JSON and opens the community
// compiler site -> paste the JSON there, compile, download the result -> "Import compiled
// workout" -> pick a date + sport mode -> "Add to Calendar". The Plan below is this screen's
// own state, saved on the phone (WorkoutPlanStore) so it survives restarts. "Sync to Watch" reads what's
// actually on the watch, erases anything dated before today, and installs whatever's next.
export default function WorkoutCalendarScreen() {
  const theme = useV3Theme();
  const s = styles(theme);
  const route = useRoute<any>();
  const params: { watch?: boolean; bryton?: boolean; magene?: string | null } = route.params ?? { watch: true };
  const available: PlanDevice[] = [
    ...(params.watch ? ['suunto' as const] : []),
    ...(params.bryton ? ['bryton' as const] : []),
    ...(params.magene ? ['magene' as const] : []),
  ];
  const [device, setDevice] = useState<PlanDevice>(available[0] ?? '');
  const [rows, setRows] = useState<StepRow[]>(defaultSteps());
  const [editIndex, setEditIndex] = useState<number | null>(null);
  const [menuIndex, setMenuIndex] = useState<number | null>(null);
  const [sendMsg, setSendMsg] = useState('');
  const [sending, setSending] = useState(false);
  const isBike = device === 'bryton' || device === 'magene';

  const [date, setDate] = useState(todayIso());
  const [modes, setModes] = useState<ExerciseMode[] | null>(null);
  const [modesLoading, setModesLoading] = useState(false);
  const [mode, setMode] = useState<string | null>(null);

  const [name, setName] = useState('My workout');

  const [generatedJson, setGeneratedJson] = useState<string | null>(null);
  const [compiledPending, setCompiledPending] = useState<CompiledApp | null>(null);

  // intervals.icu import (a date range -> planned workouts) + which pending plan row a compile is for
  const [importStart, setImportStart] = useState(todayIso());
  const [importEnd, setImportEnd] = useState(plusDaysIso(14));
  const [importing, setImporting] = useState(false);
  const [compileTarget, setCompileTarget] = useState<number | null>(null);

  const [plan, setPlan] = useState<PlanEntry[]>([]);
  // Kept on the phone across restarts (WorkoutPlanStore); saved on every change once loaded.
  const [planLoaded, setPlanLoaded] = useState(false);
  useEffect(() => { loadPlan<PlanEntry>().then(p => { setPlan(p); setPlanLoaded(true); }); }, []);
  useEffect(() => { if (planLoaded) savePlan(plan); }, [plan, planLoaded]);
  const [brytonBusy, setBrytonBusy] = useState(false);
  const [brytonMsg, setBrytonMsg] = useState('');

  // Send the plan's intervals.icu-imported workouts (those carrying a workout schema) to a plugged
  // Bryton Aero 60 as native .fit, converted with the device's own thresholds (BrytonUsb).
  async function sendToBryton() {
    const withWorkout = plan.filter((e: any) => e.workout && e.device !== 'magene');
    if (withWorkout.length === 0) { setBrytonMsg('No intervals.icu workouts in the plan to send.'); return; }
    setBrytonBusy(true); setBrytonMsg('');
    try {
      await connectBryton();
      let ok = 0, fail = 0;
      for (const e of withWorkout) {
        try { await sendSchemaWorkout({ name: (e as any).workoutName, ...(e as any).workout }); ok++; }
        catch { fail++; }
      }
      setBrytonMsg(`Sent ${ok} to Bryton${fail ? `, ${fail} failed` : ''}.`);
    } catch (err: any) {
      setBrytonMsg(String(err?.message ?? err));
    } finally {
      setBrytonBusy(false);
    }
  }

  // Send one plan entry (or all of them) to a bike computer - the long-press menu's "Send to …".
  async function sendEntry(target: 'bryton' | 'magene', e: PlanEntry): Promise<boolean> {
    if (!e.workout) return false;
    if (target === 'bryton') {
      await connectBryton();
      await sendSchemaWorkout({ name: e.workoutName, ...(e.workout as any) });
      return true;
    }
    const r = await sendMageneWorkout(params.magene!, e.workout, e.workoutName);
    if (!r.ok) throw new Error(r.error || 'send failed');
    return true;
  }
  async function sendOne(target: 'bryton' | 'magene', e: PlanEntry) {
    setSending(true); setSendMsg(`Sending “${e.workoutName}” to ${DEVICE_LABELS[target]}…`);
    try { await sendEntry(target, e); setSendMsg(`Sent “${e.workoutName}” to ${DEVICE_LABELS[target]} ✓`); }
    catch (err: any) { setSendMsg(`${DEVICE_LABELS[target]}: ${String(err?.message ?? err)}`); }
    finally { setSending(false); }
  }
  async function sendAllToMagene() {
    const list = plan.filter(e => e.workout && e.device !== 'bryton' && e.date >= todayIso());
    if (!list.length) { setSendMsg('No upcoming workouts in the plan for the Magene.'); return; }
    // The C406 keeps the workouts it's sent; send the upcoming ones, oldest first.
    setSending(true);
    let ok = 0, fail = 0;
    for (const e of [...list].sort((a, b) => a.date.localeCompare(b.date))) {
      try { await sendEntry('magene', e); ok++; } catch { fail++; }
    }
    setSending(false);
    setSendMsg(`Sent ${ok} to Magene C406${fail ? `, ${fail} failed` : ''}.`);
  }

  // The creator: a workout for `device` on `date`. Bike computers take it straight away (no
  // compile step); the watch still goes through the community compiler below.
  function addBikeEntry(sendNow: boolean) {
    const workout = toWorkout(name, rows);
    const entry: PlanEntry = { date, mode: '', workoutName: workout.name!, workout, device };
    setPlan(p => (editIndex != null ? p.map((e, i) => (i === editIndex ? entry : e)) : [...p, entry]));
    setEditIndex(null);
    if (sendNow && isBike) sendOne(device as 'bryton' | 'magene', entry);
  }

  function editEntry(i: number) {
    const e = plan[i];
    setEditIndex(i);
    setDate(e.date);
    setName(e.workoutName);
    setDevice(e.device ?? (available.includes('suunto') ? 'suunto' : available[0] ?? ''));
    if (e.workout) setRows(e.workout.steps.map(fromSchema));
  }

  const editorOk = rows.length > 0 && repeatsBalanced(rows) && fitsDevice(rows, device);

  const [syncState, setSyncState] = useState<SyncState | null>(null);
  const [syncResult, setSyncResult] = useState<SyncResult | null>(null);
  const [lastSyncWasWrite, setLastSyncWasWrite] = useState(false);
  const syncBusy = syncState != null && syncState.phase !== 'done' && syncState.phase !== 'error' && syncState.phase !== 'idle';

  useEffect(() => {
    if (!params.watch) return; // sport modes live on the watch; bike computers don't need them
    setModesLoading(true);
    readCustomModes(st => { if (st.modes) setModes(st.modes); if (st.phase === 'error') setModesLoading(false); })
      .finally(() => setModesLoading(false));
  }, []);

  function buildWorkout(): Workout {
    return toWorkout(name, rows);
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
    // The sport mode only matters for the watch install; bike computers don't have one.
    if (params.watch && !mode) { Alert.alert(t.error, t.workoutCalendarPickModeFirst); return; }
    setImporting(true);
    try {
      const { entries, skipped } = await fetchIntervalsWorkouts(importStart, importEnd, mode ?? '');
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
    const workout = buildWorkout();
    const entry: PlanEntry = { date, mode, workoutName, compiled: compiledPending, workout, device: 'suunto' };
    setPlan(p => (editIndex != null ? p.map((e, i) => (i === editIndex ? entry : e)) : [...p, entry]));
    setEditIndex(null);
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
    // Only the watch's entries: bike-computer ones are sent from their own menu.
    const watchPlan = plan.filter(e => e.device !== 'bryton' && e.device !== 'magene');
    const result = await syncCalendar(watchPlan, new Date(), write, setSyncState);
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
        {params.watch && (
          <Text style={[s.desc, { marginTop: 8 }]}>
            {mode ? `${t.workoutCalendarModeLabel}: ${mode}` : t.workoutCalendarPickModeFirst}
          </Text>
        )}
        <TouchableOpacity
          style={[s.btn, s.primaryBtn, (importing || (params.watch && !mode)) && { opacity: 0.5 }]}
          disabled={importing || (params.watch && !mode)}
          onPress={handleImportFromIntervals}
        >
          {importing
            ? <ActivityIndicator size="small" color={theme.background} />
            : <Text style={s.primaryBtnText}>{t.workoutCalendarImportBtn}</Text>}
        </TouchableOpacity>
      </Card>

      {/* ── New calendar entry: a workout FOR one device (its step editor offers only what that
          device takes - desktop Training Program parity). ── */}
      <Card style={{ width: '100%' }}>
        {editIndex != null && <Text style={[s.desc, { color: theme.primary, marginTop: 0 }]}>Editing a plan entry</Text>}
        {available.length > 0 && (
          <>
            <Text style={s.fieldLabel}>Create for</Text>
            <View style={s.chipRow}>
              {available.map(d => (
                <TouchableOpacity key={d} style={[s.chip, device === d && s.chipActive]} onPress={() => setDevice(d)}>
                  <Text style={[s.chipText, device === d && s.chipTextActive]}>{DEVICE_LABELS[d as Exclude<PlanDevice, ''>]}</Text>
                </TouchableOpacity>
              ))}
            </View>
          </>
        )}
        <Row>
          <Field label={t.workoutCalendarDateLabel} value={date} onChangeText={setDate} s={s} theme={theme} />
          <Field label={t.intervalsName} value={name} onChangeText={setName} s={s} theme={theme} />
        </Row>

        {device === 'suunto' && (
          <>
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
          </>
        )}

        <WorkoutStepsEditor rows={rows} device={device} onChange={setRows} />

        {isBike && (
          <Row>
            <TouchableOpacity style={[s.btn, { flex: 1 }, !editorOk && { opacity: 0.5 }]} disabled={!editorOk} onPress={() => addBikeEntry(false)}>
              <Text style={s.btnText}>{editIndex != null ? 'Save' : t.workoutCalendarAddBtn}</Text>
            </TouchableOpacity>
            <TouchableOpacity style={[s.btn, s.primaryBtn, { flex: 1 }, (!editorOk || sending) && { opacity: 0.5 }]}
              disabled={!editorOk || sending} onPress={() => addBikeEntry(true)}>
              <Text style={s.primaryBtnText}>{`Save & send to ${DEVICE_LABELS[device as 'bryton' | 'magene']}`}</Text>
            </TouchableOpacity>
          </Row>
        )}

        {!isBike && (
          <>
            <Text style={[s.desc, { marginTop: 12 }]}>{t.intervalsCompilerNote}</Text>
            <TouchableOpacity style={[s.btn, !editorOk && { opacity: 0.5 }]} disabled={!editorOk} onPress={handleGenerateAndOpen}>
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
          </>
        )}
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
            const bike = e.device === 'bryton' || e.device === 'magene';
            const sub = bike ? DEVICE_LABELS[e.device as 'bryton' | 'magene']
              : `${e.mode}${!e.compiled ? ` - ${compileTarget === i ? t.workoutCalendarCompilingRow : t.workoutCalendarPending}` : ''}`;
            return (
              // Long press = the desktop's right-click day menu; the ⋯ opens the same menu.
              <Pressable key={i} style={s.planRow} onLongPress={() => setMenuIndex(i)} delayLongPress={350}>
                <Text style={[s.planDate, isPast && { color: theme.error }]}>{e.date}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={s.planName}>{e.workoutName}</Text>
                  <Text style={s.desc}>{sub}</Text>
                </View>
                <TouchableOpacity onPress={() => setMenuIndex(i)} hitSlop={10}>
                  <Text style={[s.planName, { color: theme.mutedText, paddingHorizontal: 6 }]}>⋯</Text>
                </TouchableOpacity>
              </Pressable>
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
        {params.watch && (
          <Row>
            <TouchableOpacity style={[s.btn, s.primaryBtn, { flex: 1 }, syncBusy && { opacity: 0.5 }]} disabled={syncBusy} onPress={() => doSync(true)}>
              <Text style={s.primaryBtnText}>{t.workoutCalendarSyncBtn}</Text>
            </TouchableOpacity>
          </Row>
        )}

        {/* Whole plan to a connected bike computer (one entry: long-press it). */}
        {plan.length > 0 && <Text style={[s.desc, { marginTop: 8 }]}>Long-press a workout to edit, send or remove it.</Text>}
        {brytonMsg ? <Text style={[s.desc, { marginTop: 8 }]}>{brytonMsg}</Text> : null}
        {sendMsg ? <Text style={[s.desc, { marginTop: 8 }]}>{sendMsg}</Text> : null}
        <Row>
          {params.bryton && (
            <TouchableOpacity style={[s.btn, { flex: 1 }, brytonBusy && { opacity: 0.5 }]} disabled={brytonBusy} onPress={sendToBryton}>
              <Text style={s.btnText}>{brytonBusy ? 'Sending…' : 'Send all to Bryton'}</Text>
            </TouchableOpacity>
          )}
          {!!params.magene && (
            <TouchableOpacity style={[s.btn, { flex: 1 }, sending && { opacity: 0.5 }]} disabled={sending} onPress={sendAllToMagene}>
              <Text style={s.btnText}>{sending ? 'Sending…' : 'Send upcoming to Magene'}</Text>
            </TouchableOpacity>
          )}
        </Row>
      </Card>

      <ActionMenu
        visible={menuIndex != null}
        title={menuIndex != null && plan[menuIndex] ? `${plan[menuIndex].date} · ${plan[menuIndex].workoutName}` : undefined}
        onClose={() => setMenuIndex(null)}
        items={menuIndex == null || !plan[menuIndex] ? [] : (() => {
          const i = menuIndex, e = plan[i];
          return [
            { label: 'Edit', onPress: () => editEntry(i), visible: !!e.workout },
            { label: 'Send to Bryton', onPress: () => sendOne('bryton', e), visible: !!(params.bryton && e.workout), disabled: sending },
            { label: 'Send to Magene C406', onPress: () => sendOne('magene', e), visible: !!(params.magene && e.workout), disabled: sending },
            { label: t.intervalsGenerateBtn, onPress: () => handleCompileEntry(i),
              visible: !!(params.watch && e.workout && !e.compiled && e.device !== 'bryton' && e.device !== 'magene') },
            { label: 'Remove workout', onPress: () => removeFromPlan(i), tone: 'alert' as const },
          ];
        })()}
      />
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
