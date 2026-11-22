import RNFS from 'react-native-fs';
import {
  AMBIT_POI_TYPES, TRAVERSE_POI_TYPES, AMBIT_POI_TYPE_DEFAULT, TRAVERSE_POI_TYPE_DEFAULT,
  PoiTypeList, poiTypeBetween,
} from './PoiTypes';

// The POI library on the phone/tablet (André, 2026-11-22: "we have a library for the routes but
// not for the POIs... for me these two should be similar"). Twin of the desktop's
// tools/poi_library.py, with the same file: one index.json
//   { version: 1, pois: [{id, name, lat, lon, ele, type, typeList, created, updated}],
//     deleted: [{id, at}] }
// Sommet's library is one thing and what is on a watch is another: a POI kept here stays here
// whatever happens on a watch, and nothing here changes a watch until a POI is sent to it.
// `type` is the POI type number and `typeList` the list it is a number in ("ambit": the Ambit3
// family's 18; "traverse": the Traverse's 48), so a POI goes to the other kind of watch by its
// icon. `deleted` remembers what was removed, for the day the library is shared between devices.
// Saving the same POI again (same name, within a few metres) keeps one.
// tools/test_poi_library.py and __tests__/PoiLibrary.test.ts check the two against one file
// (poiLibrary.fixture.json).

const DIR = `${RNFS.DocumentDirectoryPath}/poi_library`;
const INDEX = `${DIR}/index.json`;
export const SAME_PLACE_M = 10;

export interface LibraryPoi {
  id: string;
  name: string;
  lat: number;
  lon: number;
  ele: number | null;
  type: number;
  typeList: PoiTypeList;
  created: number;              // seconds since epoch, as on the desktop
  updated: number;
  /** Only on a list made for one kind of watch: the type as a number in that watch's list. */
  typeHere?: number;
}

export interface PoiToKeep {
  name: string;
  lat: number;
  lon: number;
  ele?: number | null;
  type?: number | null;
  typeList?: PoiTypeList | null;
}

export interface PoiLibraryData {
  version: 1;
  pois: LibraryPoi[];
  deleted: { id: string; at: number }[];
}

export type KeepResult = { ok: true; id: string; existed: boolean } | { ok: false; error: string };

// ---- the rules, with no file access (tested against the desktop's) ---------------------------

function metres(lat1: number, lon1: number, lat2: number, lon2: number): number {
  const rad = (d: number) => (d * Math.PI) / 180;
  const p1 = rad(lat1), p2 = rad(lat2);
  const x = Math.sin((p2 - p1) / 2) ** 2
    + Math.cos(p1) * Math.cos(p2) * Math.sin(rad(lon2 - lon1) / 2) ** 2;
  return 2 * 6371000 * Math.asin(Math.min(1, Math.sqrt(x)));
}

function samePoi(p: LibraryPoi, name: string, lat: number, lon: number): boolean {
  return p.name.trim().toLowerCase() === name.toLowerCase()
    && metres(p.lat, p.lon, lat, lon) <= SAME_PLACE_M;
}

function round7(v: number): number {
  return Math.round(v * 1e7) / 1e7;
}

/** Checked values for one POI, or the words to show when it cannot be one. */
export function cleanPoi(item: PoiToKeep): { ok: true; poi: Omit<LibraryPoi, 'id' | 'created' | 'updated'> }
                                            | { ok: false; error: string } {
  const name = String(item.name ?? '').trim();
  if (!name) return { ok: false, error: 'a POI needs a name' };
  const lat = Number(item.lat), lon = Number(item.lon);
  if (item.lat == null || item.lon == null || Number.isNaN(lat) || Number.isNaN(lon)) {
    return { ok: false, error: 'the position is not a number' };
  }
  if (!Number.isFinite(lat) || !Number.isFinite(lon) || lat < -90 || lat > 90 || lon < -180 || lon > 180) {
    return { ok: false, error: 'that position is not a place on Earth' };
  }
  const typeList: PoiTypeList = item.typeList === 'traverse' ? 'traverse' : 'ambit';
  const known = typeList === 'traverse' ? TRAVERSE_POI_TYPES : AMBIT_POI_TYPES;
  const plain = typeList === 'traverse' ? TRAVERSE_POI_TYPE_DEFAULT : AMBIT_POI_TYPE_DEFAULT;
  const type = item.type != null && known.some(t => t.id === item.type) ? item.type : plain;
  const ele = item.ele != null && Number.isFinite(Number(item.ele)) ? Number(item.ele) : null;
  return { ok: true, poi: { name, lat: round7(lat), lon: round7(lon), ele, type, typeList } };
}

