import React, { useCallback, useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Alert, ScrollView, TouchableOpacity, Modal, Pressable, TextInput, useWindowDimensions } from 'react-native';
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
import { syncLibrariesNow, syncLibrariesSoon } from '../services/LibrarySyncRun';
import { demoPois, demoAddPoi, demoRemovePoi } from '../services/DemoData';
import { t } from '../i18n';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { Button, Dropdown, FieldRow, StatusNote } from '../components/ui/primitives';
import { TrackPreview } from '../components/TrackPreview';
import { LockableScrollView } from '../components/ScrollLock';
import { getWeatherLocation } from '../services/WeatherService';

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
//
// One map and one list, as on the desktop (André, 2026-11-23: "integrate the idea of the map +
// list but we take the map where we already have to add poi"). The map shows every POI of the
// list and is where a new one is placed: a double-tap or + Add POI puts an orange pin that can
// be dragged, and the form opens beside the map (above the list on a phone). A tap on a pin
// marks its line; a tap on a line goes to its pin and opens its actions.
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
  // The library is shared between devices through Sommet Sync: fetch what the others saved when
  // the screen opens, and send a change made here a few seconds after it.
  useFocusEffect(useCallback(() => {
    syncLibrariesNow().then(changed => { if (changed) refreshLibrary(); }).catch(() => {});
  }, [refreshLibrary]));
  // The list card's source: the watch when one is here, else the Library (always there).
  const [source, setSource] = useState<number>(watchHere ? 0 : 1);   // 0 = on the watch, 1 = Library
  useEffect(() => { if (!watchHere) setSource(1); }, [watchHere]);
  const [note, setNote] = useState<{ text: string; tone: 'success' | 'error' } | null>(null);
  // The new POI: its pin on the map and the form. `query` is "Find in this list".
  const [adding, setAdding] = useState(false);
  const [query, setQuery] = useState('');
  const pendingPick = useRef<{ name: string; lat: number; lon: number } | null>(null);
  const [renaming, setRenaming] = useState<{ id: string; value: string } | null>(null);
  const [sendingId, setSendingId] = useState<string | null>(null);

  /** Keeps POIs in the library and says what happened, in the words the desktop uses. */
  async function keep(items: PoiToKeep[]): Promise<{ text: string; tone: 'success' | 'error' }> {
    try {
      const c = countKept(await saveLibraryPois(items));
      refreshLibrary();
      if (c.saved > 0) syncLibrariesSoon(refreshLibrary);
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

  const [importPicking, setImportPicking] = useState(false);

  const [onWatch, setOnWatch] = useState<WatchPoi[] | null>(null);
  const [onWatchLoading, setOnWatchLoading] = useState(false);
  const [onWatchError, setOnWatchError] = useState<string | undefined>();
  const [exportingIndex, setExportingIndex] = useState<number | null>(null);
  const [removingIndex, setRemovingIndex] = useState<number | null>(null);
  // ONE map with every POI of the shown list as a marker, next to the list on a wide screen and
  // above it on a narrow one. A tap on a marker marks its line and brings it into view; a tap on
  // a line moves the map to its marker, and a second tap shows every marker again.
  const { height: winH } = useWindowDimensions();
  const [listW, setListW] = useState(0);
  const [picked, setPicked] = useState(-1);
  const [focus, setFocus] = useState<{ lat: number; lon: number; key: number } | null>(null);
  const paneRef = useRef<ScrollView>(null);
  const mapCentre = useRef<(() => { lat: number; lon: number }) | null>(null);
  const rowY = useRef<Record<number, number>>({});
  function pickFromList(i: number, lat: number, lon: number) {
    if (picked === i) { setPicked(-1); setFocus(null); return; }
    setPicked(i); setFocus({ lat, lon, key: Date.now() });
  }
  // Puts the new POI's pin at a place and opens the form (moves the pin if it is already open).
  function placePin(lat: number, lon: number) {
    if (!adding) { setPoiName(''); setPoiType(poiTypeDefaultFor(watchModel)); }
    setPoiLat(lat.toFixed(6)); setPoiLon(lon.toFixed(6));
    setPicked(-1);
    setAdding(true);
  }
  function closeForm() { setAdding(false); setPoiName(''); setPoiLat(''); setPoiLon(''); }
  function pickFromMap(i: number) {
    setPicked(i);
    paneRef.current?.scrollTo({ y: Math.max(0, (rowY.current[i] ?? 0) - 8), animated: true });
  }

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

  // Keeps the POI in Sommet only, then shows it in the library, marked.
  async function handleSaveToLibrary() {
    if (poiBusy || !formIsValid()) return;
    const item = formItem();
    pendingPick.current = { name: item.name, lat: parsedLat, lon: parsedLon };
    const r = await keep([item]);
    setNote(r);
    if (r.tone === 'error') { pendingPick.current = null; return; }
    closeForm();
    setSource(1);
  }

  async function handleAddPoi() {
    if (poiBusy || !formIsValid()) return;
    setNote(null);
    // Every POI made here is kept (the library and the watch then live their own lives).
    const item = formItem();
    keep([item]).then(r => { if (r.tone === 'error') setNote(r); });
    if (demo.enabled) {
      setOnWatch(demoAddPoi(demo.variant, { name: poiName.trim(), latitude: parsedLat, longitude: parsedLon, type: poiType }));
      closeForm();
      return;
    }
    try {
      await addPoiToWatch(poiName.trim(), parsedLat, parsedLon, setPoiState, poiType);
      setPoiState(s => {
        if (s.phase === 'done') {
          closeForm();
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
      // An imported GPX is kept in the library straight away; the screen shows it there, and
      // each POI can then be sent to the watch from its line.
      if (wps && wps.length > 0) {
        const r = await keep(wps.map(w => ({ name: w.name, lat: w.latitude, lon: w.longitude, ele: w.elevation ?? null })));
        setNote(r);
        if (r.tone === 'success') setSource(1);
      }
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
    } finally {
      setImportPicking(false);
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
        if (ok) syncLibrariesSoon(refreshLibrary);
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

  // The list as shown: the source, narrowed by "Find in this list". Indexes count in these.
  const q = query.trim().toLowerCase();
  const libShown = library.filter(p => !q || p.name.toLowerCase().includes(q));
  const watchShown = (watchHere && !onWatchLoading && onWatch ? onWatch : []).filter(p => !q || p.name.toLowerCase().includes(q));
  const sourceCount = source === 1 ? library.length : (watchHere && !onWatchLoading && onWatch ? onWatch.length : 0);
  const mapPoints = source === 1 ? libShown.map(p => ({ lat: p.lat, lon: p.lon }))
    : watchShown.map(p => ({ lat: p.latitude, lon: p.longitude }));
  const mapPointsKey = `${source}:${mapPoints.map(p => `${p.lat},${p.lon}`).join(';')}`;
  useEffect(() => {
    setFocus(null); rowY.current = {};
    const want = pendingPick.current;
    const i = want && source === 1 ? libShown.findIndex(p => p.name === want.name
      && Math.abs(p.lat - want.lat) < 0.0002 && Math.abs(p.lon - want.lon) < 0.0002) : -1;
    if (i >= 0) {
      pendingPick.current = null;
      setPicked(i); setFocus({ lat: libShown[i].lat, lon: libShown[i].lon, key: Date.now() });
    } else setPicked(-1);                                       // another list: nothing picked
  }, [mapPointsKey]);   // eslint-disable-line react-hooks/exhaustive-deps
  const sideBySide = listW >= 600;
  const paneH = Math.max(320, Math.min(600, winH - 270));
  const weather = getWeatherLocation();
  const addType = poiTypesFor(watchModel).find(pt => pt.id === poiType);
  const mapEl = (
    <View style={sideBySide ? { flex: 1.35 } : undefined}>
      <TrackPreview points={mapPoints} multiMarker controls onlyPointZoom={13} height={sideBySide ? paneH : 280}
        fallbackCenter={{ lat: weather.latitude, lon: weather.longitude }}
        selectedIndex={picked} onMarkerTap={pickFromMap} focusOn={focus} centreRef={mapCentre}
        onDoubleTap={placePin}
        draft={adding && hasValidCoords ? { lat: parsedLat, lon: parsedLon } : null}
        draftGlyph={addType?.glyph}
        onDraftMove={(lat, lon) => { setPoiLat(lat.toFixed(6)); setPoiLon(lon.toFixed(6)); }} />
    </View>
  );
  // A line of the list: the picked one is marked, and its actions open under it.
  const lineStyle = (i: number) => [styles.line, picked === i && { backgroundColor: theme.primary + '1F' }];

  return (
    <LockableScrollView style={styles.root} contentContainerStyle={styles.content}>

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
        {/* One source at a time (desktop's drop-down); Import and + Add POI on the right. */}
        <View style={styles.bar}>
          <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12, flexWrap: 'wrap', flexShrink: 1 }}>
            {watchHere
              ? <Dropdown value={source} onSelect={v => { setSource(v); setNote(null); if (v === 1) refreshLibrary(); }}
                  choices={[{ value: 0, label: t.poiOnWatchSection }, { value: 1, label: 'Library' }]} />
              : <Text style={styles.cardTitle}>Library</Text>}
            <TouchableOpacity style={styles.infoBadge} onPress={() => setInfoOpen(true)} hitSlop={8}>
              <Text style={styles.infoBadgeText}>i</Text>
            </TouchableOpacity>
            {source === 0 && !onWatchLoading && onWatch && onWatch.length > 0 && (
              <TouchableOpacity hitSlop={8} accessibilityLabel="Save all POIs to the library"
                onPress={async () => setNote(await keep(onWatch.map(fromWatch)))}>
                <Text style={styles.linkText}>Save all to library</Text>
              </TouchableOpacity>
            )}
          </View>
          <View style={{ flexDirection: 'row', gap: v3Spacing.small }}>
            <Button label="Import GPX…" variant="outline" grow={false} loading={importPicking} disabled={importPicking} onPress={handleImportPick} />
            <Button label="+ Add POI" accent grow={false} onPress={() => {
              const c = mapCentre.current?.() ?? { lat: weather.latitude, lon: weather.longitude };
              placePin(c.lat, c.lon);
            }} />
          </View>
        </View>
        {note && <StatusNote text={note.text} tone={note.tone} closable />}

        <View onLayout={e => { const w = Math.round(e.nativeEvent.layout.width); if (w !== listW) setListW(w); }}
              style={[{ marginTop: v3Spacing.small, gap: v3Spacing.medium },
                      sideBySide && { flexDirection: 'row', alignItems: 'flex-start' }]}>
        {mapEl}
        <View style={sideBySide ? { flex: 1, height: paneH } : undefined}>
          {/* ── The new POI ── */}
          {adding && (
            <View style={styles.form}>
              <View style={{ flexDirection: 'row', justifyContent: 'space-between', alignItems: 'baseline' }}>
                <Text style={styles.itemName}>New POI</Text>
                <Text style={styles.itemStats}>{poiName.length} / 15</Text>
              </View>
              <FieldRow icon="poi" value={poiName} onChangeText={v => setPoiName(v.slice(0, 15))} placeholder={t.poiNamePlaceholder} editable={!poiBusy} />
              <ScrollView horizontal showsHorizontalScrollIndicator={false} nestedScrollEnabled
                style={{ flexGrow: 0 }} contentContainerStyle={{ gap: v3Spacing.small, paddingVertical: 2 }}>
                {poiTypesFor(watchModel).map(pt => (
                  <TouchableOpacity key={pt.id} disabled={poiBusy} onPress={() => setPoiType(pt.id)}
                    style={[styles.typeChipRow, poiType === pt.id && styles.typeChipSel]}>
                    <Text style={[styles.typeGlyph, poiType === pt.id && styles.typeGlyphSel]}>{pt.glyph}</Text>
                    <Text style={[styles.typeChipText, poiType === pt.id && styles.typeChipTextSel]}>{pt.name}</Text>
                  </TouchableOpacity>
                ))}
              </ScrollView>
              {/* Each in its own flex:1 View: FieldRow's `style` only reaches its inner TextInput. */}
              <View style={[styles.row, { marginTop: 0 }]}>
                <View style={{ flex: 1 }}>
                  <FieldRow icon="map" value={poiLat} onChangeText={setPoiLat} placeholder={t.poiLat} keyboardType="numbers-and-punctuation" editable={!poiBusy} />
                </View>
                <View style={{ flex: 1 }}>
                  <FieldRow icon="map" value={poiLon} onChangeText={setPoiLon} placeholder={t.poiLon} keyboardType="numbers-and-punctuation" editable={!poiBusy} />
                </View>
              </View>
              {/* "Save to library" keeps it in Sommet only; a send to the watch keeps a copy too. */}
              <View style={[styles.row, { flexWrap: 'wrap', marginTop: 0 }]}>
                <Button label="Save to library" variant="outline" accent={!watchHere} grow={false} disabled={poiBusy || !poiName.trim()} onPress={handleSaveToLibrary} />
                {watchHere && (
                  <Button label={t.poiAddBtn} accent grow={false} loading={poiBusy} disabled={poiBusy || !poiName.trim()} onPress={handleAddPoi} />
                )}
                <Button label={t.cancel} variant="text" grow={false} disabled={poiBusy} onPress={closeForm} />
              </View>
            </View>
          )}
          {!adding && <Text style={styles.itemStats}>Double-tap the map to add a POI. Hold the map still to drag it, pinch to zoom.</Text>}
          {!adding && sourceCount > 0 && (
            <TextInput style={styles.findInput} value={query} onChangeText={setQuery} placeholder="Find in this list"
              placeholderTextColor={theme.mutedText} autoCorrect={false} />
          )}

          {source === 1 && library.length === 0 && (
            <Text style={styles.itemStats}>Nothing saved yet. A POI you add or import here is kept in the library, and so is one you save from a watch.</Text>
          )}
          {source === 0 && watchHere && onWatchLoading && <Text style={styles.itemStats}>{t.poiOnWatchReading}</Text>}
          {source === 0 && watchHere && !onWatchLoading && onWatchError && (
            <Text style={[styles.itemStats, { color: theme.error }]}>{t.poiOnWatchError(onWatchError)}</Text>
          )}
          {source === 0 && watchHere && !onWatchLoading && onWatchError && onWatch && onWatch.length > 0 && (
            <Text style={styles.itemStats}>{t.poiOnWatchStale}</Text>
          )}
          {source === 0 && watchHere && !onWatchLoading && !onWatchError && onWatch && onWatch.length === 0 && (
            <Text style={styles.itemStats}>{t.poiOnWatchEmpty}</Text>
          )}
          {sourceCount > 0 && mapPoints.length === 0 && <Text style={styles.itemStats}>No POI with that name.</Text>}

          {/* The list scrolls in its own pane, so map and list stay together. */}
          <ScrollView ref={paneRef} nestedScrollEnabled keyboardShouldPersistTaps="handled"
            style={sideBySide ? { flex: 1, marginTop: v3Spacing.small } : { maxHeight: 420, marginTop: v3Spacing.small }}>
          {source === 1 && libShown.map((p, i) => (
            <View key={p.id} onLayout={e => { rowY.current[i] = e.nativeEvent.layout.y; }} style={lineStyle(i)}>
              <Pressable style={styles.lineMain} onPress={() => pickFromList(i, p.lat, p.lon)}>
                {/* The icon and its name, from the list the POI's type number is in. */}
                <Text style={[styles.typeGlyph, { fontSize: 22, color: theme.primary }]}>{poiTypeIn(p.typeList, p.type).glyph}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={styles.itemName} numberOfLines={1}>{p.name}</Text>
                  <Text style={styles.itemStats} numberOfLines={1}>{poiTypeIn(p.typeList, p.type).name} · {t.poiCoords(p.lat, p.lon)}</Text>
                </View>
              </Pressable>
              {picked === i && (
                <View style={styles.lineActions}>
                  {watchHere && (
                    <Button label={t.poiAddBtn} variant="text" grow={false} busy={sendingId === p.id}
                      disabled={sendingId !== null || poiBusy} onPress={() => handleSendFromLibrary(p)} />
                  )}
                  <Button label={t.poiItemExportBtn} variant="text" grow={false} disabled={exportingIndex !== null}
                    onPress={() => handleExportItem({ name: p.name, latitude: p.lat, longitude: p.lon, elevation: p.ele ?? undefined }, i)} />
                  <Button label="Rename" variant="text" grow={false} onPress={() => setRenaming({ id: p.id, value: p.name })} />
                  <Button label="Delete" variant="text" danger grow={false} onPress={() => handleDeleteFromLibrary(p)} />
                </View>
              )}
            </View>
          ))}

          {source === 0 && watchShown.map((poi, i) => (
            <View key={`${poi.name}-${i}`} onLayout={e => { rowY.current[i] = e.nativeEvent.layout.y; }} style={lineStyle(i)}>
              <Pressable style={styles.lineMain} onPress={() => pickFromList(i, poi.latitude, poi.longitude)}>
                {/* The POI's own watch icon and the name of its type in that watch's list. */}
                <Text style={[styles.typeGlyph, { fontSize: 22, color: theme.primary }]}>{poiTypeOf(poi.type, watchModel).glyph}</Text>
                <View style={{ flex: 1 }}>
                  <Text style={styles.itemName} numberOfLines={1}>{poi.name}</Text>
                  <Text style={styles.itemStats} numberOfLines={1}>{poiTypeOf(poi.type, watchModel).name} · {t.poiCoords(poi.latitude, poi.longitude)}</Text>
                </View>
              </Pressable>
              {picked === i && (
                <View style={styles.lineActions}>
                  <Button label={t.poiItemExportBtn} variant="text" grow={false} busy={exportingIndex === i}
                    disabled={exportingIndex !== null} onPress={() => handleExportItem(poi, i)} />
                  <Button label="Save to library" variant="text" grow={false}
                    onPress={async () => setNote(await keep([fromWatch(poi)]))} />
                  <Button label={t.poiItemRemoveBtn} variant="text" danger grow={false} busy={removingIndex === i}
                    disabled={removingIndex !== null || onWatchLoading} onPress={() => handleRemoveItem(poi, i)} />
                </View>
              )}
            </View>
          ))}
          </ScrollView>
        </View>
        </View>
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
                if (x) renameLibraryPoi(x.id, x.value).then(ok => { refreshLibrary(); if (ok) syncLibrariesSoon(refreshLibrary); });
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
  bar: { flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between', flexWrap: 'wrap', gap: v3Spacing.small },
  form: { backgroundColor: t.mutedText + '14', borderRadius: 10, padding: v3Spacing.small, gap: v3Spacing.small, marginBottom: v3Spacing.small },
  findInput: { borderWidth: 1, borderColor: t.mutedText + '55', borderRadius: 10, color: t.text,
               paddingHorizontal: 12, paddingVertical: 6, fontSize: v3Type.body, marginTop: v3Spacing.small },
  line: { borderRadius: 8, marginBottom: 2 },
  lineMain: { flexDirection: 'row', alignItems: 'center', gap: v3Spacing.small, paddingHorizontal: 6, paddingVertical: 6 },
  lineActions: { flexDirection: 'row', flexWrap: 'wrap', gap: v3Spacing.small, paddingHorizontal: 6, paddingBottom: 6 },
  linkText: { fontSize: v3Type.label, color: t.mutedText, fontWeight: '700' },
  renameInput: { borderWidth: 1, borderColor: t.mutedText + '55', borderRadius: 10, color: t.text,
                 paddingHorizontal: 12, paddingVertical: 8, fontSize: v3Type.body, marginVertical: v3Spacing.small },
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
