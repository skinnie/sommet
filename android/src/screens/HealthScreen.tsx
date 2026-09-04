import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, ActivityIndicator, RefreshControl, TouchableOpacity,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { MetricChart } from '../components/MetricChart';
import { fetchWellness, WellnessDay } from '../services/WellnessService';
import { isHrStrapAvailable, measureHrv, type HrStrapReading } from '../services/HrStrapService';

// Health — the Android counterpart of desktop/qml/pages/HealthPage.qml (André, 2026-08-26:
// "port everything to android"). Resting HR, HRV, sleep, steps and VO2max from intervals.icu's
// wellness feed.
//
// Honest difference from the desktop version: no Garmin body battery, and no watch-measured
// morning HRV from the Ambit3's own R-R data. Both of those reach the desktop through the
// Python backend (python-garminconnect / tools/hrv.py), which Android has no equivalent of.
// Everything shown here is real data verified present in this athlete's own intervals.icu
// history (2026-08-26: restingHR 664 values, sleep 388, vo2max 63, steps 101, hrv 51).

interface Metric {
  key: keyof WellnessDay;
  label: string;
  unit: string;
  decimals: number;
}

// Ordered by how much they matter day to day for training readiness, which is what this screen
// is actually for - not by how much data happens to exist for each.
const METRICS: Metric[] = [
  { key: 'restingHR', label: 'Resting heart rate', unit: ' bpm', decimals: 0 },
  { key: 'hrv',       label: 'HRV',                unit: ' ms',  decimals: 0 },
  { key: 'sleepHours',label: 'Sleep',              unit: ' h',   decimals: 1 },
  { key: 'steps',     label: 'Steps',              unit: '',     decimals: 0 },
  { key: 'vo2max',    label: 'VO₂max',             unit: '',     decimals: 0 },
];

