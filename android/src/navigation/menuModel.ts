// The menu's rules, kept apart from the drawing so they can be tested (André, 2026-11-02: "toggle
// between fan out, pop up list and floating bar ... between devices some may work better").
//
// Four menu styles. Every device keeps its own choice (it is stored on the device):
//   - 'list': a round ☰ at the bottom right pops up a list of every place, with names.
//   - 'fan':  the same round ☰ fans out the places you picked (up to 5) plus More.
//   - 'bar':  a small floating bar at the bottom with the places you picked (up to 4) plus More.
//   - 'side': the side menu on the left (tablets only), on every page; ☰ folds it away.
// "More" opens a sheet with every place. Whatever the style, the menu is there on every page.

export type MenuStyle = 'list' | 'fan' | 'bar' | 'side';

// Same threshold as before: a screen whose shorter side is under 700 dp gets the phone layout.
export const TABLET_MIN_SIDE = 700;

export function isTabletSize(width: number, height: number): boolean {
  return Math.min(width, height) >= TABLET_MIN_SIDE;
}

const ALL_STYLES: MenuStyle[] = ['list', 'fan', 'bar', 'side'];

// The styles a device can pick. The side menu needs a tablet's width (it takes 244 dp).
export function styleChoices(isTablet: boolean): MenuStyle[] {
  return isTablet ? ['side', 'list', 'fan', 'bar'] : ['list', 'fan', 'bar'];
}

export function defaultStyle(isTablet: boolean): MenuStyle {
  return isTablet ? 'side' : 'list';
}

// The style to use: the stored choice when this device can use it, else the device's default.
export function resolveStyle(stored: string | null | undefined, isTablet: boolean): MenuStyle {
  const s = ALL_STYLES.find(x => x === stored);
  return s && styleChoices(isTablet).includes(s) ? s : defaultStyle(isTablet);
}

// Places picked for the fan and for the bar, each its own list, in the order picked.
export interface MenuFavourites { fan: string[]; bar: string[] }

export const FAN_MAX = 5;
export const BAR_MAX = 4;

export const DEFAULT_FAVOURITES: MenuFavourites = {
  fan: ['home', 'activities', 'routes', 'health', 'calendar'],
  bar: ['home', 'activities', 'routes', 'health'],
};

export function maxFavourites(style: 'fan' | 'bar'): number {
  return style === 'fan' ? FAN_MAX : BAR_MAX;
}

// Read the stored favourites, keeping only what makes sense (unknown shapes fall back).
export function parseFavourites(raw: string | null | undefined): MenuFavourites {
  let v: any = null;
  try { v = raw ? JSON.parse(raw) : null; } catch { v = null; }
  const clean = (x: any, max: number, dflt: string[]) =>
    Array.isArray(x) ? [...new Set(x.filter((id: any) => typeof id === 'string'))].slice(0, max) : dflt;
  return {
    fan: clean(v?.fan, FAN_MAX, DEFAULT_FAVOURITES.fan),
    bar: clean(v?.bar, BAR_MAX, DEFAULT_FAVOURITES.bar),
  };
}

// Tap a place in the picker: picked -> unpicked; unpicked -> added at the end, unless full.
export function toggleFavourite(list: string[], id: string, max: number): { list: string[]; full: boolean } {
  if (list.includes(id)) return { list: list.filter(x => x !== id), full: false };
  if (list.length >= max) return { list, full: true };
  return { list: [...list, id], full: false };
}

// The picked places that exist right now (some only show with a watch connected), in order.
export function shownFavourites(picked: string[], presentIds: string[], max: number): string[] {
  return picked.filter(id => presentIds.includes(id)).slice(0, max);
}

// Fan-out: where each round button sits, in dp from the centre of the round ☰ (left, up). Two
// quarter circles toward the thumb: 110 dp (3 buttons) and 200 dp (3 buttons). Picked places
// take the slots in order, then More takes the next one.
export const FAN_SLOTS: Array<[number, number]> = [
  [0, 110], [78, 78], [110, 0],
  [52, 193], [141, 141], [193, 52],
];

// Which menu place a page belongs to, to highlight it in the menu. Pages that are not in the menu
// (an activity's map, Backup...) highlight nothing.
const ROUTE_TO_NAV: Record<string, string[]> = {
  Home: ['home'],
  LogList: ['activities'],
  Totals: ['totals'],
  Calendar: ['calendar'],
  Route: ['routes'],
  GarminRoute: ['routes'],
  Poi: ['pois'],
  GarminPoi: ['pois'],
  SportModes: ['sportModes'],
  WatchSettings: ['watchSettings'],
  Firmware: ['firmware'],
  Apps: ['apps'],
  SmartSensor: ['smartSensor'],
  WorkoutCalendar: ['workoutCalendar', 'bikeWorkouts'],
  Gear: ['gear'],
  Bryton: ['brytonProfile'],
  Magene: ['magene'],
  Edge500: ['edge500Profile'],
  Coach: ['coach'],
  RacePlan: ['racePlan'],
  Ember: ['ember'],
  Health: ['health'],
  Weight: ['weight'],
  Settings: ['settings'],
};

export function navIdForRoute(routeName: string | undefined, presentIds: string[]): string | null {
  const ids = (routeName && ROUTE_TO_NAV[routeName]) || [];
  return ids.find(id => presentIds.includes(id)) ?? null;
}

// A short fingerprint of the place list: the menu is redrawn only when this changes, not on every
// Home render (Home rebuilds the list, with fresh tap handlers, each time it renders).
export function itemsKey(items: Array<{ id: string; label: string; icon: string }>): string {
  return items.map(i => `${i.id}|${i.label}|${i.icon}`).join('\n');
}

// Columns for the sheet that lists every place: 4 on a phone held upright, more on wider screens.
export function gridColumns(width: number): number {
  return Math.max(4, Math.min(8, Math.floor(width / 100)));
}
