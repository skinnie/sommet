import React, { useMemo, useState } from 'react';
import {
  View, Text, ScrollView, TextInput, TouchableOpacity, StyleSheet, Alert,
} from 'react-native';
import Svg, { Path, Line, Rect, Text as SvgText } from 'react-native-svg';
import { WebView } from 'react-native-webview';
import { useRoute, RouteProp } from '@react-navigation/native';
import { useV3Theme, v3Spacing, v3Radius, v3Type, V3Colors } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { Button, Chip } from '../components/ui/primitives';
import { planWeatherRoute, WeatherRoutePlan, RoutePoint } from '../services/WeatherRoute';
import { pickGpxFile } from '../native/AmbitUsbModule';
import { readGpxFile } from '../services/GpxService';
import { parseRouteGpx } from '../services/RouteGpxParser';

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

  const [start, setStart] = useState('09:00');
  const [pace, setPace] = useState('4.5');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [result, setResult] = useState<WeatherRoutePlan | undefined>();
  const [twilightOpen, setTwilightOpen] = useState(false);
  // A GPX the user imported this session takes priority over any nav param, which takes
  // priority over the built-in demo route. Kept as {route,name} so importing clears any stale
  // forecast for the previous route.
  const [imported, setImported] = useState<{ route: RoutePoint[]; name: string } | undefined>();
  const [importing, setImporting] = useState(false);

  const route: RoutePoint[] = imported?.route
    ?? ((params.route && params.route.length >= 2) ? params.route : DEMO_ROUTE);
  const routeName = imported?.name ?? params.name ?? 'Demo route (Serra da Estrela)';

  async function handleImportGpx() {
    if (importing) return;
    setImporting(true);
    try {
      const path = await pickGpxFile(); // rejects GPX_PICK_CANCELLED if the user backs out
      const xml = await readGpxFile(path);
      const fallback = (path.split('/').pop() ?? 'Route').replace(/\.gpx$/i, '');
      const parsed = parseRouteGpx(xml, fallback);
      const pts: RoutePoint[] = parsed.points.map(p => ({ lat: p.latitude, lon: p.longitude, ele: p.elevation }));
      if (pts.length < 2) { Alert.alert('GPX', 'That file has no usable route points.'); return; }
      setImported({ route: pts, name: parsed.name || fallback });
      setResult(undefined); setError(undefined); // stale forecast is for the old route
    } catch (e: any) {
      if (e?.code === 'GPX_PICK_CANCELLED') return; // user cancelled — silent
      Alert.alert('GPX import failed', e?.message ?? 'Could not read that file.');
    } finally {
      setImporting(false);
    }
  }

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
        <Button label={importing ? 'Opening…' : 'Load GPX'} icon="route" variant="outline"
          onPress={handleImportGpx} loading={importing} style={{ marginTop: v3Spacing.small }} />
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

          {/* Map — the track coloured by temperature, wind arrows, start/finish markers */}
          {result.segments && result.segments.length > 0 && (
            <Card style={{ marginTop: v3Spacing.medium }}>
              <Text style={s.cardTitle}>Map</Text>
              <Text style={s.muted}>Track coloured by temperature · wind arrows · ● start / ● finish.</Text>
              <View style={s.mapWrap}>
                <WebView
                  style={{ flex: 1, backgroundColor: theme.cardNested }}
                  originWhitelist={['*']}
                  source={{ html: buildRouteMapHtml(result.segments!, result.wind_arrows) }}
                  javaScriptEnabled
                  domStorageEnabled={false}
                  // OSM's tile policy wants an identifying UA on every request (same as MapScreen).
                  userAgent="Sommet/2.0"
                  androidLayerType="hardware"
                />
              </View>
            </Card>
          )}

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

// A self-contained Leaflet page (WebView) that draws the route coloured by temperature, one
// polyline per colour run, plus wind arrows and start/finish markers. Leaflet + OSM tiles load
// over the network — this whole screen already needs network for the forecast, so no offline
// dependency is added here (unlike MapScreen's Android-only vendored/offline setup). Only our
// own numeric coords and palette hex colours are interpolated into the page.
function buildRouteMapHtml(
  segments: NonNullable<WeatherRoutePlan['segments']>,
  windArrows: WeatherRoutePlan['wind_arrows'],
): string {
  const segJson = JSON.stringify(segments.map(s => ({ c: s.color, p: s.coords })));
  const windJson = JSON.stringify(
    (windArrows ?? []).map((w: any) => ({ lat: w.lat, lon: w.lon, dir: w.wind_dir_deg, col: w.color })),
  );
  return `<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"/>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"/>
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"></script>
<style>*{margin:0;padding:0}html,body,#map{width:100%;height:100%}.wind{color:#333;font-size:15px;line-height:15px;text-align:center}</style>
</head><body><div id="map"></div><script>
var segs = ${segJson}, winds = ${windJson};
var map = L.map('map', { zoomControl: true, attributionControl: true });
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  { maxZoom: 19, attribution: '© OpenStreetMap contributors' }).addTo(map);
var all = [];
segs.forEach(function(s){ if(!s.p.length) return; L.polyline(s.p, { color: s.c, weight: 5, opacity: 0.95 }).addTo(map); s.p.forEach(function(pt){ all.push(pt); }); });
// stitch the gap between adjacent colour runs so the line reads as continuous
for (var i=0;i<segs.length-1;i++){ var a=segs[i].p[segs[i].p.length-1], b=segs[i+1].p[0]; if(a&&b) L.polyline([a,b], { color: segs[i+1].c, weight: 5, opacity: 0.95 }).addTo(map); }
winds.forEach(function(w){
  // rotate a ↓ glyph to point where the wind blows TO (from-direction + 180°); tinted head/cross/tail
  var html = '<div class="wind" style="transform:rotate(' + w.dir + 'deg);color:' + w.col + '">&#8595;</div>';
  L.marker([w.lat, w.lon], { icon: L.divIcon({ html: html, className: '', iconSize: [15,15] }) }).addTo(map);
});
if (all.length) {
  map.fitBounds(L.latLngBounds(all), { padding: [22,22] });
  var st = all[0], en = all[all.length-1];
  L.circleMarker(st, { radius: 6, color: '#fff', weight: 2, fillColor: '#2e9e6b', fillOpacity: 1 }).addTo(map);
  L.circleMarker(en, { radius: 6, color: '#fff', weight: 2, fillColor: '#d73027', fillOpacity: 1 }).addTo(map);
} else { map.setView([40.32,-7.6], 12); }
</script></body></html>`;
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
  mapWrap: { height: 260, marginTop: 8, borderRadius: v3Radius.small, overflow: 'hidden', borderWidth: 1, borderColor: t.border },
  legendRow: { flexDirection: 'row', flexWrap: 'wrap', gap: 10, marginTop: 8 },
  legendItem: { flexDirection: 'row', alignItems: 'center', gap: 4 },
  swatch: { width: 12, height: 12, borderRadius: 3 },
  legendLabel: { fontSize: 10, color: t.mutedText },
  twiRow: { flexDirection: 'row', justifyContent: 'space-between', paddingVertical: 3 },
  twiVal: { fontSize: v3Type.caption, color: t.text },
});
