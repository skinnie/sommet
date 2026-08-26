import React, { useCallback, useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, ScrollView, ActivityIndicator, RefreshControl,
} from 'react-native';
import Svg, { Path, Line } from 'react-native-svg';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { loadCoachData, CoachData, ReadinessLight } from '../services/CoachService';

// Coach — the Android counterpart of desktop/qml/pages/CoachPage.qml's readiness beacon
// (André, 2026-08-26: "port everything to android").
//
// Scope note, stated plainly rather than left as a surprise: this ports the READINESS half -
// the traffic light, the fitness/fatigue/freshness numbers and the trend chart, which is the
// part that answers "what should I do today?". The desktop's chat half is NOT here: it needs
// an Anthropic API key, a key store, a conversation UI and the SYSTM workout catalogue, and
// shipping a half-wired chat box would be worse than shipping none. The numbers below are the
// same ones the desktop shows, from the same source, with the same thresholds.

export default function CoachScreen() {
  const t = useV3Theme();
  const [data, setData] = useState<CoachData>({ readiness: null, chart: [] });
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState('');

  const refresh = useCallback(async () => {
    setLoading(true);
    setError('');
    try {
      setData(await loadCoachData(90));
    } catch (e: any) {
      setError(String(e?.message ?? e));
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => { refresh(); }, [refresh]);

  // Semantic colours, matching the desktop's own mapping of light -> Theme colour.
  const lightColor = (l: ReadinessLight): string =>
    l === 'green' ? t.success
    : l === 'tempered' ? '#8FA33B'
    : l === 'yellow' ? t.warning
    : t.error;

  const lightLabel = (l: ReadinessLight): string =>
    l === 'green' ? 'Fresh'
    : l === 'tempered' ? 'Fresh, ease in'
    : l === 'yellow' ? 'Some fatigue'
    : 'Deep fatigue';

  const r = data.readiness;

  return (
    <ScrollView
      style={{ flex: 1, backgroundColor: t.background }}
      contentContainerStyle={{ padding: v3Spacing.medium }}
      refreshControl={<RefreshControl refreshing={loading} onRefresh={refresh} tintColor={t.primary} />}
    >
      {loading && !r && <ActivityIndicator color={t.primary} style={{ marginVertical: v3Spacing.large }} />}

      {error.length > 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.error, fontSize: v3Type.body }}>{error}</Text>
        </View>
      )}

      {!loading && !r && error.length === 0 && (
        <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
          <Text style={{ color: t.mutedText, fontSize: v3Type.body }}>
            No training load yet. Connect intervals.icu in Settings — readiness is computed from
            the fitness and fatigue it tracks.
          </Text>
        </View>
      )}

      {r && (
        <>
          <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
            <Text style={[styles.label, { color: t.mutedText }]}>Today</Text>
            <View style={styles.beacon}>
              <View style={[styles.dot, { backgroundColor: lightColor(r.light) }]} />
              <Text style={[styles.lightText, { color: lightColor(r.light) }]}>{lightLabel(r.light)}</Text>
            </View>
            <Text style={[styles.sentence, { color: t.mutedText }]}>{r.sentence}</Text>

            {/* The three numbers behind the light, as flat tiles - the same trio and the same
                flat cardNested treatment the desktop uses. */}
            <View style={styles.tiles}>
              {[
                { k: 'Fitness', v: r.fitness },
                { k: 'Fatigue', v: r.fatigue },
                { k: 'Freshness', v: r.freshness },
              ].map(m => (
                <View key={m.k} style={[styles.tile, { backgroundColor: t.cardNested, borderRadius: v3Radius.small }]}>
                  <Text style={[styles.tileLabel, { color: t.mutedText }]}>{m.k}</Text>
                  <Text style={[styles.tileValue, { color: t.text }]}>{Math.round(m.v)}</Text>
                </View>
              ))}
            </View>
          </View>

          {data.chart.length > 1 && (
            <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
              <View style={styles.legend}>
                <View style={[styles.swatch, { backgroundColor: t.primary }]} />
                <Text style={[styles.tileLabel, { color: t.mutedText, marginRight: v3Spacing.medium }]}>Fitness</Text>
                <View style={[styles.swatch, { backgroundColor: t.mutedText, opacity: 0.6 }]} />
                <Text style={[styles.tileLabel, { color: t.mutedText }]}>Fatigue</Text>
              </View>
              <FitnessChart chart={data.chart} fitnessColor={t.primary} fatigueColor={t.mutedText} grid={t.border} />
            </View>
          )}

          <Text style={[styles.caption, { color: t.mutedText }]}>
            Fitness and fatigue come from intervals.icu, computed across every device you use.
          </Text>
        </>
      )}
    </ScrollView>
  );
}

function FitnessChart({
  chart, fitnessColor, fatigueColor, grid,
}: {
  chart: { date: string; fitness: number; fatigue: number }[];
  fitnessColor: string;
  fatigueColor: string;
  grid: string;
}) {
  const W = 300, H = 110, padT = 6, padB = 6, padX = 2;
  // Both series share ONE scale, otherwise the fitness/fatigue crossover - the whole point of
  // this chart - would be a meaningless artefact of two different axes.
  const all = chart.flatMap(p => [p.fitness, p.fatigue]);
  const max = Math.max(...all) * 1.1 || 1;

  const x = (i: number) => padX + (W - padX * 2) * (i / (chart.length - 1));
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const path = (sel: (p: typeof chart[0]) => number) =>
    chart.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(1)},${y(sel(p)).toFixed(1)}`).join(' ');

  return (
    <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      <Line x1={padX} y1={H - padB} x2={W - padX} y2={H - padB} stroke={grid} strokeWidth={1} />
      <Path d={path(p => p.fatigue)} stroke={fatigueColor} strokeWidth={1.4} fill="none"
            strokeDasharray="3,3" opacity={0.7} />
      <Path d={path(p => p.fitness)} stroke={fitnessColor} strokeWidth={2.4} fill="none" />
    </Svg>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, padding: v3Spacing.medium, marginBottom: v3Spacing.medium },
  label: { fontSize: v3Type.label, fontWeight: '600' },
  beacon: { alignItems: 'center', marginTop: v3Spacing.medium },
  dot: { width: 64, height: 64, borderRadius: 32 },
  lightText: { fontSize: v3Type.largeTitle, fontWeight: '700', marginTop: v3Spacing.small },
  sentence: { fontSize: v3Type.label, textAlign: 'center', marginTop: v3Spacing.small, marginBottom: v3Spacing.medium },
  tiles: { flexDirection: 'row', justifyContent: 'space-between' },
  tile: { flex: 1, paddingVertical: 10, alignItems: 'center', marginHorizontal: 3 },
  tileLabel: { fontSize: v3Type.tiny, fontWeight: '600' },
  tileValue: { fontSize: v3Type.bodyLarge, fontWeight: '700' },
  legend: { flexDirection: 'row', alignItems: 'center', marginBottom: v3Spacing.small },
  swatch: { width: 12, height: 4, borderRadius: 2, marginRight: 6 },
  caption: { fontSize: v3Type.caption },
});
