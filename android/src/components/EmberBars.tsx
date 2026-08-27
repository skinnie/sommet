import React, { useMemo, useState } from 'react';
import { View, Text, StyleSheet } from 'react-native';
import Svg, { Path, Line, Text as SvgText } from 'react-native-svg';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { MetricPoint } from './MetricChart';

// Ember bar chart - the Android counterpart of desktop/qml/components/EmberBars.qml (André,
// 2026-08-27: "did't we had bars on some of them?"). The amber/green bars from the Ember concept
// mockup: rounded-top bars over faint gridlines, with an optional dashed goal line. Fasting hours
// and Calories-in render as bars (this), Water stays a line (MetricChart) - matching the desktop
// EmberPage exactly. Drawn with react-native-svg like MetricChart; the viewBox matches the
// measured width (1:1) so strokes and inline labels are not squashed.

// Rounded only on the top two corners, sitting on the baseline (like the QML arcTo path).
function barPath(x: number, y: number, w: number, h: number, r: number): string {
  const rr = Math.max(0, Math.min(r, w / 2, h));
  return `M${x},${y + rr} Q${x},${y} ${x + rr},${y} L${x + w - rr},${y} Q${x + w},${y} ${x + w},${y + rr} `
    + `L${x + w},${y + h} L${x},${y + h} Z`;
}

export function EmberBars({
  label,
  unit = '',
  series,
  barColor,
  goal = 0,
  height = 150,
  decimals = 0,
}: {
  label: string;
  unit?: string;
  series: MetricPoint[];
  barColor: string;
  goal?: number;
  height?: number;
  decimals?: number;
}) {
  const t = useV3Theme();
  const [w, setW] = useState(0);

  const latest = series.length > 0 ? series[series.length - 1] : null;
  const maxV = useMemo(() => {
    let m = goal || 0;
    for (const p of series) if (p.value > m) m = p.value;
    return (m * 1.15) || 1;
  }, [series, goal]);

  const W = w || 300;
  const H = height;
  const padL = 26, padR = 8, padT = 10, padB = 18;
  const pW = W - padL - padR, pH = H - padT - padB;
  const yOf = (v: number) => padT + pH - (v / maxV) * pH;

  const bw = series.length ? pW / series.length : pW;
  const bar = Math.min(24, bw * 0.6);
  const gridColor = t.mutedText;

  return (
    <View style={[styles.card, { backgroundColor: t.card, borderColor: t.border, borderRadius: v3Radius.card }]}>
      <View style={styles.header}>
        <Text style={[styles.label, { color: t.mutedText }]}>{label}</Text>
        {latest && (
          <Text style={[styles.value, { color: t.text }]}>{latest.value.toFixed(decimals)}{unit}</Text>
        )}
      </View>

      {series.length < 2 ? (
        <Text style={[styles.empty, { color: t.mutedText }]}>Not enough data yet.</Text>
      ) : (
        <View onLayout={e => setW(Math.round(e.nativeEvent.layout.width))} style={{ width: '100%' }}>
          <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`}>
            {/* gridlines + y labels */}
            {[0, 1, 2, 3].map(g => {
              const v = (maxV * g) / 3, y = yOf(v);
              return (
                <React.Fragment key={g}>
                  <Line x1={padL} y1={y} x2={W - padR} y2={y} stroke={gridColor} strokeOpacity={0.14} strokeWidth={1} />
                  <SvgText x={padL - 4} y={y + 3} fill={gridColor} fontSize={9} textAnchor="end">
                    {Math.round(v)}
                  </SvgText>
                </React.Fragment>
              );
            })}
            {/* bars + every-other-day labels */}
            {series.map((p, j) => {
              const x = padL + bw * j + (bw - bar) / 2;
              const h = Math.max(2, (p.value / maxV) * pH);
              const yy = padT + pH - h;
              const showDay = j % 2 === 0 || j === series.length - 1;
              return (
                <React.Fragment key={j}>
                  <Path d={barPath(x, yy, bar, h, 3)} fill={barColor} />
                  {/* value on top of each bar */}
                  <SvgText x={x + bar / 2} y={yy - 3} fill={t.text} fontSize={9} fontWeight="600" textAnchor="middle">
                    {p.value.toFixed(decimals)}
                  </SvgText>
                  {showDay && (
                    <SvgText x={x + bar / 2} y={H - padB + 12} fill={gridColor} fontSize={9} textAnchor="middle">
                      {String(p.date).slice(8)}
                    </SvgText>
                  )}
                </React.Fragment>
              );
            })}
            {/* dashed goal line */}
            {goal > 0 && (
              <>
                <Line
                  x1={padL} y1={yOf(goal)} x2={W - padR} y2={yOf(goal)}
                  stroke={gridColor} strokeOpacity={0.6} strokeWidth={1.5} strokeDasharray="4,4"
                />
                <SvgText x={W - padR} y={yOf(goal) - 3} fill={gridColor} fontSize={9} textAnchor="end">
                  {goal}{unit}
                </SvgText>
              </>
            )}
          </Svg>
        </View>
      )}
    </View>
  );
}

const styles = StyleSheet.create({
  card: { borderWidth: 1, padding: v3Spacing.medium, marginBottom: v3Spacing.medium },
  header: { flexDirection: 'row', alignItems: 'baseline', justifyContent: 'space-between', marginBottom: v3Spacing.small },
  label: { fontSize: v3Type.label, fontWeight: '600' },
  value: { fontSize: v3Type.title, fontWeight: '700' },
  empty: { fontSize: v3Type.body, paddingVertical: v3Spacing.medium },
});