export default function HealthScreen() {
  const t = useV3Theme();
  const navigation = useNavigation<any>();
  const [days, setDays] = useState<WellnessDay[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setDays(await fetchWellness(365));
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Morning HRV straight from a BLE strap (COOSPO HW9) - the mobile equivalent of the desktop's
  // "Measure HRV (COOSPO)". The native HrStrap module does the BLE; hrv.ts does the math.
  const [measuring, setMeasuring] = useState(false);
  const [strapResult, setStrapResult] = useState<HrStrapReading | null>(null);
  const [strapErr, setStrapErr] = useState('');
  const measureStrap = useCallback(async () => {
    setMeasuring(true); setStrapErr(''); setStrapResult(null);
    try {
      const r = await measureHrv(120, 'HW9');
      if (r.ok) setStrapResult(r);
      else setStrapErr('Strap not reading - wear it snugly and try again.');
    } catch (e: any) {
      const m = String(e?.message ?? e);
      setStrapErr(m === 'native-missing'
        ? 'Heart-rate-strap support needs a newer build.'
        : `Measurement failed: ${m}`);
    } finally {
      setMeasuring(false);
    }
  }, []);

  // Per metric: the points that actually have a value. Most wellness days are null for most
  // fields, so each chart gets its own filtered series rather than one shared date axis.
  const seriesFor = useMemo(() => {
    const out = new Map<string, { date: string; value: number }[]>();
    for (const m of METRICS) {
      out.set(m.key as string, days
        .filter(d => typeof d[m.key] === 'number')
        .map(d => ({ date: d.date, value: d[m.key] as number })));
    }
    return out;
  }, [days]);

  const present = METRICS.filter(m => (seriesFor.get(m.key as string) ?? []).length > 0);

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={refresh} tintColor={t.primary} />}
    >
      {loading && days.length === 0 && (
        <ActivityIndicator color={t.primary} style={{ marginVertical: v3Spacing.large }} />
      )}

      {error.length > 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.error, fontSize: v3Type.body }}>{error}</Text>
        </View>
      )}

      {/* Morning HRV from a heart-rate strap (COOSPO HW9) - no watch needed. */}
      {isHrStrapAvailable() && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '700' }}>
            Morning HRV (heart-rate strap)
          </Text>
          <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>
            Wear the strap (COOSPO HW9), sit or lie still, and tap Measure. It reads ~2 min of
            heart-beats over Bluetooth and computes your RMSSD.
          </Text>
          <TouchableOpacity
            disabled={measuring}
            onPress={measureStrap}
            style={{ marginTop: v3Spacing.medium, alignSelf: 'flex-start',
                     paddingVertical: v3Spacing.small, paddingHorizontal: v3Spacing.medium,
                     borderRadius: v3Radius.card, borderWidth: 1,
                     borderColor: measuring ? t.border : t.primary, opacity: measuring ? 0.6 : 1 }}>
            <Text style={{ color: t.primary, fontSize: v3Type.body }}>
              {measuring ? 'Measuring… stay still' : 'Measure HRV (COOSPO)'}
            </Text>
          </TouchableOpacity>
          {strapResult && (
            <Text style={{ color: t.text, fontSize: v3Type.subtitle, fontWeight: '700',
                           marginTop: v3Spacing.medium }}>
              RMSSD {Math.round(strapResult.rmssdMs ?? 0)} ms
              <Text style={{ color: t.mutedText, fontSize: v3Type.body, fontWeight: '400' }}>
                {'  ·  '}HR {Math.round(strapResult.meanHrBpm ?? 0)} bpm · {strapResult.nBeats} beats
              </Text>
            </Text>
          )}
          {strapErr.length > 0 && (
            <Text style={{ color: t.error, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>
              {strapErr}
            </Text>
          )}
        </View>
      )}

      {!loading && present.length === 0 && error.length === 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.mutedText, fontSize: v3Type.body }}>
            No health data yet. Connect intervals.icu — resting HR, HRV, sleep and
            steps sync from whatever device feeds it.
          </Text>
          {/* #9 (André, 2026-09-02): one-tap route to the shared connection settings. */}
          <TouchableOpacity
            onPress={() => navigation.navigate('Settings')}
            style={{ marginTop: v3Spacing.medium, alignSelf: 'flex-start',
                     paddingVertical: v3Spacing.small, paddingHorizontal: v3Spacing.medium,
                     borderRadius: v3Radius.card, borderWidth: 1, borderColor: t.border }}>
            <Text style={{ color: t.primary, fontSize: v3Type.body }}>Open Settings → Connections</Text>
          </TouchableOpacity>
        </View>
      )}

      {/* Current values first, then the trends - the summary before the detail. */}
      {present.length > 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <View style={styles.tiles}>
            {present.map(m => {
              const s = seriesFor.get(m.key as string)!;
              const last = s[s.length - 1];
              return (
                <View key={m.key as string} style={styles.tile}>
                  <Text style={[styles.label, { color: t.mutedText }]}>{m.label}</Text>
                  <Text style={[styles.big, { color: t.text }]}>
                    {last.value.toFixed(m.decimals)}{m.unit}
                  </Text>
                  <Text style={[styles.caption, { color: t.mutedText }]}>{last.date}</Text>
                </View>
              );
            })}
          </View>
        </View>
      )}

      {present.map(m => (
        <MetricChart
          key={m.key as string}
          label={m.label}
          unit={m.unit}
          decimals={m.decimals}
          series={seriesFor.get(m.key as string)!}
        />
      ))}

      {present.length > 0 && (
        <Text style={[styles.caption, { color: t.mutedText }]}>
          From intervals.icu. Body battery and the watch's own morning HRV are desktop-only.
        </Text>
      )}
    </ScrollView>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, padding: v3Spacing.medium, marginBottom: v3Spacing.medium },
  tiles: { flexDirection: 'row', flexWrap: 'wrap' },
  tile: { minWidth: '33%', marginBottom: v3Spacing.small, paddingRight: v3Spacing.small },
  label: { fontSize: v3Type.tiny, fontWeight: '600' },
  big: { fontSize: v3Type.subtitle, fontWeight: '700' },
  caption: { fontSize: v3Type.caption },
});
