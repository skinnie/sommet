import {
  isTabletSize, styleChoices, defaultStyle, resolveStyle, parseFavourites, toggleFavourite,
  shownFavourites, navIdForRoute, itemsKey, gridColumns, FAN_SLOTS, FAN_MAX, BAR_MAX, DEFAULT_FAVOURITES,
} from '../menuModel';

describe('menu style', () => {
  test('the Xperia 1 III is a phone, upright or sideways; the FZ-M1 is a tablet', () => {
    expect(isTabletSize(411, 960)).toBe(false);
    expect(isTabletSize(960, 411)).toBe(false);
    expect(isTabletSize(1280, 800)).toBe(true);
  });

  test('phones get the pop-up list, tablets the side menu, unless the device chose otherwise', () => {
    expect(defaultStyle(false)).toBe('list');
    expect(defaultStyle(true)).toBe('side');
    expect(resolveStyle(null, false)).toBe('list');
    expect(resolveStyle(null, true)).toBe('side');
    expect(resolveStyle('fan', false)).toBe('fan');
    expect(resolveStyle('bar', true)).toBe('bar');
  });

  test('the side menu is offered on tablets only; a phone that has it stored falls back', () => {
    expect(styleChoices(false)).not.toContain('side');
    expect(styleChoices(true)).toContain('side');
    expect(resolveStyle('side', false)).toBe('list');
  });

  test('an unknown stored value falls back to the default', () => {
    expect(resolveStyle('strip', false)).toBe('list');
    expect(resolveStyle('', true)).toBe('side');
  });
});

describe('places in the fan and the bar', () => {
  test('nothing stored, or something broken, gives the default places', () => {
    expect(parseFavourites(null)).toEqual(DEFAULT_FAVOURITES);
    expect(parseFavourites('not json')).toEqual(DEFAULT_FAVOURITES);
    expect(parseFavourites('{"fan": 3}')).toEqual(DEFAULT_FAVOURITES);
  });

  test('stored places are kept in order, without repeats, and cut to the size of each', () => {
    const raw = JSON.stringify({ fan: ['gear', 'gear', 'coach', 1, 'a', 'b', 'c', 'd'], bar: ['x', 'y', 'z', 'w', 'v'] });
    expect(parseFavourites(raw)).toEqual({ fan: ['gear', 'coach', 'a', 'b', 'c'], bar: ['x', 'y', 'z', 'w'] });
  });

  test('a tap adds a place at the end or takes it out; a full list stays as it is', () => {
    expect(toggleFavourite(['home'], 'gear', BAR_MAX)).toEqual({ list: ['home', 'gear'], full: false });
    expect(toggleFavourite(['home', 'gear'], 'home', BAR_MAX)).toEqual({ list: ['gear'], full: false });
    const four = ['a', 'b', 'c', 'd'];
    expect(toggleFavourite(four, 'e', BAR_MAX)).toEqual({ list: four, full: true });
    expect(toggleFavourite(four, 'e', FAN_MAX)).toEqual({ list: [...four, 'e'], full: false });
  });

  test('only places on the menu right now are shown (POIs need a watch), in the order picked', () => {
    expect(shownFavourites(['pois', 'gear', 'home'], ['home', 'gear', 'settings'], FAN_MAX)).toEqual(['gear', 'home']);
    expect(shownFavourites(['a', 'b', 'c', 'd', 'e'], ['a', 'b', 'c', 'd', 'e'], BAR_MAX)).toEqual(['a', 'b', 'c', 'd']);
  });

  test('the fan has a slot for every picked place plus More, nearest first', () => {
    expect(FAN_SLOTS).toHaveLength(FAN_MAX + 1);
    const dist = ([x, y]: [number, number]) => Math.hypot(x, y);
    expect(Math.max(...FAN_SLOTS.slice(0, 3).map(dist))).toBeLessThan(Math.min(...FAN_SLOTS.slice(3).map(dist)));
    // Every slot is left of and/or above the button, never off the right or bottom edge.
    for (const [left, up] of FAN_SLOTS) { expect(left).toBeGreaterThanOrEqual(0); expect(up).toBeGreaterThanOrEqual(0); }
  });

  test('no two fan buttons (48 dp) overlap', () => {
    for (let i = 0; i < FAN_SLOTS.length; i++) for (let j = i + 1; j < FAN_SLOTS.length; j++) {
      const [a, b] = [FAN_SLOTS[i], FAN_SLOTS[j]];
      expect(Math.hypot(a[0] - b[0], a[1] - b[1])).toBeGreaterThan(48);
    }
  });
});

describe('which place is highlighted', () => {
  const present = ['home', 'activities', 'routes', 'bikeWorkouts', 'settings'];

  test('a page lights up its place in the menu', () => {
    expect(navIdForRoute('Home', present)).toBe('home');
    expect(navIdForRoute('LogList', present)).toBe('activities');
    expect(navIdForRoute('GarminRoute', present)).toBe('routes');
    expect(navIdForRoute('Settings', present)).toBe('settings');
  });

  test('the Training Program lights the entry that is on the menu (watch or bike computer)', () => {
    expect(navIdForRoute('WorkoutCalendar', present)).toBe('bikeWorkouts');
    expect(navIdForRoute('WorkoutCalendar', ['workoutCalendar'])).toBe('workoutCalendar');
  });

  test('pages that are not on the menu light nothing', () => {
    expect(navIdForRoute('Map', present)).toBeNull();
    expect(navIdForRoute('Backup', present)).toBeNull();
    expect(navIdForRoute(undefined, present)).toBeNull();
    expect(navIdForRoute('Poi', present)).toBeNull();      // POIs not on the menu without a watch
  });
});

describe('drawing helpers', () => {
  test('the place list fingerprint changes with a place, not with a new tap handler', () => {
    const a = [{ id: 'home', label: 'Home', icon: 'mountain', onPress: () => {} }];
    const b = [{ id: 'home', label: 'Home', icon: 'mountain', onPress: () => {} }];
    expect(itemsKey(a)).toBe(itemsKey(b));
    expect(itemsKey([...a, { id: 'pois', label: 'POIs', icon: 'poi' }])).not.toBe(itemsKey(a));
  });

  test('the all-places sheet has 4 columns upright on the Xperia, up to 8 on wide screens', () => {
    expect(gridColumns(411)).toBe(4);
    expect(gridColumns(960)).toBe(8);
    expect(gridColumns(1280)).toBe(8);
  });
});