/** Adds `items` to `data` (changed in place). One result per item, in order; one already kept
 *  (same name, within SAME_PLACE_M) is left as it is. */
export function keepInto(data: PoiLibraryData, items: PoiToKeep[], nowSeconds: number,
                         newId: () => string): KeepResult[] {
  return items.map(item => {
    const c = cleanPoi(item);
    if (!c.ok) return { ok: false as const, error: c.error };
    const have = data.pois.find(p => samePoi(p, c.poi.name, c.poi.lat, c.poi.lon));
    if (have) return { ok: true as const, id: have.id, existed: true };
    const entry: LibraryPoi = { id: newId(), ...c.poi, created: nowSeconds, updated: nowSeconds };
    data.pois.push(entry);
    return { ok: true as const, id: entry.id, existed: false };
  });
}

/** By name; with `forList`, each also carries `typeHere`, ready to send to that kind of watch. */
export function listFrom(data: PoiLibraryData, forList?: PoiTypeList): LibraryPoi[] {
  return data.pois.slice()
    .sort((a, b) => {
      const x = a.name.toLowerCase(), y = b.name.toLowerCase();
      return x < y ? -1 : x > y ? 1 : a.created - b.created;
    })
    .map(p => (forList ? { ...p, typeHere: poiTypeBetween(p.type, p.typeList ?? 'ambit', forList) } : { ...p }));
}

// ---- the file --------------------------------------------------------------------------------

async function load(): Promise<PoiLibraryData> {
  let raw: any = {};
  try {
    if (await RNFS.exists(INDEX)) raw = JSON.parse(await RNFS.readFile(INDEX, 'utf8'));
  } catch {
    raw = {};
  }
  return {
    version: 1,
    pois: Array.isArray(raw?.pois) ? raw.pois : [],
    deleted: Array.isArray(raw?.deleted) ? raw.deleted : [],
  };
}

async function store(data: PoiLibraryData): Promise<void> {
  if (!(await RNFS.exists(DIR))) await RNFS.mkdir(DIR);
  const tmp = `${INDEX}.tmp`;
  await RNFS.writeFile(tmp, JSON.stringify(data), 'utf8');
  if (await RNFS.exists(INDEX)) await RNFS.unlink(INDEX);
  await RNFS.moveFile(tmp, INDEX);
}

function newId(): string {
  let s = '';
  for (let i = 0; i < 32; i++) s += Math.floor(Math.random() * 16).toString(16);
  return s;
}

// One change at a time: two saves started together would each write the list they read.
let chain: Promise<unknown> = Promise.resolve();
function oneAtATime<T>(job: () => Promise<T>): Promise<T> {
  const next = chain.then(job, job);
  chain = next.catch(() => {});
  return next;
}

export async function listLibraryPois(forList?: PoiTypeList): Promise<LibraryPoi[]> {
  return listFrom(await load(), forList);
}

export function saveLibraryPois(items: PoiToKeep[]): Promise<KeepResult[]> {
  return oneAtATime(async () => {
    const data = await load();
    const results = keepInto(data, items, Math.floor(Date.now() / 1000), newId);
    if (results.some(r => r.ok && !r.existed)) await store(data);
    return results;
  });
}

export function renameLibraryPoi(id: string, name: string): Promise<boolean> {
  return oneAtATime(async () => {
    const clean = name.trim();
    const data = await load();
    const p = data.pois.find(x => x.id === id);
    if (!p || !clean) return false;
    p.name = clean;
    p.updated = Math.floor(Date.now() / 1000);
    await store(data);
    return true;
  });
}

export function deleteLibraryPoi(id: string): Promise<boolean> {
  return oneAtATime(async () => {
    const data = await load();
    const keep = data.pois.filter(x => x.id !== id);
    if (keep.length === data.pois.length) return false;
    data.pois = keep;
    data.deleted.push({ id, at: Math.floor(Date.now() / 1000) });
    await store(data);
    return true;
  });
}

/** How many of a save's results were new, and how many were already kept. */
export function countKept(results: KeepResult[]): { saved: number; existed: number; failed: string | null } {
  let saved = 0, existed = 0;
  let failed: string | null = null;
  for (const r of results) {
    if (!r.ok) failed = failed ?? r.error;
    else if (r.existed) existed++;
    else saved++;
  }
  return { saved, existed, failed };
}
