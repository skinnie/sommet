import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, RefreshControl, TouchableOpacity,
} from 'react-native';
import { useNavigation } from '@react-navigation/native';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { MetricChart } from '../components/MetricChart';
import { fmtDate } from '../i18n';
import { fetchWellness, WellnessDay } from '../services/WellnessService';
import {
  loadWatchActivity, loadWatchRecovery, loadWatchSteps, weekActivity, weekSteps, type WatchRecovery,
} from '../services/AmbitSteps';
import { isHrStrapAvailable, measureHrv, type HrStrapReading } from '../services/HrStrapService';
import { isStrapHrvEnabled } from '../services/StrapHrvPref';
import { oldBadge, tileCaption } from '../services/HealthTrend';
import { Button, EmptyState, SkeletonList, StatTile, StatusNote } from '../components/ui/primitives';
import { ErrorBanner } from '../components/ui/ErrorBanner';

// Health — the Android counterpart of desktop/qml/pages/HealthPage.qml (André, 2026-08-26:
// "port everything to android"). Resting HR, HRV, sleep, steps and VO2max from intervals.icu's
// wellness feed, plus the Ambit3's own daily steps stored at each sync (AmbitSteps.ts), which
// win over intervals.icu on a day the watch counted.
//
// Honest difference from the desktop version: no Garmin body battery, and no watch-measured
// morning HRV from the Ambit3's own R-R data. Both of those reach the desktop through the
// Python backend (python-garminconnect / tools/hrv.py), which Android has no equivalent of.
// Everything shown here is real data verified present in this athlete's own intervals.icu
// history (2026-08-26: restingHR 664 values, sleep 388, vo2max 63, steps 101, hrv 51).

// A wellness day plus the Ambit3's own daily activity kcal (its "Activity" display).
type HealthDay = WellnessDay & { watchKcal?: number };

interface Metric {
  key: keyof HealthDay;
  label: string;
  unit: string;
  decimals: number;
  // The tile compares the latest value with the 7 days before it (desktop picks #12: resting
  // HR, HRV and sleep); the others show the date of the value.
  trend?: boolean;
}

// Ordered by how much they matter day to day for training readiness, which is what this screen
// is actually for - not by how much data happens to exist for each.
const METRICS: Metric[] = [
  { key: 'restingHR', label: 'Resting heart rate', unit: ' bpm', decimals: 0, trend: true },
  { key: 'hrv',       label: 'HRV',                unit: ' ms',  decimals: 0, trend: true },
  { key: 'sleepHours',label: 'Sleep',              unit: ' h',   decimals: 1, trend: true },
  // Train Libre's sleep health score (SleepScore.ts) - from duration alone here, see WellnessService.
  { key: 'sleepScore', label: 'Sleep score',       unit: '',     decimals: 0 },
  { key: 'steps',     label: 'Steps',              unit: '',     decimals: 0 },
  { key: 'watchKcal', label: 'Activity (watch)',   unit: ' kcal', decimals: 0 },
  { key: 'vo2max',    label: 'VO₂max',             unit: '',     decimals: 0 },
];

// The watch's own step counts over intervals.icu's on the same day; days only the watch has are
// added. Same priority as the desktop (intervals < garmin < watch).
function withWatchSteps(list: WellnessDay[], watch: Array<{ date: string; steps: number }>,
                        kcal: Array<{ date: string; kcal: number }> = []): HealthDay[] {
  if (watch.length === 0 && kcal.length === 0) return list;
  const byDate = new Map<string, HealthDay>(list.map(d => [d.date, { ...d }]));
  for (const w of watch) {
    const d = byDate.get(w.date) ?? { date: w.date };
    d.steps = w.steps;
    byDate.set(w.date, d);
  }
  for (const k of kcal) {
    const d = byDate.get(k.date) ?? { date: k.date };
    d.watchKcal = k.kcal;
    byDate.set(k.date, d);
  }
  return Array.from(byDate.values()).sort((a, b) => a.date.localeCompare(b.date));
}

const TILE_MIN = 150;
const TILE_GAP = v3Spacing.small;

function recoveryText(h: number): string {
  const m = Math.round(h * 60);
  return m < 60 ? `${m} min` : `${Math.floor(m / 60)} h ${m % 60} min`;
}

