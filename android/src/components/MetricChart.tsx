import React, { useMemo, useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Path, Line, Circle } from 'react-native-svg';
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
        <>
          <Chart series={series} geom={geom} height={height} color={t.primary} grid={t.border} />
          {/* Axis dates live OUTSIDE the SVG on purpose. The chart stretches to the card width
              via preserveAspectRatio="none", which scales glyphs horizontally too - dates drawn
              inside it came out visibly squashed. Found by actually driving the app, 2026-08-26.
              The year is kept: slicing it off made both ends of a 365-day window read "08-26". */}
          <View style={styles.axis}>
            <Text style={[styles.axisLabel, { color: t.mutedText }]}>{series[0].date}</Text>
            <Text style={[styles.axisLabel, { color: t.mutedText }]}>{series[series.length - 1].date}</Text>
          </View>
        </>
      )}
    </View>
  );
}

function Chart({
  series, geom, height, color, grid,
}: {
  series: MetricPoint[];
  geom: { min: number; max: number };
  height: number;
  color: string;
  grid: string;
}) {
  // The viewBox matches the REAL laid-out width, measured via onLayout, rather than a fixed 300
  // stretched with preserveAspectRatio="none". Stretching scaled the horizontal axis about 4x on
  // a tablet and scaled STROKES with it, so every line rendered visibly thick and blocky
  // (André, 2026-08-26: "the graphs show very thick lines") - and squashed any text drawn inside.
  // Measuring costs one extra render on first layout and keeps strokes a true 2px everywhere.
  const [w, setW] = useState(0);
  const H = height;
  const padL = 4, padR = 4, padT = 8, padB = 6;
  const W = w || 300;

  const x = (i: number) => padL + (W - padL - padR) * (series.length === 1 ? 0.5 : i / (series.length - 1));
  const y = (v: number) => padT + (H - padT - padB) * (1 - (v - geom.min) / (geom.max - geom.min));

  const d = series.map((p, i) => `${i === 0 ? 'M' : 'L'}${x(i).toFixed(2)},${y(p.value).toFixed(2)}`).join(' ');
  const last = series[series.length - 1];

  return (
    <View onLayout={e => setW(Math.round(e.nativeEvent.layout.width))} style={{ width: '100%' }}>
      <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`}>
        {/* baseline only - a faint reference, not a full grid, so the line stays the subject */}
        <Line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke={grid} strokeWidth={1} />
        <Path d={d} stroke={color} strokeWidth={1.6} fill="none" />
        {/* emphasised endpoint: the current value is what you actually look for */}
        <Circle cx={x(series.length - 1)} cy={y(last.value)} r={2.5} fill={color} />
      </Svg>
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, padding: v3Spacing.medium, marginBottom: v3Spacing.medium },
  header: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: v3Spacing.small },
  label: { fontSize: v3Type.label, fontWeight: '600' },
  value: { fontSize: v3Type.title, fontWeight: '700' },
  empty: { fontSize: v3Type.body, paddingVertical: v3Spacing.medium },
  axis: { flexDirection: 'row', justifyContent: 'space-between', marginTop: 4 },
  axisLabel: { fontSize: v3Type.tiny },
});
