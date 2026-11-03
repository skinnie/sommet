import React, { useEffect, useRef, useState } from 'react';
import { View, Text, StyleSheet, Alert, ScrollView } from 'react-native';
import { useV3Theme, v3Spacing, v3Type } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { Button, PulseDots, StatusNote } from '../components/ui/primitives';
import {
  connect, disconnect, firmwareFlash, onFirmwarePhase, FirmwarePhaseEvent,
} from '../native/AmbitUsbModule';
import { checkFirmware, downloadFirmware, FirmwareCheck } from '../services/FirmwareService';

// SuuntoLink-style automatic firmware update. On open it detects the connected watch and asks
// Suunto's own firmware service for the latest image for that model+hardware. "Stream only"
// downloads + streams the image but STOPS before the irreversible commit (recoverable — the
// watch stays in BSL); "Flash" commits. The flash transport (firmware_flash_android.c) is a
// faithful port of the desktop's hardware-proven flasher; the opcode sequence is byte-exact
// against the real Traverse capture, but the Android transport itself is validated here first
// on hardware (stream-only) before committing.
//
// The update shows as a checklist (André, 2026-11-02, desktop picks #8, ported from
// desktop/qml/pages/FirmwarePage.qml): done steps get a green tick, the current one pulses (or
// shows the % and a bar while writing), the steps to come wait in grey. Display only; the
// update itself is unchanged.

const STEPS = ['Download', 'Recovery mode', 'Erase', 'Write', 'Check', 'Restart'];
const WRITE_STEP = 3;

type Mode = 'idle' | 'flashing' | 'done' | 'error';
interface Result { text: string; tone: 'success' | 'warning' | 'error' }

// One native event -> the checklist step, the write % and the line under the title. Same
// mapping and wording as desktop FirmwarePage's handleEvent. `step` / `percent` left out =
// unchanged.
function phaseView(e: FirmwarePhaseEvent): { step?: number; percent?: number; text: string } {
  switch (e.phase) {
    case 'resume_bsl':
      return { step: 1, percent: -1, text: 'The watch is already in recovery mode. Starting again from the beginning…' };
    case 'enter_bsl':
      return { step: 1, percent: -1, text: 'Entering recovery mode…' };
    case 'reopen_bsl':
      return { step: 1, percent: -1, text: 'Waiting for the watch to come back in recovery mode…' };
    case 'header':
    case 'transfer_mode':
      return { text: 'Preparing…' };
    case 'erase':
      return { step: 2, percent: -1, text: 'Erasing flash — this takes about a minute…' };
    case 'streaming':
      return { step: WRITE_STEP, percent: e.percent ?? -1, text: 'Writing firmware…' };
    case 'streamed':
      return { step: 4, percent: 100, text: 'Verifying…' };
    case 'commit':
      return { step: 4, percent: -1, text: 'Writing the final image…' };
    case 'reboot':
    case 'rebooting':
      return { step: 5, percent: -1, text: 'Rebooting your watch…' };
    case 'stream_only_done':
      return { step: 4, percent: -1, text: 'Stream complete, not committed.' };
    case 'done':
      return { step: STEPS.length, percent: -1, text: 'Update complete.' };
    default:
      return { text: e.message };
  }
}

