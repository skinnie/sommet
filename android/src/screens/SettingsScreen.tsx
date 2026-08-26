import React, { useCallback, useState } from 'react';
import {
  View, Text, TextInput, TouchableOpacity,
  StyleSheet, Alert, ScrollView, ActivityIndicator, Linking, Modal,
} from 'react-native';
import { useFocusEffect, useNavigation } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { RootStackParamList } from '../../App';
import { useThemeMode, ThemeMode } from '../theme/ThemeModeContext';
import { useExperimental } from '../config/ExperimentalContext';
// The experimental features, one toggle each (André, 2026-08-17). Screen names must match the
// RootStackParamList routes; icons/labels reuse the existing i18n strings.
const EXP_FEATURE_ROWS = [
  { flag: 'intervals' as const,   label: t.experimentalIntervals,   desc: t.experimentalIntervalsDesc,   screen: 'Intervals' as const,   icon: 'chart' as const },
  { flag: 'smartSensor' as const, label: t.experimentalSmartSensor, desc: t.experimentalSmartSensorDesc, screen: 'SmartSensor' as const, icon: 'link' as const },
  { flag: 'workoutCalendar' as const, label: t.experimentalWorkoutCalendar, desc: t.experimentalWorkoutCalendarDesc, screen: 'WorkoutCalendar' as const, icon: 'chart' as const },
];
import { isMarkSyncedEnabled, setMarkSyncedEnabled as persistMarkSynced } from '../services/MarkSynced';
import { useDemo } from '../config/DemoContext';
import { DemoDevicePicker } from '../components/ui/DemoDevicePicker';
import Icon, { IconName } from '../components/ui/Icon';
import { CREDITS } from '../legal/credits';
import { DecodedSetting, SettingField, SettingScreen } from '../services/AmbitSettingsReader';
import { readAmbitSettings, writeAmbitSetting } from '../services/AmbitSettingsService';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { EPHEMERIS_GPS_ONLY_KEY, GlonassStatus, getGlonassStatus } from '../services/SgeeService';
import type { WriteDevice } from '../services/AmbitSettingsWriter';
import {
  getRunalyzeApiKey, saveRunalyzeApiKey, removeRunalyzeApiKey,
} from '../services/ApiRunalyze';
import {
  getIntervalsIcuCredentials, saveIntervalsIcuCredentials, removeIntervalsIcuCredentials,
} from '../services/ApiIntervalsIcu';
import { setAnthropicKey, clearAnthropicKey, hasAnthropicKey } from '../services/CoachChat';
import { isEmberUnlocked, setEmberUnlocked } from '../services/EmberUnlock';
import { CoordinatePicker } from '../components/CoordinatePicker';
// Gear <-> intervals.icu import/sync lives here (in the intervals.icu connection), not on the
// Gear screen (André, 2026-08-18: "that options regarding intervals.icu should be on settings,
// when you connect to intervals.icu"). The Gear screen just shows the gear now.
import { importFromIntervals, runGearMirror, resolveConflict } from '../services/GearMirrorService';
import { GearConflict } from '../services/GearDiff';
import { importActivitiesFromIntervals } from '../services/IntervalsImport';
import {
  isAuthenticated as stravaIsAuth, getAuthorizationUrl as stravaAuthUrl, logout as stravaLogout,
} from '../services/ApiStrava';
import {
  getMapProvider, setMapProvider, MapProvider, MAP_PROVIDER_LABELS,
} from '../services/MapProviderService';
import { detectAttachedDeviceType, isBleTransportActive } from '../native/AmbitUsbModule';
import { getTileCacheSizeBytes, clearTileCache } from '../services/TileCache';
import { t } from '../i18n';
import { APP_VERSION } from '../config/version';
import { useV3Theme } from '../theme/v3';
import { Button, Chip, Dropdown, FieldRow, IconBadge, StatusLine, Toggle } from '../components/ui/primitives';

// Real, 2026-08-09 ("no button to change provider, nor in the settings like the desktop
// version") - same 3 real choices MapScreen.tsx/TrackMapScreen.tsx's own in-map layer
// control offers, same MAP_PROVIDER_LABELS names, with this screen's own localized
// descriptive suffix (matching desktop SettingsPage.qml's "(standard)"/"(cycling-focused)"
// RadioButton text).
const MAP_PROVIDER_OPTIONS: { provider: MapProvider; label: () => string }[] = [
  { provider: 'ign',     label: () => t.mapProviderIgnLabel },
  { provider: 'osm',     label: () => t.mapProviderOsmLabel },
  { provider: 'cyclosm', label: () => t.mapProviderCyclosmLabel },
];

function formatCacheSize(bytes: number): string {
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}

const THEME_OPTIONS: { mode: ThemeMode; icon: IconName; label: () => string }[] = [
  { mode: 'light',  icon: 'sun',  label: () => t.themeLight },
  { mode: 'dark',   icon: 'moon', label: () => t.themeDark },
  { mode: 'system', icon: 'auto', label: () => t.themeSystem },
];

// SuuntoLink groups watch settings into named screens; desktop (SettingsPage.qml) shows the
// same four section headers in this order. Ported here 2026-08-16 so the two apps group
// settings identically instead of Android showing one flat list. A row with no `screen`
// (Kailash, no display metadata) falls into "Other".
const SETTINGS_SCREEN_ORDER: SettingScreen[] = ['general', 'units', 'personal', 'other'];
const SETTINGS_SCREEN_TITLE: Record<SettingScreen, string> = {
  general:  'General settings',
  units:    'Unit settings',
  personal: 'Personal settings',
  other:    'Other',
};
const settingScreenOf = (row: DecodedSetting): SettingScreen => row.screen ?? 'other';
const settingScreenRank = (s: SettingScreen): number => {
  const i = SETTINGS_SCREEN_ORDER.indexOf(s);
  return i < 0 ? 99 : i;
};

// Static, non-editable display of a settings value — used for the read-only Ambit 1/2 rows.
function readOnlyValue(row: DecodedSetting): string {
  if (row.kind === 'enum') return row.choices?.find(c => c.value === row.value)?.label ?? String(row.value);
  if (row.kind === 'bool') return row.value === 1 ? 'On' : 'Off';
  if (row.kind === 'coord') return row.value.toFixed(6);
  return String(row.value);
}

