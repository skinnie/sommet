import RNFS from 'react-native-fs';

const ACTIVITIES_DIR = `${RNFS.DocumentDirectoryPath}/activities`;

/**
 * The file name (without extension) an activity id is stored under. react-native-fs takes a path
 * containing ':' for a URL ("/data/.../activities/icu" becomes the scheme), so every write failed
 * with "No content provider" and intervals.icu imports (ids "icu:<n>") never got a track
 * (tablet, 2026-10-23). The other ids (dates, garmin_/coros_/c406_/bryton_/sommet_) only use
 * these characters already, so their files keep the names they have.
 */
export function activityFileStem(id: string): string {
  return id.replace(/[^\w.-]/g, '_');
}

/** S'assure que le dossier activities/ existe. */
async function ensureDir(): Promise<void> {
  const exists = await RNFS.exists(ACTIVITIES_DIR);
  if (!exists) await RNFS.mkdir(ACTIVITIES_DIR);
}

/**
 * Écrit un fichier GPX sur le téléphone.
 * @param id      Identifiant unique du log (ex: "20240615_093000")
 * @param gpxXml  Contenu GPX en string
 * @returns       Chemin absolu du fichier écrit, ou null si déjà existant
 */
export async function writeGpxFile(id: string, gpxXml: string, overwrite = false): Promise<string | null> {
  await ensureDir();
  const path = `${ACTIVITIES_DIR}/${activityFileStem(id)}.gpx`;
  if (!overwrite && await RNFS.exists(path)) return null; // doublon, ne pas écraser
  await RNFS.writeFile(path, gpxXml, 'utf8');
  return path;
}

/**
 * Writes the native FIT for a move next to its GPX (<id>.fit), decoding the base64 the native
 * layer produced. The device builds a richer FIT than the GPX→FIT fallback (it has every sensor
 * channel, and it works for indoor moves with no track), so MapScreen prefers this file when it
 * exists. `base64` empty → nothing written (the move had no native FIT). Returns the path written,
 * or null.
 */
export async function writeFitFile(id: string, base64: string, overwrite = false): Promise<string | null> {
  if (!base64) return null;
  await ensureDir();
  const path = `${ACTIVITIES_DIR}/${activityFileStem(id)}.fit`;
  if (!overwrite && await RNFS.exists(path)) return null;
  await RNFS.writeFile(path, base64, 'base64');
  return path;
}

/** Whether an activity with this id is already in the library (checked before a slow device
 *  download, e.g. a Magene ride over BLE). */
export async function activityExists(id: string): Promise<boolean> {
  return RNFS.exists(`${ACTIVITIES_DIR}/${activityFileStem(id)}.gpx`);
}

/** The <id>.fit path for an activity's <id>.gpx path, if that FIT file exists on disk. */
export async function fitPathForGpx(gpxPath: string): Promise<string | null> {
  const path = gpxPath.replace(/\.gpx$/, '.fit');
  return (await RNFS.exists(path)) ? path : null;
}

/** sync/08: drop the FIT kept next to a GPX (its track changed on another device). */
export async function removeFitForGpx(gpxPath: string): Promise<void> {
  const p = await fitPathForGpx(gpxPath);
  if (p) await RNFS.unlink(p).catch(() => undefined);
}

/** A move's FIT next to its GPX, as bytes (base64 from disk); null when there is none. */
export async function readFitForGpx(gpxPath: string): Promise<string | null> {
  const p = await fitPathForGpx(gpxPath);
  if (!p) return null;
  try { return await RNFS.readFile(p, 'base64'); } catch { return null; }
}

/** Lit un fichier GPX depuis le stockage local. */
export async function readGpxFile(path: string): Promise<string> {
  return RNFS.readFile(path, 'utf8');
}

/** Supprime un fichier GPX du stockage local. */
export async function deleteGpxFile(path: string): Promise<void> {
  if (await RNFS.exists(path)) await RNFS.unlink(path);
}

/** Liste tous les fichiers GPX présents dans le dossier activities/. */
export async function listGpxFiles(): Promise<string[]> {
  await ensureDir();
  const items = await RNFS.readDir(ACTIVITIES_DIR);
  return items
    .filter(item => item.name.endsWith('.gpx'))
    .map(item => item.path);
}

/** Bytes a move's Track takes here: its GPX plus the FIT kept next to it (light library, sync/13:
 *  what counts against the space for opened older moves). 0 when neither is there. */
export async function trackFileBytes(gpxPath: string): Promise<number> {
  let total = 0;
  for (const p of [gpxPath, gpxPath.replace(/\.gpx$/, '.fit')]) {
    try { if (await RNFS.exists(p)) total += Number((await RNFS.stat(p)).size) || 0; } catch { /* gone */ }
  }
  return total;
}

/** Whether a stored file is still there. */
export async function fileExists(path: string): Promise<boolean> {
  try { return !!path && await RNFS.exists(path); } catch { return false; }
}
