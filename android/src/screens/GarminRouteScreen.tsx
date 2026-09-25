import React, { useCallback, useState } from 'react';
import { StyleSheet, ScrollView, View, Text, TouchableOpacity } from 'react-native';
import { useRoute, useFocusEffect, RouteProp } from '@react-navigation/native';
import * as Garmin from '../native/GarminModule';
import { pickGpxFile, shareFile, saveToDownloads } from '../native/AmbitUsbModule';
import {
  exportGarminGpxFiles, isGarminRouteFile, listGarminRoutePreviews,
  GarminGpxExportResult, GarminGpxExportState, GarminRoutePreview,
} from '../services/GarminGpxExportService';
import RNFS from 'react-native-fs';
import { t } from '../i18n';
import type { RootStackParamList } from '../../App';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { Button, ExportedFileRow, Section, StatusLine, WarningNote } from '../components/ui/primitives';
import { TrackPreview } from '../components/TrackPreview';
import { buildEtrexGpx } from '../services/EtrexExport';

/*
 * v2.3.2 beta — mirrors the Ambit RouteScreen's structure (send / export),
 * split out from the old combined GarminScreen per André's feedback: "just
 * like the suunto counterpart, the menu should only show send a route (like
 * it does today) and export from the gps ... to by default downloads
 * folder, with ability to choose." The device is already connected by
 * HomeScreen's connecting-flow — no Connect step here.
 */

type SendState = 'idle' | 'picking' | 'uploading' | 'done' | 'error';

