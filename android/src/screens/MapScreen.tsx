import React, { useEffect, useMemo, useState, useRef } from 'react';
import { View, Text, StyleSheet, ActivityIndicator, Alert, TouchableOpacity, Linking, ScrollView, useWindowDimensions } from 'react-native';
import { shareFile, saveToDownloads } from '../native/AmbitUsbModule';
import { WebView, WebViewMessageEvent } from 'react-native-webview';
import { uploadFitToStrava, isAuthenticated as stravaIsAuthenticated } from '../services/ApiStrava';
import { getRunalyzeApiKey, uploadFitToRunalyze } from '../services/ApiRunalyze';
import { getIntervalsIcuCredentials, uploadFitToIntervalsIcu } from '../services/ApiIntervalsIcu';
import { pushGearToIntervals } from '../services/GearAutoAssign';
import { GearPicker } from '../components/GearPicker';
import { getFitFile } from '../services/FitExport';
import { RouteProp, useNavigation, useRoute } from '@react-navigation/native';
import { NativeStackNavigationProp } from '@react-navigation/native-stack';
import { RootStackParamList } from '../../App';
import { readGpxFile } from '../services/GpxService';
import { parseTrackPoints, computeElevationStats, extractGpxMetadata, GpxMetadata, TrackPoint } from '../services/GpxParser';
import { t, fmtDate } from '../i18n';
import { useV3Theme } from '../theme/v3';
import { getMapProvider, setMapProvider, MapProvider } from '../services/MapProviderService';
import { mapTileLayersJs } from '../services/MapHtml';
import { TRACK_COLOR } from '../services/MapTile';
import { TILE_CACHE_DIR_URI, downloadRegion, DownloadRegionProgress } from '../services/TileCache';
import { LEAFLET_STYLE_TAG, LEAFLET_INJECT_JS } from '../services/leafletInline';
import { writeMapPage, mapWebViewFileProps } from '../services/mapWebView';
import { getCachedPois } from '../services/PoiService';
import Icon from '../components/ui/Icon';
import { weatherLabel } from '../components/WeatherCard';
import { weatherEmoji } from '../services/WeatherService';
import { fetchActivityWeather, ActivityWeather } from '../services/ActivityWeather';
import ActivityPanel from '../components/activity/ActivityPanel';

type Route = RouteProp<RootStackParamList, 'Map'>;
type Nav   = NativeStackNavigationProp<RootStackParamList, 'Map'>;

// ─── Formatters ───────────────────────────────────────────────────────────────

function formatDurationMinSec(s: number) {
  if (isNaN(s) || s < 0) return '--:--';
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = Math.floor(s % 60);
  const mStr = m.toString().padStart(2, '0');
  const sStr = sec.toString().padStart(2, '0');
  return h > 0 ? `${h}:${mStr}:${sStr}` : `${mStr}:${sStr}`;
}


function formatDist(m: number) {
  return m >= 1000 ? `${(m / 1000).toFixed(2)} km` : `${Math.round(m)} m`;
}

// ─── Carte Leaflet ────────────────────────────────────────────────────────────

