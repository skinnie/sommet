import React, { useCallback, useRef, useState } from 'react';
import { View, Text, StyleSheet, Alert, ScrollView, TouchableOpacity, Modal, Pressable, Linking, useWindowDimensions } from 'react-native';
import { useFocusEffect, useRoute } from '@react-navigation/native';
import {
  pickAndParseRoute, uploadRoute, readOnWatchNavigation, getCachedNavigation, exportSingleRouteToGpx,
  PendingRoute, SendRouteState,
} from '../services/NavigationService';
import { WatchRoute } from '../services/RouteReader';
import { t } from '../i18n';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { Button, StatusLine } from '../components/ui/primitives';
import { TrackPreview } from '../components/TrackPreview';
import { SortBar } from '../components/ui/SortBar';
import { getViewMode, setViewMode as persistViewMode, sortItems, sortKeysFor, SortKey, ViewMode } from '../services/ListViewPrefs';
import { ViewModeToggle } from '../components/ui/ViewModeToggle';
import { detectBryton } from '../services/BrytonUsb';
import { sendRouteToBryton } from '../services/BrytonTrack';
import { getKnownMagene, type KnownMagene } from '../services/MageneStore';
import { sendRoute as sendRouteToMagene } from '../services/MageneRoute';

// The imported route as a minimal GPX, for the bike-computer encoders (MageneRoute/BrytonTrack
// both take GPX text, like their desktop tools).
function pendingToGpx(r: PendingRoute): string {
  const pts = r.points.map(p => `<trkpt lat="${p.lat}" lon="${p.lon}">${p.alt != null ? `<ele>${p.alt}</ele>` : ''}</trkpt>`);
  return `<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="Sommet"><trk><name>${r.name}</name><trkseg>${pts.join('')}</trkseg></trk></gpx>`;
}

// v3.0 UI port (2026-08-09, "re do routes... to match entirely desktop") - real structural
// rebuild matching desktop's own RoutesPage.qml: an "Import a route" card with a real
// preview (name/points/track shape) before you commit to uploading, not one opaque
// pick-and-immediately-write button, and a real "On the watch" card listing every route
// already there with its own track preview and a per-route Export - this screen used to be
// pure action buttons with no browsing at all.
//
// Real, same day ("I would prefer an immediate map view on this side") - TrackPreview
// itself now renders a real tile-map background (see its own header comment), so the
// separate tap-through "Map" button/TrackMapScreen this screen briefly had was removed -
// the preview already IS the map, immediately, matching desktop's own RoutesPage.qml (a
// live MapView per item, no tap-through screen at all).
function formatDist(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
}