export default function GarminRouteScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);
  const route = useRoute<RouteProp<RootStackParamList, 'GarminRoute'>>();
  const info = route.params.info;
  const sdCardVolume = info.volumes.find(v => !v.hasGarminDeviceXml);

  const [sendState, setSendState] = useState<SendState>('idle');
  const [sendError, setSendError] = useState<string | undefined>();
  const [etrexDone, setEtrexDone] = useState<string | undefined>();

  const [exportState, setExportState] = useState<GarminGpxExportState>({ phase: 'idle' });
  const [exportedFiles, setExportedFiles] = useState<GarminGpxExportResult[]>([]);

  // Real, 2026-08-10 ("Garmin: POIs and routes, please follow the same logic as suunto,
  // showing them on the maps") - a real browsable "On the device" list with a per-item map
  // preview (TrackPreview), matching RouteScreen.tsx's own on-watch list, instead of only
  // ever bulk-exporting unseen files to Downloads.
  const [onDevice, setOnDevice] = useState<GarminRoutePreview[] | null>(null);
  const [onDeviceLoading, setOnDeviceLoading] = useState(false);
  const [onDeviceError, setOnDeviceError] = useState<string | undefined>();
  const [exportingItem, setExportingItem] = useState<string | null>(null);

  const loadOnDevice = useCallback(async () => {
    setOnDeviceLoading(true);
    setOnDeviceError(undefined);
    try {
      setOnDevice(await listGarminRoutePreviews(info));
    } catch (e: any) {
      setOnDeviceError(e?.message ?? t.unknownError);
    } finally {
      setOnDeviceLoading(false);
    }
  }, [info]);

  useFocusEffect(useCallback(() => { loadOnDevice(); }, [loadOnDevice]));

  async function handleExportItem(item: GarminRoutePreview) {
    if (exportingItem) return;
    setExportingItem(item.fileName);
    try {
      const content = await Garmin.readGpxDirFile(item.volumeIndex, item.fileName);
      const localPath = `${RNFS.CachesDirectoryPath}/${item.fileName}`;
      await RNFS.writeFile(localPath, content, 'utf8');
      await saveToDownloads(localPath, item.fileName, 'application/gpx+xml');
    } catch {
      // silent - the bulk "Export routes" button below remains the reliable fallback
    } finally {
      setExportingItem(null);
    }
  }

  const busy = sendState === 'picking' || sendState === 'uploading' || exportState.phase === 'reading';

  async function handleSendRoute() {
    if (busy || !sdCardVolume) return;
    setSendState('picking');
    setSendError(undefined);
    try {
      const localPath = await pickGpxFile();
      setSendState('uploading');
      const content = await RNFS.readFile(localPath, 'utf8');
      const fileName = localPath.split('/').pop() ?? `route_${Date.now()}.gpx`;
      await Garmin.writeGpxToSdCard(sdCardVolume.volumeIndex, fileName, content);
      setSendState('done');
    } catch (e: any) {
      if (e?.code === 'GPX_PICK_CANCELLED') { setSendState('idle'); return; }
      setSendState('error');
      setSendError(`${e?.code ?? ''} ${e?.message ?? t.unknownError}`.trim());
    }
  }

  // eTrex 30/30x/32x: no turn guidance on tracks, 50-point cap on routes - convert the picked GPX
  // (services/EtrexExport.ts) and write it to the SD card next to any plain route.
  async function handleSendEtrex(mode: 'track' | 'route') {
    if (busy || !sdCardVolume) return;
    setSendState('picking');
    setSendError(undefined);
    setEtrexDone(undefined);
    try {
      const localPath = await pickGpxFile();
      setSendState('uploading');
      const content = await RNFS.readFile(localPath, 'utf8');
      const base = (localPath.split('/').pop() ?? 'route.gpx').replace(/\.gpx$/i, '');
      const { gpx, stats } = buildEtrexGpx(content, { mode });
      const fileName = `${base}_etrex_${mode}.gpx`;
      await Garmin.writeGpxToSdCard(sdCardVolume.volumeIndex, fileName, gpx);
      setEtrexDone(t.garminEtrexDone(fileName, stats.turns, stats.crossings));
      setSendState('idle');
    } catch (e: any) {
      if (e?.code === 'GPX_PICK_CANCELLED') { setSendState('idle'); return; }
      setSendState('error');
      setSendError(`${e?.code ?? ''} ${e?.message ?? t.unknownError}`.trim());
    }
  }

  async function handleExportRoutes() {
    if (busy) return;
    setExportedFiles([]);
    const results = await exportGarminGpxFiles(info, isGarminRouteFile, setExportState);
    setExportedFiles(results);
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>

      {/* ── Send a route to the device (SD card only) ── */}
      <Section title={t.garminRouteSendSection} description={t.garminRouteSendDesc}>
        <WarningNote>{t.garminInternalMemoryWarning}</WarningNote>

        <View style={styles.row}>
          <Button
            label={t.garminRouteSendBtn}
            variant="filled"
            loading={sendState === 'picking' || sendState === 'uploading'}
            disabled={busy || !sdCardVolume}
            onPress={handleSendRoute}
          />
        </View>

        {!sdCardVolume && <StatusLine text={t.garminNoSdCardMsg} tone="alert" />}
        {sendState === 'done' && <StatusLine text={t.garminRouteSendDone} />}
        {sendState === 'error' && <StatusLine text={sendError ?? t.error} tone="alert" />}
      </Section>

      {/* ── eTrex: convert a GPX so the unit can actually guide you (turn waypoints / smart route) ── */}
      <Section title={t.garminEtrexSection} description={t.garminEtrexDesc}>
        <View style={styles.row}>
          <Button label={t.garminEtrexTrackBtn} variant="filled" disabled={busy || !sdCardVolume}
            onPress={() => handleSendEtrex('track')} />
          <Button label={t.garminEtrexRouteBtn} variant="outline" disabled={busy || !sdCardVolume}
            onPress={() => handleSendEtrex('route')} />
        </View>
        {etrexDone && <StatusLine text={etrexDone} />}
      </Section>

      {/* ── Export routes/tracks from the device to Downloads ── */}
      <Section title={t.garminRouteExportSection} description={t.garminRouteExportDesc}>
        <View style={styles.row}>
          <Button
            label={t.garminRouteExportBtn}
            variant="outline"
            loading={exportState.phase === 'reading'}
            disabled={busy}
            onPress={handleExportRoutes}
          />
        </View>

        {exportState.phase === 'error' && <StatusLine text={exportState.error ?? t.error} tone="alert" />}
        {exportState.phase === 'done' && <StatusLine text={t.garminRouteExportDone(exportedFiles.length)} />}
        {exportedFiles.map(f => (
          <ExportedFileRow
            key={f.localPath}
            fileName={f.fileName}
            shareLabel={t.garminShareBtn}
            onShare={() => shareFile(f.localPath).catch(() => {})}
          />
        ))}
      </Section>

      {/* ── On the device - real, 2026-08-10 ("follow the same logic as suunto, showing
          them on the maps"). Same real map-thumbnail-per-item pattern as RouteScreen.tsx's
          own "On the watch" card. ── */}
      <Section title={t.garminRouteOnDeviceSection}>
        {onDeviceLoading && <StatusLine text={t.garminRouteOnDeviceReading} />}
        {!onDeviceLoading && onDeviceError && (
          <Text style={[styles.itemStats, { color: theme.error, marginTop: v3Spacing.small }]}>{onDeviceError}</Text>
        )}
        {!onDeviceLoading && !onDeviceError && onDevice && onDevice.length === 0 && (
          <Text style={[styles.itemStats, { marginTop: v3Spacing.small }]}>{t.garminRouteOnDeviceEmpty}</Text>
        )}
        {!onDeviceLoading && onDevice && onDevice.map((item, i) => (
          <View key={`${item.volumeIndex}-${item.fileName}`} style={i > 0 ? styles.onDeviceItem : { marginTop: v3Spacing.medium, gap: v3Spacing.small }}>
            {item.points.length > 1 && <TrackPreview points={item.points} height={120} variableHeight />}
            <View style={styles.row}>
              <View style={{ flex: 1 }}>
                <Text style={styles.itemName}>{item.name}</Text>
                <Text style={styles.itemStats}>{t.routeStats(formatDist(item.distanceM), item.points.length, item.ascentM, item.descentM)}</Text>
              </View>
              <TouchableOpacity
                style={styles.exportBtn}
                disabled={exportingItem !== null}
                onPress={() => handleExportItem(item)}
              >
                <Text style={styles.exportBtnText}>{exportingItem === item.fileName ? '…' : t.routeItemExportBtn}</Text>
              </TouchableOpacity>
            </View>
          </View>
        ))}
      </Section>

    </ScrollView>
  );
}

function formatDist(m: number): string {
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
}

const createStyles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  content: { padding: 20 },
  row: { flexDirection: 'row', alignItems: 'center', gap: 10, marginTop: 10 },
  itemName: { fontSize: v3Type.bodyLarge, fontWeight: '700', color: t.text },
  itemStats: { fontSize: v3Type.label, color: t.mutedText, marginTop: 2 },
  onDeviceItem: { marginTop: v3Spacing.large, paddingTop: v3Spacing.medium, borderTopWidth: 1, borderTopColor: t.mutedText + '22', gap: v3Spacing.small },
  exportBtn: {
    paddingVertical: 8, paddingHorizontal: 12, borderRadius: 8,
    backgroundColor: t.primary + '1F', borderWidth: 1, borderColor: t.primary,
  },
  exportBtnText: { color: t.primary, fontWeight: '600', fontSize: v3Type.label },
});
