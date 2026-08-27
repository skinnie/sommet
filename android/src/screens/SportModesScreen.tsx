import React, { useState, useEffect } from 'react';
import { useRoute, RouteProp, useNavigation } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { RootStackParamList } from '../../App';
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, Alert, ScrollView, ActivityIndicator, Modal, FlatList,
} from 'react-native';
import { ExerciseMode, FIELD_TYPES, fieldTypeLabel, builtInScreenName } from '../services/CustomModesReader';
import {
  readCustomModes, renameCustomMode, writeCustomModeField, writeCustomModeDisplayField,
} from '../services/CustomModesService';
import {
  readSportModes, applySportModeEdit, plans, SportSummary, SportModeManageState,
} from '../services/SportModeManage';
import { ACTIVITY_TYPES, activityIconName } from '../services/ActivityColors';
import { SPORT_MODE_ROWS, maxDisplaysForVariant } from '../services/SportModeRows';
import { t } from '../i18n';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { Toggle } from '../components/ui/primitives';
import Icon from '../components/ui/Icon';
import { WatchFacePreview, displayLayoutType } from '../components/WatchFacePreview';

// v3.0 UI port (2026-08-09, "replicate the desktop version feature wise, design wise") -
// full List<->Detail rework matching desktop's own SportModesPage.qml Phase 2 redesign
// (9d4c7be, "full SuuntoLink-style redesign"), not just a recolor: mode list -> tap a mode
// -> detail view with a watch-face filmstrip (WatchFacePreview.tsx, same layout-type
// classification as desktop's own displayLayoutType()) instead of the old inline-expand
// card. Every read/write handler below is unchanged from the previous version of this
// screen - only the JSX changed.
//
// Real, hardware-confirmed pod bits (UseHw bitmask) - see custom_modes_andre.md. 0x0004
// stays unconfirmed and is deliberately left out, same as the desktop page.
const PODS: { bit: number; label: string }[] = [
  { bit: 0x0001, label: 'HR belt' },
  { bit: 0x0100, label: 'Foot pod' },
  { bit: 0x0800, label: 'Bike pod' },
  { bit: 0x0040, label: 'Power pod' },
];

// Per-device limits now flow from the connected watch's codename (the `variant` navigation
// param HomeScreen passes from getDeviceInfo().model): maxDisplaysForVariant() gives the display
// ceiling (Traverse/Traverse Alpha = 4, rest of the Ambit3 family = 8) and the codec reads that
// variant's own create/limits row from SPORT_MODE_ROWS. See custom_modes.py's own
// max_displays_for_variant / _MAX_DISPLAYS_BY_VARIANT.

type Phase = 'idle' | 'connecting' | 'reading' | 'done' | 'error';

interface PickerTarget { mode: string; display: number; field: number }

// Activities the user can create a single mode for: those with real creation defaults on the
// reference variant (Emu = Ambit3 Peak, the fallback the codec uses for the whole Ambit3
// family), minus the multisport-container activities (those go through Create Multisport).
// Sorted by name, same as the desktop tool's --activities listing.
const DEFAULT_VARIANT = SPORT_MODE_ROWS.defaultVariant;
const CREATABLE_ACTIVITIES = Object.keys(SPORT_MODE_ROWS.activityDefaults[DEFAULT_VARIANT] ?? {})
  .map(Number)
  .filter(id => !SPORT_MODE_ROWS.multisportActivities.includes(id) && ACTIVITY_TYPES[id])
  .map(id => ({ id, name: ACTIVITY_TYPES[id].name }))
  .sort((a, b) => a.name.localeCompare(b.name));
const MULTISPORT_ACTIVITIES = SPORT_MODE_ROWS.multisportActivities
  .filter(id => ACTIVITY_TYPES[id])
  .map(id => ({ id, name: ACTIVITY_TYPES[id].name }));

type CreateKind = 'single' | 'multi';

