// The POI types a watch knows: the type byte of a POI is the icon the watch draws.
// Twin of the desktop's DeviceCapabilities.qml (ambitPoiTypes, traversePoiTypeList) and of
// tools/ambit_format.py (WAYPOINT_TYPES, TRAVERSE_WAYPOINT_TYPES); tools/test_poi_types_parity.py
// compares the three. The Ambit3 family has 18 types (0-17). The Traverse and Traverse Alpha have
// their own 48 (0-47, read from the 2.0.22 watch program and seen in its own picker in the PC
// emulator), numbered differently: 2 is "Camp" on an Ambit3 and "Car" on a Traverse. `set` is the
// watch's own grouping: it offers the "hunting" and "fishing" types during a Hunting or Fishing
// recording. Ten Traverse types have an icon of their own; the others draw the Ambit icon of
// their kind, as on the watch. Glyphs are codepoints of the bundled MaterialSymbolsRounded font
// (tools/subset_material_symbols.py). Listed as the picker shows them: the plain type first,
// then by name.

export interface PoiType { id: number; name: string; set: 'general' | 'fishing' | 'hunting'; glyph: string }

export const AMBIT_POI_TYPES: PoiType[] = [
  { id: 0, name: 'Building', set: 'general', glyph: '\uea40' },
  { id: 1, name: 'Cave', set: 'general', glyph: '\uf6e7' },
  { id: 2, name: 'Camp', set: 'general', glyph: '\uea68' },
  { id: 3, name: 'Car', set: 'general', glyph: '\ue531' },
  { id: 4, name: 'Crossroads', set: 'general', glyph: '\uebac' },
  { id: 5, name: 'Beginning', set: 'general', glyph: '\ue57b' },
  { id: 6, name: 'End', set: 'general', glyph: '\uf06e' },
  { id: 7, name: 'Food', set: 'general', glyph: '\ue56c' },
  { id: 8, name: 'Forest', set: 'general', glyph: '\uea99' },
  { id: 9, name: 'Geocache', set: 'general', glyph: '\ue87a' },
  { id: 10, name: 'Lodging', set: 'general', glyph: '\ue53a' },
  { id: 11, name: 'Meadow', set: 'general', glyph: '\uf205' },
  { id: 12, name: 'Mountain', set: 'general', glyph: '\ue3f7' },
  { id: 13, name: 'Sight', set: 'general', glyph: '\ue3b0' },
  { id: 14, name: 'Road', set: 'general', glyph: '\ueacd' },
  { id: 15, name: 'Rock', set: 'general', glyph: '\ue3df' },
  { id: 16, name: 'Water', set: 'general', glyph: '\ue798' },
  { id: 17, name: 'Waypoint', set: 'general', glyph: '\ue0c8' },
];

