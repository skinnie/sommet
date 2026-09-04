import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  View, Text, StyleSheet, TouchableOpacity, TextInput, Alert, ScrollView, Platform, ActivityIndicator,
} from 'react-native';
import RNFS from 'react-native-fs';
import { WebView, WebViewMessageEvent } from 'react-native-webview';
import { useFocusEffect } from '@react-navigation/native';
import { useV3Theme, v3Spacing, v3Radius, v3Type, V3Colors } from '../theme/v3';
import { Card } from '../components/ui/Card';
import { Button } from '../components/ui/primitives';
import Icon from '../components/ui/Icon';
import { LEAFLET_STYLE_TAG, LEAFLET_INJECT_JS } from '../services/leafletInline';
import { MapProvider, MAP_PROVIDER_LABELS } from '../services/MapProviderService';
import { downloadRegion, countRegionTiles, DownloadRegionProgress, TILE_CACHE_DIR_URI } from '../services/TileCache';
import { getCachedPois } from '../services/PoiService';
import {
  OfflineRegion, listRegions, addRegion, deleteRegion, bboxCorners, RegionBBox,
} from '../services/OfflineRegionsService';

// Offline Maps manager — the OruxMaps-style "download any area of the world for use with no
// signal" screen. Pan/zoom the map anywhere (you can be in France and grab Utah), the blue box
// marks exactly what will be saved, pick a detail level, see the size, download. Saved areas are
// listed below with their size and a delete button. Tiles live in TileCache's on-disk cache;
// OfflineRegionsService records what was downloaded. Leaflet is bundled inline (offline); the
// browsing tiles themselves stream online — you need signal to *choose* an area, not to use it
// later. Works identically on iOS, iPadOS and Android.

const PROVIDERS: MapProvider[] = ['osm', 'cyclosm', 'ign'];

// Zoom-level presets keep the tile count sane and prevent a giant continent-wide download.
// z12≈city, z15≈street, z17≈building. Higher detail = exponentially more tiles.
const DETAIL: Record<string, { label: string; zooms: number[]; hint: string }> = {
  overview: { label: 'Overview', zooms: [10, 11, 12, 13], hint: 'region / low detail' },
  standard: { label: 'Standard', zooms: [12, 13, 14, 15], hint: 'city + streets' },
  detailed: { label: 'Detailed', zooms: [13, 14, 15, 16, 17], hint: 'every street & building' },
};
const AVG_TILE_BYTES = 15000;       // OSM PNGs average ~10–20 KB; used for the size estimate
const MAX_TILES = 20000;            // refuse absurd bulk downloads (OSM tile-policy friendliness)

// The map page is loaded from a file:// URL (not inline HTML) so the WebView is allowed to read
// cached tiles off disk — the ONLY setup that works on WKWebView too. Tiles are cache-first:
// each tile's src points at the on-disk cache (${cacheDirUri}/<provider>/{z}/{x}/{y}.png); a
// cache miss fires 'tileerror' and the handler swaps that one <img> to the remote provider URL.
// So online browsing looks normal while a downloaded area still renders with no signal.
function buildOfflineMapHtml(provider: MapProvider, cacheDirUri: string): string {
  return `<!DOCTYPE html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no"/>
${LEAFLET_STYLE_TAG}
<style>*{margin:0;padding:0}html,body,#map{width:100%;height:100%}
.selbox{position:absolute;top:8%;left:8%;right:8%;bottom:8%;border:2px solid #0a79d0;border-radius:8px;box-shadow:0 0 0 9999px rgba(0,0,0,.14);pointer-events:none;z-index:600}</style>
</head><body><div id="map"></div><div class="selbox"></div><script>
var CACHE = '${cacheDirUri}';
var TPL = {
  osm: 'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
  cyclosm: 'https://a.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png',
  ign: 'https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2&STYLE=normal&FORMAT=image/png&TILEMATRIXSET=PM&TILEMATRIX={z}&TILEROW={y}&TILECOL={x}'
};
function remoteUrl(key, c) { return TPL[key].split('{z}').join(c.z).split('{x}').join(c.x).split('{y}').join(c.y); }
function makeLayer(key) {
  var lyr = L.tileLayer(CACHE + '/' + key + '/{z}/{x}/{y}.png', { maxZoom: 19, attribution: '© OpenStreetMap contributors' });
  lyr.on('tileerror', function (e) {
    if (!e.tile || e.tile.dataset.fb) return; // already tried remote — don't loop
    e.tile.dataset.fb = '1';
    e.tile.src = remoteUrl(key, e.coords);
  });
  return lyr;
}
var PAD = 0.08; // matches .selbox inset — the download box is the inset 84% of the viewport
var map = L.map('map', { zoomControl: true, attributionControl: true }).setView([40, -3], 4);
var layer = makeLayer('${provider}').addTo(map);
// POI pins (the watch's cached waypoints; RN injects them via window.showPois). Offline-safe.
var poiLayer = L.layerGroup().addTo(map);
window.showPois = function(list) {
  poiLayer.clearLayers();
  (list || []).forEach(function(p) {
    var pin = L.divIcon({ className: '', iconAnchor: [8,16],
      html: '<div style="width:16px;height:16px;background:#f39c12;border:2px solid #fff;border-radius:50% 50% 50% 0;transform:rotate(-45deg);box-shadow:0 1px 3px rgba(0,0,0,.4)"></div>' });
    L.marker([p.lat, p.lon], { icon: pin }).bindPopup(p.name || 'POI').addTo(poiLayer);
  });
};
function report() {
  var s = map.getSize();
  var tl = map.containerPointToLatLng(L.point(s.x * PAD, s.y * PAD));
  var br = map.containerPointToLatLng(L.point(s.x * (1 - PAD), s.y * (1 - PAD)));
  if (window.ReactNativeWebView) window.ReactNativeWebView.postMessage(JSON.stringify({
    type: 'BOUNDS',
    minLat: Math.min(tl.lat, br.lat), maxLat: Math.max(tl.lat, br.lat),
    minLon: Math.min(tl.lng, br.lng), maxLon: Math.max(tl.lng, br.lng), zoom: map.getZoom() }));
}
map.on('moveend', report); map.on('zoomend', report); map.whenReady(report);
window.setProvider = function (key) { map.removeLayer(layer); layer = makeLayer(key).addTo(map); };
window.flyToBox = function (a, b, c, d) { map.fitBounds([[a, b], [c, d]], { padding: [30, 30] }); };
</script></body></html>`;
}

