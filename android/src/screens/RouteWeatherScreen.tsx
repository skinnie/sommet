import React, { useEffect, useState } from 'react';
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
import { LEAFLET_STYLE_TAG, LEAFLET_INJECT_JS } from '../services/leafletInline';
import { TILE_CACHE_DIR_URI } from '../services/TileCache';
import { writeMapPage, mapWebViewFileProps } from '../services/mapWebView';
import { getCachedPois } from '../services/PoiService';

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
  const [pace, setPace] = useState('20');
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | undefined>();
  const [result, setResult] = useState<WeatherRoutePlan | undefined>();
  const [twilightOpen, setTwilightOpen] = useState(false);
  // A GPX the user imported this session takes priority over any nav param, which takes
  // priority over the built-in demo route. Kept as {route,name} so importing clears any stale
  // forecast for the previous route.
  const [imported, setImported] = useState<{ route: RoutePoint[]; name: string } | undefined>();
  const [importing, setImporting] = useState(false);
  // The coloured map is loaded from a caches-dir file:// page so it can read cached tiles off
  // disk (offline-capable + renders on iOS). Rewritten each forecast.
  const [mapUri, setMapUri] = useState<string | null>(null);
  // The watch's cached POIs, overlaid on the map as pins (offline).
  const [pois, setPois] = useState<Array<{ lat: number; lon: number; name: string }>>([]);
  useEffect(() => {
    getCachedPois().then(list => setPois((list ?? []).map(p => ({ lat: p.latitude, lon: p.longitude, name: p.name })))).catch(() => {});
  }, []);

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

  // Write the coloured-track map page whenever a new forecast lands.
  useEffect(() => {
    if (!result?.ok || !result.segments?.length) { setMapUri(null); return; }
    let alive = true;
    writeMapPage(buildRouteMapHtml(result.segments, result.wind_arrows, pois), 'sommet_weather_map.html')
      .then(uri => { if (alive) setMapUri(uri); })
      .catch(() => { if (alive) setMapUri(null); });
    return () => { alive = false; };
  }, [result, pois]);

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
            <Text style={s.muted}>temperature line · numbers = temp/feels (°C) · rain bars (peak mm) · wind (km/h)</Text>
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
                {mapUri && (
                <WebView
                  style={{ flex: 1, backgroundColor: theme.cardNested }}
                  originWhitelist={['*']}
                  source={{ uri: mapUri }}
                  injectedJavaScriptBeforeContentLoaded={LEAFLET_INJECT_JS}
                  javaScriptEnabled
                  domStorageEnabled={false}
                  {...mapWebViewFileProps()}
                  // OSM's tile policy wants an identifying UA on every request (same as MapScreen).
                  userAgent="Sommet/2.0"
                  androidLayerType="hardware"
                />)}
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
  pois: Array<{ lat: number; lon: number; name: string }> = [],
): string {
  const segJson = JSON.stringify(segments.map(s => ({ c: s.color, p: s.coords })));
  const windJson = JSON.stringify(
    (windArrows ?? []).map((w: any) => ({ lat: w.lat, lon: w.lon, dir: w.wind_dir_deg, col: w.color })),
  );
  const poiJson = JSON.stringify(pois);
  return `<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"/>
${LEAFLET_STYLE_TAG}
<style>*{margin:0;padding:0}html,body,#map{width:100%;height:100%}.wind{color:#333;font-size:15px;line-height:15px;text-align:center}</style>
</head><body><div id="map"></div><script>
var segs = ${segJson}, winds = ${windJson}, pois = ${poiJson};
var map = L.map('map', { zoomControl: true, attributionControl: true });
// cache-first OSM: use a downloaded tile off disk when present, else fetch it remotely — so the
// basemap under the forecast draws even offline if you saved the area (see Offline maps).
var tiles = L.tileLayer('${TILE_CACHE_DIR_URI}/osm/{z}/{x}/{y}.png',
  { maxZoom: 19, attribution: '© OpenStreetMap contributors' }).addTo(map);
tiles.on('tileerror', function(e){ if(!e.tile||e.tile.dataset.fb)return; e.tile.dataset.fb='1';
  e.tile.src='https://tile.openstreetmap.org/'+e.coords.z+'/'+e.coords.x+'/'+e.coords.y+'.png'; });
var all = [];
segs.forEach(function(s){ if(!s.p.length) return; L.polyline(s.p, { color: s.c, weight: 5, opacity: 0.95 }).addTo(map); s.p.forEach(function(pt){ all.push(pt); }); });
// stitch the gap between adjacent colour runs so the line reads as continuous
for (var i=0;i<segs.length-1;i++){ var a=segs[i].p[segs[i].p.length-1], b=segs[i+1].p[0]; if(a&&b) L.polyline([a,b], { color: segs[i+1].c, weight: 5, opacity: 0.95 }).addTo(map); }
winds.forEach(function(w){
  // rotate a ↓ glyph to point where the wind blows TO (from-direction + 180°); tinted head/cross/tail
  var html = '<div class="wind" style="transform:rotate(' + w.dir + 'deg);color:' + w.col + '">&#8595;</div>';
  L.marker([w.lat, w.lon], { icon: L.divIcon({ html: html, className: '', iconSize: [15,15] }) }).addTo(map);
});
pois.forEach(function(p){
  var pin = L.divIcon({ className: '', iconAnchor: [8,16],
    html: '<div style="width:16px;height:16px;background:#f39c12;border:2px solid #fff;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>' });
  L.marker([p.lat, p.lon], { icon: pin }).bindPopup(p.name || 'POI').addTo(map);
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
// The weather "croqui" - matches the desktop Plan page (2026-09-02): a single temperature line
// with compact temp/feels (21/22) labels at several points, rain bars labelled only at the
// wettest bar, a wind strip on top, and °C / mm / km+ETA axes. Elevation has its own section.
function WeatherProfile({ plan, theme }: { plan: WeatherRoutePlan; theme: V3Colors }) {
  const prof = plan.profile!;
  const W = 320, H = 182, mL = 26, mR = 30, mT = 22, mB = 22;
  const pW = W - mL - mR, pH = H - mT - mB;
  const dMax = prof[prof.length - 1].dist_m || 1;
  const X = (d: number) => mL + (d / dMax) * pW;

  // temperature axis (includes feels-like), with a minimum span so a near-constant temp reads flat
  const vals: number[] = [];
  prof.forEach((p: any) => { vals.push(p.temp_c, p.feels_c); });
  let tLo = Math.min(...vals), tHi = Math.max(...vals);
  const mid = (tLo + tHi) / 2, half = Math.max(4, (tHi - tLo) / 2 + 1);
  tLo = mid - half; tHi = mid + half;
  const Yt = (t: number) => mT + pH - ((t - tLo) / Math.max(1, tHi - tLo)) * pH;
  const rMax = Math.max(1, ...prof.map((p: any) => p.rain_mm || 0));
  const relCol: Record<string, string> = { headwind: '#d6453f', crosswind: '#e0912f', tailwind: '#2e9e6b' };

  // rain bars, tracking the wettest to label it alone
  let peak = { mm: 0, x: 0, h: 0 };
  const bars = prof.filter((p: any) => (p.rain_mm || 0) >= 0.05).map((p: any, i: number) => {
    const h = (p.rain_mm / rMax) * (pH * 0.5), bx = X(p.dist_m);
    if (p.rain_mm > peak.mm) peak = { mm: p.rain_mm, x: bx, h };
    return { bx, h, key: i };
  });
  const tempPath = prof.map((p: any, i: number) => `${i === 0 ? 'M' : 'L'}${X(p.dist_m).toFixed(1)},${Yt(p.temp_c).toFixed(1)}`).join(' ');

  const nLab = Math.min(6, prof.length);
  const labels = Array.from({ length: nLab }, (_, s) => {
    const p: any = prof[Math.round(s * (prof.length - 1) / Math.max(1, nLab - 1))];
    return { x: Math.max(mL + 12, Math.min(mL + pW - 12, X(p.dist_m))), y: Yt(p.temp_c) - 5, t: `${Math.round(p.temp_c)}/${Math.round(p.feels_c)}` };
  });
  const nW = Math.min(6, prof.length);
  const winds = Array.from({ length: nW }, (_, s) => {
    const p: any = prof[Math.round(s * (prof.length - 1) / Math.max(1, nW - 1))];
    return { x: X(p.dist_m), rel: p.wind_rel as string, kmh: Math.round(p.wind_kmh) };
  });
  const ticks = [0, 0.5, 1].map(f => {
    const d = dMax * f, p: any = prof[Math.round(f * (prof.length - 1))];
    return { x: X(d), km: (d / 1000).toFixed(dMax < 20000 ? 1 : 0), eta: p.eta as string };
  });

  return (
    <Svg width="100%" height={H} viewBox={`0 0 ${W} ${H}`} style={{ marginTop: 8 }}>
      {[0, 1, 2].map(g => <Line key={`g${g}`} x1={mL} y1={mT + pH * g / 2} x2={mL + pW} y2={mT + pH * g / 2} stroke={theme.border} strokeWidth={0.5} opacity={0.5} />)}
      {bars.map(b => <Rect key={`r${b.key}`} x={b.bx - 2} y={mT + pH - b.h} width={4} height={b.h} fill="#4e7cc4" opacity={0.5} />)}
      {peak.mm >= 0.05 && <SvgText x={peak.x} y={mT + pH - peak.h - 3} fill="#4e7cc4" fontSize={9} fontWeight="bold" textAnchor="middle">{peak.mm.toFixed(1)} mm</SvgText>}
      <Path d={tempPath} stroke="#e8833a" strokeWidth={2.4} fill="none" strokeLinecap="round" strokeLinejoin="round" />
      {labels.map((l, i) => <SvgText key={`t${i}`} x={l.x} y={l.y} fill={theme.text} fontSize={9} fontWeight="bold" textAnchor="middle">{l.t}</SvgText>)}
      {winds.map((w, i) => (
        <React.Fragment key={`w${i}`}>
          <Path d={w.rel === 'tailwind' ? `M${w.x - 3},${mT - 13} L${w.x + 3},${mT - 13} L${w.x},${mT - 7} Z` : `M${w.x - 3},${mT - 7} L${w.x + 3},${mT - 7} L${w.x},${mT - 13} Z`} fill={relCol[w.rel] || theme.mutedText} />
          <SvgText x={w.x} y={mT - 15} fill={theme.mutedText} fontSize={8} textAnchor="middle">{w.kmh}</SvgText>
        </React.Fragment>
      ))}
      <SvgText x={mL - 3} y={mT + 6} fill={theme.mutedText} fontSize={8} textAnchor="end">{Math.round(tHi)}°</SvgText>
      <SvgText x={mL - 3} y={mT + pH} fill={theme.mutedText} fontSize={8} textAnchor="end">{Math.round(tLo)}°</SvgText>
      {rMax >= 0.1 && <SvgText x={mL + pW + 3} y={mT + pH} fill={theme.mutedText} fontSize={8} textAnchor="start">{rMax.toFixed(1)}mm</SvgText>}
      {ticks.map((t, i) => (
        <React.Fragment key={`x${i}`}>
          <SvgText x={t.x} y={mT + pH + 10} fill={theme.text} fontSize={8} textAnchor="middle">{t.km}km</SvgText>
          <SvgText x={t.x} y={mT + pH + 19} fill={theme.mutedText} fontSize={8} textAnchor="middle">{t.eta}</SvgText>
        </React.Fragment>
      ))}
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
