import React, { useCallback, useState } from 'react';
import { View, Text, StyleSheet, ScrollView, TouchableOpacity, Alert } from 'react-native';
import { useFocusEffect, useRoute, RouteProp } from '@react-navigation/native';
import { RootStackParamList } from '../../App';
import {
  runFirmwareCheck, downloadFirmware, BackupState,
} from '../services/FirmwareBackupService';
import {
  createNavBackup, listNavBackups, backupsFolderPath, BackupEntry, backupNavToFile,
} from '../services/NavBackupService';
import {
  createKailashArchive, listKailashArchives, KailashArchiveEntry,
} from '../services/KailashBackupService';
import { shareFile } from '../native/AmbitUsbModule';
import { t, fmtDateTime } from '../i18n';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { Button, Section, StatusLine, WarningNote } from '../components/ui/primitives';

/*
 * v2.3.2 beta — Ambit firmware backup screen.
 *
 * BACKUP ONLY, always shown up front: the downloaded file is Suunto's real
 * proprietary firmware container (starts with an "SFI2" magic, not a real
 * zip despite the name — confirmed against a real download), and there is no
 * known way to flash it back onto the watch from this app. This screen can
 * only read what Suunto's own update-check service reports and save that
 * file untouched, for safekeeping — same spirit as e.g. Settings' "About"
 * disclaimer, but load-bearing here since a user could otherwise assume
 * "backup" implies "restore".
 */