export default function SettingsScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);
  const navigation = useNavigation<NativeStackNavigationProp<RootStackParamList>>();
  const { features, setFeature, anyEnabled } = useExperimental();
  const demo = useDemo();
  const [demoPickerOpen, setDemoPickerOpen] = useState(false);
  const { mode, setMode } = useThemeMode();

  const [runalyzeKey, setRunalyzeKey]     = useState('');
  const [savedKey, setSavedKey]           = useState<string | null>(null);
  const [saving, setSaving]               = useState(false);
  const [stravaAuth, setStravaAuth]       = useState(false);

  const [intervalsAthleteId, setIntervalsAthleteId] = useState('');
  const [intervalsApiKey, setIntervalsApiKey]       = useState('');
  const [intervalsSaved, setIntervalsSaved]         = useState(false);
  // Coach chat key (2026-08-26, desktop parity). The user's OWN Anthropic key - never bundled,
  // and NOT a claude.ai subscription, which is the thing people try first and that cannot work.
  const [anthropicKey, setAnthropicKeyInput]        = useState('');
  const [anthropicSaved, setAnthropicSaved]         = useState(false);
  const [savingAnthropic, setSavingAnthropic]       = useState(false);
  // Ember easter egg (2026-08-26, desktop parity): 10 taps on the version label reveal it.
  const [emberTaps, setEmberTaps]                   = useState(0);
  const [emberOn, setEmberOn]                       = useState(false);
  // Which coordinate row (if any) is currently being picked on a map - desktop parity with
  // WatchSettingsPage's "Pick on a map". null = picker closed.
  const [coordPickKey, setCoordPickKey]             = useState<string | null>(null);
  const [savingIntervals, setSavingIntervals]       = useState(false);
  const [gearImporting, setGearImporting]           = useState(false);
  const [gearSyncing, setGearSyncing]               = useState(false);
  const [actsImporting, setActsImporting]           = useState(false);

  // v3.0 UI port (2026-08-09, "settings was completely reworked in our desktop app...
  // proceed") - desktop's real Connections card (SettingsPage.qml) is one compact card with
  // a status-dot row per service, tap-to-open a Dialog with that service's own form - not
  // four separate always-expanded full-height sections like this screen had. Same real
  // handlers/state above, this only changes which one is visible at a time.
  const [openConnection, setOpenConnection] = useState<
    'strava' | 'runalyze' | 'intervals' | null
  >(null);

  const [mapProvider, setMapProviderState] = useState<MapProvider>('ign');

  // Experimental "mark synced workouts as synced" toggle - own persisted flag (MarkSynced.ts),
  // independent of the master experimental switch, default OFF.
  const [markSyncedEnabled, setMarkSyncedEnabledState] = useState(false);

  const [tileCacheBytes, setTileCacheBytes] = useState<number | null>(null);
  const [clearingCache, setClearingCache] = useState(false);

  useFocusEffect(useCallback(() => {
    getTileCacheSizeBytes().then(setTileCacheBytes).catch(() => setTileCacheBytes(0));
  }, []));

  async function handleClearTileCache() {
    setClearingCache(true);
    try {
      await clearTileCache();
      setTileCacheBytes(0);
    } finally {
      setClearingCache(false);
    }
  }

  // Real, 2026-08-08 ("Settings on ambit 3 - if they are already cracked to be changed by
  // cable, we will need to build a UI for it"). Not auto-loaded on focus like the API
  // credentials above - reading needs the watch actually connected via USB, which isn't
  // guaranteed just because this screen is open, so it's behind an explicit "Read
  // Settings" tap instead (same "explicit action, no surprise USB traffic" spirit as the
  // rest of this app's own on-demand connect/read/disconnect flows - see PoiService.ts).
  // Real, 2026-08-10 ("watch settings, hide it when garmin is plugged") - the AmbitSettings
  // cable protocol below is Suunto-only; a real Garmin connected live surfaced that this
  // card was showing (and its "Read Settings" tap would just fail) with a Garmin plugged
  // in. Re-checked on every focus, same on-demand pattern as the rest of this app (no
  // context/prop-drilling from Home - this screen queries what it needs itself).
  const [isGarminAttached, setIsGarminAttached] = useState(false);
  useFocusEffect(useCallback(() => {
    detectAttachedDeviceType().then(t => {
      setIsGarminAttached(t === 'garmin');
      // Auto-read the watch's settings the moment one is connected (USB or BLE) - no manual
      // "Read settings" tap (André, 2026-08-18: automatic on connect, either transport, any
      // watch, both platforms - the desktop already auto-reads on page load).
      if (t === 'ambit' || isBleTransportActive()) handleReadAmbitSettings();
    }).catch(() => {
      if (isBleTransportActive()) handleReadAmbitSettings();
    });
    AsyncStorage.getItem(EPHEMERIS_GPS_ONLY_KEY).then(v => setEphemerisGpsOnly(v === 'true')).catch(() => {});
  }, []));

  const [ambitSettings, setAmbitSettings] = useState<DecodedSetting[] | null>(null);
  const [ambitSettingsFields, setAmbitSettingsFields] = useState<SettingField[] | null>(null);
  // The connected watch's own name (from getDeviceInfo()'s device list), used to label
  // this section adaptively — "Suunto Kailash Settings" etc. — instead of a hardcoded
  // "Ambit3". Empty until the settings read connects and identifies the watch.
  const [ambitDeviceName, setAmbitDeviceName] = useState<string>('');
  // Ambit 1/2 family: settings are read-only (no write in libambit), so the rows render
  // their value statically and the write controls are hidden.
  const [ambitReadOnly, setAmbitReadOnly] = useState(false);
  const [ambitWriteDevice, setAmbitWriteDevice] = useState<WriteDevice | undefined>();
  // GLONASS orbital data - desktop parity (WatchSettingsPage's "Orbital data" group). Shown
  // only when the connected watch declares a GlonassSGEE region (Traverse/Kailash, not the
  // Ambit3 Peak/Sport), answered by the watch itself via readAmbitSettings' same connection.
  const [ambitGlonass, setAmbitGlonass] = useState<GlonassStatus | undefined>();
  const [ephemerisGpsOnly, setEphemerisGpsOnly] = useState(false);
  const [orbitalInfoOpen, setOrbitalInfoOpen] = useState(false);
  const [ambitSettingsPhase, setAmbitSettingsPhase] =
    useState<'idle' | 'connecting' | 'reading' | 'done' | 'error'>('idle');
  const [ambitSettingsError, setAmbitSettingsError] = useState<string | undefined>();
  const [writingKey, setWritingKey] = useState<string | null>(null);
  // Free-text edit state for 'coord' rows (home_latitude/home_longitude) - a plain
  // TextInput needs its own string buffer, separate from the decoded numeric row.value,
  // the same pattern SportModesScreen.tsx already uses for its own numeric fields.
  const [coordEdits, setCoordEdits] = useState<Record<string, string>>({});
  // Free-text edit buffer for the Personal numeric fields (height/weight/HR) - keyed by key,
  // the same shape as coordEdits.
  const [numEdits, setNumEdits] = useState<Record<string, string>>({});

  async function handleReadAmbitSettings() {
    await readAmbitSettings(s => {
      setAmbitSettingsPhase(s.phase);
      if (s.settings) {
        setAmbitSettings(s.settings);
        const coords: Record<string, string> = {};
        for (const row of s.settings) {
          if (row.kind === 'coord') coords[row.key] = row.value.toFixed(6);
        }
        setCoordEdits(coords);
      }
      if (s.fields) setAmbitSettingsFields(s.fields);
      if (s.deviceName) setAmbitDeviceName(s.deviceName);
      setAmbitWriteDevice(s.writeDevice);
      setAmbitReadOnly(!!s.readOnly);
      setAmbitSettingsError(s.error);
    });
    // GLONASS support is read in its OWN isolated connection AFTER the settings read has fully
    // finished (and, over USB, disconnected) - never sharing that link, so the extra 0x0b21 map
    // read can't desync the settings read on watches where it isn't hardware-confirmed (the
    // Ambit3 Peak). A watch without a GlonassSGEE region just reports unsupported and no group
    // shows. Best-effort: its failure never affects the settings already displayed.
    try { setAmbitGlonass(await getGlonassStatus()); } catch { /* group stays hidden */ }
  }

  async function handleEphemerisGpsOnly(v: boolean) {
    setEphemerisGpsOnly(v);
    await AsyncStorage.setItem(EPHEMERIS_GPS_ONLY_KEY, v ? 'true' : 'false');
  }

  async function handleWriteAmbitSetting(key: string, value: number) {
    if (!ambitSettingsFields || !ambitWriteDevice) return;
    setWritingKey(key);
    await writeAmbitSetting(key, value, ambitSettingsFields, ambitWriteDevice, s => {
      if (s.phase === 'done' || s.phase === 'error') {
        setWritingKey(null);
        if (s.error) Alert.alert(t.error, s.error);
      }
      if (s.result) {
        // Reflect the watch's own confirmed value, not blindly what was requested -
        // matches AmbitSettingsWriter.ts's own "prove it" contract.
        setAmbitSettings(prev => prev && prev.map(row =>
          row.key === key && s.result!.confirmedValue !== null
            ? { ...row, value: s.result!.confirmedValue as number }
            : row));
        if (s.result.confirmedValue !== null) {
          setCoordEdits(prev => ({ ...prev, [key]: (s.result!.confirmedValue as number).toFixed(6) }));
        }
      }
    });
  }

  // Real, hardware-independent range check, same bounds AmbitSettingsWriter.ts's own
  // writeSetting() (and settings_write.py's write_one() on the desktop side) enforce
  // again before ever sending a byte - this is just the earliest, UI-level catch for an
  // obviously-invalid typed value.
  function handleSetCoord(key: string) {
    const parsed = parseFloat(coordEdits[key] ?? '');
    if (!Number.isFinite(parsed)) {
      Alert.alert(t.error, `${key}: not a valid number`);
      return;
    }
    if (key === 'home_latitude' && (parsed < -90 || parsed > 90)) {
      Alert.alert(t.error, `${key}=${parsed} out of range [-90, 90]`);
      return;
    }
    if (key === 'home_longitude' && (parsed < -180 || parsed > 180)) {
      Alert.alert(t.error, `${key}=${parsed} out of range [-180, 180]`);
      return;
    }
    handleWriteAmbitSetting(key, parsed);
  }

  // Personal numeric write (Height/Weight/Max HR/Rest HR). Validated against the field's own
  // display range - the same bounds SuuntoLink's UI enforces (see AmbitSettingsReader ranges)
  // - so an out-of-range value is refused here rather than sent to the watch.
  function handleSetNumber(row: DecodedSetting) {
    const parsed = parseFloat(numEdits[row.key] ?? '');
    if (!Number.isFinite(parsed)) {
      Alert.alert(t.error, `${row.label ?? row.key}: not a valid number`);
      return;
    }
    if ((row.min !== undefined && parsed < row.min) || (row.max !== undefined && parsed > row.max)) {
      Alert.alert(t.error, `${row.label ?? row.key} = ${parsed} out of range [${row.min}, ${row.max}]`);
      return;
    }
    handleWriteAmbitSetting(row.key, parsed);
  }

  useFocusEffect(useCallback(() => {
    getRunalyzeApiKey().then(k => {
      setSavedKey(k);
      setRunalyzeKey(k ?? '');
    });
    stravaIsAuth().then(setStravaAuth);
    getIntervalsIcuCredentials().then(creds => {
      setIntervalsSaved(!!creds);
      setIntervalsAthleteId(creds?.athleteId ?? '');
      setIntervalsApiKey(creds?.apiKey ?? '');
    });
    // Reflect whether a Coach key is already stored (the key itself is never read back into
    // the field - only whether one exists).
    hasAnthropicKey().then(setAnthropicSaved);
    isEmberUnlocked().then(setEmberOn);
    getMapProvider().then(setMapProviderState);
    isMarkSyncedEnabled().then(setMarkSyncedEnabledState);
  }, []));

  function handleToggleMarkSynced(v: boolean) {
    setMarkSyncedEnabledState(v);
    persistMarkSynced(v);
  }

  function handleSetMapProvider(p: MapProvider) {
    setMapProviderState(p);
    setMapProvider(p);
  }

  async function handleSaveRunalyze() {
    if (!runalyzeKey.trim()) {
      Alert.alert(t.emptyKey, t.emptyKeyMsg);
      return;
    }
    setSaving(true);
    try {
      await saveRunalyzeApiKey(runalyzeKey.trim());
      setSavedKey(runalyzeKey.trim());
      Alert.alert(t.savedOk, t.keySaved);
    } finally {
      setSaving(false);
    }
  }

  async function handleRemoveRunalyze() {
    await removeRunalyzeApiKey();
    setSavedKey(null);
    setRunalyzeKey('');
    Alert.alert(t.deleted, t.keyDeleted);
  }

  async function handleSaveIntervals() {
    if (!intervalsAthleteId.trim() || !intervalsApiKey.trim()) {
      Alert.alert(t.emptyCreds, t.emptyCredsMsg);
      return;
    }
    setSavingIntervals(true);
    try {
      await saveIntervalsIcuCredentials(intervalsAthleteId.trim(), intervalsApiKey.trim());
      setIntervalsSaved(true);
      Alert.alert(t.savedOk, t.credsSaved);
    } finally {
      setSavingIntervals(false);
    }
  }

  async function handleSaveAnthropic() {
    setSavingAnthropic(true);
    try {
      await setAnthropicKey(anthropicKey);
      setAnthropicSaved(true);
      setAnthropicKeyInput('');       // don't keep the secret in component state after saving
    } finally {
      setSavingAnthropic(false);
    }
  }

  async function handleRemoveAnthropic() {
    await clearAnthropicKey();
    setAnthropicSaved(false);
    setAnthropicKeyInput('');
  }

  async function handleRemoveIntervals() {
    await removeIntervalsIcuCredentials();
    setIntervalsSaved(false);
    setIntervalsAthleteId('');
    setIntervalsApiKey('');
    Alert.alert(t.deleted, t.credsDeleted);
  }

  // ── Gear <-> intervals.icu (moved here from the Gear screen). Import (pull-only) is the
  // primary path; two-way Sync is secondary and stops on a real two-sided edit to ask. ──
  async function handleGearImport() {
    setGearImporting(true);
    try {
      const n = await importFromIntervals();
      Alert.alert(t.gearScreenTitle, t.gearImportDone(n));
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? String(e));
    } finally {
      setGearImporting(false);
    }
  }

  async function handleGearSync() {
    setGearSyncing(true);
    try {
      const res = await runGearMirror();
      if (res.conflicts.length > 0) await resolveGearConflictsSequentially(res.conflicts);
      else Alert.alert(t.gearScreenTitle, t.gearSyncDone(res.pulled, res.pushed));
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? String(e));
    } finally {
      setGearSyncing(false);
    }
  }

  function resolveGearConflictsSequentially(conflicts: GearConflict[]): Promise<void> {
    return new Promise(resolve => {
      let i = 0;
      const next = () => {
        if (i >= conflicts.length) { resolve(); return; }
        const c = conflicts[i++];
        Alert.alert(
          t.gearConflictTitle,
          t.gearConflictBody(c.local.name || c.remote?.name || ''),
          [
            { text: t.gearConflictKeepLocal, onPress: async () => { await resolveConflict(c, 'local'); next(); } },
            { text: t.gearConflictKeepRemote, onPress: async () => { await resolveConflict(c, 'remote'); next(); } },
          ],
          { cancelable: false },
        );
      };
      next();
    });
  }

  // Import ALL activities from intervals.icu into the local DB (André, 2026-08-18). Pull-only;
  // idempotent (dedups against what's already here + watch moves). Lives in the intervals.icu
  // connection, like the gear import.
  async function handleImportActivities() {
    setActsImporting(true);
    try {
      const r = await importActivitiesFromIntervals();
      Alert.alert(t.intervalsSection,
        `Imported ${r.imported} activit${r.imported === 1 ? 'y' : 'ies'} from intervals.icu.`);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? String(e));
    } finally {
      setActsImporting(false);
    }
  }

  async function handleStravaConnect() {
    try {
      const url = stravaAuthUrl();
      await Linking.openURL(url);
    } catch (e: any) {
      Alert.alert(t.stravaError, e?.message ?? String(e));
    }
  }

  async function handleStravaDisconnect() {
    await stravaLogout();
    setStravaAuth(false);
    Alert.alert('Strava', t.stravaDisconnected);
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>

      {/* ── Apparence ── */}
      <View style={styles.section}>
        <View style={styles.cardHead}>
          <IconBadge icon={mode === 'light' ? 'sun' : mode === 'dark' ? 'moon' : 'auto'} />
          <Text style={styles.cardTitle}>{t.appearanceSection}</Text>
        </View>
        <Text style={styles.sectionDesc}>{t.appearanceDesc}</Text>
        <View style={styles.themeRow}>
          {THEME_OPTIONS.map(opt => {
            const selected = mode === opt.mode;
            return (
              <TouchableOpacity
                key={opt.mode}
                onPress={() => setMode(opt.mode)}
                activeOpacity={0.75}
                style={[styles.themeOption, selected && styles.themeOptionSelected]}
              >
                <Icon name={opt.icon} size={17} color={selected ? theme.card : theme.text} />
                <Text style={[styles.themeOptionLabel, selected && styles.themeOptionLabelSelected]}>
                  {opt.label()}
                </Text>
              </TouchableOpacity>
            );
          })}
        </View>
      </View>

      {/* The map/list view choice moved out of Settings and onto each list screen itself
          (Activities/Routes/POIs) - André 2026-08-16. Still persisted via ListViewPrefs. */}

      {/* ── Testing mode (2026-08-16, ported from desktop): pretend a device is connected so
          the app can be explored without one. Toggle + a picker of the watches the app knows
          (Ambit3 family / Traverse / Kailash). Default OFF. ── */}
      <View style={styles.section}>
        <View style={styles.cardHead}>
          <IconBadge icon="watch" />
          <Text style={styles.cardTitle}>{t.testingSection}</Text>
        </View>
        <Text style={styles.sectionDesc}>{t.testingDesc}</Text>
        <View style={[styles.row, { justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }]}>
          <Text style={[styles.connRowText, { flex: 1, marginRight: 12 }]}>
            {demo.enabled ? t.testingOnShowing(demo.device.name) : ' '}
          </Text>
          <Toggle value={demo.enabled} onValueChange={demo.setEnabled} />
        </View>
        {demo.enabled && (
          <View style={{ marginTop: 10 }}>
            <Button label={t.testingChange} onPress={() => setDemoPickerOpen(true)} variant="outline" />
          </View>
        )}
      </View>
      <DemoDevicePicker
        visible={demoPickerOpen}
        currentVariant={demo.variant}
        onPick={demo.setVariant}
        onClose={() => setDemoPickerOpen(false)}
      />

      {/* ── Experimental (2026-08-14, André: "enable it with a toggle on experimental") ──
          One toggle gates the three unproven, cable-tested, community-feedback features
          (App-Zone install, Intervals builder, Smart Sensor). Default OFF so nobody who
          never opens this is exposed to an unproven flash write. ── */}
      <View style={styles.section}>
        <View style={styles.cardHead}>
          <IconBadge icon="warning" />
          <Text style={styles.cardTitle}>{t.experimentalSection}</Text>
        </View>
        <Text style={styles.sectionDesc}>{t.experimentalToggleDesc}</Text>

        {/* One toggle per experimental feature (André, 2026-08-17): toggle on = enabled, and the
            row is then tappable to open that feature's screen. */}
        {EXP_FEATURE_ROWS.map(f => {
          const on = features[f.flag];
          return (
            <TouchableOpacity
              key={f.flag}
              style={[styles.row, { alignItems: 'center', marginTop: 10 }]}
              activeOpacity={on ? 0.7 : 1}
              onPress={() => { if (on) navigation.navigate(f.screen); }}
            >
              <Icon name={f.icon} size={18} color={on ? theme.text : theme.mutedText} />
              <View style={{ flex: 1, marginHorizontal: 10 }}>
                <Text style={[styles.connRowText, { color: on ? theme.text : theme.mutedText }]}>{f.label}</Text>
                <Text style={styles.sectionDesc}>{f.desc}</Text>
              </View>
              {on && <View style={{ marginRight: 6 }}><Icon name="chevronRight" size={18} color={theme.mutedText} /></View>}
              {/* Enabling a feature opens it right away (André, 2026-08-17: App Zone should jump
                  straight to the Suunto catalog). The row stays tappable to reopen it later. */}
              <Toggle value={on} onValueChange={v => { setFeature(f.flag, v); if (v) navigation.navigate(f.screen); }} />
            </TouchableOpacity>
          );
        })}
        {anyEnabled && (
          <Text style={[styles.sectionDesc, { color: theme.warning, marginTop: 10 }]}>{t.experimentalWarningBanner}</Text>
        )}

        {/* Mark-synced write-back - independent opt-in, not gated by the master toggle
            (2026-08-16). Writes the watch's own per-move synced flag so the Suunto app /
            SuuntoLink don't duplicate; tradeoff spelled out so nobody enables it blindly. */}
        <View style={[styles.row, { justifyContent: 'space-between', alignItems: 'center', marginTop: 8 }]}>
          <Text style={[styles.connRowText, { flex: 1, marginRight: 12 }]}>{t.markSyncedLabel}</Text>
          <Toggle
            value={markSyncedEnabled}
            onValueChange={handleToggleMarkSynced}
          />
        </View>
        <Text style={styles.sectionDesc}>{t.markSyncedDesc}</Text>

      </View>

      {/* ── Watch Settings - real, 2026-08-08. Cable settings-write is confirmed working
          for both device types (see AmbitSettingsWriter.ts's own header comment: André
          confirmed on each watch's own screen that flipping display_dark visibly switched
          it Light -> Dark) - readAmbitSettings() detects which one is connected and picks
          the matching curated table (AmbitSettingsService.ts's own header comment), so
          this section works unmodified for either. "Both device types" means Ambit/
          Traverse/Kailash, though - a real Garmin connected live (2026-08-10) surfaced
          that this card doesn't apply to Garmin at all (no AmbitSettings protocol there),
          so it's hidden while one's attached rather than shown with a "Read Settings"
          button that would just fail. ── */}
      {!isGarminAttached && (
      <View style={styles.section}>
        <View style={styles.cardHead}>
          <IconBadge icon="watch" />
          <Text style={styles.cardTitle}>Firmware</Text>
        </View>
        <Text style={styles.sectionDesc}>
          Flash watch firmware over USB. Advanced and irreversible — intended for a supervised
          session. Opens the firmware tool.
        </Text>
        <Button label="Open firmware tool" icon="upload" variant="outline"
          onPress={() => navigation.navigate('Firmware')} style={{ marginTop: 10 }} />
      </View>
      )}

      {!isGarminAttached && (
      <View style={styles.section}>
        <View style={styles.cardHead}>
          <IconBadge icon="watch" />
          <Text style={styles.cardTitle}>
            {ambitDeviceName ? t.ambitSettingsTitle(ambitDeviceName) : t.ambitSettingsSection}
          </Text>
        </View>
        <Text style={styles.sectionDesc}>{t.ambitSettingsDesc}</Text>
        {ambitReadOnly && <Text style={styles.sectionDesc}>{t.ambitSettingsReadOnly}</Text>}


        {(ambitSettingsPhase === 'connecting' || ambitSettingsPhase === 'reading') && (
          <View style={styles.statusRow}>
            <ActivityIndicator size="small" color={theme.text} />
            <Text style={[styles.sectionDesc, { marginLeft: 8, marginBottom: 0 }]}>
              {ambitSettingsPhase === 'connecting' ? t.connecting : t.ambitSettingsReading}
            </Text>
          </View>
        )}

        {ambitSettingsPhase === 'error' && ambitSettingsError && !ambitSettings && (
          <Text style={[styles.sectionDesc, { color: theme.error, marginTop: 10 }]}>
            {ambitSettingsError}
          </Text>
        )}

        {/* Orbital data (GLONASS) - shown only when the watch declares a GlonassSGEE region
            (Traverse/Kailash, not the Ambit3 Peak/Sport), read in the same connection above.
            Desktop parity: WatchSettingsPage's "Orbital data" group with the "Ephemeris GPS
            only" switch + a tap-to-expand "i". The write itself rides the Home orbital update. */}
        {ambitGlonass?.supported && (
          <>
            <Text style={styles.settingsGroupTitle}>{t.orbitalDataTitle}</Text>
            <View style={styles.ambitSettingRow}>
              <View style={{ flexDirection: 'row', alignItems: 'center', flex: 1, marginRight: 10 }}>
                <Text style={[styles.ambitSettingLabel, { flex: 0, marginRight: 8 }]}>{t.ephemerisGpsOnly}</Text>
                <TouchableOpacity
                  onPress={() => setOrbitalInfoOpen(o => !o)}
                  hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}
                  style={{
                    width: 18, height: 18, borderRadius: 9, borderWidth: 1,
                    alignItems: 'center', justifyContent: 'center',
                    borderColor: orbitalInfoOpen ? theme.primary : theme.mutedText,
                  }}
                >
                  <Text style={{ fontSize: 11, fontWeight: '700', color: orbitalInfoOpen ? theme.primary : theme.mutedText }}>i</Text>
                </TouchableOpacity>
              </View>
              <Toggle value={ephemerisGpsOnly} onValueChange={handleEphemerisGpsOnly} />
            </View>
            {orbitalInfoOpen && (
              <Text style={[styles.sectionDesc, { marginTop: 4 }]}>{t.ephemerisGpsOnlyInfo}</Text>
            )}
          </>
        )}

        {ambitSettings && ambitSettings
          // Group by SuuntoLink settings screen (General/Units/Personal/Other), preserving
          // the table's own order within each group - a stable sort with an index tiebreak.
          .map((row, i) => ({ row, i }))
          .sort((a, b) =>
            settingScreenRank(settingScreenOf(a.row)) - settingScreenRank(settingScreenOf(b.row)) || a.i - b.i)
          .map(({ row }, idx, arr) => {
          // SuuntoLink's own field name (desktop renders the same `label`); the title-cased
          // key is only the fallback for a device with no display metadata (Kailash).
          const label = row.label ?? row.key.split('_')
            .map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
            .join(' ');
          const busy = writingKey === row.key;
          const showHeader = idx === 0 || settingScreenOf(arr[idx - 1].row) !== settingScreenOf(row);
          return (
            <React.Fragment key={row.key}>
              {showHeader && (
                <Text style={styles.settingsGroupTitle}>{SETTINGS_SCREEN_TITLE[settingScreenOf(row)]}</Text>
              )}
            <View style={styles.ambitSettingRow}>
              <Text style={styles.ambitSettingLabel}>{label}</Text>

              {ambitReadOnly && (
                <Text style={styles.ambitSettingValueRO}>{readOnlyValue(row)}</Text>
              )}

              {!ambitReadOnly && (<>
              {row.kind === 'bool' && (
                <Toggle
                  value={row.value === 1}
                  onValueChange={v => handleWriteAmbitSetting(row.key, v ? 1 : 0)}
                  disabled={busy}
                />
              )}

              {/* Every enum setting is a compact dropdown (André, 2026-08-16: the chip rows
                  "aren't dropdown menus like in the desktop, which puts it cluttered"). A unit
                  field the watch owns under Metric/Imperial is shown but disabled (row.locked). */}
              {row.kind === 'enum' && (
                <Dropdown
                  value={row.value}
                  choices={row.choices ?? []}
                  disabled={busy || row.locked}
                  onSelect={v => handleWriteAmbitSetting(row.key, v)}
                />
              )}

              {/* Activity class: a labelled dropdown of SuuntoLink's own values, matching
                  desktop (kind 'number' but control 'dropdown'). */}
              {row.kind === 'number' && row.control === 'dropdown' && (
                <Dropdown
                  value={row.value}
                  choices={row.choices ?? []}
                  disabled={busy}
                  onSelect={v => handleWriteAmbitSetting(row.key, v)}
                />
              )}

              {/* Personal numerics (Height/Weight/Max HR/Rest HR): a free-text field + Save,
                  range-checked in handleSetNumber. A +-5 stepper made no sense at these
                  ranges (30-250 kg, 30-240 bpm) and couldn't do Weight's 0.1 kg step. */}
              {/* Free-text numeric editors: Personal numerics (Height/Weight/Max HR/Rest HR),
                  Birth year (kind 'year'), and Compass declination (degrees, can be negative -
                  so its keyboard isn't digit-only). All range-checked in handleSetNumber. */}
              {((row.kind === 'number' && (row.control === 'number' || row.control === 'declination'))
                || row.kind === 'year') && (
                <View style={styles.coordRow}>
                  <TextInput
                    style={styles.coordInput}
                    value={numEdits[row.key] ?? String(row.value)}
                    onChangeText={v => setNumEdits(prev => ({ ...prev, [row.key]: v }))}
                    editable={!busy}
                    keyboardType={(row.min ?? 0) < 0 ? 'default' : 'numeric'}
                    placeholderTextColor={theme.mutedText}
                  />
                  {!!row.unit && <Text style={styles.ambitSettingValueRO}>{row.unit}</Text>}
                  <TouchableOpacity style={styles.coordSetBtn} disabled={busy} onPress={() => handleSetNumber(row)}>
                    <Text style={styles.btnText}>{t.saveBtn}</Text>
                  </TouchableOpacity>
                </View>
              )}

              {/* Backlight brightness (control 'slider' - no RN slider, kept as a +-step
                  stepper). Uses the field's own min/max/step when present, else 0..100 by 5. */}
              {row.kind === 'number' && row.control !== 'dropdown'
                && row.control !== 'number' && row.control !== 'declination' && (
                <View style={styles.stepperRow}>
                  <TouchableOpacity
                    style={styles.stepperBtn}
                    disabled={busy}
                    onPress={() => handleWriteAmbitSetting(row.key, Math.max(row.min ?? 0, row.value - (row.step ?? 5)))}
                  >
                    <Text style={styles.stepperBtnText}>-</Text>
                  </TouchableOpacity>
                  <Text style={styles.stepperValue}>{row.value}{row.unit ? ` ${row.unit}` : ''}</Text>
                  <TouchableOpacity
                    style={styles.stepperBtn}
                    disabled={busy}
                    onPress={() => handleWriteAmbitSetting(row.key, Math.min(row.max ?? 100, row.value + (row.step ?? 5)))}
                  >
                    <Text style={styles.stepperBtnText}>+</Text>
                  </TouchableOpacity>
                </View>
              )}

              {/* Real, 2026-08-08, Kailash only (home_latitude/home_longitude) - found
                  from real BLE captures, confirmed byte-exact against the watch's own
                  real schema descriptor (entry 0x36, GROUP HomeLocation.Latitude/
                  Longitude - see AmbitSettingsReader.ts's own field comment and the
                  ambit_app_kailash_home_location_field memory). Free-text degrees input
                  rather than a stepper (unlike 'number' above) - a +-5 nudge makes no
                  sense for a GPS coordinate, and it needs to accept a leading "-". */}
              {row.kind === 'coord' && (
                <View style={styles.coordRow}>
                  <TextInput
                    style={styles.coordInput}
                    value={coordEdits[row.key] ?? row.value.toFixed(6)}
                    onChangeText={v => setCoordEdits(prev => ({ ...prev, [row.key]: v }))}
                    editable={!busy}
                    placeholderTextColor={theme.mutedText}
                  />
                  <TouchableOpacity
                    style={styles.coordSetBtn}
                    disabled={busy}
                    onPress={() => handleSetCoord(row.key)}
                  >
                    <Text style={styles.btnText}>{t.saveBtn}</Text>
                  </TouchableOpacity>
                  {/* Pick on a map - desktop parity (WatchSettingsPage's own button). Pointing
                      at a coordinate beats typing six decimal places by hand. */}
                  <TouchableOpacity
                    style={styles.coordSetBtn}
                    disabled={busy}
                    onPress={() => setCoordPickKey(row.key)}
                  >
                    <Text style={styles.btnText}>Map</Text>
                  </TouchableOpacity>
                </View>
              )}
              </>)}

              {busy && <ActivityIndicator size="small" color={theme.primary} style={{ marginLeft: 8 }} />}
            </View>
            </React.Fragment>
          );
        })}

      </View>
      )}

      {/* Map coordinate picker (desktop parity: WatchSettingsPage's "Pick on a map").
          home_latitude and home_longitude are two SEPARATE setting rows, but a point on a map
          is inherently both - so a pick fills BOTH edit buffers. The write itself still needs
          an explicit Save per row, keeping this app's "explicit tap for any write" rule rather
          than silently pushing two values to the watch from a map tap. */}
      <CoordinatePicker
        visible={coordPickKey !== null}
        initialLat={parseFloat(coordEdits.home_latitude ?? '') || 0}
        initialLon={parseFloat(coordEdits.home_longitude ?? '') || 0}
        onCancel={() => setCoordPickKey(null)}
        onPick={(la, lo) => {
          setCoordEdits(prev => ({
            ...prev,
            home_latitude: la.toFixed(6),
            home_longitude: lo.toFixed(6),
          }));
          setCoordPickKey(null);
        }}
      />

      {/* ── Connections - real, 2026-08-09 ("settings was completely reworked in our
          desktop app... proceed"). Ports SettingsPage.qml's real Connections card: one
          compact card, a status-dot row per service, tap opens that service's own form in
          a modal - not four always-expanded full-height sections. ── */}
      <View style={styles.section}>
        <View style={styles.cardHead}>
          <IconBadge icon="link" />
          <Text style={styles.cardTitle}>{t.connectionsSection}</Text>
        </View>

        <TouchableOpacity style={styles.connRow} activeOpacity={0.7} onPress={() => setOpenConnection('strava')}>
          <View style={[styles.connDot, { backgroundColor: stravaAuth ? theme.success : theme.mutedText }]} />
          <Text style={styles.connRowText}>
            {stravaAuth ? t.stravaConnectedStatus : `${t.stravaSection} — ${t.connect}`}
          </Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.connRow} activeOpacity={0.7} onPress={() => setOpenConnection('runalyze')}>
          <View style={[styles.connDot, { backgroundColor: savedKey ? theme.success : theme.mutedText }]} />
          <Text style={styles.connRowText}>
            {savedKey ? `${t.runalyzeSection} — ${t.keyStored}` : `${t.runalyzeSection} — ${t.connect}`}
          </Text>
        </TouchableOpacity>
        <TouchableOpacity style={styles.connRow} activeOpacity={0.7} onPress={() => setOpenConnection('intervals')}>
          <View style={[styles.connDot, { backgroundColor: intervalsSaved ? theme.success : theme.mutedText }]} />
          <Text style={styles.connRowText}>
            {intervalsSaved ? `${t.intervalsSection} — ${t.credsStored}` : `${t.intervalsSection} — ${t.connect}`}
          </Text>
        </TouchableOpacity>
        {/* Dropbox / Google Drive / OneDrive rows removed (André, 2026-08-16): cloud backup is
            now just "save to a folder" on the Backup screen (point it at a cloud sync folder),
            so there's no OAuth "connection" to set up here anymore. */}
      </View>

      <Modal visible={openConnection === 'strava'} animationType="slide" transparent onRequestClose={() => setOpenConnection(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.cardTitle}>{t.stravaSection}</Text>
            <Text style={styles.sectionDesc}>{t.stravaSettingsDesc}</Text>
            {stravaAuth ? (
              <>
                <Chip icon="check" label={t.stravaConnectedStatus} />
                <View style={styles.row}>
                  <Button label={t.stravaDisconnectBtn} variant="text" grow={false} onPress={handleStravaDisconnect} />
                </View>
              </>
            ) : (
              <View style={styles.row}>
                <Button label={t.connect} variant="filled" onPress={handleStravaConnect} />
              </View>
            )}
            <Button label={t.closeBtn} variant="text" onPress={() => setOpenConnection(null)} style={{ marginTop: 12 }} />
          </View>
        </View>
      </Modal>

      <Modal visible={openConnection === 'runalyze'} animationType="slide" transparent onRequestClose={() => setOpenConnection(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.cardTitle}>{t.runalyzeSection}</Text>
            <Text style={styles.sectionDesc}>{t.runalyzeDesc}</Text>
            <Text style={styles.sectionDesc}>
              {t.runalyzeApiHint}
              <Text style={styles.link}>{t.runalyzeApiLink}</Text>
            </Text>
            <FieldRow
              icon="key"
              value={runalyzeKey}
              onChangeText={setRunalyzeKey}
              placeholder={t.apiKeyPlaceholder}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
            />
            <View style={styles.row}>
              <Button label={t.saveBtn} variant="filled" loading={saving} onPress={handleSaveRunalyze} />
              {!!savedKey && (
                <Button label={t.deleteBtn} icon="delete" variant="outline" tone="alert" grow={false} onPress={handleRemoveRunalyze} />
              )}
            </View>
            {!!savedKey && <StatusLine text={t.keyStored} />}
            <Button label={t.closeBtn} variant="text" onPress={() => setOpenConnection(null)} style={{ marginTop: 12 }} />
          </View>
        </View>
      </Modal>

      <Modal visible={openConnection === 'intervals'} animationType="slide" transparent onRequestClose={() => setOpenConnection(null)}>
        <View style={styles.modalOverlay}>
          <View style={styles.modalBox}>
            <Text style={styles.cardTitle}>{t.intervalsSection}</Text>
            <Text style={styles.sectionDesc}>{t.intervalsDesc}</Text>
            <Text style={styles.sectionDesc}>
              {t.intervalsApiHint}
              <Text style={styles.link}>{t.intervalsApiLink}</Text>
            </Text>
            <FieldRow
              icon="person"
              value={intervalsAthleteId}
              onChangeText={setIntervalsAthleteId}
              placeholder={t.athleteIdPlaceholder}
              autoCapitalize="none"
              autoCorrect={false}
            />
            <FieldRow
              icon="key"
              value={intervalsApiKey}
              onChangeText={setIntervalsApiKey}
              placeholder={t.apiKeyPlaceholder}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
            />
            <View style={styles.row}>
              <Button label={t.saveBtn} variant="filled" loading={savingIntervals} onPress={handleSaveIntervals} />
              {intervalsSaved && (
                <Button label={t.deleteBtn} icon="delete" variant="outline" tone="alert" grow={false} onPress={handleRemoveIntervals} />
              )}
            </View>
            {intervalsSaved && <StatusLine text={t.credsStored} />}

            {/* Coach chat key (2026-08-26, desktop parity). Deliberately spells out that this is
                an Anthropic API key and NOT a claude.ai subscription - that is the mistake people
                make, and it cannot work. Without a key the Coach still runs on pre-written
                replies, so this is optional. */}
            <Text style={[styles.cardTitle, { marginTop: 16 }]}>Coach chat</Text>
            <Text style={styles.sectionDesc}>
              Off, the coach replies from a few pre-written answers. Add your own Anthropic API
              key (console.anthropic.com) for a real conversation about your training — not your
              claude.ai login, which won't work here. Costs a few cents per conversation.
            </Text>
            <FieldRow
              icon="key"
              value={anthropicKey}
              onChangeText={setAnthropicKeyInput}
              placeholder={anthropicSaved ? 'Key saved (enter to replace)' : 'Anthropic API key'}
              autoCapitalize="none"
              autoCorrect={false}
              secureTextEntry
            />
            <View style={styles.row}>
              <Button label={t.saveBtn} variant="filled" loading={savingAnthropic}
                      onPress={handleSaveAnthropic} />
              {anthropicSaved && (
                <Button label={t.deleteBtn} icon="delete" variant="outline" tone="alert"
                        grow={false} onPress={handleRemoveAnthropic} />
              )}
            </View>
            {/* Gear import/sync - lives here in the intervals.icu connection (André, 2026-08-18),
                only once credentials are stored. Import pulls your gear down; Sync is two-way. */}
            {intervalsSaved && (
              <>
                <Text style={[styles.cardTitle, { marginTop: 16 }]}>{t.gearScreenTitle}</Text>
                <View style={styles.row}>
                  <Button label={t.gearImportBtn} variant="filled" loading={gearImporting} onPress={handleGearImport} />
                  <Button label={t.gearSyncBtn} variant="outline" grow={false} loading={gearSyncing} onPress={handleGearSync} />
                </View>
                <Text style={styles.sectionDesc}>{t.gearImportHint}</Text>
                {/* Import ALL activities from intervals.icu into the app (André, 2026-08-18). */}
                <Text style={[styles.cardTitle, { marginTop: 16 }]}>Activities</Text>
                <View style={styles.row}>
                  <Button label="Import activities" variant="filled" loading={actsImporting} onPress={handleImportActivities} />
                </View>
                <Text style={styles.sectionDesc}>
                  Pulls every activity from intervals.icu into the app (list, Totals, Calendar).
                  Already-synced watch moves are skipped, so nothing double-counts.
                </Text>
              </>
            )}
            <Button label={t.closeBtn} variant="text" onPress={() => setOpenConnection(null)} style={{ marginTop: 12 }} />
          </View>
        </View>
      </Modal>

      {/* ── Maps - real, 2026-08-09 ("no button to change provider, nor in the settings
          like the desktop version"). Ports SettingsPage.qml's real Maps card (icon + title
          + "Provider: tiles from X" + exclusive toggle buttons), with IGN added as a
          genuine Android-only third option (MapProviderService.ts's own header comment). ── */}
      <View style={styles.section}>
        <View style={styles.cardHead}>
          <IconBadge icon="route" />
          <Text style={styles.cardTitle}>{t.mapsSection}</Text>
        </View>
        <Text style={styles.sectionDesc}>{t.mapsProviderDesc(MAP_PROVIDER_LABELS[mapProvider])}</Text>
        <View style={[styles.chipRow, { justifyContent: 'flex-start', marginTop: 6 }]}>
          {MAP_PROVIDER_OPTIONS.map(opt => (
            <TouchableOpacity
              key={opt.provider}
              style={[styles.chip, opt.provider === mapProvider && styles.chipActive]}
              onPress={() => handleSetMapProvider(opt.provider)}
            >
              <Text style={[styles.chipText, opt.provider === mapProvider && styles.chipTextActive]}>
                {opt.label()}
              </Text>
            </TouchableOpacity>
          ))}
        </View>
        {/* Real, 2026-08-10 ("let's go for the offline maps solution") - TileCache.ts's own
            cache (route/activity map tiles saved for offline use via MapScreen.tsx's own
            download button) is unbounded until cleared manually - a simple size readout +
            clear action here, the same "let the user manage it" pattern this project uses
            for the GPX/exports cache directories elsewhere, not automatic eviction. */}
        <View style={styles.cacheRow}>
          <Text style={styles.sectionDesc}>
            {t.offlineMapCacheSize(tileCacheBytes === null ? '…' : formatCacheSize(tileCacheBytes))}
          </Text>
          <Button
            label={t.offlineMapClearCache}
            variant="outline"
            tone="alert"
            grow={false}
            loading={clearingCache}
            disabled={!tileCacheBytes}
            onPress={handleClearTileCache}
          />
        </View>
      </View>

      {/* ── About / disclaimer ── */}
      <View style={styles.section}>
        <View style={styles.cardHead}>
          <IconBadge icon="info" />
          <Text style={styles.cardTitle}>{t.aboutSection}</Text>
        </View>
        {/* Easter egg, same as the desktop's: ten taps here unlock the Ember recap screen.
            Silent until it fires - an egg that announces itself isn't one. */}
        <TouchableOpacity
          activeOpacity={1}
          onPress={async () => {
            const n = emberTaps + 1;
            setEmberTaps(n);
            if (n >= 10 && !emberOn) {
              await setEmberUnlocked(true);
              setEmberOn(true);
              Alert.alert('Ember', 'Ember unlocked — find it on Home.');
            }
          }}
        >
          <Text style={styles.sectionDesc}>{t.aboutVersion(APP_VERSION)}</Text>
        </TouchableOpacity>
        <Text style={[styles.sectionDesc, { marginTop: 10 }]}>{t.aboutDisclaimer}</Text>

        <Text style={styles.creditsHeading}>{t.aboutCreditsSection}</Text>
        <Text style={styles.sectionDesc}>{t.aboutCreditsIntro}</Text>
        {CREDITS.map(c => (
          <TouchableOpacity
            key={c.name}
            style={styles.creditItem}
            disabled={!c.url}
            activeOpacity={0.6}
            onPress={() => c.url && Linking.openURL(c.url)}
          >
            <Text style={[styles.creditName, !!c.url && styles.link]}>{c.name}</Text>
            <Text style={styles.creditDesc}>{c.description}</Text>
          </TouchableOpacity>
        ))}
      </View>

    </ScrollView>
  );
}

