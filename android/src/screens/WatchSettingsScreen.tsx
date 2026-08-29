import React, { useCallback, useState } from 'react';
import {
  View, Text, ScrollView, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert,
} from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import { useV3Theme } from '../theme/v3';
import { Dropdown, IconBadge, Toggle } from '../components/ui/primitives';
import { CoordinatePicker } from '../components/CoordinatePicker';
import { DecodedSetting, SettingField, SettingScreen } from '../services/AmbitSettingsReader';
import { readAmbitSettings, writeAmbitSetting, writeLegacyPersonalSetting } from '../services/AmbitSettingsService';
import { isWritablePersonalField } from '../services/AmbitPersonalSettingsWriter';
import type { WriteDevice } from '../services/AmbitSettingsWriter';
import { EPHEMERIS_GPS_ONLY_KEY, GlonassStatus, getGlonassStatus } from '../services/SgeeService';
import { detectAttachedDeviceType, isBleTransportActive } from '../native/AmbitUsbModule';
import AsyncStorage from '@react-native-async-storage/async-storage';
import { t } from '../i18n';

// Watch settings (2026-08-29): the on-watch settings read/write, moved into its own screen to
// match the desktop (WatchSettingsPage.qml — split out of the app Settings on 2026-08-14). This
// is the exact read/write the app Settings screen used to host; the per-model write rules live in
// the services (AmbitSettingsService picks the curated field table + writeDevice per model):
//   - Ambit1/2 (legacy 0x0b01): general + personal-profile fields writable via
//     writeLegacyPersonalSetting; device flagged readOnly with those as the exception.
//   - Ambit3 / Traverse (SBEM 0x1101 per-screen templates): writable where a write template
//     exists; a field with none stays read-only.
//   - Kailash (Hoopoe): writable over cable (full-blob), no Personal-profile fields.

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
function readOnlyValue(row: DecodedSetting): string {
  if (row.kind === 'enum') return row.choices?.find(c => c.value === row.value)?.label ?? String(row.value);
  if (row.kind === 'bool') return row.value === 1 ? 'On' : 'Off';
  if (row.kind === 'coord') return row.value.toFixed(6);
  return String(row.value);
}