export default function RouteScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);

  // Routes is always in the menu now (André, 2026-09-25): the watch parts show only when Home
  // says a watch is connected; a GPX can be imported and sent to a Bryton / Magene without one.
  const watchHere: boolean = (useRoute<any>().params?.watch) ?? true;
  const [pending, setPending] = useState<PendingRoute | null>(null);
  // Bike computers around (desktop RoutesPage parity: "Send to Bryton" / "Send to Magene").
  const [brytonPlugged, setBrytonPlugged] = useState(false);
  const [magene, setMagene] = useState<KnownMagene | null>(null);
  const [bikeBusy, setBikeBusy] = useState('');
  const [bikeMsg, setBikeMsg] = useState('');
  useFocusEffect(useCallback(() => {
    detectBryton().then(d => setBrytonPlugged(d.plugged && d.granted)).catch(() => {});
    getKnownMagene().then(setMagene).catch(() => {});
  }, []));
  async function sendToBike(target: 'bryton' | 'magene') {
    if (!pending || bikeBusy) return;
    setBikeBusy(target); setBikeMsg('');
    try {
      const gpx = pendingToGpx(pending);
      if (target === 'bryton') {
        const r = await sendRouteToBryton(gpx, pending.name);
        setBikeMsg(`“${r.name}” installed on the Bryton ✓ — Follow Track after unplugging.`);
      } else {
        const r = await sendRouteToMagene(magene!.address, gpx);
        setBikeMsg(r.ok ? `Sent to the Magene C406 ✓ (it keeps one route)` : (r.error || 'Send failed'));
      }
    } catch (e: any) { setBikeMsg(String(e?.message ?? e)); } finally { setBikeBusy(''); }
  }
  const [picking, setPicking] = useState(false);
  const [plannerOpen, setPlannerOpen] = useState(false);   // route-planner help dialog
  // Anchor the dialog just below the "i" (like desktop) rather than centering it. We measure the
  // badge's window position on tap and clamp so the card stays on screen.
  const { width: winW } = useWindowDimensions();
  const infoRef = useRef<View>(null);
  const [anchor, setAnchor] = useState<{ x: number; y: number }>({ x: 12, y: 80 });
  const CARD_W = Math.min(340, winW - 24);
  const openPlanner = () => {
    const node = infoRef.current;
    if (node && node.measureInWindow) {
      node.measureInWindow((x, y, _w, h) => {
        setAnchor({
          x: Math.max(12, Math.min(x, winW - CARD_W - 12)),
          y: y + h + 6,
        });
        setPlannerOpen(true);
      });
    } else {
      setPlannerOpen(true);
    }
  };
  const [sendState, setSendState] = useState<SendRouteState>({ phase: 'idle' });
  const sendBusy = sendState.phase === 'connecting' || sendState.phase === 'writing';

  const [onWatch, setOnWatch] = useState<WatchRoute[] | null>(null);
  const [onWatchLoading, setOnWatchLoading] = useState(false);
  const [onWatchError, setOnWatchError] = useState<string | undefined>();
  const [exportingIndex, setExportingIndex] = useState<number | null>(null);
  // Map/list view (persisted Settings pref) + in-page sort, same pattern as the Activities list.
  const [viewMode, setViewMode] = useState<ViewMode>('map');
  const [sortKey, setSortKey] = useState<SortKey>('name');
  useFocusEffect(useCallback(() => { getViewMode('routes').then(setViewMode); }, []));
  function changeViewMode(m: ViewMode) { setViewMode(m); persistViewMode('routes', m); }
  const sortedOnWatch = onWatch
    ? sortItems(onWatch.map(r => ({ r, name: r.name, distanceM: r.distanceM, ascentM: r.ascentM })), sortKey).map(x => x.r)
    : null;

  // Real, 2026-08-10 ("it is not upon the watch to give you that, is on the app to store
  // the activities, so they can load almost immediately and just refresh what is new") -
  // the persisted cache (if any) renders instantly, with no loading spinner; the real watch
  // read then runs in the background and silently updates the list when it lands. A cache
  // miss (first ever use) falls back to the old spinner+error behavior - there's nothing to
  // show instantly yet.
  const loadOnWatch = useCallback(async () => {
    const cached = await getCachedNavigation();
    if (cached) {
      setOnWatch(cached.routes);
    } else {
      setOnWatchLoading(true);
    }
    setOnWatchError(undefined);
    try {
      const nav = await readOnWatchNavigation();
      setOnWatch(nav.routes);
    } catch (e: any) {
      // A failed background refresh shouldn't blank out a list we already have real,
      // if possibly stale, data for - only surface the error when there's nothing to show.
      if (!cached) setOnWatchError(e?.message ?? t.unknownError);
    } finally {
      setOnWatchLoading(false);
    }
  }, []);

  useFocusEffect(useCallback(() => { if (watchHere) loadOnWatch(); }, [loadOnWatch, watchHere]));

  async function handlePick() {
    if (picking || sendBusy) return;
    setPicking(true);
    try {
      const route = await pickAndParseRoute();
      if (route) setPending(route);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
    } finally {
      setPicking(false);
    }
  }

  function handleUpload() {
    if (!pending || sendBusy) return;
    Alert.alert(
      t.sendRouteConfirmTitle,
      t.sendRouteConfirmMsg,
      [
        { text: t.cancel, style: 'cancel' },
        { text: t.sendRouteConfirmBtn, onPress: runUpload },
      ]
    );
  }

  async function runUpload() {
    if (!pending) return;
    try {
      await uploadRoute(pending, setSendState);
      setSendState(s => {
        if (s.phase === 'done') {
          setPending(null);
          loadOnWatch();
        } else if (s.phase === 'error') {
          Alert.alert(t.error, s.error ?? t.unknownError);
        }
        return s;
      });
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
      setSendState({ phase: 'error', error: e?.message });
    }
  }

  async function handleExportItem(route: WatchRoute, index: number) {
    if (exportingIndex !== null) return;
    setExportingIndex(index);
    try {
      await exportSingleRouteToGpx(route);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
    } finally {
      setExportingIndex(null);
    }
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>

      {/* Route-planner help dialog - the tools that produce a GPX this screen can import.
          Links open in the browser (Linking.openURL), matching desktop's RoutesPage. */}
      <Modal visible={plannerOpen} transparent animationType="fade" onRequestClose={() => setPlannerOpen(false)}>
        <Pressable style={styles.backdrop} onPress={() => setPlannerOpen(false)}>
          <Pressable style={[styles.dialogCard, { position: 'absolute', left: anchor.x, top: anchor.y, width: CARD_W }]} onPress={() => {}}>
            <Text style={styles.dialogTitle}>{t.routePlannerTitle}</Text>
            <Text style={[styles.dialogText, { fontWeight: '700', marginTop: 4 }]}>{t.routeWatchNote}</Text>
            <Text style={styles.dialogText}>{t.routePlannerIntro}</Text>
            {[
              { name: 'Suunto planner', qual: t.routePlannerOnline, url: 'https://routeplanner.suunto.com/' },
              { name: 'Komoot', qual: t.routePlannerOnline, url: 'https://www.komoot.com/' },
              { name: 'Openrunner', qual: t.routePlannerOnline, url: 'https://www.openrunner.com/' },
              { name: 'Garmin Basecamp', qual: t.routePlannerOfflineWinMac, url: 'https://www.garmin.com/en-GB/software/basecamp/' },
              { name: 'Qmapshack', qual: t.routePlannerOfflineAll, url: 'https://github.com/Maproom/qmapshack' },
              { name: 'Maps for Basecamp/garmin devices', qual: '', url: 'http://www.frikart.no/garmin/index.html' },
              { name: 'Maps for Basecamp/Qmapshack', qual: '', url: 'https://download2.bbbike.org/osm/' },
            ].map(p => (
              <TouchableOpacity key={p.url} style={styles.dialogLinkRow} onPress={() => Linking.openURL(p.url)}>
                <Text style={styles.dialogLink}>•  {p.name} </Text>
                {!!p.qual && <Text style={styles.dialogMuted}>{p.qual}</Text>}
              </TouchableOpacity>
            ))}
            <TouchableOpacity style={styles.dialogClose} onPress={() => setPlannerOpen(false)}>
              <Text style={styles.dialogCloseText}>{t.close}</Text>
            </TouchableOpacity>
          </Pressable>
        </Pressable>
      </Modal>

      {/* ── Import a route ── */}
      <Card style={{ width: '100%' }}>
        {/* Title + a "little i" that opens the route-planner help dialog (André, 2026-08-16),
            same affordance as desktop's RoutesPage. */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
          <Text style={styles.cardTitle}>{t.routeSendSection}</Text>
          <TouchableOpacity ref={infoRef} style={styles.infoBadge} onPress={openPlanner} hitSlop={8}>
            <Text style={styles.infoBadgeText}>i</Text>
          </TouchableOpacity>
        </View>
        <Button label={t.routeIdle} variant="filled" loading={picking} disabled={picking || sendBusy} onPress={handlePick} style={{ marginTop: v3Spacing.small }} />

        {pending && (
          <View style={{ marginTop: v3Spacing.medium, gap: v3Spacing.small }}>
            <TrackPreview points={pending.points.map(p => ({ lat: p.lat, lon: p.lon }))} variableHeight />
            <Text style={styles.itemName}>{pending.name}</Text>
            <Text style={styles.itemStats}>
              {t.routeStats(formatDist(pending.distanceM), pending.points.length, pending.ascentM, pending.descentM)}
            </Text>
            <View style={styles.row}>
              {watchHere && <Button label={t.routeUploadBtn} variant="filled" loading={sendBusy} disabled={sendBusy} onPress={handleUpload} />}
              <Button label={t.routeDiscardBtn} variant="text" grow={false} disabled={sendBusy} onPress={() => setPending(null)} />
            </View>
            {(brytonPlugged || magene) && (
              <View style={styles.row}>
                {brytonPlugged && <Button label={bikeBusy === 'bryton' ? 'Sending…' : 'Send to Bryton'} variant="text" grow={false}
                  disabled={!!bikeBusy} onPress={() => sendToBike('bryton')} />}
                {magene && <Button label={bikeBusy === 'magene' ? 'Sending…' : 'Send to Magene'} variant="text" grow={false}
                  disabled={!!bikeBusy} onPress={() => sendToBike('magene')} />}
              </View>
            )}
            {bikeMsg ? <StatusLine text={bikeMsg} tone={bikeMsg.includes('✓') ? 'muted' : 'alert'} /> : null}
            {sendBusy && <StatusLine text={sendState.phase === 'connecting' ? t.connecting : t.routeWritingMsg} />}
          </View>
        )}
      </Card>

      {watchHere && (<>
      {/* ── On the watch ── */}
      <Card style={{ width: '100%' }}>
        {/* Title + map/list view dropdown, right after the title on the left (moved here from
            Settings, André 2026-08-16; matches desktop). */}
        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 12 }}>
          <Text style={styles.cardTitle}>{t.routeOnWatchSection}</Text>
          {!onWatchLoading && sortedOnWatch && sortedOnWatch.length > 0 && (
            <ViewModeToggle mode={viewMode} onChange={changeViewMode} />
          )}
        </View>

        {onWatchLoading && (
          <StatusLine text={t.routeOnWatchReading} />
        )}
        {!onWatchLoading && onWatchError && (
          <Text style={[styles.itemStats, { color: theme.error, marginTop: v3Spacing.small }]}>
            {t.routeOnWatchError(onWatchError)}
          </Text>
        )}
        {!onWatchLoading && !onWatchError && onWatch && onWatch.length === 0 && (
          <Text style={[styles.itemStats, { marginTop: v3Spacing.small }]}>{t.routeOnWatchEmpty}</Text>
        )}

        {!onWatchLoading && sortedOnWatch && sortedOnWatch.length > 1 && (
          <SortBar keys={sortKeysFor('routes')} value={sortKey} onChange={setSortKey} />
        )}

        {!onWatchLoading && sortedOnWatch && sortedOnWatch.map((route, i) => (
          <View key={`${route.name}-${i}`} style={i > 0 ? styles.onWatchItem : { marginTop: v3Spacing.medium, gap: v3Spacing.small }}>
            {viewMode === 'map' && route.points.length > 1 && <TrackPreview points={route.points.map(p => ({ lat: p.latitude, lon: p.longitude }))} height={120} variableHeight />}
            <View style={styles.row}>
              <View style={{ flex: 1 }}>
                <Text style={styles.itemName}>{route.name}</Text>
                <Text style={styles.itemStats}>
                  {t.routeStats(formatDist(route.distanceM), route.points.length, route.ascentM, route.descentM)}
                </Text>
              </View>
              <TouchableOpacity
                style={styles.exportBtn}
                disabled={exportingIndex !== null}
                onPress={() => handleExportItem(route, i)}
              >
                <Text style={styles.exportBtnText}>
                  {exportingIndex === i ? '…' : t.routeItemExportBtn}
                </Text>
              </TouchableOpacity>
            </View>
          </View>
        ))}
      </Card>
      </>)}

    </ScrollView>
  );
}

