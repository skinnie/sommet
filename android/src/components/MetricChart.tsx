import React, { useMemo } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Path, Line, Circle, Text as SvgText } from 'react-native-svg';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';

// A small line chart for a dated metric series — the Android counterpart of the desktop's
// MetricChart.qml, used by the Weight and Health screens (André, 2026-08-26: "port everything
// to android"). Drawn with react-native-svg, the same dependency ElevationChart.tsx already
// uses, rather than pulling in a charting library for two screens.
//
// Design follows the 2026-08-25 tune-up rules already applied across this app: a flat
// cardNested-style plot area, a hairline border rather than a shadow, and the accent colour
// used only for the line itself.

export interface MetricPoint {
  date: string;      // "YYYY-MM-DD"
  value: number;
}

export function MetricChart({
  label,
  unit = '',
  series,
  height = 160,
  decimals = 0,
}: {
  label: string;
  unit?: string;
  series: MetricPoint[];
  height?: number;
  decimals?: number;
}) {
  const t = useV3Theme();

  // Chart geometry is computed from the data, not assumed: a flat series (every value equal)
  // would otherwise divide by a zero range and collapse the path to NaN.
  const geom = useMemo(() => {
    if (series.length < 2) return null;
    const values = series.map(p => p.value);
    let min = Math.min(...values);
    let max = Math.max(...values);
    if (max - min < 1e-9) { min -= 1; max += 1; }      // flat series: give it a visible band
    const pad = (max - min) * 0.1;
    min -= pad; max += pad;
    return { min, max, values };
  }, [series]);

  const latest = series.length > 0 ? series[series.length - 1] : null;

  return (
    <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
      <View style={styles.header}>
        <Text style={[styles.label, { color: t.mutedText }]}>{label}</Text>
        {latest && (
          <Text style={[styles.value, { color: t.text }]}>
            {latest.value.toFixed(decimals)}{unit}
          </Text>
        )}
      </View>

      {!geom ? (
        <Text style={[styles.empty, { color: t.mutedText }]}>
          Not enough data yet.
        </Text>
      ) : (
        <Chart series={series} geom={geom} height={height} color={t.primary} grid={t.border} axis={t.mutedText} />
      )}
    </View>
  );
}

function Chart({
  series, geom, height, color, grid, axis,
}: {
  series: MetricPoint[];
  geom: { min: number; max: number };
  height: number;
  color: string;
  grid: string;
  axis: string;
}) {
  // A fixed viewBox with preserveAspectRatio="none" lets the SVG stretch to whatever width the
  // card ends up being, without needing an onLayout measurement pass.
  const W = 300;
  const H = height;
  const padL = 4, padR = 4, padT = 8, padB = 18;

  const x = (i: number) => padL + (W - padL - padR) * (series.length === 1 ? 0.5 : i / (series.length - 1));
  const y = (v: number) => padT + (H - padT - padB) * (1 - (v - geom.min) / (geom.max - geom.min));

  const d = series.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(p.value).toFixed(2)}`).join(' ');
  const last = series[series.length - 1];

  return (
    <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} preserveAspectRatio="none">
      {/* baseline only - a faint reference, not a full grid, so the line stays the subject */}
      <Line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke={grid} strokeWidth={1} />
      <Path d={d} stroke={color} strokeWidth={2} fill="none" />
      {/* emphasised endpoint: the current value is what you actually look for */}
      <Circle cx={x(series.length - 1)} cy={y(last.value)} r={3} fill={color} />
      <SvgText x={padL} y={H - 5} fontSize={9} fill={axis}>{series[0].date.slice(5)}</SvgText>
      <SvgText x={W - padR} y={H - 5} fontSize={9} fill={axis} textAnchor="end">{last.date.slice(5)}</SvgText>
    </Svg>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, padding: v3Spacing.medium, marginBottom: v3Spacing.medium },
  header: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: v3Spacing.small },
  label: { fontSize: v3Type.label, fontWeight: '600' },
  value: { fontSize: v3Type.title, fontWeight: '700' },
  empty: { fontSize: v3Type.body, paddingVertical: v3Spacing.medium },
});