// Real, 2026-08-10 ("choose a better color for the route, one that remains visible but not
// aggressive, and the trace a bit more thicker") - was a hardcoded, unthemed bright red
// (#ff2200) with an unrelated hardcoded blue (#3498db) for the replay position marker.
// desktop's own MapView.qml draws its track in Theme.primary with a white halo underneath
// (same real fix TrackPreview.tsx's own header comment documents) - reused here for both
// the track and the replay marker instead of two more one-off hardcoded colors.
// Real, 2026-08-10 ("let's go for the offline maps solution") - leaflet.js/leaflet.css
// (+ its default marker images) are now vendored under android/app/src/main/assets/leaflet/
// instead of fetched from unpkg.com on every map load: a real offline map needs the map
// library itself available with no network, not just the tile images. WebView's baseUrl
// below (file:///android_asset/) is what makes both this file:// script/link tag AND
// leaflet.css's own relative `images/marker-*.png` references resolve correctly.
function buildLeafletHtml(provider: MapProvider, trackColor: string): string {
  return `<!DOCTYPE html>
<html><head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
  ${LEAFLET_STYLE_TAG}
  <!-- Leaflet INLINE (same fix as OfflineMapsScreen, 2026-09-24): injectedJavaScriptBeforeContentLoaded
       does not run before the page script on Android's Chromium WebView -> "L is not defined" ->
       blank activity map (seen again on the tablet 2026-09-28, main and branch alike). -->
  <script>${LEAFLET_INJECT_JS}</script>
  <style>* { margin:0; padding:0; } html,body,#map { width:100%; height:100%; }</style>
</head><body>
<div id="map"></div>
<script>
  var map = L.map('map', { zoomControl: false });

  ${mapTileLayersJs(provider, TILE_CACHE_DIR_URI)}

  // POI overlay — pins for the watch's cached waypoints (RN injects them after load via
  // window.showPois). Works offline: the POI list is already stored on-device.
  var poiLayer = L.layerGroup().addTo(map);
  window.showPois = function(list) {
    poiLayer.clearLayers();
    (list || []).forEach(function(p) {
      var icon = L.divIcon({ className: '',
        html: '<div style="width:16px;height:16px;background:#f39c12;border:2px solid #fff;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>',
        iconAnchor: [8, 16] });
      L.marker([p.lat, p.lon], { icon: icon }).bindPopup(p.name || 'POI').addTo(poiLayer);
    });
  };

  var line = null;
  var startMarker = null;
  var endMarker = null;

  var dot = function(color) {
    return L.divIcon({ className: '',
      html: '<div style="width:14px;height:14px;background:' + color +
            ';border:2px solid #fff;border-radius:50%;box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>',
      iconAnchor: [7, 7] });
  };
  
  var playerIcon = L.divIcon({ className: '',
      html: '<div style="width:18px;height:18px;background:${trackColor};border:3px solid #fff;border-radius:50%;box-shadow:0 0 8px rgba(0,0,0,.6);"></div>',
      iconAnchor: [9, 9] });

  window.Replay = {
    state: {
      playing: false,
      speed: 1,
      currentVal: 0,
      maxVal: 0,
      mode: 'time',
      lastTime: 0,
      lastTick: 0,
    },
    points: [],
    marker: null,

    init: function(payload) {
      window.Replay.points = payload.points;
      window.Replay.state.mode = payload.mode;
      window.Replay.state.maxVal = payload.maxVal;
      window.Replay.state.currentVal = 0;
      window.Replay.state.playing = false;

      var lls = window.Replay.points.map(function(p){ return [p.lat, p.lon]; });
      
      if (line) map.removeLayer(line);
      if (startMarker) map.removeLayer(startMarker);
      if (endMarker) map.removeLayer(endMarker);
      if (window.Replay.marker) map.removeLayer(window.Replay.marker);

      if (lls.length > 0) {
        // The map sits in a fixed box laid out after the page loads, and Leaflet may have cached
        // its size as 0x0 before that; invalidateSize() is a no-op until the map has a view, so
        // give it a provisional one first, then re-measure, then fit. Without this the fit ran on
        // a 0x0 box: max zoom, tiles in one corner, no track (tablet, 2026-09-28, ~1 run in 3).
        if (!map._loaded) { window.__progMove = true; map.setView(lls[0], 13, { animate: false }); }
        window.__progMove = true;
        map.invalidateSize(false);
        line = L.polyline(lls, { color: '${trackColor}', weight: 7, opacity: 0.95 }).addTo(map);
        // Zone colours may have arrived first: keep the plain track hidden under them.
        if ((ovl.segs && ovl.segs.getLayers().length) || ovl.pending) line.setStyle({ opacity: 0 });
        startMarker = L.marker(lls[0], { icon: dot('#2ecc71') }).addTo(map);
        endMarker = L.marker(lls[lls.length - 1], { icon: dot('#e74c3c') }).addTo(map);
        window.__progMove = true;
        map.fitBounds(line.getBounds(), { padding: [30, 30] });

        window.Replay.marker = L.marker(lls[0], { icon: playerIcon, zIndexOffset: 1000 }).addTo(map);
        if (ovl.pending) {
          var p = ovl.pending; ovl.pending = null;
          setTimeout(function() { window.ActivityOverlay.setColoured(p[0], p[1]); }, 0);
        }
      }
    },

    play: function(speed) {
      if (window.Replay.state.currentVal >= window.Replay.state.maxVal) {
        window.Replay.state.currentVal = 0;
      }
      window.Replay.state.speed = speed;
      window.Replay.state.playing = true;
      window.Replay.state.lastTime = Date.now();
      requestAnimationFrame(window.Replay.loop);
    },

    pause: function() {
      window.Replay.state.playing = false;
    },

    setSpeed: function(speed) {
      window.Replay.state.speed = speed;
    },

    seek: function(val) {
      window.Replay.state.currentVal = Math.max(0, Math.min(val, window.Replay.state.maxVal));
      window.Replay.renderCurrent();
    },

    loop: function() {
      if (!window.Replay.state.playing) return;
      
      var now = Date.now();
      var dt = (now - window.Replay.state.lastTime) / 1000;
      window.Replay.state.lastTime = now;
      
      window.Replay.state.currentVal += (dt * window.Replay.state.speed);
      
      if (window.Replay.state.currentVal >= window.Replay.state.maxVal) {
         window.Replay.state.currentVal = window.Replay.state.maxVal;
         window.Replay.renderCurrent();
         window.Replay.pause();
         window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'REPLAY_END' }));
         return;
      }
      
      window.Replay.renderCurrent();
      
      // Tick to RN (~10 FPS max)
      if (now - window.Replay.state.lastTick > 100) {
         window.Replay.state.lastTick = now;
         window.ReactNativeWebView.postMessage(JSON.stringify({ 
           type: 'REPLAY_TICK', 
           payload: { val: window.Replay.state.currentVal, dist: window.Replay.state.currentDist } 
         }));
      }
      
      requestAnimationFrame(window.Replay.loop);
    },

    renderCurrent: function() {
      var pts = window.Replay.points;
      if (pts.length < 2) return;
      
      var val = window.Replay.state.currentVal;
      var mode = window.Replay.state.mode;
      
      // Binary search
      var low = 0, high = pts.length - 1;
      while (low <= high) {
        var mid = (low + high) >> 1;
        var mVal = mode === 'time' ? pts[mid].t : pts[mid].d;
        if (mVal < val) low = mid + 1;
        else if (mVal > val) high = mid - 1;
        else { low = mid; break; }
      }
      
      var idx = Math.max(0, Math.min(low, pts.length - 1));
      
      var p1 = pts[idx > 0 ? idx - 1 : 0];
      var p2 = pts[idx];
      var v1 = mode === 'time' ? p1.t : p1.d;
      var v2 = mode === 'time' ? p2.t : p2.d;
      
      var ratio = (v2 > v1) ? (val - v1) / (v2 - v1) : 0;
      var lat = p1.lat + (p2.lat - p1.lat) * ratio;
      var lon = p1.lon + (p2.lon - p1.lon) * ratio;
      
      // Store currentDistance for RN
      window.Replay.state.currentDist = p1.d + (p2.d - p1.d) * ratio;
      
      var newPos = [lat, lon];
      window.Replay.marker.setLatLng(newPos);
      
      if (!map.getBounds().pad(-0.1).contains(newPos)) {
        window.__progMove = true;
        map.panTo(newPos, { animate: true, duration: 0.5 });
      }
    }
  };

  // Activity screen (André, 2026-09-27; desktop parity): zone-coloured route, the chart's cursor
  // dot, the stretch selected in the chart, fitting the map to it, and "the map moved" back to RN
  // so the chart can follow the part of the route on screen.
  // Nothing is drawn before the map has a view: Leaflet queues such layers and adds them in the
  // middle of the first fitBounds, before its renderer has bounds -> "reading 'min'" thrown out of
  // Replay.init (seen on the tablet 2026-09-28: no track, no markers). Colours that arrive early
  // wait in ovl.pending and are drawn right after Replay.init has fitted the track.
  var ovl = { segs: null, hl: null, cur: null, pending: null };
  window.ActivityOverlay = {
    setColoured: function(segs, casing) {
      if (!map._loaded) { ovl.pending = [segs, casing]; return; }
      if (!ovl.segs) ovl.segs = L.layerGroup().addTo(map);
      ovl.segs.clearLayers();
      if (line) line.setStyle({ opacity: segs && segs.length ? 0 : 0.95 });
      (segs || []).forEach(function(s) { L.polyline(s.coords, { color: casing, weight: 9, opacity: 0.9, interactive: false }).addTo(ovl.segs); });
      (segs || []).forEach(function(s) { L.polyline(s.coords, { color: s.color, weight: 5.5, opacity: 1, interactive: false }).addTo(ovl.segs); });
    },
    setCursor: function(p) {
      if (!map._loaded) return;
      if (ovl.cur) { map.removeLayer(ovl.cur); ovl.cur = null; }
      if (p) ovl.cur = L.circleMarker([p.lat, p.lon], { radius: 8, color: '#ffffff', weight: 3, fillColor: '#B5652F', fillOpacity: 1 }).addTo(map);
    },
    setHighlight: function(coords) {
      if (!map._loaded) return;
      if (ovl.hl) { map.removeLayer(ovl.hl); ovl.hl = null; }
      if (coords && coords.length > 1) { ovl.hl = L.polyline(coords, { color: '#1a1d22', opacity: 0.3, weight: 18, interactive: false }).addTo(map); ovl.hl.bringToBack(); }
    },
    fitCoords: function(coords) {
      if (!coords || coords.length < 2) return;
      window.__progMove = true;
      map.fitBounds(L.latLngBounds(coords), { padding: [24, 24] });
    },
    resetView: function() {
      if (!line) return;
      window.__progMove = true;
      map.fitBounds(line.getBounds(), { padding: [30, 30] });
    }
  };
  // The box can still change size after the replay is fitted (layout settling, rotation): follow
  // it and fit the route again, unless the user has already moved the map.
  var _userMoved = false;
  window.addEventListener('resize', function() {
    map.invalidateSize(false);
    if (line && !_userMoved) { window.__progMove = true; map.fitBounds(line.getBounds(), { padding: [30, 30] }); }
  });
  map.on('moveend', function() {
    if (window.__progMove) { window.__progMove = false; return; }
    _userMoved = true;
    var b = map.getBounds();
    window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'MAP_VIEW',
      payload: { minLat: b.getSouth(), maxLat: b.getNorth(), minLon: b.getWest(), maxLon: b.getEast() } }));
  });
</script>
</body></html>`;
}

