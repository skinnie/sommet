import RNFS from 'react-native-fs';

const ACTIVITIES_DIR = `${RNFS.DocumentDirectoryPath}/activities`;

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
  const path = `${ACTIVITIES_DIR}/${id}.gpx`;
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
  const path = `${ACTIVITIES_DIR}/${id}.fit`;
  if (!overwrite && await RNFS.exists(path)) return null;
  await RNFS.writeFile(path, base64, 'base64');
  return path;
}

/** Whether an activity with this id is already in the library (checked before a slow device
 *  download, e.g. a Magene ride over BLE). */
export async function activityExists(id: string): Promise<boolean> {
  return RNFS.exists(`${ACTIVITIES_DIR}/${id}.gpx`);
}

/** The <id>.fit path for an activity's <id>.gpx path, if that FIT file exists on disk. */
export async function fitPathForGpx(gpxPath: string): Promise<string | null> {
  const path = gpxPath.replace(/\.gpx$/, '.fit');
  return (await RNFS.exists(path)) ? path : null;
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