export default function WatchSettingsScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);

  const [isGarminAttached, setIsGarminAttached] = useState(false);
  const [ambitSettings, setAmbitSettings] = useState<DecodedSetting[] | null>(null);
  const [ambitSettingsFields, setAmbitSettingsFields] = useState<SettingField[] | null>(null);
  const [ambitDeviceName, setAmbitDeviceName] = useState<string>('');
  const [ambitReadOnly, setAmbitReadOnly] = useState(false);
  const [ambitWriteDevice, setAmbitWriteDevice] = useState<WriteDevice | undefined>();
  const [ambitGlonass, setAmbitGlonass] = useState<GlonassStatus | undefined>();
  const [ephemerisGpsOnly, setEphemerisGpsOnly] = useState(false);
  const [orbitalInfoOpen, setOrbitalInfoOpen] = useState(false);
  const [ambitSettingsPhase, setAmbitSettingsPhase] =
    useState<'idle' | 'connecting' | 'reading' | 'done' | 'error'>('idle');
  const [ambitSettingsError, setAmbitSettingsError] = useState<string | undefined>();
  const [writingKey, setWritingKey] = useState<string | null>(null);
  const [coordEdits, setCoordEdits] = useState<Record<string, string>>({});
  const [numEdits, setNumEdits] = useState<Record<string, string>>({});
  const [coordPickKey, setCoordPickKey] = useState<string | null>(null);

  // Auto-read the watch's settings when this screen opens (USB or BLE) - same on-connect read
  // the app Settings screen did, and the desktop reads on page load.
  useFocusEffect(useCallback(() => {
    detectAttachedDeviceType().then(dt => {
      setIsGarminAttached(dt === 'garmin');
      if (dt === 'ambit' || isBleTransportActive()) handleReadAmbitSettings();
    }).catch(() => {
      if (isBleTransportActive()) handleReadAmbitSettings();
    });
    AsyncStorage.getItem(EPHEMERIS_GPS_ONLY_KEY).then(v => setEphemerisGpsOnly(v === 'true')).catch(() => {});
  }, []));

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
    try { setAmbitGlonass(await getGlonassStatus()); } catch { /* group stays hidden */ }
  }

  async function handleEphemerisGpsOnly(v: boolean) {
    setEphemerisGpsOnly(v);
    await AsyncStorage.setItem(EPHEMERIS_GPS_ONLY_KEY, v ? 'true' : 'false');
  }

  async function handleWriteAmbitSetting(key: string, value: number) {
    if (ambitReadOnly && isWritablePersonalField(key)) {
      setWritingKey(key);
      await writeLegacyPersonalSetting(key, value, s => {
        if (s.phase === 'done' || s.phase === 'error') {
          setWritingKey(null);
          if (s.error) Alert.alert(t.error, s.error);
        }
        if (s.result && s.result.confirmedValue !== null) {
          setAmbitSettings(prev => prev && prev.map(row =>
            row.key === key ? { ...row, value: s.result!.confirmedValue as number } : row));
          setNumEdits(prev => ({ ...prev, [key]: String(s.result!.confirmedValue) }));
        }
      });
      return;
    }
    if (!ambitSettingsFields || !ambitWriteDevice) return;
    setWritingKey(key);
    await writeAmbitSetting(key, value, ambitSettingsFields, ambitWriteDevice, s => {
      if (s.phase === 'done' || s.phase === 'error') {
        setWritingKey(null);
        if (s.error) Alert.alert(t.error, s.error);
      }
      if (s.result) {
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

  function handleSetCoord(key: string) {
    const parsed = parseFloat(coordEdits[key] ?? '');
    if (!Number.isFinite(parsed)) { Alert.alert(t.error, `${key}: not a valid number`); return; }
    if (key === 'home_latitude' && (parsed < -90 || parsed > 90)) {
      Alert.alert(t.error, `${key}=${parsed} out of range [-90, 90]`); return;
    }
    if (key === 'home_longitude' && (parsed < -180 || parsed > 180)) {
      Alert.alert(t.error, `${key}=${parsed} out of range [-180, 180]`); return;
    }
    handleWriteAmbitSetting(key, parsed);
  }

  function handleSetNumber(row: DecodedSetting) {
    const parsed = parseFloat(numEdits[row.key] ?? '');
    if (!Number.isFinite(parsed)) { Alert.alert(t.error, `${row.label ?? row.key}: not a valid number`); return; }
    if ((row.min !== undefined && parsed < row.min) || (row.max !== undefined && parsed > row.max)) {
      Alert.alert(t.error, `${row.label ?? row.key} = ${parsed} out of range [${row.min}, ${row.max}]`); return;
    }
    handleWriteAmbitSetting(row.key, parsed);
  }

  return (
    <ScrollView style={{ flex: 1, backgroundColor: theme.background }} contentContainerStyle={{ padding: 16 }}>
      {isGarminAttached ? (
        <View style={styles.section}>
          <Text style={styles.sectionDesc}>{t.ambitSettingsDesc}</Text>
          <Text style={[styles.sectionDesc, { color: theme.mutedText }]}>
            A Garmin is connected — on-watch settings apply to Suunto watches only.
          </Text>
        </View>
      ) : (
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
          <Text style={[styles.sectionDesc, { color: theme.error, marginTop: 10 }]}>{ambitSettingsError}</Text>
        )}

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
          .map((row, i) => ({ row, i }))
          .sort((a, b) =>
            settingScreenRank(settingScreenOf(a.row)) - settingScreenRank(settingScreenOf(b.row)) || a.i - b.i)
          .map(({ row }, idx, arr) => {
          const label = row.label ?? row.key.split('_')
            .map((w, i) => (i === 0 ? w.charAt(0).toUpperCase() + w.slice(1) : w))
            .join(' ');
          const busy = writingKey === row.key;
          const roDisplay = ambitReadOnly && !isWritablePersonalField(row.key);
          const showHeader = idx === 0 || settingScreenOf(arr[idx - 1].row) !== settingScreenOf(row);
          return (
            <React.Fragment key={row.key}>
              {showHeader && (
                <Text style={styles.settingsGroupTitle}>{SETTINGS_SCREEN_TITLE[settingScreenOf(row)]}</Text>
              )}
            <View style={styles.ambitSettingRow}>
              <Text style={styles.ambitSettingLabel}>{label}</Text>

              {roDisplay && (<Text style={styles.ambitSettingValueRO}>{readOnlyValue(row)}</Text>)}

              {!roDisplay && (<>
              {row.kind === 'bool' && (
                <Toggle value={row.value === 1} onValueChange={v => handleWriteAmbitSetting(row.key, v ? 1 : 0)} disabled={busy} />
              )}

              {row.kind === 'enum' && (
                <Dropdown value={row.value} choices={row.choices ?? []} disabled={busy || row.locked}
                  onSelect={v => handleWriteAmbitSetting(row.key, v)} />
              )}

              {row.kind === 'number' && row.control === 'dropdown' && (
                <Dropdown value={row.value} choices={row.choices ?? []} disabled={busy}
                  onSelect={v => handleWriteAmbitSetting(row.key, v)} />
              )}

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

              {row.kind === 'number' && row.control !== 'dropdown'
                && row.control !== 'number' && row.control !== 'declination' && (
                <View style={styles.stepperRow}>
                  <TouchableOpacity style={styles.stepperBtn} disabled={busy}
                    onPress={() => handleWriteAmbitSetting(row.key, Math.max(row.min ?? 0, row.value - (row.step ?? 5)))}>
                    <Text style={styles.stepperBtnText}>-</Text>
                  </TouchableOpacity>
                  <Text style={styles.stepperValue}>{row.value}{row.unit ? ` ${row.unit}` : ''}</Text>
                  <TouchableOpacity style={styles.stepperBtn} disabled={busy}
                    onPress={() => handleWriteAmbitSetting(row.key, Math.min(row.max ?? 100, row.value + (row.step ?? 5)))}>
                    <Text style={styles.stepperBtnText}>+</Text>
                  </TouchableOpacity>
                </View>
              )}

              {row.kind === 'coord' && (
                <View style={styles.coordRow}>
                  <TextInput
                    style={styles.coordInput}
                    value={coordEdits[row.key] ?? row.value.toFixed(6)}
                    onChangeText={v => setCoordEdits(prev => ({ ...prev, [row.key]: v }))}
                    editable={!busy}
                    placeholderTextColor={theme.mutedText}
                  />
                  <TouchableOpacity style={styles.coordSetBtn} disabled={busy} onPress={() => handleSetCoord(row.key)}>
                    <Text style={styles.btnText}>{t.saveBtn}</Text>
                  </TouchableOpacity>
                  <TouchableOpacity style={styles.coordSetBtn} disabled={busy} onPress={() => setCoordPickKey(row.key)}>
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

      <CoordinatePicker
        visible={coordPickKey !== null}
        initialLat={parseFloat(coordEdits.home_latitude ?? '') || 0}
        initialLon={parseFloat(coordEdits.home_longitude ?? '') || 0}
        onCancel={() => setCoordPickKey(null)}
        onPick={(la, lo) => {
          setCoordEdits(prev => ({ ...prev, home_latitude: la.toFixed(6), home_longitude: lo.toFixed(6) }));
          setCoordPickKey(null);
        }}
      />
    </ScrollView>
  );
}

const createStyles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  section: { backgroundColor: t.card, borderColor: t.mutedText + '33', borderWidth: 1, borderRadius: 16, padding: 16, marginBottom: 16 },
  cardHead: { flexDirection: 'row', alignItems: 'center', gap: 10, marginBottom: 4 },
  cardTitle: { fontSize: 16, fontWeight: '700', color: t.text },
  sectionDesc: { fontSize: 13, color: t.mutedText, marginBottom: 6, lineHeight: 19 },
  btnText: { color: t.card, fontWeight: '600', fontSize: 14 },
  statusRow: { flexDirection: 'row', alignItems: 'center', marginTop: 10 },
  settingsGroupTitle: { color: t.mutedText, fontWeight: '700', fontSize: 12, marginTop: 18, marginBottom: 2 },
  ambitSettingRow: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', marginTop: 14 },
  ambitSettingLabel: { color: t.text, fontSize: 14, flex: 1, marginRight: 10 },
  ambitSettingValueRO: { color: t.mutedText, fontSize: 14, fontWeight: '600', textAlign: 'right' },
  stepperRow: { flexDirection: 'row', alignItems: 'center' },
  stepperBtn: { width: 32, height: 32, borderRadius: 8, borderWidth: 1, borderColor: t.mutedText + '33', alignItems: 'center', justifyContent: 'center', backgroundColor: t.card },
  stepperBtnText: { color: t.primary, fontSize: 18, fontWeight: '700' },
  stepperValue: { color: t.text, fontSize: 14, marginHorizontal: 10, minWidth: 30, textAlign: 'center' },
  coordRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  coordInput: { backgroundColor: t.card, borderRadius: 8, borderWidth: 1, borderColor: t.mutedText + '33', paddingHorizontal: 10, paddingVertical: 6, color: t.text, fontSize: 13, width: 110, textAlign: 'right' },
  coordSetBtn: { paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8, backgroundColor: t.primary + '1F', borderWidth: 1, borderColor: t.primary },
});
