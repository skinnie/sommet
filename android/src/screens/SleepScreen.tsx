import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, ActivityIndicator, TouchableOpacity,
} from 'react-native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { MetricChart, type MetricPoint } from '../components/MetricChart';
import {
  isPolarSleepAvailable, searchBands, armForNight, getStatus, fetchAndProcess,
  getSavedDeviceId, saveDeviceId, getHistory,
  type PolarBand, type SleepNight,
} from '../services/PolarSleepService';
import type { SleepResult } from '../services/sleepStage';

// Sleep & Recovery — overnight HRV + resting-HR trend from a Polar Verity Sense recorded OFFLINE.
// André sleeps with just the band (nothing else connected), and in the morning the band's raw
// PPG+ACC is downloaded and turned into HRV entirely on-device (sleepStage.ts, the twin of the
// desktop's tools/sleep_stage.py). This complements HealthScreen's morning-HRV SPOT reading: this
// one is the whole night, unattended.
//
// Two taps: at bedtime "Start recording", in the morning "Get last night". The band's flash holds
// ~600 h, so a missed morning just waits.

export default function SleepScreen() {
  const t = useV3Theme();
  const [deviceId, setDeviceId] = useState<string | null>(null);
  const [bands, setBands] = useState<PolarBand[]>([]);
  const [scanning, setScanning] = useState(false);
  const [arming, setArming] = useState(false);
  const [fetching, setFetching] = useState(false);
  const [recording, setRecording] = useState<boolean | null>(null);
  const [result, setResult] = useState<SleepResult | null>(null);
  const [history, setHistory] = useState<SleepNight[]>([]);
  const [msg, setMsg] = useState('');
  const [err, setErr] = useState('');

  const available = isPolarSleepAvailable();

  useEffect(() => {
    (async () => {
      setDeviceId(await getSavedDeviceId());
      setHistory(await getHistory());
    })();
  }, []);

  const onScan = useCallback(async () => {
    setScanning(true); setErr(''); setMsg('');
    try {
      const found = await searchBands();
      setBands(found);
      if (found.length === 0) setErr('No Polar band found — turn it on / take it out of the case.');
    } catch (e: any) {
      setErr(bleErr(e));
    } finally {
      setScanning(false);
    }
  }, []);

  const onPick = useCallback(async (id: string) => {
    setDeviceId(id); setBands([]); await saveDeviceId(id);
  }, []);

  const onArm = useCallback(async () => {
    if (!deviceId) return;
    setArming(true); setErr(''); setMsg('');
    try {
      const r = await armForNight(deviceId);
      setRecording(true);
      setMsg(`Recording started (${r.types.join(' + ')}). You can put the phone down — the band
records on its own. Tap "Get last night" in the morning.`);
    } catch (e: any) {
      setErr(bleErr(e));
    } finally {
      setArming(false);
    }
  }, [deviceId]);

  const onStatus = useCallback(async () => {
    if (!deviceId) return;
    setErr(''); setMsg('');
    try {
      const s = await getStatus(deviceId);
      setRecording(s.recording);
      setMsg(s.recording ? `Still recording (${s.types.join(' + ')}).` : 'Not currently recording.');
    } catch (e: any) {
      setErr(bleErr(e));
    }
  }, [deviceId]);

  const onFetch = useCallback(async () => {
    if (!deviceId) return;
    setFetching(true); setErr(''); setMsg('');
    try {
      const r = await fetchAndProcess(deviceId, true);
      setResult(r);
      setRecording(false);
      if (!r.ok) setErr(r.error || 'Could not derive HRV from the recording.');
      setHistory(await getHistory());
    } catch (e: any) {
      setErr(bleErr(e));
    } finally {
      setFetching(false);
    }
  }, [deviceId]);

  const rhrSeries: MetricPoint[] = useMemo(
    () => history.filter(n => n.restingHrBpm != null).map(n => ({ date: n.date, value: n.restingHrBpm as number })),
    [history],
  );
  const hrvSeries: MetricPoint[] = useMemo(
    () => history.filter(n => n.overnightRmssdMs != null).map(n => ({ date: n.date, value: n.overnightRmssdMs as number })),
    [history],
  );

  const card = (children: React.ReactNode) => (
    <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
      {children}
    </View>
  );

  const btn = (label: string, onPress: () => void, busy = false, disabled = false) => (
    <TouchableOpacity
      disabled={busy || disabled}
      onPress={onPress}
      style={{ marginTop: v3Spacing.medium, alignSelf: 'flex-start',
               paddingVertical: v3Spacing.small, paddingHorizontal: v3Spacing.medium,
               borderRadius: v3Radius.card, borderWidth: 1,
               borderColor: (busy || disabled) ? t.border : t.primary, opacity: (busy || disabled) ? 0.6 : 1 }}>
      <Text style={{ color: t.primary, fontSize: v3Type.body }}>{busy ? 'Working…' : label}</Text>
    </TouchableOpacity>
  );

  return (
    <ScrollView style={{ flex: 1, backgroundColor: t.background }} contentContainerStyle={{ padding: v3Spacing.medium }}>
      {!available && card(
        <>
          <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '700' }}>Sleep & Recovery</Text>
          <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>
            Overnight HRV from a Polar Verity Sense needs a newer build (the offline-recording module
            isn't in this one yet).
          </Text>
        </>,
      )}

      {available && card(
        <>
          <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '700' }}>Sleep & Recovery</Text>
          <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>
            Wear the Polar Verity Sense to bed. At bedtime tap Start; in the morning tap Get last
            night. The band records on its own — nothing has to stay connected. HRV is computed on
            your phone; your data never leaves it.
          </Text>

          {!deviceId && (
            <>
              {btn(scanning ? 'Scanning…' : 'Find my band', onScan, scanning)}
              {bands.map(b => (
                <TouchableOpacity key={b.deviceId} onPress={() => onPick(b.deviceId)}
                  style={{ marginTop: v3Spacing.small, paddingVertical: v3Spacing.small }}>
                  <Text style={{ color: t.primary, fontSize: v3Type.body }}>
                    {b.name || b.deviceId} <Text style={{ color: t.mutedText }}>({b.rssi} dBm)</Text>
                  </Text>
                </TouchableOpacity>
              ))}
            </>
          )}

          {deviceId && (
            <>
              <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginTop: v3Spacing.medium }}>
                Band: {deviceId}
                {recording === true ? '  · recording' : recording === false ? '  · idle' : ''}
              </Text>
              <View style={{ flexDirection: 'row', flexWrap: 'wrap', gap: v3Spacing.small }}>
                {btn('Start recording (bedtime)', onArm, arming)}
                {btn('Get last night (morning)', onFetch, fetching)}
                {btn('Check status', onStatus)}
              </View>
            </>
          )}

          {arming && <ActivityIndicator color={t.primary} style={{ marginTop: v3Spacing.small }} />}
          {fetching && (
            <>
              <ActivityIndicator color={t.primary} style={{ marginTop: v3Spacing.small }} />
              <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>
                Downloading the night and computing HRV… this can take a minute.
              </Text>
            </>
          )}
          {msg.length > 0 && <Text style={{ color: t.text, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>{msg}</Text>}
          {err.length > 0 && <Text style={{ color: t.error, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>{err}</Text>}
        </>,
      )}

      {result?.ok && card(
        <>
          <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '700' }}>Last night</Text>
          <View style={{ flexDirection: 'row', flexWrap: 'wrap', marginTop: v3Spacing.small }}>
            {stat(t, 'Overnight HRV', result.overnight.overnightRmssdMs, ' ms')}
            {stat(t, 'Resting HR', result.overnight.restingHrBpm, ' bpm')}
            {stat(t, 'Lowest HR', result.overnight.minHrBpm, ' bpm')}
            {stat(t, 'ln(RMSSD)×20', result.overnight.overnightLnRmssdX20, '')}
          </View>
          <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>
            {result.overnight.nWindows} five-minute windows over {result.overnight.recordingSpanMin} min ·
            {' '}{result.beatsUsed} beats used, {result.beatsDroppedMotion} dropped for movement.
          </Text>
        </>,
      )}

      {hrvSeries.length >= 2 && (
        <MetricChart label="Overnight HRV (RMSSD)" unit=" ms" series={hrvSeries} decimals={0} />
      )}
      {rhrSeries.length >= 2 && (
        <MetricChart label="Resting HR (overnight)" unit=" bpm" series={rhrSeries} decimals={0} />
      )}
    </ScrollView>
  );
}

function stat(t: any, label: string, value: number | null, unit: string) {
  return (
    <View style={{ width: '50%', paddingVertical: v3Spacing.small }}>
      <Text style={{ color: t.mutedText, fontSize: v3Type.caption }}>{label}</Text>
      <Text style={{ color: t.text, fontSize: v3Type.title, fontWeight: '700' }}>
        {value == null ? '—' : `${value}${unit}`}
      </Text>
    </View>
  );
}

function bleErr(e: any): string {
  const m = String(e?.message ?? e);
  if (m === 'native-missing') return 'Sleep support needs a newer build.';
  if (m.includes('NO_RECORDING')) return 'No recording on the band — was it armed at bedtime?';
  if (m.includes('NOT_READY') || m.includes('CONNECT')) return 'Could not reach the band — is it on, charged and near the phone?';
  return m;
}

const styles = StyleSheet.create({
  card: { padding: v3Spacing.medium, marginBottom: v3Spacing.medium, borderWidth: 1 },
});
