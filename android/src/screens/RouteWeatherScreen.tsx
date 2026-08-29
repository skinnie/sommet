import React, { useMemo, useState } from 'react';
import {
  View, Text, ScrollView, TextInput, TouchableOpacity, StyleSheet, ActivityIndicator,
} from 'react-native';
import Svg, { Path, Line, Rect, Circle, Text as SvgText } from 'react-native-svg';
import { useRoute, RouteProp } from '@react-navigation/native';
import { useV3Theme, v3Spacing, v3Radius, v3Type, V3Colors } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { Button, Chip } from '../components/ui/primitives';
import { planWeatherRoute, WeatherRoutePlan, RoutePoint } from '../services/WeatherRoute';

// Weather + sun/moon along a route — the "what am I walking into?" layer, ported from the
// desktop Plan page's Weather panel (weather_route.py + astro.py, both verified equal in TS).
// Given a route + start time + pace, it stamps each point with an ETA, fetches the Open-Meteo
// forecast there, and shows a plain-language verdict, a sun/moon summary, and a temperature-
// coloured elevation/rain/wind profile. Offline maths; only the forecast fetch needs network.
// Map colouring of the track is a follow-up (see the design note in memory).

// A real short demo route (Serra da Estrela) so the feature is usable before iOS GPX import
// (pickGpxFile) is implemented — replaced by nav param `route` when one is passed in.
const DEMO_ROUTE: RoutePoint[] = Array.from({ length: 41 }, (_, i) => {
  const east = i * 150;
  const ele = 1100 + (east < 3000 ? east * 0.14 : 420 - (east - 3000) * 0.12);
  return { lat: 40.322, lon: -7.616 + east / (111320 * Math.cos(40.322 * Math.PI / 180)), ele: Math.round(ele) };
});

type Params = { RouteWeather?: { route?: RoutePoint[]; name?: string } };