export default function HealthScreen() {
  const t = useV3Theme();
  const navigation = useNavigation<any>();
  const [days, setDays] = useState<HealthDay[]>([]);
  const [recovery, setRecovery] = useState<WatchRecovery | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    let list: WellnessDay[] = [];
    try {
      list = await fetchWellness(365);
    } catch (e: any) {
      setError(String(e?.message ?? e));
    }
    try {
      setDays(withWatchSteps(list, await loadWatchSteps(), await loadWatchActivity()));
      setRecovery(await loadWatchRecovery());
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Strap morning-HRV is opt-in (Settings → Health), default OFF, mirroring the desktop toggle.
  // Re-read on focus so flipping it in Settings takes effect without a restart.
  const [strapHrvEnabled, setStrapHrvEnabled] = useState(false);
  useEffect(() => {
    const unsub = navigation.addListener('focus', () => { isStrapHrvEnabled().then(setStrapHrvEnabled); });
    isStrapHrvEnabled().then(setStrapHrvEnabled);
    return unsub;
  }, [navigation]);

  // Morning HRV straight from a BLE heart-rate strap (Polar Verity Sense, COOSPO HW9, …) - the
  // mobile equivalent of the desktop's "Measure HRV". The native HrStrap module does the BLE and
  // finds any peripheral advertising the Heart Rate service; hrv.ts does the math.
  const [measuring, setMeasuring] = useState(false);
  const [strapResult, setStrapResult] = useState<HrStrapReading | null>(null);
  const [strapErr, setStrapErr] = useState('');
  const measureStrap = useCallback(async () => {
    setMeasuring(true); setStrapErr(''); setStrapResult(null);
    try {
      const r = await measureHrv(120);
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
  // Tiles about 150 wide (desktop 176 on a wider window), as many per row as fit, sharing it.
  const [tilesWidth, setTilesWidth] = useState(0);
  const tileCols = Math.max(1, Math.floor((tilesWidth + TILE_GAP) / (TILE_MIN + TILE_GAP)));
  const tileWidth = tilesWidth > 0 ? Math.floor((tilesWidth - TILE_GAP * (tileCols - 1)) / tileCols) : TILE_MIN;
  const week = useMemo(() => weekActivity(days.map(d => ({ date: d.date, kcal: d.watchKcal }))),
                       [days]);
  const stepsWeek = useMemo(() => weekSteps(days.map(d => ({ date: d.date, steps: d.steps ?? undefined }))),
                            [days]);

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium }}
      refreshControl={<RefreshControl refreshing={loading && days.length > 0} onRefresh={refresh} tintColor={t.primary} />}
    >
      {/* Grey rows while the first read runs (desktop picks #13). */}
      {loading && days.length === 0 && (
        <View style={{ marginBottom: v3Spacing.medium }}><SkeletonList count={3} /></View>
      )}

      {/* A failed read: one plain sentence, Try again and Send logs (desktop picks #19). */}
      <ErrorBanner
        detail={loading ? '' : error}
        context="reading health data"
        showPlainDetail
        onRetry={refresh}
      />

      {/* Sleep & Recovery: overnight HRV + resting-HR trend from a Polar Verity Sense recorded
          offline. A whole-night companion to the morning spot reading below. */}
      <TouchableOpacity
        onPress={() => navigation.navigate('Sleep')}
        style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card,
                 flexDirection: 'row', justifyContent: 'space-between', alignItems: 'center' }]}>
        <View style={{ flex: 1 }}>
          <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '700' }}>Sleep & Recovery</Text>
          <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>
            Overnight HRV + resting HR — sleep with just the Polar band, read it in the morning.
          </Text>
        </View>
        <Text style={{ color: t.primary, fontSize: v3Type.title, marginLeft: v3Spacing.medium }}>›</Text>
      </TouchableOpacity>

      {/* Morning HRV from a heart-rate strap (Polar Verity Sense, COOSPO HW9, …) - no watch needed.
          Shown only when the native strap module is present AND the user enabled it in Settings. */}
      {isHrStrapAvailable() && strapHrvEnabled && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '700' }}>
            Morning HRV (heart-rate strap)
          </Text>
          <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>
            Wear your heart-rate strap (Polar Verity Sense, COOSPO HW9, …), sit or lie still, and
            tap Measure. It reads ~2 min of heart-beats over Bluetooth and computes your RMSSD.
          </Text>
          {/* Busy button (desktop picks #5): the name stays and dots pulse while it measures. */}
          <View style={{ marginTop: v3Spacing.medium, alignSelf: 'flex-start' }}>
            <Button label="Measure HRV" onPress={measureStrap} busy={measuring} grow={false} />
          </View>
          {measuring && (
            <Text style={{ color: t.mutedText, fontSize: v3Type.caption, marginTop: v3Spacing.small }}>
              Stay still while it reads.
            </Text>
          )}
          {strapResult && (
            <Text style={{ color: t.text, fontSize: v3Type.subtitle, fontWeight: '700',
                           marginTop: v3Spacing.medium }}>
              RMSSD {Math.round(strapResult.rmssdMs ?? 0)} ms
              <Text style={{ color: t.mutedText, fontSize: v3Type.body, fontWeight: '400' }}>
                {'  ·  '}HR {Math.round(strapResult.meanHrBpm ?? 0)} bpm · {strapResult.nBeats} beats
              </Text>
            </Text>
          )}
          <StatusNote text={strapErr} tone="error" />
        </View>
      )}

      {/* Nothing to show yet: what to do next (desktop picks #11). #9 (André, 2026-09-02):
          one-tap route to the shared connection settings. */}
      {!loading && present.length === 0 && !recovery && week.days === 0 && error.length === 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <EmptyState
            icon="health"
            title="No health data yet"
            text="Connect intervals.icu, or plug in your watch to read its steps, activity calories and recovery time."
            actionLabel="Open Settings → Connections"
            onAction={() => navigation.navigate('Settings', { section: 'connections' })}
          />
        </View>
      )}

      {/* Current values first, then the trends - the summary before the detail. */}
      {/* One tile per number (desktop picks #12): resting HR, HRV and sleep compare with the 7
          days before, and an old value says how old it is. The tiles share each row evenly. */}
      {(present.length > 0 || recovery || week.days > 0) && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <View style={styles.tiles} onLayout={e => setTilesWidth(Math.floor(e.nativeEvent.layout.width))}>
            {present.map(m => {
              const s = seriesFor.get(m.key as string)!;
              const last = s[s.length - 1];
              const unit = m.unit.trim();
              return (
                <StatTile
                  key={m.key as string}
                  style={{ width: tileWidth }}
                  label={m.label}
                  value={last.value.toFixed(m.decimals)}
                  unit={unit}
                  caption={m.trend ? tileCaption(s, unit, m.decimals) : (fmtDate(last.date) || last.date)}
                  badge={oldBadge(s)}
                />
              );
            })}
            {/* The watch's activity kcal from Monday to today, every watch merged. */}
            {week.days > 0 && (
              <StatTile
                style={{ width: tileWidth }}
                label="This week (watch)"
                value={String(Math.round(week.kcal))}
                unit="kcal"
                caption={`since Monday ${fmtDate(week.from) || week.from}`}
              />
            )}
            {/* The watch's "Recovery time" at the last watch read, and its peak over the
                watch's last 24 h of quarter-hour samples. */}
            {recovery && (
              <StatTile
                style={{ width: tileWidth }}
                label="Recovery time (watch)"
                value={recoveryText(recovery.hours)}
                caption={`${fmtDate(recovery.time.slice(0, 10)) || recovery.time.slice(0, 10)} ${recovery.time.slice(11, 16)}`
                  + ` · peak ${recoveryText(recovery.peak)}`}
              />
            )}
          </View>
        </View>
      )}

      {/* Steps this week (André, 2026-10-26): one bar per day, Monday to Sunday. Days still
          to come, and days nothing counted, show a flat stub. */}
      {stepsWeek.days.some(d => d.steps >= 0) && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.text, fontSize: v3Type.caption, fontWeight: '700' }}>
            Steps this week{'  '}
            <Text style={{ color: t.primary, fontSize: v3Type.caption }}>{Math.round(stepsWeek.total)}</Text>
          </Text>
          <View style={styles.bars}>
            {stepsWeek.days.map(d => (
              <View key={d.date} style={styles.barCol}>
                <View style={styles.barArea}>
                  <View style={{
                    width: '70%', borderRadius: 4,
                    height: d.steps > 0 ? `${Math.max(4, (100 * d.steps) / Math.max(1, stepsWeek.max))}%` : 4,
                    backgroundColor: d.steps > 0 ? t.primary : t.border,
                  }} />
                </View>
                <Text style={[styles.caption, { color: t.mutedText }]}>{d.steps >= 0 ? Math.round(d.steps) : ' '}</Text>
                <Text style={[styles.caption, { color: d.today ? t.text : t.mutedText, fontWeight: d.today ? '700' : '400' }]}>
                  {d.label}
                </Text>
              </View>
            ))}
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
  tiles: { flexDirection: 'row', flexWrap: 'wrap', gap: TILE_GAP },
  caption: { fontSize: v3Type.caption },
  bars: { flexDirection: 'row', height: 130, marginTop: v3Spacing.small },
  barCol: { flex: 1, alignItems: 'center' },
  barArea: { flex: 1, width: '100%', justifyContent: 'flex-end', alignItems: 'center' },
});