// ─── Composant ────────────────────────────────────────────────────────────────

export default function MapScreen() {
  const theme = useV3Theme();
  const styles = createStyles(theme);
  const route      = useRoute<Route>();
  const navigation = useNavigation<Nav>();
  const { activity } = route.params;

  const [points, setPoints] = useState<TrackPoint[]>([]);
  const [meta, setMeta] = useState<GpxMetadata | null>(null);   // summary (used for no-GPS moves)
  const [loading, setLoading] = useState(true);
  const [exporting, setExporting] = useState(false);
  const [showExportMenu, setShowExportMenu] = useState(false);
  const [showGearPicker, setShowGearPicker] = useState(false);
  const [downloadProgress, setDownloadProgress] = useState<DownloadRegionProgress | null>(null);

  // ── Replay State
  const webViewRef = useRef<WebView>(null);
  // Activity panel <-> map (desktop parity: chart hover dot, selected stretch, zone colours, and
  // the chart following the part of the route the user pans/zooms to).
  const [mapView, setMapView] = useState<{ minLat: number; maxLat: number; minLon: number; maxLon: number } | null>(null);
  const mapJs = (js: string) => webViewRef.current?.injectJavaScript(`window.ActivityOverlay && (${js}); true;`);
  const onPanelHover = (p: { lat: number; lon: number } | null) => mapJs(`window.ActivityOverlay.setCursor(${JSON.stringify(p)})`);
  const onPanelStretch = (coords: [number, number][] | null) => {
    if (!coords) { mapJs('window.ActivityOverlay.setHighlight(null)'); return; }
    mapJs(`window.ActivityOverlay.setHighlight(${JSON.stringify(coords)}), window.ActivityOverlay.fitCoords(${JSON.stringify(coords)})`);
  };
  // Remembered so the colours survive the map page loading after the panel computed them.
  const colouredRef = useRef<string | null>(null);
  const onPanelColoured = (segs: { color: string; coords: [number, number][] }[], casing: string) => {
    colouredRef.current = `window.ActivityOverlay.setColoured(${JSON.stringify(segs)}, ${JSON.stringify(casing)})`;
    mapJs(colouredRef.current);
  };
  const win = useWindowDimensions();
  const sideBySide = win.width > win.height && win.width >= 900;   // tablet landscape: map left, panel right
  
  const [isPlaying, setIsPlaying] = useState(false);
  const [replaySpeed, setReplaySpeed] = useState(1);
  const [currentReplayVal, setCurrentReplayVal] = useState(0);
  const [isReady, setIsReady] = useState(false);

  // Real, 2026-08-09 ("no button to change provider, nor in the settings like the desktop
  // version") - starts on the persisted default (getMapProvider()'s own 'ign' fallback) and
  // rebuilds once the real stored value loads; picking a different layer inside the map
  // itself (see onMessage's MAP_PROVIDER_CHANGE) writes back to the same storage.
  const [mapProvider, setMapProviderState] = useState<MapProvider>('ign');
  useEffect(() => { getMapProvider().then(setMapProviderState); }, []);
  // TRACK_COLOR, not theme.primary - see MapTile.ts's own header comment ("it is grey, not
  // very visible" - Android's own primary is a deliberately muted slate grey, wrong for a
  // map overlay that needs to read against arbitrary tile colors).
  const leafletHtml = useMemo(() => buildLeafletHtml(mapProvider, TRACK_COLOR), [mapProvider]);

  // Write the map page to the caches dir and load it by file:// URL (mapWebView.ts) so the
  // WebView can read cached tiles off disk on both platforms. Rewritten when the provider changes.
  const [mapUri, setMapUri] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setIsReady(false);
    writeMapPage(leafletHtml, 'sommet_activity_map.html')
      .then(uri => { if (alive) setMapUri(uri); })
      .catch(() => { if (alive) setMapUri(null); });
    return () => { alive = false; };
  }, [leafletHtml]);

  useEffect(() => {
    // No GPX at all (an intervals.icu import without a track): nothing to read - the activity
    // panel shows it from its FIT. Reading '' only raised a "Cannot read GPX file" alert.
    if (!activity.gpx_path) { setLoading(false); return; }
    readGpxFile(activity.gpx_path)
      .then(xml => { setPoints(parseTrackPoints(xml)); setMeta(extractGpxMetadata(xml)); })
      .catch(e => Alert.alert(t.error, t.readError + e?.message))
      .finally(() => setLoading(false));
  }, [activity.gpx_path]);

  const stats = useMemo(() => computeElevationStats(points), [points]);

  // The weather it was done in (André, 2026-09-27) - same line as the desktop's ActivityWeather.qml.
  const [weather, setWeather] = useState<ActivityWeather | null>(null);
  useEffect(() => {
    let alive = true;
    setWeather(null);
    if (points.length === 0) return;
    const track = points.map(p => ({ lat: p.latitude, lon: p.longitude }));
    fetchActivityWeather(activity.id, Date.parse(activity.date), activity.duration_s, track)
      .then(w => { if (alive) setWeather(w); });
    return () => { alive = false; };
  }, [points, activity.id, activity.date, activity.duration_s]);

  // Determine mode (time vs distance) based on whether timestamps exist
  const replayMode = useMemo(() => {
    if (points.length < 2) return 'time';
    return points[points.length - 1].cumTime > 0 ? 'time' : 'distance';
  }, [points]);

  const maxReplayVal = replayMode === 'time' ? activity.duration_s : stats.totalDistance;

  // Initialize Replay when WebView is ready
  useEffect(() => {
    if (isReady && points.length > 0) {
      const lightweightPoints = points.map(p => ({
        lat: p.latitude,
        lon: p.longitude,
        t: p.cumTime,
        d: p.cumDist,
      }));
      const payload = {
        points: lightweightPoints,
        mode: replayMode,
        maxVal: maxReplayVal,
      };
      webViewRef.current?.injectJavaScript(`window.Replay.init(${JSON.stringify(payload)}); true;`);
    }
  }, [isReady, points, replayMode, maxReplayVal]);

  const togglePlay = () => {
    if (isPlaying) {
      setIsPlaying(false);
      webViewRef.current?.injectJavaScript(`window.Replay.pause(); true;`);
    } else {
      setIsPlaying(true);
      webViewRef.current?.injectJavaScript(`window.Replay.play(${replaySpeed}); true;`);
    }
  };

  const cycleSpeed = () => {
    const nextSpeed = replaySpeed === 1 ? 10 : replaySpeed === 10 ? 60 : replaySpeed === 60 ? 120 : 1;
    setReplaySpeed(nextSpeed);
    if (isPlaying) {
      webViewRef.current?.injectJavaScript(`window.Replay.setSpeed(${nextSpeed}); true;`);
    }
  };

  const seekRelative = (delta: number) => {
    let d = delta;
    if (replayMode === 'distance') {
      if (activity.duration_s > 0) {
        const avgSpeedMps = stats.totalDistance / activity.duration_s;
        d = delta * avgSpeedMps;
      } else {
        d = (stats.totalDistance * 0.02) * Math.sign(delta);
      }
    }
    const newVal = Math.max(0, Math.min(currentReplayVal + d, maxReplayVal));
    setCurrentReplayVal(newVal);
    webViewRef.current?.injectJavaScript(`window.Replay.seek(${newVal}); true;`);
  };

  const onWebViewLoad = () => {
    setIsReady(true);
    if (colouredRef.current) mapJs(colouredRef.current);
    // Overlay the watch's cached POIs (available offline). Best-effort — no POIs is fine.
    getCachedPois().then(pois => {
      if (!pois || pois.length === 0) return;
      const list = pois.map(p => ({ lat: p.latitude, lon: p.longitude, name: p.name }));
      webViewRef.current?.injectJavaScript(`window.showPois && window.showPois(${JSON.stringify(list)}); true;`);
    }).catch(() => {});
  };

  const onMessage = (event: WebViewMessageEvent) => {
    try {
      const data = JSON.parse(event.nativeEvent.data);
      if (data.type === 'REPLAY_TICK') {
        setCurrentReplayVal(data.payload.val);
      } else if (data.type === 'REPLAY_END') {
        setIsPlaying(false);
        setCurrentReplayVal(maxReplayVal);
      } else if (data.type === 'MAP_PROVIDER_CHANGE') {
        setMapProviderState(data.provider);
        setMapProvider(data.provider);
      } else if (data.type === 'MAP_VIEW') {
        setMapView(data.payload);
      }
    } catch (e) {}
  };

  // ─── Offline map download ───
  // Real, 2026-08-10 ("let's go for the offline maps solution") - z13-16: wide enough to
  // still see the surrounding area at the low end, close enough to read trail detail at the
  // high end, the same practical range general-purpose hiking map apps default an offline
  // download to. Re-running this after it already succeeded is cheap (TileCache.ts's own
  // ensureTileCached() is a no-op past the first RNFS.exists() check per tile).
  const OFFLINE_ZOOMS = [13, 14, 15, 16];

  async function handleDownloadOffline() {
    if (downloadProgress && downloadProgress.done < downloadProgress.total) return;
    setDownloadProgress({ done: 0, total: 0, failed: 0 });
    try {
      const result = await downloadRegion(
        mapProvider,
        points.map(p => ({ lat: p.latitude, lon: p.longitude })),
        OFFLINE_ZOOMS,
        setDownloadProgress,
      );
      if (result.failed > 0) {
        Alert.alert(t.offlineMapTitle, t.offlineMapPartial(result.total - result.failed, result.total));
      } else {
        Alert.alert(t.offlineMapTitle, t.offlineMapDone(result.total));
      }
    } catch (e: any) {
      Alert.alert(t.offlineMapTitle, e?.message ?? String(e));
    } finally {
      setTimeout(() => setDownloadProgress(null), 1500);
    }
  }

  // ─── Exports ───

  async function handleUploadRunalyze() {
    setShowExportMenu(false);
    const apiKey = await getRunalyzeApiKey();
    if (!apiKey) {
      Alert.alert(
        t.noApiKey,
        t.noApiKeyMsg,
        [
          { text: t.cancel, style: 'cancel' },
          { text: t.settings, onPress: () => navigation.navigate('Settings') },
        ]
      );
      return;
    }
    setExporting(true);
    try {
      const fitPath = await getFitFile(activity.gpx_path, activity);
      const result  = await uploadFitToRunalyze(fitPath, apiKey);
      Alert.alert('Runalyze ✓', t.runalyzeOk(result.activityId));
    } catch (e: any) {
      Alert.alert(t.runalyzeError, e?.message);
    } finally {
      setExporting(false);
    }
  }

  async function handleUploadIntervals() {
    setShowExportMenu(false);
    const creds = await getIntervalsIcuCredentials();
    if (!creds) {
      Alert.alert(
        t.noCreds,
        t.noCredsMsg,
        [
          { text: t.cancel, style: 'cancel' },
          { text: t.settings, onPress: () => navigation.navigate('Settings') },
        ]
      );
      return;
    }
    setExporting(true);
    try {
      const fitPath = await getFitFile(activity.gpx_path, activity);
      // activity_type holds the watch's real activity name (custom mode names included), so the
      // upload titles the activity with it rather than the .fit filename.
      const result  = await uploadFitToIntervalsIcu(fitPath, creds.athleteId, creds.apiKey, activity.activity_type);
      // Auto-assign the default bike/shoes for this sport type (best-effort, non-fatal).
      const assignedGear = await pushGearToIntervals(result.activityId, activity.activity_type);
      Alert.alert(
        'Intervals.icu ✓',
        assignedGear ? `${t.intervalsSuccess}\n${t.gearAssignedTo(assignedGear)}` : t.intervalsSuccess,
        [
          { text: t.close, style: 'cancel' },
          { text: t.viewOnIntervals, onPress: () => Linking.openURL(result.viewerUrl) },
        ]
      );
    } catch (e: any) {
      Alert.alert(t.intervalsError, e?.message ?? String(e));
    } finally {
      setExporting(false);
    }
  }

  async function handleUploadStrava() {
    setShowExportMenu(false);
    try {
      const auth = await stravaIsAuthenticated();
      if (!auth) {
        Alert.alert(
          'Strava',
          t.stravaNotConnected,
          [
            { text: t.cancel, style: 'cancel' },
            { text: t.settings, onPress: () => navigation.navigate('Settings') },
          ]
        );
        return;
      }
      setExporting(true);
      try {
        // Upload the FIT (correct sport + indoor moves), not the GPX. getFitFile prefers the
        // native FIT and falls back to converting the GPX for older outdoor moves.
        const fitPath = await getFitFile(activity.gpx_path, activity);
        const result = await uploadFitToStrava(fitPath, activity.activity_type);
        Alert.alert(
          'Strava',
          t.stravaSuccess,
          [
            { text: t.close, style: 'cancel' },
            { text: t.viewOnStrava, onPress: () => Linking.openURL(result.stravaUrl) },
          ]
        );
      } catch (e: any) {
        Alert.alert(t.stravaError, e?.message ?? String(e));
      } finally {
        setExporting(false);
      }
    } catch (e: any) {
      Alert.alert(t.stravaError, e?.message ?? String(e));
    }
  }

  async function handleShareGpx() {
    setShowExportMenu(false);
    try {
      await shareFile(activity.gpx_path);
    } catch (e: any) {
      Alert.alert(t.error, t.shareError + e?.message);
    }
  }

  async function handleSaveToDownloads() {
    setShowExportMenu(false);
    try {
      const fileName = activity.gpx_path.split('/').pop() ?? `${activity.id}.gpx`;
      await saveToDownloads(activity.gpx_path, fileName);
      Alert.alert(t.savedOk, t.savedMsg(fileName));
    } catch (e: any) {
      Alert.alert(t.error, t.saveError + e?.message);
    }
  }

  async function handleShareFit() {
    setShowExportMenu(false);
    setExporting(true);
    try {
      const fitPath = await getFitFile(activity.gpx_path, activity);
      await shareFile(fitPath, 'application/vnd.ant.fit');
    } catch (e: any) {
      Alert.alert(t.error, t.shareError + e?.message);
    } finally {
      setExporting(false);
    }
  }

  async function handleSaveFitToDownloads() {
    setShowExportMenu(false);
    setExporting(true);
    try {
      const fitPath  = await getFitFile(activity.gpx_path, activity);
      const fileName = fitPath.split('/').pop() ?? `${activity.id}.fit`;
      await saveToDownloads(fitPath, fileName, 'application/vnd.ant.fit');
      Alert.alert(t.savedOk, t.savedMsg(fileName));
    } catch (e: any) {
      Alert.alert(t.error, t.saveError + e?.message);
    } finally {
      setExporting(false);
    }
  }

  if (loading) {
    return (
      <View style={styles.centered}>
        <ActivityIndicator size="large" color={theme.text} />
        <Text style={styles.loadingText}>{t.loading}</Text>
      </View>
    );
  }

  // No GPS track (indoor / home-trainer move). Instead of a dead "No GPS" screen, show the
  // activity's summary and the SAME export controls - an indoor move is still a real move to
  // export (its FIT carries hr/cadence/power/etc. once it's been synced by a build that writes
  // the native FIT). Heart rate / cadence / energy show when the GPX carries them.
  if (points.length === 0) {
    return (
      <View style={styles.container}>
        <ScrollView contentContainerStyle={{ paddingBottom: 96 }}>
          <View style={[styles.noGpsContent, { paddingBottom: 0 }]}>
            {/* Sport and date are the screen's header (App.tsx); only what is new goes here. */}
            <Text style={styles.noGpsSub}>{t.noTrack}</Text>
          </View>
          {/* Overview / Charts / Laps - the same panel as the map view (desktop parity). */}
          <ActivityPanel activity={activity} meta={meta} hasMap={false} mapView={null}
            onHover={() => undefined} onStretch={() => undefined} onColoured={() => undefined} />
        </ScrollView>

        {/* Same export FAB + menu as the map view, so an indoor move is exportable too. */}
        <TouchableOpacity
          style={[styles.exportFab, exporting && styles.btnDisabled]}
          onPress={() => setShowExportMenu(v => !v)}
          disabled={exporting}
        >
          {exporting
            ? <Text style={styles.exportFabText}>…</Text>
            : <Icon name="upload" size={22} color={theme.primary} />}
        </TouchableOpacity>

        {showExportMenu && (
          <View style={styles.exportMenu}>
            <ExportMenuItem styles={styles} label={t.shareGpx}       onPress={handleShareGpx} />
            <ExportMenuItem styles={styles} label={t.saveDownloads}  onPress={handleSaveToDownloads} />
            <ExportMenuItem styles={styles} label={t.shareFit}       onPress={handleShareFit} />
            <ExportMenuItem styles={styles} label={t.saveFitDownloads} onPress={handleSaveFitToDownloads} />
            <ExportMenuItem styles={styles} label={t.uploadRunalyze} onPress={handleUploadRunalyze} />
            <ExportMenuItem styles={styles} label={t.uploadIntervals} onPress={handleUploadIntervals} />
            <ExportMenuItem styles={styles} label={t.uploadStrava}   onPress={handleUploadStrava} />
            <ExportMenuItem styles={styles} label={t.gearSetForActivity} onPress={() => { setShowExportMenu(false); setShowGearPicker(true); }} />
          </View>
        )}

        <GearPicker
          visible={showGearPicker}
          activityId={activity.id}
          distanceM={activity.distance_m}
          timeS={activity.duration_s}
          date={activity.date}
          onClose={() => setShowGearPicker(false)}
        />
      </View>
    );
  }

  return (
    <View style={[styles.container, sideBySide && { flexDirection: 'row' }]}>
      {/* ── Carte Leaflet ── (a fixed box now: the activity panel scrolls below, or beside it on a
          landscape tablet) */}
      <View style={sideBySide ? { flex: 1 } : { height: Math.round(win.height * 0.42) }}>
      {/* Loaded from a caches-dir file:// page (mapWebView.ts) with Leaflet bundled inline — no
          more android_asset dependency, so this renders on iOS too, and cached tiles read off
          disk for offline use (mapWebViewFileProps grants the read access per platform). */}
      {mapUri && (
      <WebView
        ref={webViewRef}
        style={styles.map}
        source={{ uri: mapUri }}
        originWhitelist={['*']}
        javaScriptEnabled
        domStorageEnabled={false}
        {...mapWebViewFileProps()}
        // tile.openstreetmap.org's usage policy requires an identifying User-Agent on every tile
        // request, or it's treated as bulk/anonymous traffic (same as desktop's main.cpp).
        userAgent="Sommet/2.0"
        onLoad={onWebViewLoad}
        onMessage={onMessage}
      />)}

      {/* ── Weather overlay ── (the numbers moved into the activity panel's Overview) */}
      {weather && (
        <View style={styles.overlay}>
          <Text style={styles.weatherLine} numberOfLines={2}>
            {weatherLine(weather)}
          </Text>
        </View>
      )}

      {/* ── Bouton téléchargement hors-ligne ── */}
      <TouchableOpacity
        style={[styles.offlineFab, downloadProgress && styles.btnDisabled]}
        onPress={handleDownloadOffline}
        disabled={!!downloadProgress}
      >
        {downloadProgress ? (
          <Text style={styles.exportFabText}>
            {downloadProgress.total > 0
              ? `${Math.round((downloadProgress.done / downloadProgress.total) * 100)}%`
              : '…'}
          </Text>
        ) : (
          <Icon name="download" size={22} color={theme.primary} />
        )}
      </TouchableOpacity>

      {/* ── Bouton export flottant ── */}
      <TouchableOpacity
        style={[styles.exportFab, exporting && styles.btnDisabled]}
        onPress={() => setShowExportMenu(v => !v)}
        disabled={exporting}
      >
        {exporting
          ? <Text style={styles.exportFabText}>…</Text>
          : <Icon name="upload" size={22} color={theme.primary} />}
      </TouchableOpacity>

      {/* ── Menu d'export ── */}
      {showExportMenu && (
        <View style={styles.exportMenu}>
          <ExportMenuItem styles={styles} label={t.shareGpx}       onPress={handleShareGpx} />
          <ExportMenuItem styles={styles} label={t.saveDownloads}  onPress={handleSaveToDownloads} />
          <ExportMenuItem styles={styles} label={t.shareFit}       onPress={handleShareFit} />
          <ExportMenuItem styles={styles} label={t.saveFitDownloads} onPress={handleSaveFitToDownloads} />
          <ExportMenuItem styles={styles} label={t.uploadRunalyze} onPress={handleUploadRunalyze} />
          <ExportMenuItem styles={styles} label={t.uploadIntervals} onPress={handleUploadIntervals} />
          <ExportMenuItem styles={styles} label={t.uploadStrava}   onPress={handleUploadStrava} />
          <ExportMenuItem styles={styles} label={t.gearSetForActivity} onPress={() => { setShowExportMenu(false); setShowGearPicker(true); }} />
        </View>
      )}
      </View>

      <GearPicker
        visible={showGearPicker}
        activityId={activity.id}
        distanceM={activity.distance_m}
        timeS={activity.duration_s}
        date={activity.date}
        onClose={() => setShowGearPicker(false)}
      />

      <ScrollView style={sideBySide ? { width: Math.min(560, Math.round(win.width * 0.45)) } : { flex: 1 }}>
      {/* ── Barre de Replay ── */}
      <View style={styles.replayBar}>
        <Text style={styles.replayModeText}>{replayMode === 'time' ? t.replayTime : t.replayDist}</Text>
        
        <View style={styles.replayControls}>
          <TouchableOpacity onPress={() => seekRelative(-15)} style={styles.replayBtn}>
            <Icon name="skipBack" size={18} color={theme.text} />
          </TouchableOpacity>
          
          <TouchableOpacity onPress={togglePlay} style={styles.replayBtnMain}>
            <Icon name={isPlaying ? 'pause' : 'play'} size={20} color={theme.card} />
          </TouchableOpacity>
          
          <TouchableOpacity onPress={() => seekRelative(15)} style={styles.replayBtn}>
            <Icon name="skipForward" size={18} color={theme.text} />
          </TouchableOpacity>
        </View>

        <View style={styles.replayRight}>
          <Text style={styles.replayTimeText}>
            {replayMode === 'time' 
              ? `${formatDurationMinSec(currentReplayVal)} / ${formatDurationMinSec(maxReplayVal)}`
              : `${formatDist(currentReplayVal)} / ${formatDist(maxReplayVal)}`}
          </Text>
          <TouchableOpacity onPress={cycleSpeed} style={styles.speedBtn}>
            <Text style={styles.speedText}>x{replaySpeed}</Text>
          </TouchableOpacity>
        </View>
      </View>

      {/* (The old altitude profile / replay scrubber is gone, André 2026-09-29: the activity
          panel's own chart shows altitude now; the replay keeps its play/rewind/forward buttons.) */}
      {/* ── Overview / Charts / Laps ── (desktop parity: ActivityDetail.qml) */}
      <ActivityPanel activity={activity} meta={meta} hasMap mapView={mapView}
        onHover={onPanelHover} onStretch={onPanelStretch} onColoured={onPanelColoured} />
      </ScrollView>
    </View>
  );
}