export default function RouteWeatherScreen() {
  const theme = useV3Theme();
  const s = styles(theme);
  const params = (useRoute<RouteProp<Params, 'RouteWeather'>>().params) || {};
  const route: RoutePoint[] = (params.route && params.route.length >= 2) ? params.route : DEMO_ROUTE;
  const routeName = params.name || 'Demo route (Serra da Estrela)';

  const [start, setStart] = useState('09:00');
  const [pace, setPace] = useState('4.5');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [result, setResult] = useState<WeatherRoutePlan | undefined>();
  const [twilightOpen, setTwilightOpen] = useState(false);

  const tzOffsetH = -new Date().getTimezoneOffset() / 60;

  async function handleForecast() {
    setLoading(true); setError(undefined); setResult(undefined);
    try {
      const now = new Date();
      const paceNum = parseFloat(pace) || 4.5;
      const plan = await planWeatherRoute(route, {
        start, pace_kmh: paceNum, tzOffsetH,
        date: { y: now.getFullYear(), mo: now.getMonth() + 1, d: now.getDate() },
      });
      if (!plan.ok) setError(plan.error || 'Forecast failed');
      else setResult(plan);
    } catch (e: any) {
      setError(e?.message || 'Forecast failed (no network?)');
    } finally {
      setLoading(false);
    }
  }

  const verdictColor = (state?: string) =>
    state === 'critical' ? theme.warning : state === 'warn' ? theme.warning : theme.primary;

  return (
    <ScrollView style={{ flex: 1, backgroundColor: theme.background }} contentContainerStyle={{ padding: v3Spacing.medium }}>
      <Text style={s.h1}>Weather along route</Text>

      <Card style={{ marginTop: v3Spacing.small }}>
        <Text style={s.cardTitle}>{routeName}</Text>
        <Text style={s.muted}>{route.length} points · plan the forecast at each point's ETA.</Text>
        <View style={s.inputRow}>
          <View style={{ flex: 1 }}>
            <Text style={s.label}>Start</Text>
            <TextInput value={start} onChangeText={setStart} placeholder="HH:MM"
              placeholderTextColor={theme.mutedText} style={s.input} keyboardType="numbers-and-punctuation" />
          </View>
          <View style={{ width: v3Spacing.medium }} />
          <View style={{ flex: 1 }}>
            <Text style={s.label}>Pace (km/h)</Text>
            <TextInput value={pace} onChangeText={setPace} placeholder="4.5"
              placeholderTextColor={theme.mutedText} style={s.input} keyboardType="decimal-pad" />
          </View>
        </View>
        <Button label={loading ? 'Fetching forecast…' : 'Forecast'} icon="sun" variant="filled"
          onPress={handleForecast} loading={loading} style={{ marginTop: v3Spacing.medium }} />
        {error && <Text style={[s.muted, { color: theme.warning, marginTop: 8 }]}>{error}</Text>}
      </Card>

      {result && result.ok && (
        <>
          {/* Verdict strip */}
          <View style={[s.verdict, { borderColor: verdictColor(result.verdict!.state) }]}>
            <Text style={[s.verdictHead, { color: verdictColor(result.verdict!.state) }]}>
              {result.verdict!.headline}
            </Text>
            <Text style={s.muted}>{result.verdict!.detail}</Text>
          </View>

          {/* Summary chips */}
          <View style={s.chipRow}>
            <Chip label={`${(result.summary.distance_m / 1000).toFixed(1)} km`} />
            <Chip label={`finish ${result.summary.finish}`} />
            <Chip label={`${result.summary.temp_min_c}–${result.summary.temp_max_c}°`} />
            <Chip label={`rain ≤ ${result.summary.rain_max_mm} mm`} />
            <Chip label={`wind ≤ ${result.summary.wind_max_kmh} km/h`} />
          </View>

          {/* Profile */}
          <Card style={{ marginTop: v3Spacing.medium }}>
            <Text style={s.cardTitle}>Profile</Text>
            <Text style={s.muted}>Elevation coloured by temperature · rain bars · sunset marker.</Text>
            <WeatherProfile plan={result} theme={theme} />
            <View style={s.legendRow}>
              {result.legend!.map(l => (
                <View key={l.key} style={s.legendItem}>
                  <View style={[s.swatch, { backgroundColor: l.color }]} />
                  <Text style={s.legendLabel}>{l.label}</Text>
                </View>
              ))}
            </View>
          </Card>

          {/* Sun / moon */}
          <Card style={{ marginTop: v3Spacing.medium }}>
            <Text style={s.cardTitle}>Sun & moon</Text>
            <View style={s.chipRow}>
              <Chip label={`☀︎ ${result.astro!.sun.sunrise || '--'} → ${result.astro!.sun.sunset || '--'}`} />
              <Chip label={`☾ ${result.astro!.moon_phase} ${Math.round(result.astro!.moon_illumination * 100)}%`} />
              <Chip label={`moonrise ${result.astro!.moon.moonrise || '--'}`} />
              <Chip label={`moonset ${result.astro!.moon.moonset || '--'}`} />
            </View>
            <TouchableOpacity onPress={() => setTwilightOpen(o => !o)} style={{ marginTop: 8 }}>
              <Text style={[s.label, { color: theme.primary }]}>{twilightOpen ? 'Hide twilight' : 'Show twilight'}</Text>
            </TouchableOpacity>
            {twilightOpen && (
              <View style={{ marginTop: 6 }}>
                {[
                  ['Astronomical dawn', result.astro!.sun.astronomical_dawn],
                  ['Nautical dawn', result.astro!.sun.nautical_dawn],
                  ['Civil dawn', result.astro!.sun.civil_dawn],
                  ['Sunrise', result.astro!.sun.sunrise],
                  ['Solar noon', result.astro!.sun.solar_noon],
                  ['Sunset', result.astro!.sun.sunset],
                  ['Civil dusk', result.astro!.sun.civil_dusk],
                  ['Nautical dusk', result.astro!.sun.nautical_dusk],
                  ['Astronomical dusk', result.astro!.sun.astronomical_dusk],
                ].map(([k, v]) => (
                  <View key={k as string} style={s.twiRow}>
                    <Text style={s.muted}>{k}</Text>
                    <Text style={s.twiVal}>{(v as string) || '--'}</Text>
                  </View>
                ))}
              </View>
            )}
          </Card>
        </>
      )}
    </ScrollView>
  );
}