export default function BackupScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);

  // A Kailash (Hoopoe) has no Routes/Waypoints database to back up - what's irreplaceable is its
  // travel history + GPS track, so it gets its own archive card instead of the nav-backup cards
  // (parity with the desktop's DeviceCapabilities.supportsTravelArchive gating). The model is
  // handed in from Home, which already knows the connected watch.
  const route = useRoute<RouteProp<RootStackParamList, 'Backup'>>();
  const isKailash = route.params?.deviceModel === 'Hoopoe';

  const [archives, setArchives] = useState<KailashArchiveEntry[]>([]);
  const [archiveBusy, setArchiveBusy] = useState(false);
  const [archiveError, setArchiveError] = useState<string | undefined>();

  const [state, setState] = useState<BackupState>({ phase: 'idle' });
  const [downloadPct, setDownloadPct] = useState(0);
  const [downloadedTo, setDownloadedTo] = useState<string | undefined>();
  const [downloadError, setDownloadError] = useState<string | undefined>();
  const [downloading, setDownloading] = useState(false);

  const [backups, setBackups] = useState<BackupEntry[]>([]);
  const [navBackupBusy, setNavBackupBusy] = useState(false);
  const [navBackupError, setNavBackupError] = useState<string | undefined>();

  const refreshBackups = useCallback(() => {
    listNavBackups().then(setBackups).catch(() => {});
    listKailashArchives().then(setArchives).catch(() => {});
  }, []);
  useFocusEffect(useCallback(() => { refreshBackups(); }, [refreshBackups]));

  async function handleCreateArchive() {
    if (archiveBusy) return;
    setArchiveBusy(true);
    setArchiveError(undefined);
    try {
      const r = await createKailashArchive();
      refreshBackups();
      Alert.alert(t.backupKailashTitle,
        t.backupKailashSavedMsg(r.historySaved ? 1 : 0, r.trackPoints));
    } catch (e: any) {
      setArchiveError(e?.message ?? t.unknownError);
    } finally {
      setArchiveBusy(false);
    }
  }

  async function handleShareArchive(entry: KailashArchiveEntry, kind: 'history' | 'track') {
    const name = kind === 'history' ? `${entry.prefix}_kailash-history.json` : `${entry.prefix}_kailash-track.gpx`;
    const mime = kind === 'history' ? 'application/json' : 'application/gpx+xml';
    try {
      await shareFile(`${backupsFolderPath()}/${name}`, mime);
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
    }
  }

  // ── Backup database to folder - replaces the cloud-OAuth upload (André, 2026-08-16). Reads
  // the nav DB and hands it to the system "Save as" picker so the user can drop it in any
  // folder, including a cloud-sync folder - keyless, no sign-in. ──
  const [folderBusy, setFolderBusy] = useState(false);
  const [folderError, setFolderError] = useState<string | undefined>();
  const [folderInfoOpen, setFolderInfoOpen] = useState(false);

  async function handleBackupToFolder() {
    if (folderBusy) return;
    setFolderBusy(true);
    setFolderError(undefined);
    try {
      await backupNavToFile();
    } catch (e: any) {
      // The user simply backing out of the "Save as" picker isn't an error.
      if (e?.code === 'SAVE_AS_CANCELLED') return;
      setFolderError(e?.message ?? t.unknownError);
    } finally {
      setFolderBusy(false);
    }
  }

  async function handleCreateNavBackup() {
    if (navBackupBusy) return;
    setNavBackupBusy(true);
    setNavBackupError(undefined);
    try {
      await createNavBackup();
      refreshBackups();
    } catch (e: any) {
      setNavBackupError(e?.message ?? t.unknownError);
    } finally {
      setNavBackupBusy(false);
    }
  }

  async function handleShareBackup(entry: BackupEntry) {
    try {
      await shareFile(`${backupsFolderPath()}/${entry.prefix}_routes.bin`, 'application/octet-stream');
    } catch (e: any) {
      Alert.alert(t.error, e?.message ?? t.unknownError);
    }
  }

  const busy = state.phase === 'connecting' || state.phase === 'reading' ||
    state.phase === 'checking' || downloading;

  async function handleCheck() {
    if (busy) return;
    setDownloadedTo(undefined);
    setDownloadError(undefined);
    await runFirmwareCheck(setState);
  }

  async function handleDownload() {
    if (busy || !state.deviceInfo || !state.firmwareInfo?.downloadUri) return;
    setDownloading(true);
    setDownloadPct(0);
    setDownloadError(undefined);
    try {
      const path = await downloadFirmware(
        state.firmwareInfo.downloadUri,
        state.deviceInfo.model,
        state.firmwareInfo.latestFirmwareVersion ?? 'unknown',
        (received, total) => setDownloadPct(total > 0 ? Math.round((received / total) * 100) : 0)
      );
      setDownloadedTo(path);
    } catch (e: any) {
      if (e?.code === 'SAVE_AS_CANCELLED') { setDownloading(false); return; } // not an error — user just backed out of the picker
      setDownloadError(e?.message ?? t.unknownError);
    } finally {
      setDownloading(false);
    }
  }

  return (
    <ScrollView style={styles.root} contentContainerStyle={styles.content}>

      {/* ── Kailash travel archive - its own backup (no Routes/Waypoints DB to save; parity with
          the desktop's supportsTravelArchive card). One-way archive: history + GPS track. ── */}
      {isKailash && (
        <Section title={t.backupKailashTitle} description={t.backupKailashDesc} style={{ marginTop: 16 }}>
          <View style={styles.row}>
            <Button label={t.backupKailashSaveBtn} variant="filled" loading={archiveBusy} disabled={archiveBusy} onPress={handleCreateArchive} />
          </View>
          {archiveBusy && <StatusLine text={t.backupNavWorking} />}
          {!!archiveError && <StatusLine text={archiveError} tone="alert" />}
          {archives.length === 0 && !archiveBusy && <StatusLine text={t.backupExistingEmpty} />}
          {archives.map(a => (
            <View key={a.prefix} style={styles.backupRow}>
              <Text style={styles.backupDate}>{fmtDateTime(a.createdAt)}</Text>
              <View style={styles.backupRowBtns}>
                {a.hasHistory && (
                  <TouchableOpacity style={styles.shareBtn} onPress={() => handleShareArchive(a, 'history')}>
                    <Text style={styles.shareBtnText}>{t.backupKailashHistoryBtn}</Text>
                  </TouchableOpacity>
                )}
                {a.hasTrack && (
                  <TouchableOpacity style={styles.shareBtn} onPress={() => handleShareArchive(a, 'track')}>
                    <Text style={styles.shareBtnText}>{t.backupKailashTrackBtn}</Text>
                  </TouchableOpacity>
                )}
              </View>
            </View>
          ))}
          <Text style={styles.restoreNote}>{t.backupKailashArchiveNote}</Text>
        </Section>
      )}

      {/* ── Navigation backup - real "Backup & Restore" card (BackupPage.qml parity). Not for a
          Kailash: it has no Routes/Waypoints regions to save (they read back empty). ── */}
      {!isKailash && (<>
      <Section title={t.backupNavSection} description={t.backupNavDesc} style={{ marginTop: 16 }}>
        <View style={styles.row}>
          <Button label={t.backupNavCreateBtn} variant="filled" loading={navBackupBusy} disabled={navBackupBusy} onPress={handleCreateNavBackup} />
        </View>
        {navBackupBusy && <StatusLine text={t.backupNavWorking} />}
        {!!navBackupError && <StatusLine text={navBackupError} tone="alert" />}
      </Section>

      <Section title={t.backupExistingSection}>
        {backups.length === 0 && <StatusLine text={t.backupExistingEmpty} />}
        {backups.map(b => (
          <View key={b.prefix} style={styles.backupRow}>
            <Text style={styles.backupDate}>{fmtDateTime(b.createdAt)}</Text>
            <View style={styles.backupRowBtns}>
              <TouchableOpacity style={styles.shareBtn} onPress={() => handleShareBackup(b)}>
                <Text style={styles.shareBtnText}>{t.backupShareBtn}</Text>
              </TouchableOpacity>
            </View>
          </View>
        ))}
        <Text style={styles.restoreNote}>{t.backupRestoreUnavailable}</Text>
      </Section>

      {/* ── Backup database to folder - keyless replacement for the cloud-OAuth upload
          (André, 2026-08-16). Save the nav DB anywhere via the system picker; point it at a
          cloud-sync folder and it syncs. ── */}
      <Section>
        <View style={styles.folderTitleRow}>
          <Text style={styles.folderTitle}>{t.backupFolderSection}</Text>
          <TouchableOpacity style={styles.infoBadge} onPress={() => setFolderInfoOpen(o => !o)} hitSlop={8}>
            <Text style={styles.infoBadgeText}>i</Text>
          </TouchableOpacity>
        </View>
        {folderInfoOpen && <Text style={styles.folderInfoText}>{t.backupFolderInfo}</Text>}
        <View style={styles.row}>
          <Button label={t.backupFolderBtn} variant="filled" loading={folderBusy} disabled={folderBusy}
            onPress={handleBackupToFolder} />
        </View>
        {folderBusy && <StatusLine text={t.backupNavWorking} />}
        {!!folderError && <StatusLine text={folderError} tone="alert" />}
      </Section>
      </>)}

      <WarningNote>{t.backupWarning}</WarningNote>

      {/* ── Check available firmware ── */}
      <Section title={t.backupCheckSection} description={t.backupCheckDesc}>
        <View style={styles.row}>
          <Button
            label={t.backupCheckBtn}
            variant="filled"
            loading={state.phase === 'connecting' || state.phase === 'reading' || state.phase === 'checking'}
            disabled={busy}
            onPress={handleCheck}
          />
        </View>

        {state.phase === 'reading' && <StatusLine text={t.backupReading} />}
        {state.phase === 'checking' && <StatusLine text={t.backupChecking} />}
        {state.phase === 'error' && <StatusLine text={state.error ?? t.error} tone="alert" />}

        {state.phase === 'done' && state.deviceInfo && (
          <View style={styles.deviceInfoBox}>
            <Text style={styles.deviceInfoPrimary}>
              {state.deviceInfo.name} — {state.deviceInfo.fwVersion}
            </Text>
            <Text style={styles.deviceInfoSecondary}>{state.deviceInfo.model} / {state.deviceInfo.hwVersion}</Text>
          </View>
        )}

        {state.phase === 'done' && state.firmwareInfo && (
          state.firmwareInfo.latestFirmwareVersion ? (
            <View style={styles.deviceInfoBox}>
              <Text style={styles.deviceInfoPrimary}>{t.backupLatestVersion(state.firmwareInfo.latestFirmwareVersion)}</Text>
              {!!state.firmwareInfo.uploadDate && (
                <Text style={styles.deviceInfoSecondary}>{t.backupUploadDate(state.firmwareInfo.uploadDate)}</Text>
              )}
            </View>
          ) : (
            <StatusLine text={t.backupNoUpdateInfo} tone="alert" />
          )
        )}
      </Section>

      {/* ── Download backup ── */}
      {state.phase === 'done' && state.firmwareInfo?.downloadUri && (
        <Section title={t.backupDownloadSection} description={t.backupDownloadDesc}>
          <View style={styles.row}>
            <Button label={t.backupDownloadBtn} variant="filled" loading={downloading} disabled={busy} onPress={handleDownload} />
          </View>

          {downloading && <StatusLine text={t.backupDownloading(downloadPct)} />}
          {!!downloadedTo && <StatusLine text={t.backupDownloadDone} />}
          {!!downloadError && <StatusLine text={downloadError} tone="alert" />}
        </Section>
      )}

    </ScrollView>
  );
}

