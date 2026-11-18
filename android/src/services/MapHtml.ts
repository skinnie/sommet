import { MapProvider, MAP_PROVIDER_LABELS } from './MapProviderService';

// Shared by MapScreen.tsx (activity map) and TrackMapScreen.tsx (route/POI
// preview map) - both real Leaflet WebViews, same three tile hosts/attribution text as
// desktop's own qml/MapService.qml (IGN was dropped 2026-11-18 - see MapProviderService.ts's
// header comment). Pulled out so both screens share one real tile/provider-switcher
// implementation instead of two copies drifting apart.
//
// collapsed:false - real, 2026-08-09 ("no button to change provider") - Leaflet's default
// collapsed layer control is a small, easy-to-miss icon in the corner; expanded makes it a
// real, obviously-tappable set of buttons, closer to what "a button to change provider"
// actually asked for than the control that was already technically there.
//
// baselayerchange -> postMessage: mirrors desktop's own MapService.provider = "..." write
// on click (SettingsPage.qml) - switching the provider from inside the map itself also
// persists as the new default, not just a one-time in-session override.
//
// Real, 2026-08-10 ("let's go for the offline maps solution") - each layer's own URL
// template now points at the LOCAL tile cache first (TileCache.ts's own localTilePath()
// layout - `${cacheDirUri}/<provider>/{z}/{x}/{y}.png`), not the remote host directly.
// A cache miss is expected and silent for anywhere not explicitly downloaded yet
// (MapScreen.tsx's own "download for offline" button, TileCache.ts's downloadRegion()) -
// Leaflet's 'tileerror' event fires for that missing local file exactly like it would for
// a real network failure, and the handler below swaps the same <img> element's src to the
// real remote URL right there, so normal online browsing looks identical to before this
// change; only a genuinely offline device (no cache, no network either) sees a blank tile.
// Tiles fetched through that fallback aren't written back to the RNFS cache (that fetch
// happens inside the WebView's own network stack, not through RNFS) - passive browsing
// doesn't grow the offline cache, only the explicit download does. That's a deliberate
// scope boundary, not an oversight: silently caching everything panned past would make
// cache size unpredictable for no real benefit over "download the area you actually need".
export function mapTileLayersJs(defaultProvider: MapProvider, cacheDirUri: string): string {
  return `
  var _remoteTileUrl = {
    osm: function(z, x, y) { return 'https://tile.openstreetmap.org/' + z + '/' + x + '/' + y + '.png'; },
    cyclosm: function(z, x, y) { return 'https://a.tile-cyclosm.openstreetmap.fr/cyclosm/' + z + '/' + x + '/' + y + '.png'; },
  };
  function _onTileError(providerKey) {
    return function(e) {
      if (e.tile.dataset.offlineFallback) return; // already swapped once - don't loop
      e.tile.dataset.offlineFallback = '1';
      e.tile.src = _remoteTileUrl[providerKey](e.coords.z, e.coords.x, e.coords.y);
    };
  }

  var osm = L.tileLayer('${cacheDirUri}/osm/{z}/{x}/{y}.png',
    { maxZoom: 19, attribution: '© OpenStreetMap contributors' });
  var cyclosm = L.tileLayer('${cacheDirUri}/cyclosm/{z}/{x}/{y}.png',
    { maxZoom: 20, attribution: '© OpenStreetMap contributors, CyclOSM' });
  osm.on('tileerror', _onTileError('osm'));
  cyclosm.on('tileerror', _onTileError('cyclosm'));

  var _layers = { osm: osm, cyclosm: cyclosm };
  (_layers['${defaultProvider}'] || cyclosm).addTo(map);
  L.control.layers({
    '${MAP_PROVIDER_LABELS.osm}': osm,
    '${MAP_PROVIDER_LABELS.cyclosm}': cyclosm,
  }, null, { position: 'topright', collapsed: false }).addTo(map);
  var _layerNameToKey = {
    '${MAP_PROVIDER_LABELS.osm}': 'osm',
    '${MAP_PROVIDER_LABELS.cyclosm}': 'cyclosm',
  };
  map.on('baselayerchange', function(e) {
    var key = _layerNameToKey[e.name];
    if (key && window.ReactNativeWebView) {
      window.ReactNativeWebView.postMessage(JSON.stringify({ type: 'MAP_PROVIDER_CHANGE', provider: key }));
    }
  });
  `;
}
