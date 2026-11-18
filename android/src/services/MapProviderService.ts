import AsyncStorage from '@react-native-async-storage/async-storage';
import RNFS from 'react-native-fs';

// Real, 2026-08-09 ("no button to change provider, nor in the settings like the desktop
// version") - desktop's qml/MapService.qml is the real reference (same tile hosts/
// attribution text for osm/cyclosm), but persisted here via AsyncStorage since this is
// plain RN, not QML singletons (MapService.qml's own header comment calls persistence "a
// real, small follow-up, not done here" - this is that follow-up, done on Android).
// IGN (France) was dropped 2026-11-18 (André: it only covers France, so every map in Portugal
// was empty). A saved 'ign' choice becomes CyclOSM, and the saved IGN areas and tiles go.
export type MapProvider = 'osm' | 'cyclosm';

const STORAGE_KEY = 'sommet:mapProvider';
const DEFAULT_PROVIDER: MapProvider = 'cyclosm';

export async function getMapProvider(): Promise<MapProvider> {
  try {
    const v = await AsyncStorage.getItem(STORAGE_KEY);
    if (v === 'osm' || v === 'cyclosm') return v;
    if (v === 'ign') {
      await AsyncStorage.setItem(STORAGE_KEY, DEFAULT_PROVIDER);
      dropIgnTiles().catch(() => {});
    }
  } catch {}
  return DEFAULT_PROVIDER;
}

/** Deletes the saved IGN areas and their tiles (IGN was dropped, 2026-11-18). */
export async function dropIgnTiles(): Promise<void> {
  const dir = `${RNFS.CachesDirectoryPath}/maptiles/ign`;
  if (await RNFS.exists(dir)) await RNFS.unlink(dir);
  const raw = await AsyncStorage.getItem('sommet:offlineRegions');
  if (!raw) return;
  const arr = JSON.parse(raw);
  if (!Array.isArray(arr)) return;
  const kept = arr.filter((r: { provider?: string }) => r && r.provider !== 'ign');
  if (kept.length !== arr.length) await AsyncStorage.setItem('sommet:offlineRegions', JSON.stringify(kept));
}

export async function setMapProvider(p: MapProvider): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, p).catch(() => {});
}

// Display names - not run through i18n (t.*): these are real third-party service/project
// names (OpenStreetMap, CyclOSM), same as desktop's own MapService.qml/SettingsPage.qml
// which don't translate them either.
export const MAP_PROVIDER_LABELS: Record<MapProvider, string> = {
  osm: 'OpenStreetMap',
  cyclosm: 'CyclOSM',
};
