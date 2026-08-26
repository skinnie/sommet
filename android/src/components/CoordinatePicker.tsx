import React, { useEffect, useState } from 'react';
import {
  View, Text, StyleSheet, Modal, TouchableOpacity,
} from 'react-native';
import { WebView, WebViewMessageEvent } from 'react-native-webview';
import { useV3Theme, v3Radius, v3Spacing, v3Type } from '../theme/v3';
import { mapTileLayersJs } from '../services/MapHtml';
import { TILE_CACHE_DIR_URI } from '../services/TileCache';
import { getMapProvider, MapProvider } from '../services/MapProviderService';

// Pick a latitude/longitude by tapping a map — the Android counterpart of the desktop's
// "Pick on a map" button on WatchSettingsPage (its HomeLocationDialog). Used for the Kailash
// home-location setting, where typing degrees by hand is the only alternative and a coordinate
// is far easier to point at than to type.
//
// Built on the same Leaflet-in-a-WebView stack every other map here uses (vendored leaflet under
// android/app/src/main/assets, tiles from the offline cache first), so it works with no network
// and needs no new dependency.

export function CoordinatePicker({
  visible, initialLat, initialLon, onCancel, onPick,
}: {
  visible: boolean;
  initialLat: number;
  initialLon: number;
  onCancel: () => void;
  onPick: (lat: number, lon: number) => void;
}) {
  const t = useV3Theme();
  const [provider, setProvider] = useState<MapProvider>('osm');
  // The currently-pinned point. Starts at whatever the setting already holds, so opening the
  // picker shows where the watch thinks home is rather than a blank world map.
  const [lat, setLat] = useState(initialLat);
  const [lon, setLon] = useState(initialLon);

  useEffect(() => { getMapProvider().then(setProvider); }, []);
  useEffect(() => {
    if (visible) { setLat(initialLat); setLon(initialLon); }
  }, [visible, initialLat, initialLon]);

  // A tap moves the marker and posts the coordinate back. Nothing is written to the watch here -
  // the caller still has to confirm, matching this app's "explicit tap for any write" rule.
  const html = `<!DOCTYPE html>
<html><head>
  <meta charset="utf-8"/>
  <meta name="viewport" content="width=device-width,initial-scale=1,maximum-scale=1,user-scalable=no">
  <link rel="stylesheet" href="leaflet/leaflet.css"/>
  <script src="leaflet/leaflet.js"></script>
  <style>* { margin:0; padding:0; } html,body,#map { width:100%; height:100%; }</style>
</head><body>
<div id="map"></div>
<script>
  var map = L.map('map', { zoomControl: true });
  ${mapTileLayersJs(provider, TILE_CACHE_DIR_URI)}
  map.setView([${initialLat || 0}, ${initialLon || 0}], ${initialLat || initialLon ? 13 : 2});

  var marker = L.marker([${initialLat || 0}, ${initialLon || 0}]).addTo(map);
  map.on('click', function(e) {
    marker.setLatLng(e.latlng);
    window.ReactNativeWebView.postMessage(JSON.stringify({
      type: 'PICK', lat: e.latlng.lat, lon: e.latlng.lng
    }));
  });
</script>
</body></html>`;

  function onMessage(ev: WebViewMessageEvent) {
    try {
      const msg = JSON.parse(ev.nativeEvent.data);
      if (msg?.type === 'PICK' && typeof msg.lat === 'number' && typeof msg.lon === 'number') {
        setLat(msg.lat);
        setLon(msg.lon);
      }
    } catch {
      /* ignore anything that isn't our own message */
    }
  }

  return (
    <Modal visible={visible} animationType="slide" onRequestClose={onCancel}>
      <View style={{ flex: 1, backgroundColor: t.background }}>
        <WebView
          originWhitelist={['*']}
          source={{ html, baseUrl: 'file:///android_asset/' }}
          onMessage={onMessage}
          style={{ flex: 1 }}
        />
        <View style={[styles.bar, { backgroundColor: t.card, borderColor: t.border }]}>
          <Text style={[styles.coord, { color: t.text }]}>
            {lat.toFixed(6)}, {lon.toFixed(6)}
          </Text>
          <View style={styles.actions}>
            <TouchableOpacity
              onPress={onCancel}
              style={[styles.btn, { backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small }]}
            >
              <Text style={{ color: t.text, fontSize: v3Type.body }}>Cancel</Text>
            </TouchableOpacity>
            <TouchableOpacity
              onPress={() => onPick(lat, lon)}
              style={[styles.btn, { backgroundColor: t.cardNested, borderColor: t.border, borderRadius: v3Radius.small, marginLeft: v3Spacing.small }]}
            >
              <Text style={{ color: t.text, fontSize: v3Type.body, fontWeight: '600' }}>Use this point</Text>
            </TouchableOpacity>
          </View>
        </View>
      </View>
    </Modal>
  );
}

const styles = StyleSheet.create({
  bar: { borderTopWidth: 1, padding: v3Spacing.medium },
  coord: { fontSize: v3Type.body, fontWeight: '600', marginBottom: v3Spacing.small, textAlign: 'center' },
  actions: { flexDirection: 'row', justifyContent: 'flex-end' },
  btn: { borderWidth: 1, paddingVertical: 10, paddingHorizontal: v3Spacing.medium },
});
