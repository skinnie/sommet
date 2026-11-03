import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View, Text, ScrollView, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator, Alert, FlatList, Modal,
} from 'react-native';
import { Card } from '../components/ui/Card';
import Icon from '../components/ui/Icon';
import { SearchField } from '../components/ui/primitives';
import { useV3Theme } from '../theme/v3';
import { t } from '../i18n';
import {
  isCatalogAvailable, hasCatalog, importCatalog, getEntries, invalidateEntries, CatalogEntry,
} from '../services/CatalogService';
import { activityForName, ACTIVITY_TYPES } from '../services/ActivityColors';
import { readCustomModes } from '../services/CustomModesService';
import { ExerciseMode } from '../services/CustomModesReader';
import { installApp, InstallState } from '../services/AppInstall';

// App Zone - experimental, gated behind the Experimental flag. We ship NONE of Suunto's app
// catalog; the user imports their own SuuntoLink index.json (parsed natively into a compact
// local catalog - see CatalogService.ts). This screen: import notice + button when there's no
// catalog; search + browse once imported. The install path (choose a sport-mode screen, write
// the Apps region + the 51/52/53 shortcut via the proven AppsCodec/SportModeCodec) lands next.
export default function AppZoneScreen() {
  const theme = useV3Theme();
  const s = styles(theme);
  const available = isCatalogAvailable();
  const [ready, setReady] = useState<boolean | null>(null);
  const [importing, setImporting] = useState(false);
  const [entries, setEntries] = useState<CatalogEntry[]>([]);
  const [query, setQuery] = useState('');

  const refresh = useCallback(async () => {
    if (!available) { setReady(false); return; }
    const has = await hasCatalog();
    setReady(has);
    if (has) setEntries(await getEntries());
  }, [available]);

  useEffect(() => { refresh(); }, [refresh]);

  async function handleImport() {
    setImporting(true);
    try {
      const res = await importCatalog();
      invalidateEntries();
      setEntries(await getEntries());
      setReady(true);
      Alert.alert(t.experimentalAppZone, t.appZoneImported(res.count));
    } catch (e: any) {
      if (e?.message !== 'CANCELLED' && e?.code !== 'CANCELLED') {
        Alert.alert(t.appZoneImportFailed, e?.message ?? String(e));
      }
    } finally {
      setImporting(false);
    }
  }

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return entries;
    return entries.filter(e => e.name.toLowerCase().includes(q) || e.description.toLowerCase().includes(q));
  }, [entries, query]);

  // ── Install flow: tap an app → read the watch's modes → pick mode → screen → field. ──
  const [installApp_, setInstallApp] = useState<CatalogEntry | null>(null);
  const [modes, setModes] = useState<ExerciseMode[] | null>(null);
  const [modesLoading, setModesLoading] = useState(false);
  const [chosenMode, setChosenMode] = useState<number | null>(null);   // index into modes/exercise_modes
  const [chosenDisplay, setChosenDisplay] = useState<number | null>(null); // index into that mode's displays
  const [installState, setInstallState] = useState<InstallState | null>(null);
  const installBusy = installState != null && installState.phase !== 'done' && installState.phase !== 'error' && installState.phase !== 'idle';

  async function openInstall(entry: CatalogEntry) {
    setInstallApp(entry);
    setModes(null); setChosenMode(null); setChosenDisplay(null); setInstallState(null);
    setModesLoading(true);
    await readCustomModes(s => { if (s.modes) setModes(s.modes); });
    setModesLoading(false);
  }
  function closeInstall() { setInstallApp(null); }

  async function runInstall(fieldIndex: number) {
    if (installApp_ == null || chosenMode == null || chosenDisplay == null) return;
    const ok = await installApp(installApp_, chosenMode, chosenDisplay, fieldIndex, setInstallState);
    if (ok) {
      Alert.alert(t.experimentalAppZone, t.appZoneInstalledMsg);
      closeInstall();
    }
    // On failure installState holds the error phase/message, rendered in the modal below.
  }

  if (!available) {
    return (
      <ScrollView style={s.root} contentContainerStyle={s.content}>
        <Card style={{ width: '100%' }}>
          <Text style={[s.desc, { color: theme.warning }]}>{t.appZoneNativeMissing}</Text>
        </Card>
      </ScrollView>
    );
  }

  // ── No catalog yet: notice + import ──
  if (ready === false || ready === null) {
    return (
      <ScrollView style={s.root} contentContainerStyle={s.content}>
        <Card style={{ width: '100%' }}>
          <Text style={[s.desc, { color: theme.warning }]}>{t.experimentalWarningBanner}</Text>
          <Text style={s.title}>{t.appZoneNoCatalogTitle}</Text>
          <Text style={s.desc}>{t.appZoneInstructions}</Text>
          <TouchableOpacity style={[s.btn, importing && { opacity: 0.5 }]} disabled={importing || ready === null} onPress={handleImport}>
            {importing ? <ActivityIndicator size="small" color={theme.primary} />
              : <Text style={s.btnText}>{t.appZoneImportBtn}</Text>}
          </TouchableOpacity>
          {importing && <Text style={s.desc}>{t.appZoneImporting}</Text>}
        </Card>
      </ScrollView>
    );
  }

  // ── Catalog present: search + browse ──
  return (
    <View style={s.root}>
      <View style={s.header}>
        {/* One search box for the app (desktop picks #9): magnifier, and a × once there's text. */}
        <SearchField value={query} onChangeText={setQuery} placeholder={t.appZoneSearchPlaceholder} />
        <View style={s.headerMeta}>
          <Text style={s.metaText}>{t.appZoneAppsCount(filtered.length)}</Text>
          <TouchableOpacity onPress={handleImport} disabled={importing}>
            <Text style={[s.metaText, { color: theme.primary, fontWeight: '700' }]}>
              {importing ? t.appZoneImporting : t.appZoneReimportBtn}
            </Text>
          </TouchableOpacity>
        </View>
      </View>
      <FlatList
        data={filtered}
        keyExtractor={e => String(e.ruleId)}
        contentContainerStyle={{ padding: 12, gap: 8 }}
        initialNumToRender={20}
        windowSize={10}
        renderItem={({ item }) => (
          <TouchableOpacity style={s.appCard} activeOpacity={0.7} onPress={() => openInstall(item)}>
            <View style={[s.appDot, { backgroundColor: ACTIVITY_TYPES[item.activityId]?.color ?? activityForName(null).color }]} />
            <View style={{ flex: 1 }}>
              <Text style={s.appName} numberOfLines={1}>{item.name}</Text>
              {!!item.description && <Text style={s.appDesc} numberOfLines={2}>{item.description}</Text>}
            </View>
            <Icon name="chevronRight" size={18} color={theme.mutedText} />
          </TouchableOpacity>
        )}
        ListHeaderComponent={<Text style={[s.desc, { paddingHorizontal: 4, paddingBottom: 6 }]}>{t.appZoneInstallNote}</Text>}
      />

      {/* ── Install flow modal ── */}
      <Modal visible={installApp_ != null} animationType="slide" transparent onRequestClose={closeInstall}>
        <View style={s.modalOverlay}>
          <View style={s.modalBox}>
            <Text style={s.title}>{installApp_ ? t.appZoneInstallTitle(installApp_.name) : ''}</Text>
            <Text style={[s.desc, { color: theme.warning }]}>{t.experimentalWarningBanner}</Text>

            {modesLoading && (
              <View style={s.rowCenter}><ActivityIndicator size="small" color={theme.primary} />
                <Text style={[s.desc, { marginLeft: 8 }]}>{t.appZoneReadingModes}</Text></View>
            )}

            {installBusy && (
              <View style={s.rowCenter}><ActivityIndicator size="small" color={theme.primary} />
                <Text style={[s.desc, { marginLeft: 8 }]}>{t.appZoneInstalling}</Text></View>
            )}

            {installState?.phase === 'error' && (
              <Text style={[s.desc, { color: theme.error }]}>{installState.error}</Text>
            )}

            {!modesLoading && !installBusy && modes && (
              <ScrollView style={{ maxHeight: 420 }}>
                {/* Step 1: mode */}
                {chosenMode == null && (
                  <>
                    <Text style={s.stepLabel}>{t.appZonePickMode}</Text>
                    {modes.map((m, i) => (
                      <TouchableOpacity key={i} style={s.pickRow} onPress={() => { setChosenMode(i); setChosenDisplay(null); }}>
                        <Text style={s.pickText}>{m.settings.name}</Text>
                        <Icon name="chevronRight" size={16} color={theme.mutedText} />
                      </TouchableOpacity>
                    ))}
                  </>
                )}
                {/* Step 2: screen (real, editable displays only) */}
                {chosenMode != null && chosenDisplay == null && (() => {
                  const real = modes[chosenMode].displays
                    .map((d, idx) => ({ d, idx }))
                    .filter(x => !x.d.isBuiltIn && x.d.fields.length > 0);
                  if (real.length === 0) return <Text style={s.desc}>{t.appZoneNoRealScreens}</Text>;
                  return (
                    <>
                      <Text style={s.stepLabel}>{t.appZonePickScreen}</Text>
                      {real.map(({ d, idx }) => (
                        <TouchableOpacity key={idx} style={s.pickRow} onPress={() => setChosenDisplay(idx)}>
                          <Text style={s.pickText}>{t.appZoneScreenLabel(d.screenNumber ?? idx + 1)}</Text>
                          <Icon name="chevronRight" size={16} color={theme.mutedText} />
                        </TouchableOpacity>
                      ))}
                    </>
                  );
                })()}
                {/* Step 3: field */}
                {chosenMode != null && chosenDisplay != null && (
                  <>
                    <Text style={s.stepLabel}>{t.appZonePickField}</Text>
                    {modes[chosenMode].displays[chosenDisplay].fields.map((f, fi) => (
                      <TouchableOpacity key={fi} style={s.pickRow} onPress={() => runInstall(fi)}>
                        <Text style={s.pickText}>{f.typeLabel}</Text>
                        <Text style={[s.pickText, { color: theme.primary, fontWeight: '700' }]}>{t.appZoneInstallBtn}</Text>
                      </TouchableOpacity>
                    ))}
                  </>
                )}
              </ScrollView>
            )}

            <TouchableOpacity style={[s.btn, { alignSelf: 'center', marginTop: 14 }]} onPress={closeInstall} disabled={installBusy}>
              <Text style={s.btnText}>{t.cancel}</Text>
            </TouchableOpacity>
          </View>
        </View>
      </Modal>
    </View>
  );
}