// ─── Helpers Components ───────────────────────────────────────────────────────

function weatherLine(w: ActivityWeather): string {
  const lo = Math.round(w.tempMin), hi = Math.round(w.tempMax);
  const temp = lo === hi ? `${hi} °C` : `${lo}–${hi} °C`;
  const rain = w.rainMm >= 0.2 ? ` · ${w.rainMm.toFixed(1)} mm rain` : '';
  const pct = (x?: number) => Math.round((x ?? 0) * 100);
  const share = w.headShare !== undefined
    ? ` · headwind ${pct(w.headShare)}% · crosswind ${pct(w.crossShare)}% · tailwind ${pct(w.tailShare)}%` : '';
  return `${weatherEmoji(w.code)} ${weatherLabel(w.code)} · ${temp}${rain} · 💨 ${Math.round(w.windKmh)} km/h ${w.windCompass} (gusts ${Math.round(w.gustKmh)})${share}`;
}


function ExportMenuItem({ styles, label, onPress }: { styles: ReturnType<typeof createStyles>; label: string; onPress: () => void }) {
  return (
    <TouchableOpacity style={styles.exportItem} onPress={onPress}>
      <Text style={styles.exportItemText}>{label}</Text>
    </TouchableOpacity>
  );
}


// ─── Styles ───────────────────────────────────────────────────────────────────

