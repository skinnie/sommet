import React, { useCallback, useEffect, useState } from 'react';
import { View, Text, StyleSheet, Alert, ScrollView, TouchableOpacity, Modal, Pressable, TextInput } from 'react-native';
import { useFocusEffect, useRoute } from '@react-navigation/native';
import {
  addPoiToWatch, AddPoiState, readOnWatchPois, getCachedPois, exportSinglePoiToGpx, pickAndParseWaypoints,
  WatchPoi, removePoisFromWatch, getCachedPoiWatchModel,
} from '../services/PoiService';
import { poiTypesFor, poiTypeDefaultFor, poiTypeOf, isTraverseWatch, poiTypeListFor, poiTypeIn } from '../services/PoiTypes';
import {
  LibraryPoi, PoiToKeep, listLibraryPois, saveLibraryPois, renameLibraryPoi, deleteLibraryPoi, countKept,
} from '../services/PoiLibrary';
import { useDemo } from '../config/DemoContext';
import { demoPois, demoAddPoi, demoRemovePoi } from '../services/DemoData';
import { t } from '../i18n';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { Button, Dropdown, FieldRow, StatusNote } from '../components/ui/primitives';
import { TrackPreview } from '../components/TrackPreview';
import { LockableScrollView } from '../components/ScrollLock';
import { getWeatherLocation } from '../services/WeatherService';
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
//
// The library (André, 2026-11-22: "we have a library for the routes but not for the POIs... these
// two should be similar"), twin of the desktop's POIs page: the list card shows one source, the
// watch or the Library (services/PoiLibrary.ts). The library is Sommet's own list and what is on
// a watch is another: nothing here changes a watch until a POI is sent to it, and deleting here
// leaves the watch as it is. What goes in: "Save to library" on the Add card, every POI sent to
// the watch from that card, every POI of an imported GPX, and "Save to library" on a watch's POI.
// The screen is in the menu without a watch too (Home passes `watch`), as Routes is.
// Its new words are in English only for now, like the route library's.
export default function PoiScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);
  const params = useRoute<any>().params || {};

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
  // A watch to read and send to: Home says so; Testing mode's sample watch counts.
  const watchHere: boolean = demo.enabled || (params.watch ?? true);
  const typeList = poiTypeListFor(watchModel);

  // ---- The library ---------------------------------------------------------------------------
  const [library, setLibrary] = useState<LibraryPoi[]>([]);
  const refreshLibrary = useCallback(() => {
    listLibraryPois(typeList).then(setLibrary).catch(() => {});
  }, [typeList]);
  useFocusEffect(refreshLibrary);
  // The list card's source: the watch when one is here, else the Library (always there).
  const [source, setSource] = useState<number>(watchHere ? 0 : 1);   // 0 = on the watch, 1 = Library
  useEffect(() => { if (!watchHere) setSource(1); }, [watchHere]);
  const [note, setNote] = useState<{ text: string; tone: 'success' | 'error' } | null>(null);
  const [formNote, setFormNote] = useState<{ text: string; tone: 'success' | 'error' } | null>(null);
  const [importNote, setImportNote] = useState<{ text: string; tone: 'success' | 'error' } | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null);
  const [sendingId, setSendingId] = useState<string | null>(null);

  /** Keeps POIs in the library and says what happened, in the words the desktop uses. */
  async function keep(items: PoiToKeep[]): Promise<{ text: string; tone: 'success' | 'error' }> {
    try {
      const c = countKept(await saveLibraryPois(items));
      refreshLibrary();
      if (c.failed) return { text: `Couldn't save to the library: ${c.failed}`, tone: 'error' };
      if (c.saved === 0) {
        return { text: items.length === 1 ? 'Already in the library.' : 'All of them are already in the library.', tone: 'success' };
      }
      return { text: c.saved === 1 ? 'Saved to the library.' : `${c.saved} POIs saved to the library.`, tone: 'success' };
    } catch (e: any) {
      return { text: `Couldn't save to the library: ${e?.message ?? t.unknownError}`, tone: 'error' };
    }
  }
  // A POI of the watch's list, as the library keeps it (its type is a number in that watch's list).
  const fromWatch = (poi: WatchPoi): PoiToKeep => ({
    name: poi.name, lat: poi.latitude, lon: poi.longitude, ele: poi.elevation ?? null,
    type: poi.type ?? null, typeList,
  });
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
    if (!watchHere) return;          // nothing to read: the screen shows the library
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
  }, [demo.enabled, demo.variant, watchHere]);

  useFocusEffect(useCallback(() => { loadOnWatch(); }, [loadOnWatch]));

  const parsedLat = parseFloat(poiLat.replace(',', '.'));
  const parsedLon = parseFloat(poiLon.replace(',', '.'));
  const hasValidCoords = Number.isFinite(parsedLat) && parsedLat >= -90 && parsedLat <= 90
    && Number.isFinite(parsedLon) && parsedLon >= -180 && parsedLon <= 180;

  function formIsValid(): boolean {
    if (!poiName.trim()) {
      Alert.alert(t.poiInvalid, t.poiNameRequired);
      return false;
    }
    if (!hasValidCoords) {
      Alert.alert(t.poiInvalid, t.poiCoordsInvalid);
      return false;
    }
    return true;
  }
  const formItem = (): PoiToKeep => ({ name: poiName.trim(), lat: parsedLat, lon: parsedLon, type: poiType, typeList });

  // Keeps the POI in Sommet only; the fields stay, so it can still be sent to the watch.
  async function handleSaveToLibrary() {
    if (poiBusy || !formIsValid()) return;
    setFormNote(await keep([formItem()]));
  }

  async function handleAddPoi() {
    if (poiBusy || !formIsValid()) return;
    setFormNote(null);
    // Every POI made here is kept (the library and the watch then live their own lives).
    const item = formItem();
    keep([item]).then(r => { if (r.tone === 'error') setFormNote(r); });
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
      if (wps) {
        setImported(wps);
        setImportNote(null);
        // An imported GPX is kept in the library straight away; each POI can then go to the watch.
        if (wps.length > 0) {
          setImportNote(await keep(wps.map(w => ({ name: w.name, lat: w.latitude, lon: w.longitude, ele: w.elevation ?? null }))));
        }
      }
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

  // One library POI to the watch: its type as a number in this watch's list, and its altitude.
  async function handleSendFromLibrary(p: LibraryPoi) {
    if (sendingId !== null || poiBusy) return;
    setNote(null);
    if (demo.enabled) {
      setOnWatch(demoAddPoi(demo.variant, { name: p.name, latitude: p.lat, longitude: p.lon, type: p.typeHere }));
      setNote({ text: `“${p.name}” sent to the watch.`, tone: 'success' });
      return;
    }
    setSendingId(p.id);
    try {
      let failure: string | undefined;
      await addPoiToWatch(p.name, p.lat, p.lon, st => {
        if (st.phase === 'error') failure = st.error ?? t.unknownError;
      }, p.typeHere ?? -1, p.ele ?? undefined);
      if (failure) setNote({ text: failure, tone: 'error' });
      else { setNote({ text: `“${p.name}” sent to the watch.`, tone: 'success' }); loadOnWatch(); }
    } catch (e: any) {
      setNote({ text: e?.message ?? t.unknownError, tone: 'error' });
    } finally {
      setSendingId(null);
    }
  }

  function handleDeleteFromLibrary(p: LibraryPoi) {
    Alert.alert('Delete from the library', `Delete “${p.name}” from your saved POIs? A watch that has it keeps it.`, [
      { text: t.cancel, style: 'cancel' },
      { text: t.delete, style: 'destructive', onPress: async () => {
        const ok = await deleteLibraryPoi(p.id);
        setNote(ok ? { text: 'Deleted from the library.', tone: 'success' } : { text: 'That POI is no longer in the library.', tone: 'error' });
        refreshLibrary();
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
    <LockableScrollView style={styles.root} contentContainerStyle={styles.content}>

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
          {/* Hold still to grab and drag the map, pinch to zoom, double-tap to put the POI
              there (André, 2026-11-21). Before a place is chosen it opens on the weather place. */}
          <TrackPreview
            points={hasValidCoords ? [{ lat: parsedLat, lon: parsedLon }] : []}
            markerOnly
            height={220}
            fallbackCenter={{ lat: getWeatherLocation().latitude, lon: getWeatherLocation().longitude }}
            onDoubleTap={(lat, lon) => { setPoiLat(lat.toFixed(6)); setPoiLon(lon.toFixed(6)); }}
          />
          <Text style={styles.itemStats}>{t.poiMapHint}</Text>
        </View>
        {/* "Save to library" keeps it in Sommet only; a send to the watch keeps a copy too. */}
        <View style={[styles.row, { flexWrap: 'wrap' }]}>
          <Button label="Save to library" variant={watchHere ? 'outline' : 'filled'} grow={false} disabled={poiBusy} onPress={handleSaveToLibrary} />
          {watchHere && (
            <Button label={t.poiAddBtn} variant="filled" grow={false} loading={poiBusy} disabled={poiBusy} onPress={handleAddPoi} />
          )}
        </View>
        {formNote && <StatusNote text={formNote.text} tone={formNote.tone} closable />}
      </Card>

      {/* ── Import from GPX ── */}
      <Card style={{ width: '100%' }}>
        <Text style={styles.cardTitle}>{t.poiImportSection}</Text>
        <Text style={styles.itemStats}>{t.poiImportDesc}</Text>
        <Button label={t.poiImportBtn} variant="filled" loading={importPicking} disabled={importPicking} onPress={handleImportPick} style={{ marginTop: v3Spacing.small }} />
        {importNote && <StatusNote text={importNote.text} tone={importNote.tone} closable />}

        {imported && imported.map((wp, i) => (
          <View key={`${wp.name}-${i}`} style={styles.row}>
            <View style={{ flex: 1 }}>
              <Text style={styles.itemName} numberOfLines={1}>{wp.name}</Text>
              <Text style={styles.itemStats}>{t.poiCoords(wp.latitude, wp.longitude)}</Text>
            </View>
            {watchHere && (
              <TouchableOpacity style={styles.exportBtn} disabled={addingIndex !== null} onPress={() => handleAddImported(wp, i)}>
                <Text style={styles.exportBtnText}>{addingIndex === i ? '…' : t.poiItemAddBtn}</Text>
              </TouchableOpacity>
            )}
          </View>
        ))}
      </Card>

      {/* ── One source at a time: the watch or the Library (desktop's drop-down) ── */}
      <Card style={{ width: '100%' }}>
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap' }}>
          {watchHere
            ? <Dropdown value={source} onSelect={v => { setSource(v); setNote(null); if (v === 1) refreshLibrary(); }}
                choices={[{ value: 0, label: t.poiOnWatchSection }, { value: 1, label: 'Library' }]} />
            : <Text style={styles.cardTitle}>Library</Text>}
          {(source === 1 ? library.length > 0 : (!onWatchLoading && onWatch && onWatch.length > 0)) && (
            <ViewModeToggle mode={viewMode} onChange={changeViewMode} />
          )}
          {source === 0 && !onWatchLoading && onWatch && onWatch.length > 0 && (
            <TouchableOpacity hitSlop={8} accessibilityLabel="Save all POIs to the library"
              onPress={async () => setNote(await keep(onWatch.map(fromWatch)))}>
              <Text style={styles.linkText}>Save all to library</Text>
            </TouchableOpacity>
          )}
        </View>
        {note && <StatusNote text={note.text} tone={note.tone} closable />}

        {/* ── Library ── */}
        {source === 1 && library.length === 0 && (
          <Text style={styles.itemStats}>Nothing saved yet. A POI you add or import here is kept in the library, and so is one you save from a watch.</Text>
        )}
        {source === 1 && library.length > 0 && (
          <Text style={styles.itemStats}>Your POIs, kept in Sommet. A watch has its own list: send it the ones you want there. Deleting here leaves the watch as it is.</Text>
        )}
        {source === 1 && library.map((p, i) => (
          <View key={p.id} style={i > 0 ? styles.onWatchItem : { marginTop: v3Spacing.medium, gap: v3Spacing.small }}>
            {viewMode === 'map' && <TrackPreview points={[{ lat: p.lat, lon: p.lon }]} markerOnly height={90} />}
            <View style={styles.row}>
              {/* The icon and its name, from the list the POI's type number is in. */}
              <Text style={[styles.typeGlyph, { fontSize: 22, color: theme.primary }]}>{poiTypeIn(p.typeList, p.type).glyph}</Text>
              <View style={{ flex: 1 }}>
                <Text style={styles.itemName} numberOfLines={1}>{p.name}</Text>
                <Text style={styles.itemStats}>{poiTypeIn(p.typeList, p.type).name} · {t.poiCoords(p.lat, p.lon)}</Text>
              </View>
            </View>
            <View style={[styles.row, { flexWrap: 'wrap', marginTop: 0 }]}>
              {watchHere && (
                <Button label={t.poiAddBtn} variant="text" grow={false} busy={sendingId === p.id}
                  disabled={sendingId !== null || poiBusy} onPress={() => handleSendFromLibrary(p)} />
              )}
              <Button label={t.poiItemExportBtn} variant="text" grow={false} disabled={exportingIndex !== null}
                onPress={() => handleExportItem({ name: p.name, latitude: p.lat, longitude: p.lon, elevation: p.ele ?? undefined }, i)} />
              <Button label="Rename" variant="text" grow={false} onPress={() => setRenaming({ id: p.id, value: p.name })} />
              <Button label="Delete" variant="text" danger grow={false} onPress={() => handleDeleteFromLibrary(p)} />
            </View>
          </View>
        ))}

        {/* ── On the watch ── */}
        {source === 0 && watchHere && (<>
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
              </View>
              {/* Under the name, so three buttons fit a phone (as the route library's rows). */}
              <View style={[styles.row, { flexWrap: 'wrap', marginTop: 0 }]}>
                <Button label={t.poiItemExportBtn} variant="text" grow={false} busy={exportingIndex === i}
                  disabled={exportingIndex !== null} onPress={() => handleExportItem(poi, i)} />
                <Button label="Save to library" variant="text" grow={false}
                  onPress={async () => setNote(await keep([fromWatch(poi)]))} />
                <Button label={t.poiItemRemoveBtn} variant="text" danger grow={false} busy={removingIndex === i}
                  disabled={removingIndex !== null || onWatchLoading} onPress={() => handleRemoveItem(poi, i)} />
              </View>
            </View>
          ))}
        </>)}
      </Card>

      <Modal visible={renaming != null} transparent animationType="fade" onRequestClose={() => setRenaming(null)}>
        <Pressable style={styles.backdrop} onPress={() => setRenaming(null)}>
          <Pressable style={[styles.dialogCard, { paddingBottom: 16 }]} onPress={() => {}}>
            <Text style={styles.dialogTitle}>Rename POI</Text>
            <TextInput style={styles.renameInput} autoFocus value={renaming?.value ?? ''}
              onChangeText={v => setRenaming(x => x && { ...x, value: v })} placeholderTextColor={theme.mutedText} />
            <View style={[styles.row, { justifyContent: 'flex-end' }]}>
              <Button label={t.cancel} variant="text" grow={false} onPress={() => setRenaming(null)} />
              <Button label="OK" variant="text" grow={false} onPress={() => {
                const x = renaming; setRenaming(null);
                if (x) renameLibraryPoi(x.id, x.value).then(refreshLibrary);
              }} />
            </View>
          </Pressable>
        </Pressable>
      </Modal>

    </LockableScrollView>
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
  linkText: { fontSize: v3Type.label, color: t.mutedText, fontWeight: '700' },
  renameInput: { borderWidth: 1, borderColor: t.mutedText + '55', borderRadius: 10, color: t.text,
                 paddingHorizontal: 12, paddingVertical: 8, fontSize: v3Type.body, marginVertical: v3Spacing.small },
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
