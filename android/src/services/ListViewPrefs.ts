// ─── ListViewPrefs ──────────────────────────────────────────────────────────
// Shared prefs + sort helpers for the list-bearing screens (Activities, Routes, POIs).
//
// Two independent concerns, both matching what André asked for (2026-08-16) and mirroring
// desktop's own `Theme.activitiesView` pattern:
//   • View mode (map ⇆ list), a PERSISTED per-surface preference set in Settings - "map"
//     shows each item's track/thumbnail, "list" is a lighter text-only list. Independent per
//     surface: activities / routes / pois each have their own key + Settings toggle.
//   • Sort key (last uploaded / name / distance / ascent), an IN-PAGE control at the top of
//     each list (not persisted - "on each list page", per André). POIs sort by name only
//     (they have no distance/ascent/upload time).

import AsyncStorage from '@react-native-async-storage/async-storage';

export type ViewMode = 'map' | 'list';
export type ListSurface = 'activities' | 'routes' | 'pois';

const VIEW_KEY: Record<ListSurface, string> = {
  activities: 'sommet:view:activities',
  routes: 'sommet:view:routes',
  pois: 'sommet:view:pois',
};

/** Persisted view mode for a surface. Default "map" (matches desktop's `activitiesView`
 * default and this app's "immediate map view" house style for routes/POIs). */
export async function getViewMode(surface: ListSurface): Promise<ViewMode> {
  try {
    return (await AsyncStorage.getItem(VIEW_KEY[surface])) === 'list' ? 'list' : 'map';
  } catch {
    return 'map';
  }
}

export async function setViewMode(surface: ListSurface, mode: ViewMode): Promise<void> {
  try {
    await AsyncStorage.setItem(VIEW_KEY[surface], mode);
  } catch {
    // non-persisted this run; not fatal
  }
}

// ─── Configurable Activities columns ──────────────────────────────────────────
// The ordered metric keys shown as columns in the Activities list (ActivityMetrics). Persisted
// as CSV, default matches the original fixed columns.
const COLUMNS_KEY = 'sommet:activityColumns';
const DEFAULT_COLUMNS = ['distance', 'duration', 'ascent', 'calories'];

export async function getActivityColumns(): Promise<string[]> {
  try {
    const raw = await AsyncStorage.getItem(COLUMNS_KEY);
    if (raw && raw.length > 0) return raw.split(',');
  } catch {
    // fall through to default
  }
  return DEFAULT_COLUMNS.slice();
}

export async function setActivityColumns(keys: string[]): Promise<void> {
  try {
    await AsyncStorage.setItem(COLUMNS_KEY, keys.join(','));
  } catch {
    // non-persisted this run; not fatal
  }
}

// ─── Sorting ────────────────────────────────────────────────────────────────

export type SortKey = 'uploaded' | 'name' | 'distance' | 'ascent';

/** Fields a sortable list item may expose; all optional so one comparator serves activities,
 * routes and POIs (each fills in what it has). */
export interface Sortable {
  uploadedAt?: number;   // ms epoch - when it was synced/added locally
  name?: string;         // activity type / route name / POI name
  distanceM?: number;
  ascentM?: number;
}

/** The sort options a given surface offers. Activities have an upload time + full stats;
 * routes have name/distance/ascent but no on-device upload timestamp; POIs are points, so
 * only name is meaningful. */
export function sortKeysFor(surface: ListSurface): SortKey[] {
  switch (surface) {
    case 'activities': return ['uploaded', 'name', 'distance', 'ascent'];
    case 'routes':     return ['name', 'distance', 'ascent'];
    case 'pois':       return ['name'];
  }
}

/** Return a NEW sorted array. "uploaded" is newest-first (most recent upload on top, the
 * previous default); name is A→Z; distance/ascent are largest-first (the interesting ones
 * on top). Stable-ish: ties keep input order. */
export function sortItems<T extends Sortable>(items: T[], key: SortKey): T[] {
  const out = items.slice();
  out.sort((a, b) => {
    switch (key) {
      case 'name':
        return (a.name ?? '').localeCompare(b.name ?? '', undefined, { sensitivity: 'base' });
      case 'distance':
        return (b.distanceM ?? 0) - (a.distanceM ?? 0);
      case 'ascent':
        return (b.ascentM ?? 0) - (a.ascentM ?? 0);
      case 'uploaded':
      default:
        return (b.uploadedAt ?? 0) - (a.uploadedAt ?? 0);
    }
  });
  return out;
}