function createStyles(t: ReturnType<typeof useV3Theme>) {
  return StyleSheet.create({
    container: { flex: 1, backgroundColor: t.background },
    map: { flex: 1 },
    centered: { flex: 1, alignItems: 'center', justifyContent: 'center', backgroundColor: t.background },
    loadingText: { color: t.mutedText, marginTop: 12 },
    errorText: { color: t.error, fontSize: 15 },
    overlay: {
      position: 'absolute',
      top: 12,
      left: 12,
      right: 12,
      backgroundColor: t.card,
      borderColor: t.mutedText + '33',
      borderWidth: 1,
      borderRadius: 14,
      paddingVertical: 8,
      paddingHorizontal: 8,
      gap: 8,
    },
    statsRow: {
      flexDirection: 'row',
      justifyContent: 'space-around',
    },
    chip: { alignItems: 'center', flex: 1 },
    chipLabel: { fontSize: 10, color: t.mutedText, marginBottom: 2 },
    chipValue: { fontSize: 13, fontWeight: '700', color: t.text },
    weatherLine: { fontSize: 11, color: t.mutedText, textAlign: 'center' },
    // No-GPS (indoor) detail: a plain summary + the same export controls, instead of a map.
    noGpsContent: { flex: 1, padding: 16, gap: 16 },
    noGpsTitle: { fontSize: 20, fontWeight: '700', color: t.text },
    noGpsSub: { fontSize: 13, color: t.mutedText, marginTop: 2 },
    noGpsCard: {
      backgroundColor: t.card,
      borderColor: t.mutedText + '33',
      borderWidth: 1,
      borderRadius: 14,
      paddingVertical: 12,
      paddingHorizontal: 8,
      gap: 12,
    },
    exportFab: {
      position: 'absolute',
      bottom: 12,
      right: 16,
      width: 48,
      height: 48,
      borderRadius: 24,
      backgroundColor: t.primary + '1F',
      borderColor: t.primary,
      borderWidth: 1,
      alignItems: 'center',
      justifyContent: 'center',
    },
    // Real, 2026-08-10 (offline maps) - same pill as exportFab, placed just to its left
    // (76 = 16 + 48 + 12, exportFab's own right offset + width + a real gap) rather than
    // stacking vertically, so it can't collide with exportMenu popping up above exportFab.
    offlineFab: {
      position: 'absolute',
      bottom: 12,
      right: 76,
      width: 48,
      height: 48,
      borderRadius: 24,
      backgroundColor: t.primary + '1F',
      borderColor: t.primary,
      borderWidth: 1,
      alignItems: 'center',
      justifyContent: 'center',
    },
    btnDisabled: { opacity: 0.5 },
    exportFabText: { fontSize: 20, color: t.primary },
    exportMenu: {
      position: 'absolute',
      bottom: 68,
      right: 16,
      backgroundColor: t.card,
      borderColor: t.mutedText + '33',
      borderWidth: 1,
      borderRadius: 14,
      overflow: 'hidden',
      minWidth: 200,
    },
    exportItem: {
      paddingVertical: 12,
      paddingHorizontal: 16,
      borderBottomWidth: 1,
      borderBottomColor: t.mutedText + '33',
    },
    exportItemText: { color: t.text, fontSize: 14 },

    // Replay bar
    replayBar: {
      flexDirection: 'row',
      alignItems: 'center',
      justifyContent: 'space-between',
      backgroundColor: t.card,
      paddingHorizontal: 12,
      paddingVertical: 8,
      borderTopWidth: 1,
      borderTopColor: t.mutedText + '33',
    },
    replayModeText: {
      fontSize: 10,
      color: t.mutedText,
      width: 50,
    },
    replayControls: {
      flexDirection: 'row',
      alignItems: 'center',
      gap: 16,
    },
    replayBtn: {
      padding: 8,
    },
    // Real, 2026-08-09 ("change the buttons to match the colors... of our new theme") -
    // the main play/pause control is the one real primary action on this bar, so it stays a
    // solid Theme.primary circle (fg Theme.card) rather than blending into the bar with a
    // plain t.card fill. This is a media control with no desktop counterpart (track replay is
    // Android-only), so it keeps the solid-primary affordance even though labeled action
    // buttons (primitives.tsx Button) are bordered to match desktop as of the 2026-08-15
    // parity audit - a round icon play button reads as its own thing, not an action button.
    replayBtnMain: {
      padding: 8,
      backgroundColor: t.primary,
      borderRadius: 20,
    },
    replayIcon: { fontSize: 16, color: t.text },
    replayIconMain: { fontSize: 20, color: t.card },
    replayRight: {
      alignItems: 'flex-end',
      width: 80,
    },
    replayTimeText: {
      color: t.text,
      fontSize: 10,
      fontWeight: '600',
    },
    // Tinted-primary pill, same convention as RouteScreen/PoiScreen's own exportBtn -
    // a real secondary action (not the main play button, not neutral bar chrome either).
    speedBtn: {
      marginTop: 4,
      backgroundColor: t.primary + '1F',
      borderWidth: 1,
      borderColor: t.primary,
      paddingHorizontal: 6,
      paddingVertical: 2,
      borderRadius: 4,
    },
    speedText: {
      color: t.primary,
      fontSize: 10,
      fontWeight: 'bold',
    },
  });
}
