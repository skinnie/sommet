import AsyncStorage from '@react-native-async-storage/async-storage';

// Real, 2026-08-09 ("no button to change provider, nor in the settings like the desktop
// version") - desktop's qml/MapService.qml is the real reference (same tile hosts/
// attribution text for osm/cyclosm), but persisted here via AsyncStorage since this is
// plain RN, not QML singletons (MapService.qml's own header comment calls persistence "a
// real, small follow-up, not done here" - this is that follow-up, done on Android). IGN is
// a genuine Android-only extra, not a stale copy of desktop: MapScreen.tsx's own header
// comment explains desktop's plain XYZ tile renderer can't address IGN's WMTS scheme,
// while Leaflet (used here) has no such limit.
export type MapProvider = 'ign' | 'osm' | 'cyclosm';

const STORAGE_KEY = 'sommet:mapProvider';
const DEFAULT_PROVIDER: MapProvider = 'ign';

export async function getMapProvider(): Promise<MapProvider> {
  try {
    const v = await AsyncStorage.getItem(STORAGE_KEY);
    if (v === 'ign' || v === 'osm' || v === 'cyclosm') return v;
  } catch {}
  return DEFAULT_PROVIDER;
}

export async function setMapProvider(p: MapProvider): Promise<void> {
  await AsyncStorage.setItem(STORAGE_KEY, p).catch(() => {});
}

// Display names - not run through i18n (t.*): these are real third-party service/project
// names (IGN, OpenStreetMap, CyclOSM), same as desktop's own MapService.qml/SettingsPage.qml
// which don't translate them either.
export const MAP_PROVIDER_LABELS: Record<MapProvider, string> = {
  ign: 'IGN (France)',
  osm: 'OpenStreetMap',
  cyclosm: 'CyclOSM',
};
