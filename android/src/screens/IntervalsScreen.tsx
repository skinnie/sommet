import React, { useState } from 'react';
import {
  View, Text, ScrollView, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert, Modal,
  Linking,
} from 'react-native';
import { Card } from '../components/ui/Card';
import Icon from '../components/ui/Icon';
import { useV3Theme } from '../theme/v3';
import { t } from '../i18n';
import { Workout } from '../services/WorkoutSource';
import {
  buildWorkoutRequest, parseCompiledApp, installCompiledInterval, writePlannedMoves,
  PlannedMoveState, CompiledApp, COMPILE_SITE_URL,
} from '../services/IntervalsService';
import { pickFile } from '../services/CatalogService';
import { InstallState } from '../services/AppInstall';
import { readCustomModes } from '../services/CustomModesService';
import { ExerciseMode } from '../services/CustomModesReader';

// Intervals - experimental, gated behind the Experimental flag. André's choice (2026-08-14):
// BOTH an interval Suunto App (build -> third-party compiler -> App-Zone install) and a native
// planned move (TrainingProgram). Both carry a clear "unproven" warning - the compiler isn't
// ours and the install-compiled-app path has a known unresolved on-hardware "app error"; the
// planned-move format may be inert. What's proven is that every byte we generate matches the
// Python tools (WorkoutSource/AppsCodec/AppInstall/TrainingProgramCodec tests).
export default function IntervalsScreen() {
  const theme = useV3Theme();
  const s = styles(theme);

  // Interval-workout builder inputs (minutes).
  const [warmup, setWarmup] = useState('10');
  const [reps, setReps] = useState('5');
  const [work, setWork] = useState('3');
  const [rest, setRest] = useState('2');
  const [cooldown, setCooldown] = useState('5');
  const [appName, setAppName] = useState('Intervals');

  // Planned-move inputs.
  const [pmName, setPmName] = useState('Long run');
  const [pmDuration, setPmDuration] = useState('60');
  const [pmIntensity, setPmIntensity] = useState('3');
  const [pmActivity, setPmActivity] = useState(3); // Running
  const [pmState, setPmState] = useState<PlannedMoveState | null>(null);
  const pmBusy = pmState != null && pmState.phase !== 'done' && pmState.phase !== 'error' && pmState.phase !== 'idle';

  // The compiled interval app the user imported back from the compiler site (null until then).
  const [compiledApp, setCompiledApp] = useState<CompiledApp | null>(null);
  // The generated App-Zone source, shown for the user to select+copy into the compiler site.
  const [generatedSource, setGeneratedSource] = useState<string | null>(null);

  // Install picker (shared shape with App-Zone: read modes -> pick mode -> screen -> field).
  const [pickerOpen, setPickerOpen] = useState(false);
  const [modes, setModes] = useState<ExerciseMode[] | null>(null);
  const [modesLoading, setModesLoading] = useState(false);
  const [chosenMode, setChosenMode] = useState<number | null>(null);
  const [chosenDisplay, setChosenDisplay] = useState<number | null>(null);
  const [installState, setInstallState] = useState<InstallState | null>(null);
  const installBusy = installState != null && installState.phase !== 'done' && installState.phase !== 'error' && installState.phase !== 'idle';

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
    return { name: appName.trim() || 'Intervals', steps };
  }

  // Step 1: generate the App-Zone source, copy it, and open the PUBLIC compiler site in the
  // browser. No key, no API call - the user compiles there and downloads the result. (The
  // compiler is run by a Suunto employee and is legally sensitive; this app never automates it.)
  function handleGenerateAndOpen() {
    try {
      setGeneratedSource(buildWorkoutRequest(buildWorkout(), appName.trim() || 'Intervals'));
      Linking.openURL(COMPILE_SITE_URL);
      Alert.alert(t.experimentalIntervals, t.intervalsSourceCopiedMsg);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? String(e));
    }
  }

  // Step 2: import the compiled app the user downloaded, then pick where to install it.
  async function handleImportCompiled() {
    try {
      const picked = await pickFile();
      const compiled = parseCompiledApp(picked.base64, appName.trim() || 'Intervals');
      if (compiled.binary.length === 0) { Alert.alert(t.error, 'Empty compiled app.'); return; }
      setCompiledApp(compiled);
      await openPicker();
    } catch (e: any) {
      if (e?.message !== 'CANCELLED' && e?.code !== 'CANCELLED') Alert.alert(t.error, e?.message ?? String(e));
    }
  }

  async function openPicker() {
    setPickerOpen(true);
    setModes(null); setChosenMode(null); setChosenDisplay(null); setInstallState(null);
    setModesLoading(true);
    await readCustomModes(st => { if (st.modes) setModes(st.modes); });
    setModesLoading(false);
  }

  async function runIntervalInstall(fieldIndex: number) {
    if (chosenMode == null || chosenDisplay == null || compiledApp == null) return;
    const ok = await installCompiledInterval(compiledApp, chosenMode, chosenDisplay, fieldIndex, setInstallState);
    if (ok) { Alert.alert(t.experimentalIntervals, t.appZoneInstalledMsg); setPickerOpen(false); }
  }

  async function writePlanned() {
    // Base date = the move's own date (dayOffset 0 => today). The watch validates it as a real
    // calendar date (year 2013-2099); the old literal 0 made the firmware reject the header.
    const today = new Date();
    const ok = await writePlannedMoves(
      [{ activityId: pmActivity, durationMinutes: num(pmDuration), intensity: num(pmIntensity), name: pmName.trim() || 'Move', dayOffset: 0 }],
      { year: today.getFullYear(), month: today.getMonth() + 1, day: today.getDate() }, setPmState,
    );
    if (ok) Alert.alert(t.experimentalIntervals, t.intervalsWritten);
  }

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      <Card style={{ width: '100%' }}>
        <Text style={[s.desc, { color: theme.warning }]}>{t.intervalsWarning}</Text>
      </Card>

      {/* ── Interval Suunto App ── */}
      <Card style={{ width: '100%' }}>
        <Text style={s.title}>{t.intervalsAppSection}</Text>
        <Text style={s.desc}>{t.intervalsAppDesc}</Text>
        <Field label={t.intervalsName} value={appName} onChangeText={setAppName} s={s} theme={theme} />
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
        {generatedSource != null && (
          <View style={{ marginTop: 10 }}>
            <Text style={s.fieldLabel}>{t.intervalsSourceLabel}</Text>
            <TextInput
              style={[s.input, { minHeight: 120, fontFamily: 'monospace', fontSize: 11 }]}
              value={generatedSource}
              editable={false}
              multiline
              selectTextOnFocus
            />
          </View>
        )}
        <TouchableOpacity style={[s.btn, { marginTop: 8 }]} onPress={handleImportCompiled}>
          <Text style={s.btnText}>{t.intervalsImportBtn}</Text>
        </TouchableOpacity>
      </Card>

      {/* ── Native planned move ── */}
      <Card style={{ width: '100%' }}>
        <Text style={s.title}>{t.intervalsPlannedSection}</Text>
        <Text style={[s.desc, { color: theme.warning }]}>{t.intervalsPlannedDesc}</Text>
        <Field label={t.intervalsName} value={pmName} onChangeText={setPmName} s={s} theme={theme} />
        <Row>
          <Field label={t.intervalsDuration} value={pmDuration} onChangeText={setPmDuration} numeric s={s} theme={theme} />
          <Field label={t.intervalsIntensity} value={pmIntensity} onChangeText={setPmIntensity} numeric s={s} theme={theme} />
        </Row>
        <View style={s.chipRow}>
          {[{ id: 3, name: 'Running' }, { id: 4, name: 'Cycling' }, { id: 12, name: 'Walking' }].map(a => (
            <TouchableOpacity key={a.id} style={[s.chip, pmActivity === a.id && s.chipActive]} onPress={() => setPmActivity(a.id)}>
              <Text style={[s.chipText, pmActivity === a.id && s.chipTextActive]}>{a.name}</Text>
            </TouchableOpacity>
          ))}
        </View>
        {pmBusy && <View style={s.rowCenter}><ActivityIndicator size="small" color={theme.primary} /><Text style={[s.desc, { marginLeft: 8 }]}>{t.intervalsWriting}</Text></View>}
        {pmState?.phase === 'error' && <Text style={[s.desc, { color: theme.error }]}>{pmState.error}</Text>}
        <TouchableOpacity style={[s.btn, pmBusy && { opacity: 0.5 }]} disabled={pmBusy} onPress={writePlanned}>
          <Text style={s.btnText}>{t.intervalsWriteBtn}</Text>
        </TouchableOpacity>
      </Card>

      {/* ── Install picker (compile+install) ── */}
      <Modal visible={pickerOpen} animationType="slide" transparent onRequestClose={() => setPickerOpen(false)}>
        <View style={s.modalOverlay}>
          <View style={s.modalBox}>
            <Text style={s.title}>{t.intervalsAppSection}</Text>
            <Text style={[s.desc, { color: theme.warning }]}>{t.intervalsWarning}</Text>

            {(modesLoading || installBusy) && (
              <View style={s.rowCenter}><ActivityIndicator size="small" color={theme.primary} />
                <Text style={[s.desc, { marginLeft: 8 }]}>
                  {installState?.phase === 'compiling' ? t.intervalsCompiling
                    : installBusy ? t.appZoneInstalling : t.appZoneReadingModes}
                </Text></View>
            )}
            {installState?.phase === 'error' && <Text style={[s.desc, { color: theme.error }]}>{installState.error}</Text>}

            {!modesLoading && !installBusy && modes && (
              <ScrollView style={{ maxHeight: 420 }}>
                {chosenMode == null && (<>
                  <Text style={s.stepLabel}>{t.appZonePickMode}</Text>
                  {modes.map((m, i) => (
                    <TouchableOpacity key={i} style={s.pickRow} onPress={() => { setChosenMode(i); setChosenDisplay(null); }}>
                      <Text style={s.pickText}>{m.settings.name}</Text><Icon name="chevronRight" size={16} color={theme.mutedText} />
                    </TouchableOpacity>
                  ))}
                </>)}
                {chosenMode != null && chosenDisplay == null && (() => {
                  const real = modes[chosenMode].displays.map((d, idx) => ({ d, idx })).filter(x => !x.d.isBuiltIn && x.d.fields.length > 0);
                  if (real.length === 0) return <Text style={s.desc}>{t.appZoneNoRealScreens}</Text>;
                  return (<>
                    <Text style={s.stepLabel}>{t.appZonePickScreen}</Text>
                    {real.map(({ d, idx }) => (
                      <TouchableOpacity key={idx} style={s.pickRow} onPress={() => setChosenDisplay(idx)}>
                        <Text style={s.pickText}>{t.appZoneScreenLabel(d.screenNumber ?? idx + 1)}</Text><Icon name="chevronRight" size={16} color={theme.mutedText} />
                      </TouchableOpacity>
                    ))}
                  </>);
                })()}
                {chosenMode != null && chosenDisplay != null && (<>
                  <Text style={s.stepLabel}>{t.appZonePickField}</Text>
                  {modes[chosenMode].displays[chosenDisplay].fields.map((f, fi) => (
                    <TouchableOpacity key={fi} style={s.pickRow} onPress={() => runIntervalInstall(fi)}>
                      <Text style={s.pickText}>{f.typeLabel}</Text>
                      <Text style={[s.pickText, { color: theme.primary, fontWeight: '700' }]}>{t.appZoneInstallBtn}</Text>
                    </TouchableOpacity>
                  ))}
                </>)}
              </ScrollView>
            )}

            <TouchableOpacity style={[s.btn, { alignSelf: 'center', marginTop: 14 }]} onPress={() => setPickerOpen(false)} disabled={installBusy}>
              <Text style={s.btnText}>{t.cancel}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </ScrollView>
  );
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
  rowCenter: { flexDirection: 'row', alignItems: 'center', marginTop: 10 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 12 },
  chip: {
    paddingHorizontal: 12, paddingVertical: 6, borderRadius: 999,
    backgroundColor: th.card, borderWidth: 1, borderColor: th.mutedText + '33',
  },
  chipActive: { backgroundColor: th.primary + '1F', borderColor: th.primary },
  chipText: { fontSize: 13, color: th.mutedText },
  chipTextActive: { color: th.primary, fontWeight: '700' },
  modalOverlay: { flex: 1, backgroundColor: '#00000066', justifyContent: 'flex-end' },
  modalBox: { backgroundColor: th.background, borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 20, maxHeight: '85%' },
  stepLabel: { fontSize: 13, fontWeight: '700', color: th.mutedText, marginTop: 8, marginBottom: 4 },
  pickRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: th.mutedText + '22',
  },
  pickText: { fontSize: 14, color: th.text },
});