export default function SportModesScreen() {
  const theme = useV3Theme();
  // The watch may be connected over USB or BLE. Over BLE the link is already open and owned
  // by the Home screen, so the sport-mode reads/writes must run on it directly - calling the
  // USB connect() would pop the OTG prompt and tear down the BLE session (André, 2026-08-17).
  const route = useRoute<RouteProp<RootStackParamList, 'SportModes'>>();
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const overBle = route.params?.overBle ?? false;
  // The connected watch's codename (getDeviceInfo().model, e.g. Emu/Jabiru/Loon), passed from
  // HomeScreen. Everything per-device flows from it: the max-displays cap AND the create/limits
  // the codec applies (SPORT_MODE_ROWS has real rows for every variant). Undefined -> the Emu
  // default. André, 2026-08-17.
  const variant = route.params?.variant;
  const maxDisplays = maxDisplaysForVariant(variant);
  const [modes, setModes] = useState<ExerciseMode[] | null>(null);
  // Ambit1/2: read via the legacy 0x2000 path, rendered read-only (no Ambit3 editor/structural
  // read, which crashes on this family). See CustomModesService.readCustomModes.
  const [isLegacy, setIsLegacy] = useState(false);
  const [phase, setPhase] = useState<Phase>('idle');
  const [error, setError] = useState<string | undefined>();
  const [writingMode, setWritingMode] = useState<string | null>(null);
  const [selectedName, setSelectedName] = useState<string | null>(null);
  const [selectedDisplayIndex, setSelectedDisplayIndex] = useState(0);
  const [picker, setPicker] = useState<PickerTarget | null>(null);

  const [nameEdits, setNameEdits] = useState<Record<string, string>>({});
  const [autolapEdits, setAutolapEdits] = useState<Record<string, string>>({});
  const [hrLowEdits, setHrLowEdits] = useState<Record<string, string>>({});
  const [hrHighEdits, setHrHighEdits] = useState<Record<string, string>>({});
  const [hrLimitsEdits, setHrLimitsEdits] = useState<Record<string, boolean>>({});

  // ── Structural manage (create / delete / multisport) - full-codec view alongside the
  // lossy-reader edit flow above (SportModeManage.ts / SportModeCodec.ts). ──
  const [summary, setSummary] = useState<SportSummary | null>(null);
  const [writeState, setWriteState] = useState<SportModeManageState | null>(null);
  const [createKind, setCreateKind] = useState<CreateKind | null>(null);
  const [createName, setCreateName] = useState('');
  const [createActivityId, setCreateActivityId] = useState<number | null>(null);
  const [legs, setLegs] = useState<string[]>([]);
  const manageBusy = writeState != null
    && writeState.phase !== 'done' && writeState.phase !== 'error' && writeState.phase !== 'idle';

  function applyModes(loaded: ExerciseMode[]) {
    setModes(loaded);
    const names: Record<string, string> = {};
    const autolaps: Record<string, string> = {};
    const hrLows: Record<string, string> = {};
    const hrHighs: Record<string, string> = {};
    const hrLimits: Record<string, boolean> = {};
    for (const m of loaded) {
      names[m.settings.name] = m.settings.name;
      autolaps[m.settings.name] = String(m.settings.autolap);
      hrLows[m.settings.name] = String(m.settings.hrLow);
      hrHighs[m.settings.name] = String(m.settings.hrHigh);
      hrLimits[m.settings.name] = m.settings.hrLimitsUse !== 0;
    }
    setNameEdits(names);
    setAutolapEdits(autolaps);
    setHrLowEdits(hrLows);
    setHrHighEdits(hrHighs);
    setHrLimitsEdits(hrLimits);
  }

  async function handleRead() {
    let legacy = false;
    await readCustomModes(s => {
      setPhase(s.phase);
      setError(s.error);
      if (s.modes) applyModes(s.modes);
      if (s.legacy) legacy = true;
    }, overBle, maxDisplays);
    setIsLegacy(legacy);
    // Ambit1/2 have no Ambit3 structural view, and readSportModes() reads the same CustomModes
    // region that crashes on this family - skip it entirely for legacy (the read-only list is
    // all that's shown). Otherwise load the structural view (menu order, multisport, slots).
    if (legacy) {
      setSummary(null);
      return;
    }
    try {
      setSummary(await readSportModes(overBle));
    } catch {
      setSummary(null);
    }
  }

  // Auto-read on open - this screen is only reachable from Home while a watch is connected
  // (USB or BLE, `overBle` says which), so read the modes automatically instead of making the
  // user tap a button (André, 2026-08-18: automatic on connect, either transport, both apps).
  useEffect(() => { handleRead(); }, []); // eslint-disable-line react-hooks/exhaustive-deps

  // Run one structural edit (create/delete/multisport), then refresh both views. The error, if
  // any, is captured from the state callback (async state can't be read back after the await).
  async function runEdit(build: Parameters<typeof applySportModeEdit>[0]) {
    let errorMsg: string | undefined;
    const fresh = await applySportModeEdit(build, s => {
      setWriteState(s);
      if (s.phase === 'error') errorMsg = s.error;
    }, overBle);
    if (fresh) {
      setSummary(fresh);
      await handleRead();
    } else if (errorMsg) {
      Alert.alert(t.error, errorMsg);
    }
  }

  function confirmDelete(name: string, multisport: boolean) {
    Alert.alert(t.sportModesDeleteTitle, t.sportModesDeleteMsg(name), [
      { text: t.cancel, style: 'cancel' },
      {
        text: t.sportModesDeleteBtn, style: 'destructive',
        onPress: () => runEdit(multisport ? plans.deleteMultisport(name) : plans.delete(name)),
      },
    ]);
  }

  function openCreate(kind: CreateKind) {
    setCreateKind(kind);
    setCreateName('');
    setCreateActivityId(kind === 'multi' ? (MULTISPORT_ACTIVITIES[0]?.id ?? null) : null);
    setLegs([]);
  }

  async function submitCreate() {
    const name = createName.trim();
    if (!name || createActivityId == null) return;
    const kind = createKind;
    setCreateKind(null);
    if (kind === 'single') await runEdit(plans.create(name, createActivityId, variant));
    else if (kind === 'multi') await runEdit(plans.createMultisport(name, createActivityId, legs, variant));
  }

  async function withWrite(modeName: string, action: () => Promise<{ ok: boolean; error?: string } | undefined>) {
    setWritingMode(modeName);
    const result = await action();
    setWritingMode(null);
    if (result && !result.ok && result.error) Alert.alert(t.error, result.error);
    await handleRead();
  }

  function handleRename(originalName: string) {
    const newName = (nameEdits[originalName] ?? '').trim();
    if (!newName || newName === originalName) return;
    withWrite(originalName, () =>
      renameCustomMode(originalName, newName, () => {}, overBle));
    setSelectedName(newName);
  }

  function handleSetAutolap(modeName: string) {
    const value = parseInt(autolapEdits[modeName] ?? '', 10);
    if (!Number.isFinite(value)) return;
    withWrite(modeName, () =>
      writeCustomModeField(modeName, { Autolap: value }, () => {}, overBle));
  }

  function handleSetHrLimits(modeName: string) {
    const low = parseInt(hrLowEdits[modeName] ?? '', 10);
    const high = parseInt(hrHighEdits[modeName] ?? '', 10);
    if (!Number.isFinite(low) || !Number.isFinite(high)) return;
    withWrite(modeName, () =>
      writeCustomModeField(modeName, {
        HrLow: low, HrHigh: high, HrLimitsUse: hrLimitsEdits[modeName] ? 1 : 0,
      }, () => {}, overBle));
  }

  function handleTogglePod(modeName: string, currentUseHw: number, bit: number, enabled: boolean) {
    const newUseHw = enabled ? (currentUseHw | bit) : (currentUseHw & ~bit);
    withWrite(modeName, () =>
      writeCustomModeField(modeName, { UseHw: newUseHw }, () => {}, overBle));
  }

  function handleSelectFieldType(typeName: string) {
    if (!picker) return;
    const { mode, display, field } = picker;
    setPicker(null);
    withWrite(mode, () =>
      writeCustomModeDisplayField(mode, display, field, undefined, typeName, () => {}, overBle));
  }

  const busy = phase === 'connecting' || phase === 'reading';
  const selectedMode = modes?.find(m => m.settings.name === selectedName) ?? null;

  // ── Detail view ──
  if (selectedMode) {
    const name = selectedMode.settings.name;
    const isWriting = writingMode === name;
    const realDisplays = selectedMode.displays.filter(d => !d.isBuiltIn);
    const currentDisplay = selectedMode.displays[selectedDisplayIndex] ?? null;

    return (
      <ScrollView style={styles(theme).root} contentContainerStyle={styles(theme).content}>
        <TouchableOpacity style={styles(theme).backRow} onPress={() => { setSelectedName(null); setSelectedDisplayIndex(0); }}>
          <Icon name="chevronLeft" size={18} color={theme.text} />
          <Text style={[styles(theme).backText]}>{t.sportModesBackBtn}</Text>
        </TouchableOpacity>
        <Text style={styles(theme).modeTitle}>{name}</Text>

        <Card style={{ width: '100%' }}>
          <Text style={styles(theme).label}>{t.sportModesNameLabel}</Text>
          <View style={styles(theme).row}>
            <TextInput
              style={[styles(theme).input, { flex: 1 }]}
              value={nameEdits[name] ?? name}
              onChangeText={v => setNameEdits(prev => ({ ...prev, [name]: v }))}
              placeholderTextColor={theme.mutedText}
              editable={!isWriting}
            />
            <TouchableOpacity
              style={styles(theme).smallBtn}
              disabled={isWriting || (nameEdits[name] ?? name) === name}
              onPress={() => handleRename(name)}
            >
              <Text style={styles(theme).smallBtnText}>{t.sportModesRenameBtn}</Text>
            </TouchableOpacity>
          </View>

          <Text style={styles(theme).label}>{t.sportModesAutolapLabel}</Text>
          <View style={styles(theme).row}>
            <TextInput
              style={[styles(theme).input, { flex: 1 }]}
              value={autolapEdits[name] ?? ''}
              onChangeText={v => setAutolapEdits(prev => ({ ...prev, [name]: v }))}
              keyboardType="numeric"
              editable={!isWriting}
            />
            <TouchableOpacity style={styles(theme).smallBtn} disabled={isWriting} onPress={() => handleSetAutolap(name)}>
              <Text style={styles(theme).smallBtnText}>{t.sportModesSetBtn}</Text>
            </TouchableOpacity>
          </View>

          <Text style={styles(theme).label}>{t.sportModesHrLimitsLabel}</Text>
          <View style={styles(theme).row}>
            <Toggle
              value={hrLimitsEdits[name] ?? false}
              onValueChange={v => setHrLimitsEdits(prev => ({ ...prev, [name]: v }))}
              disabled={isWriting}
            />
            <TextInput
              style={[styles(theme).input, { flex: 1, marginLeft: 10 }]}
              value={hrLowEdits[name] ?? ''}
              onChangeText={v => setHrLowEdits(prev => ({ ...prev, [name]: v }))}
              placeholder={t.sportModesHrLowLabel}
              placeholderTextColor={theme.mutedText}
              keyboardType="numeric"
              editable={!isWriting}
            />
            <TextInput
              style={[styles(theme).input, { flex: 1, marginLeft: 10 }]}
              value={hrHighEdits[name] ?? ''}
              onChangeText={v => setHrHighEdits(prev => ({ ...prev, [name]: v }))}
              placeholder={t.sportModesHrHighLabel}
              placeholderTextColor={theme.mutedText}
              keyboardType="numeric"
              editable={!isWriting}
            />
            <TouchableOpacity style={[styles(theme).smallBtn, { marginLeft: 10 }]} disabled={isWriting} onPress={() => handleSetHrLimits(name)}>
              <Text style={styles(theme).smallBtnText}>{t.sportModesSetBtn}</Text>
            </TouchableOpacity>
          </View>

          <Text style={styles(theme).label}>{t.sportModesPodsLabel}</Text>
          <View style={styles(theme).chipRow}>
            {PODS.map(pod => {
              const active = (selectedMode.settings.useHw & pod.bit) !== 0;
              return (
                <TouchableOpacity
                  key={pod.bit}
                  style={[styles(theme).chip, active && styles(theme).chipActive]}
                  disabled={isWriting}
                  onPress={() => handleTogglePod(name, selectedMode.settings.useHw, pod.bit, !active)}
                >
                  <Text style={[styles(theme).chipText, active && styles(theme).chipTextActive]}>{pod.label}</Text>
                </TouchableOpacity>
              );
            })}
          </View>

          {isWriting && (
            <View style={styles(theme).statusRow}>
              <ActivityIndicator size="small" color={theme.primary} />
            </View>
          )}
        </Card>

        <Card style={{ width: '100%' }}>
          <Text style={styles(theme).cardTitle}>
            {t.sportModesDisplaysCount(realDisplays.length, maxDisplays)}
          </Text>
          <ScrollView horizontal showsHorizontalScrollIndicator={false} style={{ marginTop: v3Spacing.small }}>
            <View style={{ flexDirection: 'row', gap: v3Spacing.small }}>
              {/* Only the real user displays (per-device max: Traverse=4, rest=8). The
                  built-in system screens beyond that are hidden - the watch doesn't show them
                  as editable screens either (André, 2026-08-18: "if more than 4, hide it"). */}
              {selectedMode.displays.map((display, i) => ({ display, i }))
                .filter(({ display }) => !display.isBuiltIn)
                .map(({ display, i }) => (
                <TouchableOpacity key={i} onPress={() => setSelectedDisplayIndex(i)} style={{ alignItems: 'center' }}>
                  <WatchFacePreview layoutType={displayLayoutType(display)} selected={i === selectedDisplayIndex} diameter={80} />
                  <Text style={[styles(theme).filmLabel, { color: i === selectedDisplayIndex ? theme.primary : theme.mutedText }]}>
                    {String(display.screenNumber)}
                  </Text>
                </TouchableOpacity>
              ))}
            </View>
          </ScrollView>

          {currentDisplay && (
            <View style={{ marginTop: v3Spacing.medium }}>
              {currentDisplay.isBuiltIn ? (
                <Text style={styles(theme).sectionDesc}>
                  {t.sportModesBuiltInMsg(builtInScreenName(currentDisplay.templateName))}
                </Text>
              ) : (
                <>
                  <Text style={styles(theme).label}>{t.sportModesScreenLabel(currentDisplay.screenNumber ?? 0)}</Text>
                  {currentDisplay.fields.map((field, fi) => (
                    <View key={fi} style={styles(theme).fieldRow}>
                      <Text style={styles(theme).fieldText} numberOfLines={1}>{field.typeLabel}</Text>
                      <TouchableOpacity
                        style={styles(theme).smallBtn}
                        disabled={isWriting}
                        onPress={() => setPicker({ mode: name, display: selectedDisplayIndex, field: fi })}
                      >
                        <Text style={styles(theme).smallBtnText}>{t.sportModesChangeBtn}</Text>
                      </TouchableOpacity>
                    </View>
                  ))}
                </>
              )}
            </View>
          )}
        </Card>

        <Modal visible={!!picker} animationType="slide" transparent onRequestClose={() => setPicker(null)}>
          <View style={styles(theme).modalOverlay}>
            <View style={styles(theme).modalBox}>
              <Text style={styles(theme).cardTitle}>{t.sportModesPickerTitle}</Text>
              {/* Install a Suunto App onto this row - the App Zone catalog now lives inside Sport
                  Modes (André, 2026-08-17), matching the desktop. Opens the catalog browser. */}
              <TouchableOpacity
                style={[styles(theme).pickerRow, { flexDirection: 'row', alignItems: 'center', gap: 10 }]}
                onPress={() => { setPicker(null); navigation.navigate('AppZone'); }}
              >
                <Icon name="watch" size={18} color={theme.primary} />
                <Text style={[styles(theme).pickerRowText, { color: theme.primary, fontWeight: '700' }]}>
                  {t.sportModesInstallApp}
                </Text>
              </TouchableOpacity>
              <FlatList
                data={FIELD_TYPES}
                keyExtractor={item => String(item.value)}
                style={{ maxHeight: 400 }}
                renderItem={({ item }) => (
                  <TouchableOpacity style={styles(theme).pickerRow} onPress={() => handleSelectFieldType(item.name)}>
                    <Text style={styles(theme).pickerRowText}>{fieldTypeLabel(item.name)}</Text>
                  </TouchableOpacity>
                )}
              />
              <TouchableOpacity style={[styles(theme).smallBtn, { marginTop: 10, alignSelf: 'center' }]} onPress={() => setPicker(null)}>
                <Text style={styles(theme).smallBtnText}>{t.sportModesCloseBtn}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </Modal>
      </ScrollView>
    );
  }

  // ── List view ──
  return (
    <ScrollView style={styles(theme).root} contentContainerStyle={styles(theme).content}>
      <Card style={{ width: '100%' }}>
        <Text style={styles(theme).sectionDesc}>{t.sportModesDesc}</Text>

        {busy && (
          <View style={styles(theme).statusRow}>
            <ActivityIndicator size="small" color={theme.primary} />
            <Text style={[styles(theme).sectionDesc, { marginLeft: 8, marginBottom: 0 }]}>
              {phase === 'connecting' ? t.connecting : t.sportModesReading}
            </Text>
          </View>
        )}

        {phase === 'error' && !modes && (
          <>
            {/* The read only fails when there's no live link, so lead with a plain
                "check your watch connection" instead of the raw (USB-worded) error. The detail
                stays below, muted, for when it's something else. André, 2026-08-17. */}
            <Text style={[styles(theme).sectionDesc, { color: theme.error, marginTop: 10 }]}>
              {t.sportModesCheckConnection}
            </Text>
            {error ? (
              <Text style={[styles(theme).sectionDesc, { color: theme.mutedText, marginTop: 2 }]}>{error}</Text>
            ) : null}
          </>
        )}

      </Card>

      {/* ── Manage: create / delete / multisport (full-codec structural view) ── */}
      {summary && (
        <Card style={{ width: '100%' }}>
          <View style={styles(theme).listRow}>
            <View style={{ flex: 1 }}>
              <Text style={styles(theme).cardTitle}>{t.sportModesManageTitle}</Text>
              <Text style={styles(theme).sectionDesc}>
                {t.sportModesCounts(summary.used, summary.maxUsed, summary.multi, summary.maxMulti)}
              </Text>
            </View>
          </View>

          <Text style={[styles(theme).sectionDesc, { color: theme.warning }]}>{t.sportModesWriteWarning}</Text>

          {manageBusy && (
            <View style={styles(theme).statusRow}>
              <ActivityIndicator size="small" color={theme.primary} />
              <Text style={[styles(theme).sectionDesc, { marginLeft: 8, marginBottom: 0 }]}>
                {writeState?.phase === 'verifying' ? t.sportModesVerifying
                  : writeState?.phase === 'reading' ? t.sportModesReading
                  : writeState?.phase === 'connecting' ? t.connecting
                  : t.sportModesWritingStep(writeState?.step ?? 1, writeState?.totalSteps ?? 1)}
              </Text>
            </View>
          )}

          {/* The mode list itself lives once, below this card (single sports there + any
              multisport modes appended). This card keeps only the counts, the warning and the
              Create buttons, so a mode is never listed twice. André, 2026-08-17. */}

          <View style={[styles(theme).row, { marginTop: 12 }]}>
            <TouchableOpacity
              style={[styles(theme).smallBtn, { opacity: summary.used >= summary.maxUsed || manageBusy ? 0.4 : 1 }]}
              disabled={summary.used >= summary.maxUsed || manageBusy}
              onPress={() => openCreate('single')}
            >
              <Text style={styles(theme).smallBtnText}>{t.sportModesCreateBtn}</Text>
            </TouchableOpacity>
            {summary.maxMulti > 0 && (
              <TouchableOpacity
                style={[styles(theme).smallBtn, { opacity: summary.used >= summary.maxUsed || summary.multi >= summary.maxMulti || manageBusy ? 0.4 : 1 }]}
                disabled={summary.used >= summary.maxUsed || summary.multi >= summary.maxMulti || manageBusy}
                onPress={() => openCreate('multi')}
              >
                <Text style={styles(theme).smallBtnText}>{t.sportModesCreateMultiBtn}</Text>
              </TouchableOpacity>
            )}
          </View>
        </Card>
      )}

      {/* The one and only mode list: tap a mode to edit its displays/settings, delete inline
          (delete needs the structural codec, so it shows only once `summary` has loaded). Icon +
          colour come from the mode's own ActivityID (the same table Calendar/Totals/log use),
          falling back to the generic glyph for an unknown id. André, 2026-08-17. */}
      {modes && modes.map(mode => {
        const name = mode.settings.name;
        const realCount = mode.displays.filter(d => !d.isBuiltIn).length;
        const activity = ACTIVITY_TYPES[mode.settings.activityId];
        const iconColor = activity ? activity.color : theme.mutedText;
        return (
          <TouchableOpacity key={name} onPress={() => setSelectedName(name)} activeOpacity={0.7}>
            <Card style={{ width: '100%' }}>
              <View style={styles(theme).listRow}>
                <Icon name={activityIconName(activity?.name)} size={22} color={iconColor} />
                <View style={{ flex: 1, marginLeft: 10 }}>
                  <Text style={styles(theme).cardTitle}>{name}</Text>
                  <Text style={styles(theme).sectionDesc}>
                    {t.sportModesDisplaysCount(realCount, maxDisplays)}
                  </Text>
                </View>
                {summary && (
                  <TouchableOpacity style={[styles(theme).smallBtn, styles(theme).deleteBtn]} disabled={manageBusy} onPress={() => confirmDelete(name, false)}>
                    <Text style={styles(theme).deleteBtnText}>{t.sportModesDeleteBtn}</Text>
                  </TouchableOpacity>
                )}
                <Icon name="chevronRight" size={20} color={theme.mutedText} />
              </View>
            </Card>
          </TouchableOpacity>
        );
      })}

      {/* Multisport modes group other modes and have no single-mode editor, so they aren't in
          `modes` (the reader) - append them here from the codec summary as their own rows. */}
      {summary && summary.modes.filter(m => m.multisport).map(m => {
        const activity = ACTIVITY_TYPES[m.activityId];
        return (
          <Card key={`multi-${m.order}-${m.name}`} style={{ width: '100%' }}>
            <View style={styles(theme).listRow}>
              <Icon name="activity" size={22} color={activity ? activity.color : theme.mutedText} />
              <View style={{ flex: 1, marginLeft: 10 }}>
                <Text style={styles(theme).cardTitle}>{m.name}</Text>
                <Text style={styles(theme).sectionDesc}>{m.legs.join(' → ')}</Text>
              </View>
              <Text style={styles(theme).multiBadge}>{t.sportModesMultiBadge}</Text>
              <TouchableOpacity style={[styles(theme).smallBtn, styles(theme).deleteBtn]} disabled={manageBusy} onPress={() => confirmDelete(m.name, true)}>
                <Text style={styles(theme).deleteBtnText}>{t.sportModesDeleteBtn}</Text>
              </TouchableOpacity>
            </View>
          </Card>
        );
      })}

      {/* ── Create / Multisport modal ── */}
      <Modal visible={createKind != null} animationType="slide" transparent onRequestClose={() => setCreateKind(null)}>
        <View style={styles(theme).modalOverlay}>
          <View style={styles(theme).modalBox}>
            <Text style={styles(theme).cardTitle}>
              {createKind === 'multi' ? t.sportModesCreateMultiTitle : t.sportModesCreateTitle}
            </Text>

            <Text style={styles(theme).label}>{t.sportModesNamePlaceholder}</Text>
            <TextInput
              style={styles(theme).input}
              value={createName}
              onChangeText={setCreateName}
              placeholder={t.sportModesNamePlaceholder}
              placeholderTextColor={theme.mutedText}
            />

            <Text style={styles(theme).label}>{t.sportModesActivityLabel}</Text>
            <ScrollView style={{ maxHeight: createKind === 'multi' ? 120 : 220 }}>
              <View style={styles(theme).chipRow}>
                {(createKind === 'multi' ? MULTISPORT_ACTIVITIES : CREATABLE_ACTIVITIES).map(a => (
                  <TouchableOpacity
                    key={a.id}
                    style={[styles(theme).chip, createActivityId === a.id && styles(theme).chipActive]}
                    onPress={() => setCreateActivityId(a.id)}
                  >
                    <Text style={[styles(theme).chipText, createActivityId === a.id && styles(theme).chipTextActive]}>{a.name}</Text>
                  </TouchableOpacity>
                ))}
              </View>
            </ScrollView>

            {createKind === 'multi' && (
              <>
                <Text style={styles(theme).label}>{t.sportModesLegsLabel}</Text>
                <Text style={styles(theme).sectionDesc}>{t.sportModesLegsHint}</Text>
                <Text style={[styles(theme).sectionDesc, { color: legs.length ? theme.primary : theme.mutedText }]}>
                  {legs.length ? t.sportModesLegsChosen(legs.join(' → ')) : t.sportModesNoLegsYet}
                </Text>
                <View style={styles(theme).chipRow}>
                  {(summary?.modes ?? []).filter(m => !m.multisport).map(m => (
                    <TouchableOpacity
                      key={m.name}
                      style={[styles(theme).chip]}
                      onPress={() => setLegs(prev => [...prev, m.name])}
                    >
                      <Text style={styles(theme).chipText}>＋ {m.name}</Text>
                    </TouchableOpacity>
                  ))}
                </View>
                {legs.length > 0 && (
                  <TouchableOpacity style={{ marginTop: 6 }} onPress={() => setLegs([])}>
                    <Text style={[styles(theme).sectionDesc, { color: theme.error }]}>✕ {t.cancel}</Text>
                  </TouchableOpacity>
                )}
              </>
            )}

            <View style={[styles(theme).row, { marginTop: 16, justifyContent: 'flex-end' }]}>
              <TouchableOpacity style={styles(theme).smallBtn} onPress={() => setCreateKind(null)}>
                <Text style={styles(theme).smallBtnText}>{t.cancel}</Text>
              </TouchableOpacity>
              <TouchableOpacity
                style={[styles(theme).smallBtn, { opacity: !createName.trim() || createActivityId == null || (createKind === 'multi' && legs.length < 2) ? 0.4 : 1 }]}
                disabled={!createName.trim() || createActivityId == null || (createKind === 'multi' && legs.length < 2)}
                onPress={submitCreate}
              >
                <Text style={styles(theme).smallBtnText}>{t.sportModesCreateConfirm}</Text>
              </TouchableOpacity>
            </View>
          </View>
        </View>
      </Modal>
    </ScrollView>
  );
}

