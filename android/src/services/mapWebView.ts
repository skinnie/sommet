import RNFS from 'react-native-fs';
import { Platform } from 'react-native';

// Shared plumbing for every Leaflet map WebView in the app (activity replay, POI picker, route
// weather, offline-maps manager). Two jobs:
//  1. Load the page from a file:// URL under the caches dir instead of inline HTML, so the
//     WebView — WKWebView included — is allowed to read cached tiles off disk (the only setup
//     that renders a downloaded area with no signal on both platforms).
//  2. Hand back the file-access props each platform needs.
// Leaflet itself is bundled inline via leafletInline.ts (LEAFLET_STYLE_TAG in the head +
// LEAFLET_INJECT_JS as injectedJavaScriptBeforeContentLoaded) — no android_asset/vendored file,
// no CDN. Together these replace the old Android-only `baseUrl: file:///android_asset/` path
// that left iOS with no map at all.

/** Read-access root granted to the WebView (covers both the written html and the tile cache). */
export const CACHES_ROOT_URI = `file://${RNFS.CachesDirectoryPath}`;

/** Writes a map page to the caches dir and returns its file:// URL for <WebView source={{uri}}>. */
export async function writeMapPage(html: string, fileName: string): Promise<string> {
  const path = `${RNFS.CachesDirectoryPath}/${fileName}`;
  await RNFS.writeFile(path, html, 'utf8');
  return `file://${path}`;
}

/** Props to spread onto a <WebView> that loads a caches-dir page and reads cached tiles.
 * iOS uses allowingReadAccessToURL (→ loadFileURL:allowingReadAccessToURL:); Android needs the
 * file flags, and allowUniversalAccessFromFileURLs so the file:// page can fetch remote tiles on
 * a cache miss. */
export function mapWebViewFileProps(): Record<string, unknown> {
  return {
    allowFileAccess: true,
    allowFileAccessFromFileURLs: true,
    allowUniversalAccessFromFileURLs: true,
    ...(Platform.OS === 'ios' ? { allowingReadAccessToURL: CACHES_ROOT_URI } : {}),
  };
}
