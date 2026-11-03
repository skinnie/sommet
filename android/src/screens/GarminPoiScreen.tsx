import React, { useCallback, useState } from 'react';
import { StyleSheet, ScrollView, View, Text, TouchableOpacity } from 'react-native';
import { useRoute, useFocusEffect, RouteProp } from '@react-navigation/native';
import * as Garmin from '../native/GarminModule';
import { pickGpxFile, shareFile, saveToDownloads } from '../native/AmbitUsbModule';
import {
  exportGarminGpxFiles, isGarminWaypointFile, listGarminPoiPreviews, waypointToGpx,
  GarminGpxExportResult, GarminGpxExportState, GarminPoiPreview,
} from '../services/GarminGpxExportService';
import RNFS from 'react-native-fs';
import { t } from '../i18n';
import type { RootStackParamList } from '../../App';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { Button, ExportedFileRow, Section, WarningNote, StatusNote } from '../components/ui/primitives';
import { TrackPreview } from '../components/TrackPreview';

/*
 * v2.3.2 beta — mirrors the Ambit PoiScreen's structure (send / retrieve),
 * split out from the old combined GarminScreen per André's feedback: "today
 * we have the same page as routes. It is non ok. Just like the suunto
 * counterpart, it should send a poi from gpx ... or retrieve the POIs from
 * the device." Unlike the Ambit3, Garmin has no separate manual-entry POI
 * protocol — POIs are plain GPX-waypoint files (BaseCamp's "Waypoints*.gpx"
 * naming, confirmed on real hardware), so this screen has no manual-entry
 * form. The device is already connected by HomeScreen's connecting-flow —
 * no Connect step here.
 */

type SendState = 'idle' | 'picking' | 'uploading' | 'done' | 'error';