export default function OfflineMapsScreen() {
  const theme = useV3Theme();
  const s = styles(theme);
  const webRef = useRef<WebView>(null);

  const [provider, setProvider] = useState<MapProvider>('osm');
  const [detail, setDetail] = useState<keyof typeof DETAIL>('standard');
  const [bbox, setBbox] = useState<RegionBBox | null>(null);
  const [name, setName] = useState('');
  const [progress, setProgress] = useState<DownloadRegionProgress | null>(null);
  const [regions, setRegions] = useState<OfflineRegion[]>([]);

  // Write the map page to a file under the caches dir and load it by file:// URL — the only
  // setup that lets the WebView (WKWebView included) read cached tiles off disk. Provider is
  // swapped live via injectJS, so this is written once. cachesRoot grants the WebView read
  // access to both this html and the sibling maptiles/ cache.
  const cachesRoot = `file://${RNFS.CachesDirectoryPath}`;
  const [mapUri, setMapUri] = useState<string | null>(null);
  useEffect(() => {
    const path = `${RNFS.CachesDirectoryPath}/sommet_offline_map.html`;
    RNFS.writeFile(path, buildOfflineMapHtml('osm', TILE_CACHE_DIR_URI), 'utf8')
      .then(() => setMapUri(`file://${path}`))
      .catch(() => setMapUri(null));
  }, []);

  const refresh = useCallback(() => { listRegions().then(setRegions); }, []);
  useFocusEffect(useCallback(() => { refresh(); }, [refresh]));

  const zooms = DETAIL[detail].zooms;
  const estTiles = useMemo(
    () => (bbox ? countRegionTiles(bboxCorners(bbox), zooms, 0) : 0),
    [bbox, zooms],
  );
  const estMB = (estTiles * AVG_TILE_BYTES) / 1e6;
  const tooBig = estTiles > MAX_TILES;

  function onMessage(e: WebViewMessageEvent) {
    try {
      const m = JSON.parse(e.nativeEvent.data);
      if (m.type === 'BOUNDS') {
        setBbox({ minLat: m.minLat, minLon: m.minLon, maxLat: m.maxLat, maxLon: m.maxLon });
      }
    } catch {}
  }

  function pickProvider(p: MapProvider) {
    setProvider(p);
    webRef.current?.injectJavaScript(`window.setProvider(${JSON.stringify(p)}); true;`);
  }

  async function handleDownload() {
    if (!bbox || tooBig || progress) return;
    setProgress({ done: 0, total: estTiles, failed: 0 });
    try {
      const result = await downloadRegion(provider, bboxCorners(bbox), zooms, setProgress, 0);
      await addRegion({
        name: name.trim() || defaultName(bbox),
        provider, bbox, zooms,
        tileCount: result.done - result.failed,
        bytes: Math.round((result.done - result.failed) * AVG_TILE_BYTES),
      });
      setName('');
      refresh();
      const msg = result.failed
        ? `Saved ${result.done - result.failed} of ${result.total} tiles (${result.failed} failed — try again on better signal).`
        : `Saved ${result.done} tiles for offline use.`;
      Alert.alert('Offline area', msg);
    } catch (err: any) {
      Alert.alert('Download failed', err?.message ?? 'Could not download this area.');
    } finally {
      setProgress(null);
    }
  }

  function handleDelete(r: OfflineRegion) {
    Alert.alert('Delete area', `Remove "${r.name}" and its downloaded tiles?`, [
      { text: 'Cancel', style: 'cancel' },
      {
        text: 'Delete', style: 'destructive',
        onPress: async () => { await deleteRegion(r.id); refresh(); },
      },
    ]);
  }

  const totalBytes = regions.reduce((a, r) => a + r.bytes, 0);

  return (
    <View style={{ flex: 1, backgroundColor: theme.background }}>
      <View style={s.mapWrap}>
        {mapUri ? (
          <WebView
            ref={webRef}
            style={{ flex: 1, backgroundColor: theme.cardNested }}
            originWhitelist={['*']}
            source={{ uri: mapUri }}
            injectedJavaScriptBeforeContentLoaded={LEAFLET_INJECT_JS}
            javaScriptEnabled
            domStorageEnabled={false}
            onMessage={onMessage}
            onLoad={() => {
              getCachedPois().then(list => {
                if (!list || list.length === 0) return;
                const pts = list.map(p => ({ lat: p.latitude, lon: p.longitude, name: p.name }));
                webRef.current?.injectJavaScript(`window.showPois && window.showPois(${JSON.stringify(pts)}); true;`);
              }).catch(() => {});
            }}
            userAgent="Sommet/2.0"
            androidLayerType="hardware"
            // Read cached tiles off disk (iOS grants read to cachesRoot; Android needs the file
            // flags). allowUniversalAccess… lets the file:// page fetch remote tiles on a miss.
            allowFileAccess
            allowFileAccessFromFileURLs
            allowUniversalAccessFromFileURLs
            {...(Platform.OS === 'ios' ? { allowingReadAccessToURL: cachesRoot } : {})}
          />
        ) : (
          <View style={{ flex: 1, alignItems: 'center', justifyContent: 'center' }}>
            <ActivityIndicator color={theme.primary} />
          </View>
        )}
        <View style={s.hintPill} pointerEvents="none">
          <Text style={s.hintText}>Frame the area inside the box</Text>
        </View>
      </View>

      <ScrollView style={s.panel} contentContainerStyle={{ padding: v3Spacing.medium }} keyboardShouldPersistTaps="handled">
        {/* Provider */}
        <View style={s.row}>
          {PROVIDERS.map(p => (
            <TouchableOpacity key={p} onPress={() => pickProvider(p)}
              style={[s.seg, provider === p && s.segOn]}>
              <Text style={[s.segText, provider === p && s.segTextOn]}>{MAP_PROVIDER_LABELS[p]}</Text>
            </TouchableOpacity>
          ))}
        </View>

        {/* Detail */}
        <Text style={[s.label, { marginTop: v3Spacing.medium }]}>Detail</Text>
        <View style={s.row}>
          {(Object.keys(DETAIL) as Array<keyof typeof DETAIL>).map(k => (
            <TouchableOpacity key={k} onPress={() => setDetail(k)}
              style={[s.seg, detail === k && s.segOn]}>
              <Text style={[s.segText, detail === k && s.segTextOn]}>{DETAIL[k].label}</Text>
            </TouchableOpacity>
          ))}
        </View>
        <Text style={s.muted}>{DETAIL[detail].hint} · zoom {zooms[0]}–{zooms[zooms.length - 1]}</Text>

        {/* Estimate + name + download */}
        <View style={[s.estRow, { marginTop: v3Spacing.medium }]}>
          <Text style={[s.est, tooBig && { color: theme.warning }]}>
            {bbox ? `≈ ${estTiles.toLocaleString('en-GB')} tiles · ~${estMB.toFixed(estMB < 10 ? 1 : 0)} MB` : 'Move the map to pick an area'}
          </Text>
        </View>
        {tooBig && <Text style={[s.muted, { color: theme.warning }]}>Too large — zoom in or choose a lower detail.</Text>}

        <TextInput value={name} onChangeText={setName}
          placeholder={bbox ? defaultName(bbox) : 'Area name'} placeholderTextColor={theme.mutedText}
          style={s.input} />

        {progress ? (
          <View style={s.progWrap}>
            <View style={s.progBarBg}>
              <View style={[s.progBar, { width: `${progress.total ? Math.round((progress.done / progress.total) * 100) : 0}%` }]} />
            </View>
            <Text style={s.muted}>Downloading {progress.done}/{progress.total}{progress.failed ? ` · ${progress.failed} failed` : ''}</Text>
          </View>
        ) : (
          <Button label="Download this area" icon="backup" variant="filled"
            onPress={handleDownload} disabled={!bbox || tooBig}
            style={{ marginTop: v3Spacing.small }} />
        )}

        {/* Saved areas */}
        <View style={s.savedHead}>
          <Text style={s.cardTitle}>Saved areas</Text>
          {regions.length > 0 && <Text style={s.muted}>{regions.length} · ~{(totalBytes / 1e6).toFixed(totalBytes / 1e6 < 10 ? 1 : 0)} MB</Text>}
        </View>
        {regions.length === 0 && <Text style={s.muted}>None yet. Frame an area above and download it.</Text>}
        {regions.map(r => (
          <Card key={r.id} style={{ marginTop: v3Spacing.small }}>
            <View style={s.regionRow}>
              <TouchableOpacity style={{ flex: 1 }} onPress={() =>
                webRef.current?.injectJavaScript(`window.flyToBox(${r.bbox.minLat},${r.bbox.minLon},${r.bbox.maxLat},${r.bbox.maxLon}); true;`)}>
                <Text style={s.regionName}>{r.name}</Text>
                <Text style={s.muted}>
                  {MAP_PROVIDER_LABELS[r.provider]} · z{r.zooms[0]}–{r.zooms[r.zooms.length - 1]} · {r.tileCount.toLocaleString('en-GB')} tiles · ~{(r.bytes / 1e6).toFixed(1)} MB
                </Text>
              </TouchableOpacity>
              <TouchableOpacity onPress={() => handleDelete(r)} style={s.delBtn} hitSlop={{ top: 8, bottom: 8, left: 8, right: 8 }}>
                <Icon name="delete" size={18} color={theme.warning} />
              </TouchableOpacity>
            </View>
          </Card>
        ))}
      </ScrollView>
    </View>
  );
}