export default function FirmwareScreen() {
  const theme = useV3Theme();
  const s = createStyles(theme);
  const [chk, setChk] = useState<FirmwareCheck | null>(null);
  const [checking, setChecking] = useState(false);
  const [checkError, setCheckError] = useState('');
  const [mode, setMode] = useState<Mode>('idle');
  const [committing, setCommitting] = useState(false);
  const [step, setStep] = useState(0);
  const [percent, setPercent] = useState(-1);
  const [phase, setPhase] = useState('');
  const [result, setResult] = useState<Result | null>(null);
  const modeRef = useRef<Mode>('idle');
  modeRef.current = mode;

  useEffect(() => onFirmwarePhase(e => {
    if (modeRef.current !== 'flashing') return; // a stray event after the flash ended
    const v = phaseView(e);
    if (v.step !== undefined) setStep(v.step);
    if (v.percent !== undefined) setPercent(v.percent);
    setPhase(v.text);
  }), []);
  useEffect(() => { runCheck(); }, []); // auto-detect + check on open

  const flashing = mode === 'flashing';

  async function runCheck() {
    setChecking(true); setCheckError('');
    try {
      setChk(await checkFirmware());
    } catch (e: any) {
      setCheckError(e?.message ?? 'firmware check failed');
    } finally {
      setChecking(false);
    }
  }

  const battery = chk?.battery ?? -1;
  const batteryLow = battery >= 0 && battery < 30;
  const upToDate = !!chk && !!chk.currentFw && chk.currentFw === chk.latestVersion;

  async function downloadAndFlash(commit: boolean) {
    if (!chk?.downloadUrl) return;
    modeRef.current = 'flashing';
    setMode('flashing'); setCommitting(commit); setResult(null);
    setStep(0); setPercent(-1); setPhase('Downloading firmware…');
    try {
      const path = await downloadFirmware(chk.downloadUrl);
      setStep(1); setPhase('Starting…');
      await connect();
      const out = await firmwareFlash(path, commit, true /* confirm */);
      modeRef.current = 'done';
      setMode('done');
      if (commit) {
        setStep(STEPS.length);
        setResult(out?.rebooted === false
          ? { tone: 'warning', text: 'The firmware is written, but the watch hasn’t shown up again yet. Check that it starts normally.' }
          : { tone: 'success', text: `Firmware ${chk.latestVersion} is on your watch. Check that it starts normally.` });
      } else {
        setStep(4);
        setResult({
          tone: 'success',
          text: 'Streamed the whole image without committing. The watch is in recovery mode and can be recovered: run Flash to finish, or turn it off and on again.',
        });
      }
    } catch (e: any) {
      modeRef.current = 'error';
      setMode('error');
      setResult({
        tone: 'error',
        text: `${e?.message ?? 'The flash failed.'} If the watch is stuck in recovery mode, it can be recovered: run Flash again, or flash it with SuuntoLink on Windows.`,
      });
    } finally {
      await disconnect().catch(() => {});
    }
  }

  function confirmStreamOnly() {
    Alert.alert('Stream only (recoverable test)',
      'Downloads the firmware, enters the bootloader and streams the whole image but STOPS ' +
      'before committing. The watch is left in BSL and is fully recoverable. Keep the USB cable still. Proceed?',
      [{ text: 'Cancel', style: 'cancel' }, { text: 'Stream', onPress: () => downloadAndFlash(false) }]);
  }

  function confirmCommit() {
    Alert.alert('⚠ Flash firmware',
      `Downloads and writes firmware ${chk?.latestVersion} to the ${chk?.model}, then reboots it. ` +
      'An interruption can brick the watch (recoverable via SuuntoLink). Keep the cable still. Proceed?',
      [{ text: 'Cancel', style: 'cancel' },
       { text: 'Flash now', style: 'destructive', onPress: () => downloadAndFlash(true) }]);
  }

  return (
    <ScrollView style={s.root} contentContainerStyle={s.content}>
      <Card style={s.card}>
        <Text style={s.title}>Firmware</Text>
        <Text style={s.warn}>
          Detects the watch and fetches the matching firmware from Suunto automatically.
          Use “Stream only” first (recoverable) to validate, then “Flash”.
        </Text>
        <Button label="Detect & check" icon="sync" onPress={runCheck} busy={checking} disabled={checking || flashing} />
        <StatusNote text={checkError} tone="error" />
      </Card>

      {!!chk && (
        <Card style={s.card}>
          <Text style={s.sectionTitle}>{chk.model || 'Watch'}</Text>
          <Row s={s} k="Serial" v={chk.serial || '—'} />
          <Row s={s} k="Hardware" v={chk.hwVersion || '—'} />
          <Row s={s} k="Current firmware" v={chk.currentFw || '—'} />
          <Row s={s} k="Latest available" v={`${chk.latestVersion || '—'}${chk.releaseType ? ` (${chk.releaseType})` : ''}`} />
          <Row s={s} k="Battery" v={battery >= 0 ? `${battery}%` : '—'} alert={batteryLow} />
          {upToDate && <StatusNote tone="success" text="Already on the latest firmware. Re-flashing installs the same version (safe)." />}
          {batteryLow && <StatusNote tone="warning" closable={false} text="Battery under 30% — charge before flashing." />}
        </Card>
      )}

      {!!chk?.downloadUrl && (
        <Card style={s.card}>
          <Text style={s.sectionTitle}>Update</Text>
          <Text style={s.warn}>Downloads firmware {chk.latestVersion} and writes it over USB. Keep the cable still.</Text>
          <Button label="Stream only (safe, recoverable)" icon="upload" variant="outline"
            onPress={confirmStreamOnly} disabled={flashing || checking || batteryLow} busy={flashing && !committing} />
          <View style={{ height: v3Spacing.small }} />
          <Button label={`Flash ${chk.latestVersion} (commit)`} icon="warning" danger
            onPress={confirmCommit} disabled={flashing || checking || batteryLow} busy={flashing && committing} />
        </Card>
      )}

      {mode !== 'idle' && (
        <Card style={s.card}>
          <Text style={s.sectionTitle}>Progress</Text>
          {flashing && !!phase && <Text style={s.phaseText}>{phase}</Text>}
          <Checklist step={step} percent={percent} active={flashing} failed={mode === 'error'} />
          {!!result && <StatusNote text={result.text} tone={result.tone} />}
        </Card>
      )}
    </ScrollView>
  );
}