// SVG profile: temperature-coloured elevation line, rain bars along the bottom, a sunset marker.
function WeatherProfile({ plan, theme }: { plan: WeatherRoutePlan; theme: V3Colors }) {
  const W = 320, H = 150, padL = 4, padR = 4, padT = 8, padB = 18;
  const prof = plan.profile!;
  const eles = prof.map(p => p.ele_m).filter((e: any) => e != null) as number[];
  const hasEle = eles.length > 1;
  const dMax = prof[prof.length - 1].dist_m || 1;
  const eMin = hasEle ? Math.min(...eles) : 0, eMax = hasEle ? Math.max(...eles) : 1;
  const rains = prof.map(p => p.rain_mm);
  const rMax = Math.max(0.1, ...rains);

  const x = (d: number) => padL + (d / dMax) * (W - padL - padR);
  const y = (e: number) => hasEle ? padT + (1 - (e - eMin) / Math.max(1, eMax - eMin)) * (H - padT - padB) : (H - padB) / 2;

  // temperature-coloured elevation segments (one path per consecutive same-colour run)
  const segs: Array<{ color: string; pts: Array<[number, number]> }> = [];
  let cur: { color: string; pts: Array<[number, number]> } | null = null;
  for (let i = 0; i < prof.length; i++) {
    const p = prof[i]; const px = x(p.dist_m); const py = y(p.ele_m ?? eMin);
    if (!cur || cur.color !== p.color) { if (cur) segs.push(cur); cur = { color: p.color, pts: [] }; }
    cur.pts.push([px, py]);
  }
  if (cur) segs.push(cur);
  // stitch: append the first point of the next segment so the line is continuous
  for (let i = 0; i < segs.length - 1; i++) segs[i].pts.push(segs[i + 1].pts[0]);

  // sunset marker: distance reached at sunset (from the verdict's dark_km, or none)
  const darkKm = plan.verdict!.dark_km;
  const sunsetX = darkKm != null ? x(darkKm * 1000) : null;

  return (
    <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} style={{ marginTop: 8 }}>
      {/* rain bars */}
      {prof.map((p, i) => {
        if (p.rain_mm <= 0) return null;
        const bx = x(p.dist_m); const bh = (p.rain_mm / rMax) * (H - padT - padB) * 0.5;
        return <Rect key={`r${i}`} x={bx - 2} y={H - padB - bh} width={4} height={bh} fill="#5b8def" opacity={0.55} />;
      })}
      {/* elevation, temp-coloured */}
      {hasEle && segs.map((seg, i) => (
        <Path key={`s${i}`} d={seg.pts.map((pt, j) => `${j === 0 ? 'M' : 'L'}${pt[0].toFixed(1)},${pt[1].toFixed(1)}`).join(' ')}
          stroke={seg.color} strokeWidth={2.5} fill="none" strokeLinecap="round" strokeLinejoin="round" />
      ))}
      {/* sunset marker */}
      {sunsetX != null && (
        <>
          <Line x1={sunsetX} y1={padT} x2={sunsetX} y2={H - padB} stroke={theme.warning} strokeWidth={1} strokeDasharray="3 3" />
          <SvgText x={Math.min(sunsetX + 3, W - 40)} y={padT + 9} fill={theme.warning} fontSize={9}>sunset</SvgText>
        </>
      )}
      {/* baseline */}
      <Line x1={padL} y1={H - padB} x2={W - padR} y2={H - padB} stroke={theme.border} strokeWidth={1} />
    </Svg>
  );
}

const styles = (t: V3Colors) => StyleSheet.create({
  h1: { fontSize: v3Type.largeTitle, color: t.text, fontWeight: '700' as const },
  cardTitle: { fontSize: v3Type.bodyLarge, color: t.text, fontWeight: '700' as const },
  muted: { fontSize: v3Type.caption, color: t.mutedText, marginTop: 2 },
  label: { fontSize: v3Type.label, color: t.mutedText, marginBottom: 4 },
  input: { backgroundColor: t.cardNested, color: t.text, borderRadius: v3Radius.small, borderWidth: 1, borderColor: t.border, paddingHorizontal: 12, paddingVertical: 10, fontSize: v3Type.body },
  inputRow: { flexDirection: 'row', marginTop: v3Spacing.medium },
  verdict: { marginTop: v3Spacing.medium, padding: v3Spacing.medium, borderRadius: v3Radius.card, borderWidth: 1.5, backgroundColor: t.card },
  verdictHead: { fontSize: v3Type.bodyLarge, fontWeight: '700' as const },
  chipRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: v3Spacing.medium },
  legendRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 8 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  swatch: { width: 12, height: 12, borderRadius: 3 },
  legendLabel: { fontSize: 10, color: t.mutedText },
  twiRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 3 },
  twiVal: { fontSize: v3Type.caption, color: t.text },
});
