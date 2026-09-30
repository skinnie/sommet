import RNFS from 'react-native-fs';

// The saved-route library on the phone/tablet (André, 2026-09-29: yes to a library on Android,
// desktop parity with tools/route_library.py - "routes you import or plan are kept, so they're
// still there next time"). Same layout as the desktop: one <id>.gpx per route plus index.json
// [{id, name, created, distanceMeters, ascentMeters, hash}] in one folder. Saving the same GPX
// again (same content hash) doesn't add a second copy - it returns the existing entry.
// Android also keeps a small `preview` polyline per entry so the list draws its thumbnails
// without parsing any GPX (the Activities list taught that lesson: parse on open = minutes).

const DIR = `${RNFS.DocumentDirectoryPath}/route_library`;
const INDEX = `${DIR}/index.json`;
const PREVIEW_POINTS = 120;

export interface LibraryRoute {
  id: string;
  name: string;
  created: number;              // ms since epoch
  distanceMeters: number;
  ascentMeters: number;
  hash: string;
  preview: { lat: number; lon: number }[];
}

async function readIndex(): Promise<LibraryRoute[]> {
  try {
    if (!(await RNFS.exists(INDEX))) return [];
    const v = JSON.parse(await RNFS.readFile(INDEX, 'utf8'));
    return Array.isArray(v) ? v : [];
  } catch {
    return [];
  }
}

async function writeIndex(items: LibraryRoute[]): Promise<void> {
  if (!(await RNFS.exists(DIR))) await RNFS.mkdir(DIR);
  const tmp = `${INDEX}.tmp`;
  await RNFS.writeFile(tmp, JSON.stringify(items), 'utf8');
  if (await RNFS.exists(INDEX)) await RNFS.unlink(INDEX);
  await RNFS.moveFile(tmp, INDEX);
}

// Content hash for "same file twice = one entry" (FNV-1a, 32-bit, over the GPX text). Not a
// security hash - only equality of files the user imports.
function hashText(s: string): string {
  let h = 0x811c9dc5;
  for (let i = 0; i < s.length; i++) {
    h ^= s.charCodeAt(i);
    h = Math.imul(h, 0x01000193) >>> 0;
  }
  return h.toString(16).padStart(8, '0') + ':' + s.length;
}

function thin(points: { lat: number; lon: number }[]): { lat: number; lon: number }[] {
  if (points.length <= PREVIEW_POINTS) return points.map(p => ({ lat: p.lat, lon: p.lon }));
  const step = (points.length - 1) / (PREVIEW_POINTS - 1);
  const out: { lat: number; lon: number }[] = [];
  for (let i = 0; i < PREVIEW_POINTS; i++) {
    const p = points[Math.round(i * step)];
    out.push({ lat: p.lat, lon: p.lon });
  }
  return out;
}

/** Every saved route, newest first. */
export async function listLibrary(): Promise<LibraryRoute[]> {
  const items = await readIndex();
  return items.slice().sort((a, b) => b.created - a.created);
}

/** Keeps a GPX in the library (the caller passes its already-parsed stats and points). Same
 *  content as an existing entry = that entry, renamed if a different name is given. */
export async function saveToLibrary(name: string, gpx: string,
                                    stats: { distanceM: number; ascentM: number; points: { lat: number; lon: number }[] })
                                    : Promise<{ id: string; existed: boolean }> {
  const items = await readIndex();
  const hash = hashText(gpx);
  const same = items.find(r => r.hash === hash);
  if (same) {
    if (name && name !== same.name) { same.name = name; await writeIndex(items); }
    return { id: same.id, existed: true };
  }
  const id = Date.now().toString(36) + Math.random().toString(36).slice(2, 6);
  if (!(await RNFS.exists(DIR))) await RNFS.mkdir(DIR);
  await RNFS.writeFile(`${DIR}/${id}.gpx`, gpx, 'utf8');
  items.push({ id, name: name || 'Route', created: Date.now(), distanceMeters: Math.round(stats.distanceM),
               ascentMeters: Math.round(stats.ascentM), hash, preview: thin(stats.points) });
  await writeIndex(items);
  return { id, existed: false };
}

/** A saved route's GPX text. */
export async function getLibraryGpx(id: string): Promise<string> {
  return RNFS.readFile(`${DIR}/${id}.gpx`, 'utf8');
}

export async function renameLibraryRoute(id: string, name: string): Promise<void> {
  const items = await readIndex();
  const r = items.find(x => x.id === id);
  if (!r || !name.trim()) return;
  r.name = name.trim();
  await writeIndex(items);
}

export async function deleteLibraryRoute(id: string): Promise<void> {
  const items = await readIndex();
  await writeIndex(items.filter(x => x.id !== id));
  await RNFS.unlink(`${DIR}/${id}.gpx`).catch(() => {});
}

/** Route points as a minimal GPX (a route from the planner or the watch, to keep or re-parse). */
export function pointsToGpx(name: string, points: { lat: number; lon: number; ele?: number | null }[]): string {
  const esc = (s: string) => s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const pts = points.map(p => `<trkpt lat="${p.lat}" lon="${p.lon}">${p.ele != null ? `<ele>${p.ele}</ele>` : ''}</trkpt>`);
  return `<?xml version="1.0" encoding="UTF-8"?><gpx version="1.1" creator="Sommet"><trk><name>${esc(name)}</name><trkseg>${pts.join('')}</trkseg></trk></gpx>`;
}