export const TRAVERSE_POI_TYPES: PoiType[] = [
  { id: 26, name: 'POI', set: 'general', glyph: '\ue0c8' },
  { id: 37, name: 'Bedding', set: 'hunting', glyph: '\ue0c8' },
  { id: 23, name: 'Begin', set: 'general', glyph: '\ue57b' },
  { id: 43, name: 'Big game', set: 'hunting', glyph: '\ue71a' },
  { id: 45, name: 'Bird', set: 'hunting', glyph: '\uf555' },
  { id: 0, name: 'Building', set: 'general', glyph: '\uea40' },
  { id: 8, name: 'Cafe', set: 'general', glyph: '\ue56c' },
  { id: 4, name: 'Camp', set: 'general', glyph: '\uea68' },
  { id: 5, name: 'Camping', set: 'general', glyph: '\uea68' },
  { id: 2, name: 'Car', set: 'general', glyph: '\ue531' },
  { id: 31, name: 'Cave', set: 'general', glyph: '\uf6e7' },
  { id: 19, name: 'Cliff', set: 'general', glyph: '\ue3f7' },
  { id: 15, name: 'Coast', set: 'general', glyph: '\ue798' },
  { id: 21, name: 'Crossroad', set: 'general', glyph: '\uebac' },
  { id: 32, name: 'Emergency', set: 'general', glyph: '\uf2c3' },
  { id: 24, name: 'End', set: 'general', glyph: '\uf06e' },
  { id: 47, name: 'Fish', set: 'fishing', glyph: '\uf1ea' },
  { id: 36, name: 'Fishing spot', set: 'fishing', glyph: '\uead7' },
  { id: 6, name: 'Food', set: 'general', glyph: '\ue56c' },
  { id: 20, name: 'Forest', set: 'general', glyph: '\uea99' },
  { id: 25, name: 'Geocache', set: 'general', glyph: '\ue87a' },
  { id: 17, name: 'Hill', set: 'general', glyph: '\ue3f7' },
  { id: 1, name: 'Home', set: 'general', glyph: '\uea40' },
  { id: 10, name: 'Hostel', set: 'general', glyph: '\ue53a' },
  { id: 11, name: 'Hotel', set: 'general', glyph: '\ue53a' },
  { id: 33, name: 'Info', set: 'general', glyph: '\ue88e' },
  { id: 14, name: 'Lake', set: 'general', glyph: '\ue798' },
  { id: 9, name: 'Lodging', set: 'general', glyph: '\ue53a' },
  { id: 30, name: 'Meadow', set: 'general', glyph: '\uf205' },
  { id: 16, name: 'Mountain', set: 'general', glyph: '\ue3f7' },
  { id: 3, name: 'Parking', set: 'general', glyph: '\ue531' },
  { id: 34, name: 'Peak', set: 'general', glyph: '\ue3f7' },
  { id: 38, name: 'Prints', set: 'hunting', glyph: '\ue91d' },
  { id: 7, name: 'Restaurant', set: 'general', glyph: '\ue56c' },
  { id: 13, name: 'River', set: 'fishing', glyph: '\ue798' },
  { id: 27, name: 'Road', set: 'general', glyph: '\ueacd' },
  { id: 29, name: 'Rock', set: 'fishing', glyph: '\ue3df' },
  { id: 39, name: 'Rub', set: 'hunting', glyph: '\uea99' },
  { id: 40, name: 'Scrape', set: 'hunting', glyph: '\uea99' },
  { id: 46, name: 'Shot', set: 'hunting', glyph: '\ue719' },
  { id: 22, name: 'Sight', set: 'general', glyph: '\ue3b0' },
  { id: 44, name: 'Small game', set: 'hunting', glyph: '\ue799' },
  { id: 41, name: 'Stand', set: 'hunting', glyph: '\ue2cb' },
  { id: 28, name: 'Trail', set: 'general', glyph: '\ueacd' },
  { id: 42, name: 'Trail cam', set: 'hunting', glyph: '\ue3b0' },
  { id: 18, name: 'Valley', set: 'general', glyph: '\ue3f7' },
  { id: 12, name: 'Water', set: 'fishing', glyph: '\ue798' },
  { id: 35, name: 'Waterfall', set: 'fishing', glyph: '\ue798' },
];

// The Traverse type that draws the same icon as each Ambit3 type (ambit_format.py
// AMBIT_TO_TRAVERSE_WAYPOINT_TYPE): Testing mode's sample POIs carry Ambit3 numbers.
export const AMBIT_TO_TRAVERSE_POI_TYPE: Record<number, number> = {
  0: 0, 1: 31, 2: 4, 3: 2, 4: 21, 5: 23, 6: 24, 7: 6, 8: 20, 9: 25, 10: 9, 11: 30,
  12: 16, 13: 22, 14: 27, 15: 29, 16: 12, 17: 26,
};

export const AMBIT_POI_TYPE_DEFAULT = 17;       // "Waypoint"
export const TRAVERSE_POI_TYPE_DEFAULT = 26;    // "POI", the Traverse's plain flag

/** True for a Traverse or Traverse Alpha, by the name the watch gives itself ("Jabiru", "Loon")
 *  or the product name. */
export function isTraverseWatch(model: string | null | undefined): boolean {
  const m = (model || '').toLowerCase();
  return m === 'jabiru' || m === 'loon' || m.includes('traverse');
}

/** The types the Add-a-POI picker offers for this watch. */
export function poiTypesFor(model: string | null | undefined): PoiType[] {
  return isTraverseWatch(model) ? TRAVERSE_POI_TYPES : AMBIT_POI_TYPES;
}

/** The type a new POI gets when none is picked. */
export function poiTypeDefaultFor(model: string | null | undefined): number {
  return isTraverseWatch(model) ? TRAVERSE_POI_TYPE_DEFAULT : AMBIT_POI_TYPE_DEFAULT;
}

/** The entry for a type byte read off this watch; an unknown number reads as its plain type. */
export function poiTypeOf(id: number | null | undefined, model: string | null | undefined): PoiType {
  const list = poiTypesFor(model);
  return list.find(t => t.id === id) ?? list.find(t => t.id === poiTypeDefaultFor(model))!;
}
