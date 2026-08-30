import RNFS from 'react-native-fs';
import { getDb } from '../database/db';
import { saveFileAs, pickGpxFile } from '../native/AmbitUsbModule';

/**
 * Back up (and restore) THIS app's own data - your synced activities (with their GPX/FIT
 * files) and your gear - to a file you choose (André, 2026-08-30: "can't choose where to save
 * the database"). Every other card on the Backup screen saves the connected WATCH's regions;
 * this is the app's own database, which had no backup path at all before.
 *
 * Desktop keeps GPX/FIT as columns inside activities.db, so its backup is one file copy. Mobile
 * keeps only metadata in SQLite (ambitsync.db) and the real GPX/FIT as loose files under
 * DocumentDirectory/activities - so a portable backup here is a self-contained bundle: a dump of
 * every user table PLUS the activity files, base64/utf8 inline. Dumping rows (not copying the raw
 * .db) makes it portable across installs and SQLite versions, and lets restore rewrite the
 * absolute gpx_path onto whatever DocumentDirectory this install happens to have.
 *
 * Save/restore reuse the platform pickers already wired for firmware backup and GPX import:
 * saveFileAs (SAF CREATE_DOCUMENT on Android, an export UIDocumentPicker on iOS) and
 * pickGpxFile (a generic OPEN_DOCUMENT / import picker - its all-types wildcard + UTTypeData fallback makes a
 * .sommetdata file selectable on both). No new native surface on Android; iOS only needed
 * saveFileAs, which this ships alongside.
 */

export const BUNDLE_FORMAT = 'sommet-appdata-backup';
export const BUNDLE_VERSION = 1;

const ACTIVITIES_DIR = `${RNFS.DocumentDirectoryPath}/activities`;

// SQLite's own bookkeeping tables - never dumped or restored.
const SKIP_TABLES = new Set(['sqlite_sequence', 'sqlite_stat1', 'sqlite_stat4', 'android_metadata']);

export interface BundleFile {
  name: string;                 // basename inside activities/, e.g. "20240615_093000.gpx"
  enc: 'utf8' | 'base64';
  data: string;
}
export interface AppDataBundle {
  format: string;
  version: number;
  createdAt: number;            // ms
  tables: Record<string, any[]>;
  files: BundleFile[];
}

// ─── Pure core (unit-tested; no IO) ─────────────────────────────────────────

/** A valid SQLite identifier we're willing to interpolate into SQL. Table/column names in a
 * restore bundle are attacker-influenced (it's a file the user picked), so anything that isn't a
 * plain identifier is refused rather than concatenated into a statement. */
export function isSafeIdent(name: unknown): name is string {
  return typeof name === 'string' && /^[A-Za-z_][A-Za-z0-9_]*$/.test(name);
}

/** Build an `INSERT OR REPLACE` for one row, or null if the table/any column name is unsafe.
 * Restore is a merge by primary key (REPLACE), so it never wipes rows the bundle doesn't mention. */
export function buildInsert(table: string, row: Record<string, any>):
  { sql: string; params: any[] } | null {
  if (!isSafeIdent(table)) return null;
  const cols = Object.keys(row);
  if (cols.length === 0 || !cols.every(isSafeIdent)) return null;
  const placeholders = cols.map(() => '?').join(', ');
  const sql = `INSERT OR REPLACE INTO ${table} (${cols.join(', ')}) VALUES (${placeholders})`;
  const params = cols.map(c => {
    const v = row[c];
    // SQLite bindings take primitives; a nested object/array (shouldn't occur in these tables)
    // is JSON-stringified rather than silently coerced to "[object Object]".
    return (v !== null && typeof v === 'object') ? JSON.stringify(v) : v;
  });
  return { sql, params };
}

/** Point an activities row's gpx_path at the CURRENT install's activities dir. The column stores
 * an absolute path, and DocumentDirectory differs across installs/devices, so a restored row's
 * old path would dangle. The file is restored by basename, so recompute from id. */
export function rewriteActivityPath<T extends Record<string, any>>(row: T, activitiesDir: string): T {
  if (row && typeof row.id === 'string' && 'gpx_path' in row) {
    return { ...row, gpx_path: `${activitiesDir}/${row.id}.gpx` };
  }
  return row;
}

/** Whether a parsed object is a bundle this version can restore. */
export function isRestorableBundle(b: any): b is AppDataBundle {
  return !!b && b.format === BUNDLE_FORMAT && typeof b.version === 'number'
    && b.version <= BUNDLE_VERSION && !!b.tables && typeof b.tables === 'object'
    && Array.isArray(b.files);
}

function timestampName(now = new Date()): string {
  const pad = (n: number) => String(n).padStart(2, '0');
  return `Sommet-data-${now.getFullYear()}${pad(now.getMonth() + 1)}${pad(now.getDate())}`
    + `-${pad(now.getHours())}${pad(now.getMinutes())}.sommetdata`;
}

// ─── Backup ─────────────────────────────────────────────────────────────────