const createStyles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  content: { padding: v3Spacing.medium, gap: v3Spacing.medium },
  cardTitle: { fontSize: v3Type.heading, fontWeight: '700', color: t.text },
  // "little i" info badge next to the title (matches desktop's RoutesPage).
  infoBadge: {
    width: 15, height: 15, borderRadius: 7.5,
    borderWidth: 1, borderColor: t.mutedText,
    alignItems: 'center', justifyContent: 'center',
  },
  infoBadgeText: { fontSize: 10, fontWeight: '700', color: t.mutedText, lineHeight: 11 },
  // Route-planner help dialog.
  // Full-screen dimmer only; the card is absolutely positioned near the "i" (padding here would
  // offset the card's left/top, so keep it at 0).
  backdrop: { flex: 1, backgroundColor: '#00000066' },
  dialogCard: {
    backgroundColor: t.card, borderRadius: 16,
    borderWidth: 1, borderColor: t.mutedText + '55',
    padding: v3Spacing.medium, gap: v3Spacing.small,
  },
  dialogTitle: { fontSize: v3Type.bodyLarge, fontWeight: '700', color: t.text },
  dialogText: { fontSize: v3Type.body, color: t.text, marginTop: 2 },
  dialogLinkRow: { flexDirection: 'row', alignItems: 'center', flexWrap: 'wrap', paddingVertical: 4 },
  dialogLink: { fontSize: v3Type.body, color: t.primary, fontWeight: '600' },
  dialogMuted: { fontSize: v3Type.body, color: t.mutedText },
  // Close on the last line's right corner to save space (André, 2026-08-16).
  dialogClose: { position: 'absolute', right: 10, bottom: 8, paddingVertical: 4, paddingHorizontal: 8 },
  dialogCloseText: { fontSize: v3Type.body, color: t.primary, fontWeight: '700' },
  row: { flexDirection: 'row', alignItems: 'center', gap: v3Spacing.small },
  itemName: { fontSize: v3Type.bodyLarge, fontWeight: '700', color: t.text },
  itemStats: { fontSize: v3Type.label, color: t.mutedText, marginTop: 2 },
  onWatchItem: { marginTop: v3Spacing.large, paddingTop: v3Spacing.medium, borderTopWidth: 1, borderTopColor: t.mutedText + '22', gap: v3Spacing.small },
  exportBtn: {
    paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8,
    backgroundColor: t.primary + '1F', borderWidth: 1, borderColor: t.primary,
  },
  exportBtnText: { color: t.primary, fontWeight: '600', fontSize: v3Type.label },
});