const styles = (th: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: th.background },
  content: { padding: 16, gap: 14 },
  title: { fontSize: 16, fontWeight: '800', color: th.text, marginTop: 8 },
  desc: { fontSize: 12.5, color: th.mutedText, marginTop: 6, lineHeight: 18 },
  btn: {
    marginTop: 14, paddingVertical: 10, paddingHorizontal: 16, borderRadius: 10, alignSelf: 'flex-start',
    backgroundColor: th.primary + '1F', borderWidth: 1, borderColor: th.primary,
  },
  btnText: { color: th.primary, fontWeight: '700', fontSize: 13 },
  header: { padding: 12, gap: 8, borderBottomWidth: StyleSheet.hairlineWidth, borderBottomColor: th.mutedText + '33' },
  searchRow: {
    flexDirection: 'row', alignItems: 'center', gap: 8, backgroundColor: th.card,
    borderRadius: 10, paddingHorizontal: 12, borderWidth: 1, borderColor: th.mutedText + '33',
  },
  search: { flex: 1, color: th.text, fontSize: 14, paddingVertical: 9 },
  headerMeta: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' },
  metaText: { fontSize: 12, color: th.mutedText },
  appCard: {
    flexDirection: 'row', alignItems: 'center', gap: 10, backgroundColor: th.card,
    borderRadius: 12, padding: 12,
  },
  appDot: { width: 10, height: 10, borderRadius: 5 },
  appName: { fontSize: 14, fontWeight: '600', color: th.text },
  appDesc: { fontSize: 12, color: th.mutedText, marginTop: 2, lineHeight: 16 },
  rowCenter: { flexDirection: 'row', alignItems: 'center', marginTop: 10 },
  modalOverlay: { flex: 1, backgroundColor: '#00000066', justifyContent: 'flex-end' },
  modalBox: { backgroundColor: th.background, borderTopLeftRadius: 20, borderTopRightRadius: 20, padding: 20, maxHeight: '85%' },
  stepLabel: { fontSize: 13, fontWeight: '700', color: th.mutedText, marginTop: 8, marginBottom: 4 },
  pickRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    paddingVertical: 12, borderBottomWidth: 1, borderBottomColor: th.mutedText + '22',
  },
  pickText: { fontSize: 14, color: th.text },
});