/** Read every user table + every activity file into an in-memory bundle. */
export async function buildAppDataBundle(): Promise<AppDataBundle> {
  const db = await getDb();
  const [meta] = await db.executeSql(
    "SELECT name FROM sqlite_master WHERE type='table'"
  );
  const tables: Record<string, any[]> = {};
  for (let i = 0; i < meta.rows.length; i++) {
    const name = meta.rows.item(i).name as string;
    if (SKIP_TABLES.has(name) || !isSafeIdent(name)) continue;
    const [res] = await db.executeSql(`SELECT * FROM ${name}`);
    const rows: any[] = [];
    for (let r = 0; r < res.rows.length; r++) rows.push(res.rows.item(r));
    tables[name] = rows;
  }

  const files: BundleFile[] = [];
  if (await RNFS.exists(ACTIVITIES_DIR)) {
    for (const item of await RNFS.readDir(ACTIVITIES_DIR)) {
      if (!item.isFile()) continue;
      if (item.name.endsWith('.gpx')) {
        files.push({ name: item.name, enc: 'utf8', data: await RNFS.readFile(item.path, 'utf8') });
      } else if (item.name.endsWith('.fit')) {
        files.push({ name: item.name, enc: 'base64', data: await RNFS.readFile(item.path, 'base64') });
      }
    }
  }

  return { format: BUNDLE_FORMAT, version: BUNDLE_VERSION, createdAt: Date.now(), tables, files };
}

export interface BackupResult { activities: number; gear: number; files: number }

/** Build the bundle, write it to a temp file, and hand it to the system "Save as" picker.
 * Throws SAVE_AS_CANCELLED (via the native picker) if the user backs out - callers treat that as
 * a no-op, not an error, exactly like the nav-backup and firmware-download flows. */
export async function backupAppDataToFile(): Promise<BackupResult> {
  const bundle = await buildAppDataBundle();
  const name = timestampName();
  const tmp = `${RNFS.CachesDirectoryPath}/${name}`;
  await RNFS.writeFile(tmp, JSON.stringify(bundle), 'utf8');
  try {
    await saveFileAs(tmp, name, 'application/json');
  } finally {
    RNFS.unlink(tmp).catch(() => {});
  }
  return {
    activities: bundle.tables.activities?.length ?? 0,
    gear: bundle.tables.gear?.length ?? 0,
    files: bundle.files.length,
  };
}

// ─── Restore ────────────────────────────────────────────────────────────────

export interface RestoreResult { activities: number; gear: number; files: number; tables: number }

/** Write a bundle's files + rows into this install. Merge by primary key (INSERT OR REPLACE), so
 * it restores what's in the file without deleting unrelated newer rows. Files are restored first,
 * so a restored activity row always has its GPX/FIT on disk. */
export async function applyAppDataBundle(bundle: AppDataBundle): Promise<RestoreResult> {
  if (!isRestorableBundle(bundle)) {
    throw new Error('This file is not a Sommet app-data backup.');
  }
  const db = await getDb();  // ensures every table exists before we insert

  if (!(await RNFS.exists(ACTIVITIES_DIR))) await RNFS.mkdir(ACTIVITIES_DIR);
  let fileCount = 0;
  for (const f of bundle.files) {
    // Guard against path traversal from a hand-edited bundle - restore only into activities/.
    if (f.name.includes('/') || f.name.includes('\\') || f.name.includes('..')) continue;
    await RNFS.writeFile(`${ACTIVITIES_DIR}/${f.name}`, f.data, f.enc === 'base64' ? 'base64' : 'utf8');
    fileCount++;
  }

  let activities = 0, gear = 0, tableCount = 0;
  for (const [table, rows] of Object.entries(bundle.tables)) {
    if (SKIP_TABLES.has(table) || !isSafeIdent(table) || !Array.isArray(rows)) continue;
    let touched = 0;
    for (const raw of rows) {
      if (!raw || typeof raw !== 'object') continue;
      const row = table === 'activities' ? rewriteActivityPath(raw, ACTIVITIES_DIR) : raw;
      const stmt = buildInsert(table, row);
      if (!stmt) continue;
      try {
        await db.executeSql(stmt.sql, stmt.params);
        touched++;
      } catch {
        // A row referencing a column this install's schema doesn't have (an older/newer bundle)
        // is skipped rather than aborting the whole restore.
      }
    }
    if (touched > 0) tableCount++;
    if (table === 'activities') activities = touched;
    if (table === 'gear') gear = touched;
  }

  return { activities, gear, files: fileCount, tables: tableCount };
}

/** Full restore flow: pick a bundle file, parse it, apply it. Throws GPX_PICK_CANCELLED (from the
 * native picker) if the user backs out - callers treat that as a no-op. */
export async function restoreAppDataFromFile(): Promise<RestoreResult> {
  const path = await pickGpxFile();   // generic open-document picker; a .sommetdata is selectable
  const text = await RNFS.readFile(path, 'utf8');
  let bundle: any;
  try {
    bundle = JSON.parse(text);
  } catch {
    throw new Error('This file could not be read as a Sommet backup.');
  }
  return applyAppDataBundle(bundle);
}