function defaultName(b: RegionBBox): string {
  const cLat = (b.minLat + b.maxLat) / 2, cLon = (b.minLon + b.maxLon) / 2;
  return `Area ${cLat.toFixed(2)}, ${cLon.toFixed(2)}`;
}

const styles = (t: V3Colors) => StyleSheet.create({
  mapWrap: { height: '46%', borderBottomWidth: 1, borderBottomColor: t.border },
  hintPill: { position: 'absolute', top: 10, alignSelf: 'center', backgroundColor: 'rgba(0,0,0,0.55)', paddingHorizontal: 12, paddingVertical: 5, borderRadius: 999 },
  hintText: { color: '#fff', fontSize: 11, fontWeight: '600' as const },
  panel: { flex: 1 },
  row: { flexDirection: 'row', flexWrap: 'wrap', gap: 8, marginTop: 6 },
  seg: { paddingHorizontal: 12, paddingVertical: 8, borderRadius: v3Radius.small, borderWidth: 1, borderColor: t.border, backgroundColor: t.cardNested },
  segOn: { backgroundColor: t.primary, borderColor: t.primary },
  segText: { fontSize: v3Type.label, color: t.text },
  segTextOn: { color: '#fff', fontWeight: '700' as const },
  label: { fontSize: v3Type.label, color: t.mutedText, marginBottom: 2 },
  muted: { fontSize: v3Type.caption, color: t.mutedText, marginTop: 3 },
  estRow: { flexDirection: 'row', alignItems: 'center' },
  est: { fontSize: v3Type.bodyLarge, color: t.text, fontWeight: '700' as const },
  input: { backgroundColor: t.cardNested, color: t.text, borderRadius: v3Radius.small, borderWidth: 1, borderColor: t.border, paddingHorizontal: 12, paddingVertical: 10, fontSize: v3Type.body, marginTop: v3Spacing.small },
  progWrap: { marginTop: v3Spacing.medium },
  progBarBg: { height: 8, borderRadius: 4, backgroundColor: t.cardNested, overflow: 'hidden' },
  progBar: { height: 8, borderRadius: 4, backgroundColor: t.primary },
  savedHead: { flexDirection: 'row', justifyContent: 'space-between', alignItems: 'flex-end', marginTop: v3Spacing.large },
  cardTitle: { fontSize: v3Type.bodyLarge, color: t.text, fontWeight: '700' as const },
  regionRow: { flexDirection: 'row', alignItems: 'center', gap: 10 },
  regionName: { fontSize: v3Type.body, color: t.text, fontWeight: '600' as const },
  delBtn: { padding: 6 },
});