const createStyles = (t: ReturnType<typeof useV3Theme>) => StyleSheet.create({
  root: { flex: 1, backgroundColor: t.background },
  content: { padding: 20 },
  row: { flexDirection: 'row', gap: 10, marginTop: 4 },
  deviceInfoBox: { marginTop: 14, paddingTop: 14, borderTopWidth: 1, borderTopColor: t.mutedText + '33' },
  deviceInfoPrimary: { color: t.text, fontSize: 15, fontWeight: '600', marginBottom: 4 },
  deviceInfoSecondary: { color: t.mutedText, fontSize: 12, marginBottom: 2 },
  backupRow: {
    flexDirection: 'row', alignItems: 'center', justifyContent: 'space-between',
    marginTop: v3Spacing.small,
  },
  backupDate: { color: t.text, fontSize: v3Type.bodyLarge },
  backupRowBtns: { flexDirection: 'row', gap: v3Spacing.small },
  shareBtn: {
    paddingVertical: 6, paddingHorizontal: 12, borderRadius: 8,
    backgroundColor: t.primary + '1F', borderWidth: 1, borderColor: t.primary,
  },
  shareBtnText: { color: t.primary, fontWeight: '600', fontSize: v3Type.label },
  restoreNote: { color: t.mutedText, fontSize: v3Type.caption, marginTop: v3Spacing.small, lineHeight: 16 },
  // "Backup database to folder" section - title + "little i" that toggles the cloud-folder hint.
  folderTitleRow: { flexDirection: 'row', alignItems: 'center', gap: 8 },
  folderTitle: { fontSize: v3Type.heading, fontWeight: '700', color: t.text },
  infoBadge: {
    width: 15, height: 15, borderRadius: 7.5,
    borderWidth: 1, borderColor: t.mutedText,
    alignItems: 'center', justifyContent: 'center',
  },
  infoBadgeText: { fontSize: 10, fontWeight: '700', color: t.mutedText, lineHeight: 11 },
  folderInfoText: { fontSize: v3Type.body, color: t.mutedText, lineHeight: 19, marginTop: 6 },
});