const createStyles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  content: { padding: 20 },
  section: {
    backgroundColor: t.card,
    borderColor: t.mutedText + '33',
    borderWidth: 1,
    borderRadius: 16,
    padding: 16,
    marginBottom: 16,
  },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  cardTitle: { fontSize: 16, fontWeight: '700', color: t.text },
  sectionDesc: { fontSize: 13, color: t.mutedText, marginBottom: 6, lineHeight: 19 },
  link: { color: t.text, fontWeight: '600' },
  row: { flexDirection: 'row', gap: 10, marginTop: 12 },
  cacheRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 10, gap: 10 },
  creditsHeading: {
    fontSize: 13, fontWeight: '700', color: t.text,
    marginTop: 16, paddingTop: 12, borderTopWidth: 1, borderTopColor: t.mutedText + '33',
  },
  creditItem: { marginTop: 10 },
  creditName: { fontSize: 13, fontWeight: '700', color: t.text },
  creditDesc: { fontSize: 12, color: t.mutedText, lineHeight: 17, marginTop: 2 },
  themeRow: { flexDirection: 'row', gap: 8, marginTop: 12 },
  viewPrefBlock: { marginTop: 14 },
  viewPrefLabel: { fontSize: 13.5, color: t.text, fontWeight: '600', marginBottom: 2 },
  themeOption: {
    flex: 1,
    flexDirection: 'row',
    alignItems: 'center',
    justifyContent: 'center',
    gap: 6,
    paddingVertical: 10,
    borderRadius: 10,
    borderWidth: 1.4,
    borderColor: t.mutedText + '33',
    backgroundColor: t.card,
  },
  themeOptionSelected: {
    backgroundColor: t.primary,
    borderColor: t.primary,
  },
  themeOptionLabel: { fontSize: 13, fontWeight: '600', color: t.text },
  themeOptionLabelSelected: { color: t.card },
  btnText: { color: t.card, fontWeight: '600', fontSize: 14 },
  statusRow: { flexDirection: 'row', alignItems: 'center', marginTop: 10 },
  // Section header for a settings group (General/Units/Personal/Other) - matches desktop's
  // group title (mutedText, bold, fontSizeLabel, a little top space above the first row).
  settingsGroupTitle: {
    color: t.mutedText, fontWeight: '700', fontSize: 12, marginTop: 18, marginBottom: 2,
  },
  ambitSettingRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginTop: 14,
  },
  ambitSettingLabel: { color: t.text, fontSize: 14, flex: 1, marginRight: 10 },
  // Read-only (Ambit 1/2) value display, right-aligned where the control would be.
  ambitSettingValueRO: { color: t.mutedText, fontSize: 14, fontWeight: '600', textAlign: 'right' },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 6, flexShrink: 1, justifyContent: 'flex-end' },
  // Same tinted-primary pill treatment as primitives.tsx's own Chip - a real selected
  // state, not a neutral bordered box.
  chip: {
    paddingHorizontal: 10, paddingVertical: 6, borderRadius: 8,
    borderWidth: 1, borderColor: t.mutedText + '33', backgroundColor: t.card,
  },
  chipActive: { borderColor: t.primary, backgroundColor: t.primary + '1F' },
  chipText: { color: t.mutedText, fontSize: 12 },
  chipTextActive: { color: t.primary, fontWeight: '600' },
  stepperRow: { flexDirection: 'row', alignItems: 'center' },
  stepperBtn: {
    width: 32, height: 32, borderRadius: 8, borderWidth: 1, borderColor: t.mutedText + '33',
    alignItems: 'center', justifyContent: 'center', backgroundColor: t.card,
  },
  stepperBtnText: { color: t.primary, fontSize: 18, fontWeight: '700' },
  stepperValue: { color: t.text, fontSize: 14, marginHorizontal: 10, minWidth: 30, textAlign: 'center' },
  coordRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  coordInput: {
    backgroundColor: t.card,
    borderRadius: 8,
    borderWidth: 1,
    borderColor: t.mutedText + '33',
    paddingHorizontal: 10,
    paddingVertical: 6,
    color: t.text,
    fontSize: 13,
    width: 110,
    textAlign: 'right',
  },
  coordSetBtn: {
    paddingVertical: 8,
    paddingHorizontal: 12,
    borderRadius: 8,
    backgroundColor: t.primary + '1F',
    borderWidth: 1,
    borderColor: t.primary,
  },
  // ── Connections card - compact tap-to-open rows + modal (v3.0 UI port) ──
  connRow: { flexDirection: 'row', alignItems: 'center', gap: 8, paddingVertical: 8 },
  connDot: { width: 8, height: 8, borderRadius: 4 },
  connRowText: { color: t.text, fontSize: 14, flex: 1 },
  modalOverlay: {
    flex: 1, backgroundColor: '#00000066', justifyContent: 'flex-end',
  },
  modalBox: {
    backgroundColor: t.background, borderTopLeftRadius: 20, borderTopRightRadius: 20,
    padding: 20, maxHeight: '85%',
  },
});