export default function GarminPoiScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);
  const route = useRoute<RouteProp<RootStackParamList, 'GarminPoi'>>();
  const info = route.params.info;
  const sdCardVolume = info.volumes.find(v => !v.hasGarminDeviceXml);

  const [sendState, setSendState] = useState<SendState>('idle');
  const [sendError, setSendError] = useState<string | undefined>();

  const [retrieveState, setRetrieveState] = useState<GarminGpxExportState>({ phase: 'idle' });
  const [retrievedFiles, setRetrievedFiles] = useState<GarminGpxExportResult[]>([]);

  // Real, 2026-08-10 ("Garmin: POIs and routes, please follow the same logic as suunto,
  // showing them on the maps") - a real browsable "On the device" list with a per-item pin
  // preview (TrackPreview markerOnly), matching PoiScreen.tsx's own on-watch list, instead
  // of only ever bulk-retrieving unseen files to Downloads.
  const [onDevice, setOnDevice] = useState<GarminPoiPreview[] | null>(null);
  const [onDeviceLoading, setOnDeviceLoading] = useState(false);
  const [onDeviceError, setOnDeviceError] = useState<string | undefined>();
  const [exportingIndex, setExportingIndex] = useState<number | null>(null);

  const loadOnDevice = useCallback(async () => {
    setOnDeviceLoading(true);
    setOnDeviceError(undefined);
    try {
      setOnDevice(await listGarminPoiPreviews(info));
    } catch (e: any) {
      setOnDeviceError(e?.message ?? t.unknownError);
    } finally {
      setOnDeviceLoading(false);
    }
  }, [info]);

  useFocusEffect(useCallback(() => { loadOnDevice(); }, [loadOnDevice]));

  async function handleExportItem(item: GarminPoiPreview, index: number) {
    if (exportingIndex !== null) return;
    setExportingIndex(index);
    try {
      const gpx = waypointToGpx(item.waypoint);
      const safeName = item.waypoint.name.replace(/[\\/:*?"<>|]/g, '_') || 'poi';
      const fileName = `${safeName}.gpx`;
      const localPath = `${RNFS.CachesDirectoryPath}/${fileName}`;
      await RNFS.writeFile(localPath, gpx, 'utf8');
      await saveToDownloads(localPath, fileName, 'application/gpx+xml');
    } catch {
      // silent - the bulk "Retrieve POIs" button below remains the reliable fallback
    } finally {
      setExportingIndex(null);
    }
  }

  const busy = sendState === 'picking' || sendState === 'uploading' || retrieveState.phase === 'reading';

  async function handleSendPoi() {
    if (busy || !sdCardVolume) return;
    setSendState('picking');
    setSendError(undefined);
    try {
      const localPath = await pickGpxFile();
      setSendState('uploading');
      const content = await RNFS.readFile(localPath, 'utf8');
      const fileName = localPath.split('/').pop() ?? `poi_${Date.now()}.gpx`;
      await Garmin.writeGpxToSdCard(sdCardVolume.volumeIndex, fileName, content);
      setSendState('done');
    } catch (e: any) {
      if (e?.code === 'GPX_PICK_CANCELLED') { setSendState('idle'); return; }
      setSendState('error');
      setSendError(`${e?.code ?? ''} ${e?.message ?? t.unknownError}`.trim());
    }
  }

  async function handleRetrievePois() {
    if (busy) return;
    setRetrievedFiles([]);
    const results = await exportGarminGpxFiles(info, isGarminWaypointFile, setRetrieveState);
    setRetrievedFiles(results);
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>

      {/* ── Send a POI to the device (SD card only) ── */}
      <Section title={t.garminPoiSendSection} description={t.garminPoiSendDesc}>
        <WarningNote>{t.garminInternalMemoryWarning}</WarningNote>

        <View style={styles.row}>
          <Button
            label={t.garminPoiSendBtn}
            variant="filled"
            loading={sendState === 'picking' || sendState === 'uploading'}
            disabled={busy || !sdCardVolume}
            onPress={handleSendPoi}
          />
        </View>

        {!sdCardVolume && <StatusNote text={t.garminNoSdCardMsg} tone="warning" />}
        {sendState === 'done' && <StatusNote text={t.garminPoiSendDone} tone="success" />}
        {sendState === 'error' && <StatusNote text={sendError ?? t.error} tone="error" />}
      </Section>

      {/* ── Retrieve POIs from the device to Downloads ── */}
      <Section title={t.garminPoiRetrieveSection} description={t.garminPoiRetrieveDesc}>
        <View style={styles.row}>
          <Button
            label={t.garminPoiRetrieveBtn}
            variant="outline"
            loading={retrieveState.phase === 'reading'}
            disabled={busy}
            onPress={handleRetrievePois}
          />
        </View>

        {retrieveState.phase === 'error' && <StatusNote text={retrieveState.error ?? t.error} tone="error" />}
        {retrieveState.phase === 'done' && <StatusNote text={t.garminPoiRetrieveDone(retrievedFiles.length)} tone="success" />}
        {retrievedFiles.map(f => (
          <ExportedFileRow
            key={f.localPath}
            fileName={f.fileName}
            shareLabel={t.garminShareBtn}
            onShare={() => shareFile(f.localPath).catch(() => {})}
          />
        ))}
      </Section>

      {/* ── On the device - real, 2026-08-10 ("follow the same logic as suunto, showing
          them on the maps"). Same real pin-preview-per-item pattern as PoiScreen.tsx's own
          "On the watch" card. ── */}
      <Section title={t.garminPoiOnDeviceSection}>
        {onDeviceLoading && <StatusNote text={t.garminPoiOnDeviceReading} tone="working" />}
        {!onDeviceLoading && onDeviceError && (
          <Text style={[styles.itemStats, { color: theme.error, marginTop: v3Spacing.small }]}>{onDeviceError}</Text>
        )}
        {!onDeviceLoading && !onDeviceError && onDevice && onDevice.length === 0 && (
          <Text style={[styles.itemStats, { marginTop: v3Spacing.small }]}>{t.garminPoiOnDeviceEmpty}</Text>
        )}
        {!onDeviceLoading && onDevice && onDevice.map((item, i) => (
          <View key={`${item.volumeIndex}-${item.fileName}-${i}`} style={i > 0 ? styles.onDeviceItem : { marginTop: v3Spacing.medium, gap: v3Spacing.small }}>
            <TrackPreview points={[{ lat: item.waypoint.latitude, lon: item.waypoint.longitude }]} markerOnly height={90} />
            <View style={styles.row}>
              <View style={{ flex: 1 }}>
                <Text style={styles.itemName} numberOfLines={1}>{item.waypoint.name}</Text>
                <Text style={styles.itemStats}>{item.waypoint.latitude.toFixed(5)}, {item.waypoint.longitude.toFixed(5)}</Text>
              </View>
              <TouchableOpacity
                style={styles.exportBtn}
                disabled={exportingIndex !== null}
                onPress={() => handleExportItem(item, i)}
              >
                <Text style={styles.exportBtnText}>{exportingIndex === i ? '…' : t.poiItemExportBtn}</Text>
              </TouchableOpacity>
            </View>
          </View>
        ))}
      </Section>

    </ScrollView>
  );
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