const styles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  content: { padding: v3Spacing.medium, gap: v3Spacing.medium },
  backRow: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  backText: { color: t.text, fontSize: v3Type.bodyLarge, fontWeight: '600' },
  modeTitle: { fontSize: v3Type.largeTitle, fontWeight: '800', color: t.text, marginBottom: 4 },
  cardTitle: { fontSize: v3Type.heading, fontWeight: '700', color: t.text },
  sectionDesc: { fontSize: v3Type.body, color: t.mutedText, marginBottom: 6, lineHeight: 19 },
  label: { fontSize: v3Type.body, color: t.mutedText, marginTop: 12, marginBottom: 4 },
  input: {
    backgroundColor: t.background,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: t.mutedText + '33',
    paddingHorizontal: 12,
    paddingVertical: 10,
    color: t.text,
    fontSize: v3Type.bodyLarge,
  },
  row: { flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 4 },
  smallBtn: {
    paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8,
    backgroundColor: t.primary + '1F', borderWidth: 1, borderColor: t.primary,
    alignItems: 'center', justifyContent: 'center',
  },
  smallBtnText: { color: t.primary, fontWeight: '600', fontSize: v3Type.label },
  statusRow: { flexDirection: 'row', alignItems: 'center', marginTop: 10 },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, marginTop: 4 },
  chip: {
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8,
    borderWidth: 1, borderColor: t.mutedText + '33', backgroundColor: t.background,
  },
  chipActive: { borderColor: t.primary, backgroundColor: t.primary + '1F' },
  chipText: { color: t.mutedText, fontSize: v3Type.label },
  chipTextActive: { color: t.primary, fontWeight: '600' },
  filmLabel: { fontSize: v3Type.tiny, fontWeight: '700', marginTop: 4, textAlign: 'center' },
  fieldRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 6,
  },
  fieldText: { color: t.text, fontSize: v3Type.body, flex: 1, marginRight: 8 },
  listRow: { flexDirection: 'row', alignItems: 'center' },
  manageRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8, marginTop: 10,
    borderTopWidth: 1, borderTopColor: t.mutedText + '22', paddingTop: 10,
  },
  manageName: { fontSize: v3Type.bodyLarge, fontWeight: '600', color: t.text },
  manageSub: { fontSize: v3Type.label, color: t.mutedText, marginTop: 2 },
  multiBadge: {
    fontSize: v3Type.tiny, fontWeight: '700', color: t.primary,
    backgroundColor: t.primary + '1F', paddingHorizontal: 8, paddingVertical: 3, borderRadius: 999, overflow: 'hidden',
  },
  deleteBtn: { backgroundColor: t.error + '1A', borderColor: t.error },
  deleteBtnText: { color: t.error, fontWeight: '600', fontSize: v3Type.label },
  modalOverlay: { flex: 1, backgroundColor: '#00000066', justifyContent: 'flex-end' },
  modalBox: { backgroundColor: t.background, borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 20, maxHeight: '85%' },
  pickerRow: { paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: t.mutedText + '22' },
  pickerRowText: { color: t.text, fontSize: v3Type.bodyLarge },
});