// The update's steps. Done = a green circle with the drawn tick (the checkbox's), current = bold
// with pulsing dots, or the % and a thin bar while writing; the steps to come wait in grey. After
// a failure the step it stopped at gets a red ring.
function Checklist({
  step, percent, active, failed,
}: { step: number; percent: number; active: boolean; failed: boolean }) {
  const t = useV3Theme();
  return (
    <View style={{ gap: 7 }}>
      {STEPS.map((label, i) => {
        const done = i < step;
        const current = active && i === step;
        const stopped = failed && i === step;
        const writing = current && i === WRITE_STEP && percent >= 0;
        const pct = Math.max(0, Math.min(100, percent));
        const state = done ? 'done' : current ? (writing ? `${Math.round(pct)}%` : 'in progress')
          : stopped ? 'stopped' : 'to do';
        return (
          <View key={label} style={{ gap: 4 }} accessible accessibilityLabel={`${label}, ${state}`}>
            <View style={{ flexDirection: 'row', alignItems: 'center', gap: v3Spacing.small }}>
              <View style={{
                width: 18, height: 18, borderRadius: 9, alignItems: 'center', justifyContent: 'center',
                backgroundColor: done ? t.success : 'transparent',
                borderWidth: done ? 0 : 1.5,
                borderColor: stopped ? t.error : current ? t.primary : t.borderStrong,
              }}>
                {done && (
                  <View style={{ width: 4.5, height: 8.5, marginTop: -1.5, transform: [{ rotate: '45deg' }] }}>
                    <View style={{ position: 'absolute', right: 0, top: 0, width: 2, height: 8.5, borderRadius: 1, backgroundColor: t.card }} />
                    <View style={{ position: 'absolute', left: 0, bottom: 0, width: 4.5, height: 2, borderRadius: 1, backgroundColor: t.card }} />
                  </View>
                )}
              </View>
              <Text style={{
                fontSize: v3Type.body, fontWeight: current ? '700' : '400',
                color: done || current || stopped ? t.text : t.mutedText,
              }}>{label}</Text>
              {current && !writing && <PulseDots size={5} />}
              {writing && <Text style={{ fontSize: v3Type.label, color: t.mutedText }}>{Math.round(pct)}%</Text>}
            </View>
            {writing && (
              <View style={{ marginLeft: 26, height: 4, borderRadius: 2, backgroundColor: t.cardNested, overflow: 'hidden' }}>
                <View style={{ height: 4, borderRadius: 2, backgroundColor: t.primary, width: `${pct}%` }} />
              </View>
            )}
          </View>
        );
      })}
    </View>
  );
}

function Row({ s, k, v, alert }: { s: any; k: string; v: string; alert?: boolean }) {
  return (
    <View style={s.row}>
      <Text style={s.rowKey}>{k}</Text>
      <Text style={[s.rowVal, alert && s.alertText]}>{v}</Text>
    </View>
  );
}

const createStyles = (theme: ReturnType<typeof useV3Theme>) =>
  StyleSheet.create({
    root: { flex: 1, backgroundColor: theme.background },
    content: { padding: v3Spacing.medium, gap: v3Spacing.medium },
    card: { width: '100%' },
    title: { color: theme.text, fontSize: v3Type.title, fontWeight: '700', marginBottom: v3Spacing.small },
    sectionTitle: { color: theme.text, fontSize: v3Type.subtitle, fontWeight: '700', marginBottom: v3Spacing.small },
    warn: { color: theme.mutedText, fontSize: v3Type.caption, marginBottom: v3Spacing.small, lineHeight: 18 },
    row: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 4 },
    rowKey: { color: theme.mutedText, fontSize: v3Type.body },
    rowVal: { color: theme.text, fontSize: v3Type.body, fontWeight: '600', flexShrink: 1, textAlign: 'right' },
    alertText: { color: theme.error },
    phaseText: { color: theme.mutedText, fontSize: v3Type.label, marginBottom: v3Spacing.small },
  });
