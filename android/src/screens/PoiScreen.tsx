import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, StyleSheet, Alert, ScrollView, TouchableOpacity, Modal, Pressable } from 'react-native';
import { useFocusEffect } from '@react-navigation/native';
import {
  addPoiToWatch, AddPoiState, readOnWatchPois, getCachedPois, exportSinglePoiToGpx, pickAndParseWaypoints,
  WatchPoi, removePoisFromWatch, getCachedPoiWatchModel,
} from '../services/PoiService';
import { poiTypesFor, poiTypeDefaultFor, poiTypeOf, isTraverseWatch } from '../services/PoiTypes';
import { useDemo } from '../config/DemoContext';
import { demoPois, demoAddPoi, demoRemovePoi } from '../services/DemoData';
import { t } from '../i18n';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { Button, FieldRow } from '../components/ui/primitives';
import { TrackPreview } from '../components/TrackPreview';
import { getViewMode, setViewMode as persistViewMode, ViewMode } from '../services/ListViewPrefs';
import { ViewModeToggle } from '../components/ui/ViewModeToggle';

// v3.0 UI port (2026-08-09, "re do... pois to match entirely desktop") - real structural
// rebuild matching desktop's own PoisPage.qml: "Add a POI" gets a live pin preview, "Import
// from GPX" now shows each parsed waypoint with its own real Add button (not one opaque
// write-everything action), and a real "On the watch" list with a per-POI preview + Export -
// this screen used to be pure action buttons with no browsing at all.
//
// Real, same day ("I would prefer an immediate map view on this side") - TrackPreview
// itself now renders a real tile-map background (see its own header comment), so the
// separate tap-through "Map" button/TrackMapScreen this screen briefly had was removed -
// the preview already IS the map, immediately, matching desktop's own PoisPage.qml (a live
// MapView per item, no tap-through screen at all).
// POI types (the icon the watch shows) come from services/PoiTypes.ts: the Ambit3's 18, or the
// 48 of a Traverse / Traverse Alpha, which are numbered differently. The list offered is the one
// of the watch the POI list was last read from.
export default function PoiScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);

  const [poiName, setPoiName] = useState('');
  const [poiLat, setPoiLat]   = useState('');
  const [poiLon, setPoiLon]   = useState('');
  // The watch the POI list was last read from ("Emu", "Jabiru", "Loon" ...): it decides which
  // type list is offered and how a type byte read off the watch is named.
  // In Testing mode it is the sample watch picked there, as on the desktop.
  const demo = useDemo();
  const [readModel, setWatchModel] = useState('');
  const watchModel = demo.enabled ? demo.variant : readModel;
  const traverse = isTraverseWatch(watchModel);
  // The type BYTE written to the watch; starts at that watch's plain type (17, or 26 on a Traverse).
  const [poiType, setPoiType] = useState(17);
  // Another kind of watch: a number picked from the other list means something else there.
  useEffect(() => { setPoiType(poiTypeDefaultFor(watchModel)); }, [traverse]);   // eslint-disable-line react-hooks/exhaustive-deps
  const [poiState, setPoiState] = useState<AddPoiState>({ phase: 'idle' });
  const [infoOpen, setInfoOpen] = useState(false);  // the little "i" POI-note dialog
  const poiBusy = poiState.phase === 'connecting' || poiState.phase === 'writing';

  const [imported, setImported] = useState<WatchPoi[] | null>(null);
  const [importPicking, setImportPicking] = useState(false);
  const [addingIndex, setAddingIndex] = useState<number | null>(null);

  const [onWatch, setOnWatch] = useState<WatchPoi[] | null>(null);
  const [onWatchLoading, setOnWatchLoading] = useState(false);
  const [onWatchError, setOnWatchError] = useState<string | undefined>();
  const [exportingIndex, setExportingIndex] = useState<number | null>(null);
  const [removingIndex, setRemovingIndex] = useState<number | null>(null);
  // Map/list view (persisted Settings pref). POIs are single points, so no sort control
  // (name is the only meaningful key) - just the view toggle.
  const [viewMode, setViewMode] = useState<ViewMode>('map');
  useFocusEffect(useCallback(() => { getViewMode('pois').then(setViewMode); }, []));
  function changeViewMode(m: ViewMode) { setViewMode(m); persistViewMode('pois', m); }

  // Real, 2026-08-10 ("it is not upon the watch to give you that, is on the app to store
  // the activities, so they can load almost immediately and just refresh what is new") -
  // same real local-first pattern as RouteScreen.tsx's own loadOnWatch(): the persisted
  // cache renders instantly, a real watch read refreshes it silently in the background.
  const loadOnWatch = useCallback(async () => {
    // Testing mode: the sample watch's POIs (in memory), never a real watch.
    if (demo.enabled) {
      setOnWatchError(undefined);
      setOnWatchLoading(false);
      setOnWatch(demoPois(demo.variant));
      return;
    }
    const cached = await getCachedPois();
    setWatchModel(await getCachedPoiWatchModel());
    if (cached) {
      setOnWatch(cached);
    } else {
      setOnWatchLoading(true);
    }
    setOnWatchError(undefined);
    try {
      setOnWatch(await readOnWatchPois());
      setWatchModel(await getCachedPoiWatchModel());
    } catch (e: any) {
      // Say so even with a cached list on screen: it used to stay up silently, so a
      // watch whose POIs were gone kept showing them (Peak, 2026-10-24).
      setOnWatchError(e?.message ?? t.unknownError);
    } finally {
      setOnWatchLoading(false);
    }
  }, [demo.enabled, demo.variant]);

  useFocusEffect(useCallback(() => { loadOnWatch(); }, [loadOnWatch]));

  const parsedLat = parseFloat(poiLat.replace(',', '.'));
  const parsedLon = parseFloat(poiLon.replace(',', '.'));
  const hasValidCoords = Number.isFinite(parsedLat) && parsedLat >= -90 && parsedLat <= 90
    && Number.isFinite(parsedLon) && parsedLon >= -180 && parsedLon <= 180;

  async function handleAddPoi() {
    if (poiBusy) return;
    if (!poiName.trim()) {
      Alert.alert(t.poiInvalid, t.poiNameRequired);
      return;
    }
    if (!hasValidCoords) {
      Alert.alert(t.poiInvalid, t.poiCoordsInvalid);
      return;
    }
    if (demo.enabled) {
      setOnWatch(demoAddPoi(demo.variant, { name: poiName.trim(), latitude: parsedLat, longitude: parsedLon, type: poiType }));
      setPoiName(''); setPoiLat(''); setPoiLon('');
      return;
    }
    try {
      await addPoiToWatch(poiName.trim(), parsedLat, parsedLon, setPoiState, poiType);
      setPoiState(s => {
        if (s.phase === 'done') {
          setPoiName('');
          setPoiLat('');
          setPoiLon('');
          loadOnWatch();
        } else if (s.phase === 'error') {
          Alert.alert(t.error, s.error ?? t.unknownError);
        }
        return s;
      });
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
      setPoiState({ phase: 'error', error: e?.message });
    }
  }

  async function handleImportPick() {
    if (importPicking) return;
    setImportPicking(true);
    try {
      const wps = await pickAndParseWaypoints();
      if (wps) setImported(wps);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
    } finally {
      setImportPicking(false);
    }
  }

  async function handleAddImported(wp: WatchPoi, index: number) {
    if (addingIndex !== null) return;
    if (demo.enabled) {
      setOnWatch(demoAddPoi(demo.variant, { ...wp, type: poiTypeDefaultFor(watchModel) }));
      setImported(list => (list ? list.filter((_, k) => k !== index) : list));
      return;
    }
    setAddingIndex(index);
    try {
      // addPoiToWatch reports a failure through its state callback, it does not throw. This
      // used to pass a no-op, so a POI the watch never got still left the list as if added
      // (8 of them on 2026-10-24, after the watch had closed Bluetooth).
      let failure: string | undefined;
      // The watch's plain type (-1) and, for a Traverse, the file's elevation.
      await addPoiToWatch(wp.name, wp.latitude, wp.longitude, st => {
        if (st.phase === 'error') failure = st.error ?? t.unknownError;
      }, -1, wp.elevation);
      if (failure) {
        Alert.alert(t.error, failure);
        return;
      }
      setImported(prev => prev?.filter((_, i) => i !== index) ?? null);
      loadOnWatch();
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
    } finally {
      setAddingIndex(null);
    }
  }

  function handleRemoveItem(poi: WatchPoi, index: number) {
    if (removingIndex !== null) return;
    Alert.alert(t.poiRemoveTitle, t.poiRemoveConfirm(poi.name), [
      { text: t.cancel, style: 'cancel' },
      { text: t.poiItemRemoveBtn, style: 'destructive', onPress: async () => {
        if (demo.enabled) { setOnWatch(demoRemovePoi(demo.variant, poi)); return; }
        setRemovingIndex(index);
        try {
          const r = await removePoisFromWatch([poi]);
          Alert.alert(t.poiRemoveTitle, t.poiRemoved(poi.name, r.left, r.emptyCleared));
        } catch (e: any) {
          Alert.alert(t.error, e?.message ?? t.unknownError);
        } finally {
          setRemovingIndex(null);
          loadOnWatch();
        }
      } },
    ]);
  }

  async function handleExportItem(poi: WatchPoi, index: number) {
    if (exportingIndex !== null) return;
    setExportingIndex(index);
    try {
      await exportSinglePoiToGpx(poi);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
    } finally {
      setExportingIndex(null);
    }
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>

      {/* ── Add a POI ── */}
      {/* POI note dialog: what happens to POIs sent to the watch (André, 2026-08-17), same
          little "i" affordance as the Routes screen. */}
      <Modal visible={infoOpen} transparent animationType="fade" onRequestClose={() => setInfoOpen(false)}>
        <Pressable style={styles.backdrop} onPress={() => setInfoOpen(false)}>
          <Pressable style={styles.dialogCard} onPress={() => {}}>
            <Text style={styles.dialogTitle}>{t.poiInfoTitle}</Text>
            <Text style={[styles.dialogText, { fontWeight: '700', marginTop: 4 }]}>{t.poiWatchNote}</Text>
            <TouchableOpacity style={styles.dialogClose} onPress={() => setInfoOpen(false)}>
              <Text style={styles.dialogCloseText}>{t.close}</Text>
            </TouchableOpacity>
          </Pressable>
        </Pressable>
      </Modal>

      <Card style={{ width: '100%' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={styles.cardTitle}>{t.poiSection}</Text>
          <TouchableOpacity style={styles.infoBadge} onPress={() => setInfoOpen(true)} hitSlop={8}>
            <Text style={styles.infoBadgeText}>i</Text>
          </TouchableOpacity>
        </View>
        <FieldRow icon="poi" value={poiName} onChangeText={setPoiName} placeholder={t.poiNamePlaceholder} editable={!poiBusy} style={{ marginTop: v3Spacing.small }} />
        <View style={styles.row}>
          {/* Real bug, found live on device (2026-08-09): FieldRow's own `style` prop only
              reaches its inner TextInput, not the outer row container - passing flex:1
              directly to FieldRow sized the text *inside* each field, not the field's own
              box, so Longitude was pushed off the right edge of the screen instead of
              sitting beside Latitude. Wrapping each in its own flex:1 View is the real fix. */}
          <View style={{ flex: 1 }}>
            <FieldRow icon="map" value={poiLat} onChangeText={setPoiLat} placeholder={t.poiLat} keyboardType="numbers-and-punctuation" editable={!poiBusy} />
          </View>
          <View style={{ flex: 1 }}>
            <FieldRow icon="map" value={poiLon} onChangeText={setPoiLon} placeholder={t.poiLon} keyboardType="numbers-and-punctuation" editable={!poiBusy} />
          </View>
        </View>
        {/* POI type (the icon the watch shows) - a horizontal chip picker, default Waypoint. */}
        <Text style={styles.typeLabel}>{t.poiType}</Text>
        <ScrollView horizontal showsHorizontalScrollIndicator={false}
          contentContainerStyle={{ gap: v3Spacing.small, paddingVertical: 2 }}>
          {poiTypesFor(watchModel).map(pt => (
            <TouchableOpacity key={pt.id} disabled={poiBusy} onPress={() => setPoiType(pt.id)}
              style={[styles.typeChipRow, poiType === pt.id && styles.typeChipSel]}>
              <Text style={[styles.typeGlyph, poiType === pt.id && styles.typeGlyphSel]}>{pt.glyph}</Text>
              <Text style={[styles.typeChipText, poiType === pt.id && styles.typeChipTextSel]}>{pt.name}</Text>
            </TouchableOpacity>
          ))}
        </ScrollView>
        <View style={{ marginTop: v3Spacing.small }}>
          <TrackPreview
            points={hasValidCoords ? [{ lat: parsedLat, lon: parsedLon }] : []}
            markerOnly
          />
        </View>
        <Button label={t.poiAddBtn} variant="filled" loading={poiBusy} disabled={poiBusy} onPress={handleAddPoi} style={{ marginTop: v3Spacing.small }} />
      </Card>

      {/* ── Import from GPX ── */}
      <Card style={{ width: '100%' }}>
        <Text style={styles.cardTitle}>{t.poiImportSection}</Text>
        <Text style={styles.itemStats}>{t.poiImportDesc}</Text>
        <Button label={t.poiImportBtn} variant="filled" loading={importPicking} disabled={importPicking} onPress={handleImportPick} style={{ marginTop: v3Spacing.small }} />

        {imported && imported.map((wp, i) => (
          <View key={`${wp.name}-${i}`} style={styles.row}>
            <View style={{ flex: 1 }}>
              <Text style={styles.itemName} numberOfLines={1}>{wp.name}</Text>
              <Text style={styles.itemStats}>{t.poiCoords(wp.latitude, wp.longitude)}</Text>
            </View>
            <TouchableOpacity style={styles.exportBtn} disabled={addingIndex !== null} onPress={() => handleAddImported(wp, i)}>
              <Text style={styles.exportBtnText}>{addingIndex === i ? '…' : t.poiItemAddBtn}</Text>
            </TouchableOpacity>
          </View>
        ))}
      </Card>

      {/* ── On the watch ── */}
      <Card style={{ width: '100%' }}>
        {/* Title + map/list view dropdown, right after the title on the left (moved here from
            Settings, André 2026-08-16; matches desktop). */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <Text style={styles.cardTitle}>{t.poiOnWatchSection}</Text>
          {!onWatchLoading && onWatch && onWatch.length > 0 && (
            <ViewModeToggle mode={viewMode} onChange={changeViewMode} />
          )}
        </View>

        {onWatchLoading && <Text style={styles.itemStats}>{t.poiOnWatchReading}</Text>}
        {!onWatchLoading && onWatchError && (
          <Text style={[styles.itemStats, { color: theme.error }]}>{t.poiOnWatchError(onWatchError)}</Text>
        )}
        {!onWatchLoading && onWatchError && onWatch && onWatch.length > 0 && (
          <Text style={styles.itemStats}>{t.poiOnWatchStale}</Text>
        )}
        {!onWatchLoading && !onWatchError && onWatch && onWatch.length === 0 && (
          <Text style={styles.itemStats}>{t.poiOnWatchEmpty}</Text>
        )}

        {!onWatchLoading && onWatch && onWatch.map((poi, i) => (
          <View key={`${poi.name}-${i}`} style={i > 0 ? styles.onWatchItem : { marginTop: v3Spacing.medium, gap: v3Spacing.small }}>
            {viewMode === 'map' && <TrackPreview points={[{ lat: poi.latitude, lon: poi.longitude }]} markerOnly height={90} />}
            <View style={styles.row}>
              {/* The POI's own watch icon and the name of its type in that watch's list. */}
              <Text style={[styles.typeGlyph, { fontSize: 22, color: theme.primary }]}>{poiTypeOf(poi.type, watchModel).glyph}</Text>
              <View style={{ flex: 1 }}>
                <Text style={styles.itemName} numberOfLines={1}>{poi.name}</Text>
                <Text style={styles.itemStats}>{poiTypeOf(poi.type, watchModel).name} · {t.poiCoords(poi.latitude, poi.longitude)}</Text>
              </View>
              <TouchableOpacity style={styles.exportBtn} disabled={exportingIndex !== null} onPress={() => handleExportItem(poi, i)}>
                <Text style={styles.exportBtnText}>{exportingIndex === i ? '…' : t.poiItemExportBtn}</Text>
              </TouchableOpacity>
              <TouchableOpacity style={styles.exportBtn} disabled={removingIndex !== null || onWatchLoading} onPress={() => handleRemoveItem(poi, i)}>
                <Text style={styles.exportBtnText}>{removingIndex === i ? '…' : t.poiItemRemoveBtn}</Text>
              </TouchableOpacity>
            </View>
          </View>
        ))}
      </Card>

    </ScrollView>
  );
}

const createStyles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  content: { padding: v3Spacing.medium, gap: v3Spacing.medium },
  cardTitle: { fontSize: v3Type.heading, fontWeight: '700', color: t.text },
  infoBadge: { width: 17, height: 17, borderRadius: 9, borderWidth: 1, borderColor: t.mutedText, alignItems: 'center', justifyContent: 'center' },
  infoBadgeText: { fontSize: 10, fontWeight: '700', color: t.mutedText, lineHeight: 11 },
  backdrop: { flex: 1, backgroundColor: '#00000066', alignItems: 'center', justifyContent: 'center', padding: 24 },
  dialogCard: { width: '100%', maxWidth: 420, backgroundColor: t.card, borderRadius: 14, padding: 16, paddingBottom: 40 },
  dialogTitle: { fontSize: v3Type.bodyLarge, fontWeight: '700', color: t.text },
  dialogText: { fontSize: v3Type.body, color: t.text, marginTop: 2 },
  dialogClose: { position: 'absolute', right: 10, bottom: 8, paddingVertical: 4, paddingHorizontal: 8 },
  dialogCloseText: { fontSize: v3Type.body, color: t.primary, fontWeight: '700' },
  row: { flexDirection: 'row', alignItems: 'center', gap: v3Spacing.small, marginTop: v3Spacing.small },
  itemName: { fontSize: v3Type.bodyLarge, fontWeight: '700', color: t.text },
  itemStats: { fontSize: v3Type.label, color: t.mutedText, marginTop: 2 },
  onWatchItem: { marginTop: v3Spacing.large, paddingTop: v3Spacing.medium, borderTopWidth: 1, borderTopColor: t.mutedText + '22', gap: v3Spacing.small },
  exportBtn: {
    paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8,
    backgroundColor: t.primary + '1F', borderWidth: 1, borderColor: t.primary,
  },
  exportBtnText: { color: t.primary, fontWeight: '600', fontSize: v3Type.label },
  typeLabel: { fontSize: v3Type.label, color: t.mutedText, marginTop: v3Spacing.small, marginBottom: 4 },
  typeChipRow: {
    flexDirection: 'row', alignItems: 'center', gap: 5,
    paddingVertical: 6, paddingHorizontal: 12, borderRadius: 999,
    borderWidth: 1, borderColor: t.mutedText + '55',
  },
  typeGlyph: { fontFamily: 'MaterialSymbolsRounded', fontSize: 16, color: t.text },
  // The selected chip's icon: the colour only. The icon font has one weight, and asking for bold
  // made Android fall back to another font, which drew an empty box (seen on the tablet 2026-11-13).
  typeGlyphSel: { color: t.background },
  typeChipSel: { backgroundColor: t.primary, borderColor: t.primary },
  typeChipText: { color: t.text, fontSize: v3Type.label },
  typeChipTextSel: { color: t.background, fontWeight: '700' },
});
