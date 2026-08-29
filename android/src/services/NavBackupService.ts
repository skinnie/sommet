import RNFS from 'react-native-fs';
import { connect, disconnect, readRegion, saveFileAs, getDeviceInfo } from '../native/AmbitUsbModule';
import { readNavBases } from './MemoryMap';
import { isAmbit12 } from './AmbitSettingsService';
import { backupLegacyRoutes, restoreLegacyRoutes, backupLegacyWaypoints, restoreLegacyWaypoints, LegacyWaypoint } from './AmbitLegacyNav';
import { base64ToBytes, bytesToBase64 } from './Base64';

// v3.0 UI port (2026-08-09, "re do... backup to match entirely desktop") - real "Backup &
// Restore" card from desktop's own BackupPage.qml ("the backup that milestone 4 asked for
// and never had" - write_nav.py's own words), covering Routes+Waypoints together (the
// watch's whole navigation database), same scope as desktop's own mechanism.
//
// **Real, deliberate scope cut from desktop parity**: this is create-backup only. Desktop's
// own Restore button calls write_nav.py's `restore PREFIX --write`, which needs a raw
// region WRITE (not just read()) - a real, currently-nonexistent native capability on
// Android (jni_bridge.cpp/device_driver_ambit3.c have no generic raw-region-write function
// at all, only the specific typed writers: writeRoute/writeSettingsRaw/
// writeCustomModesRaw). Building that blind, the same session this app's other real
// flash-write bug (CustomModes' used-extent/commit fix) was found and fixed, is exactly the
// kind of new native write path that needs its own careful, deliberate treatment - not
// something to add as a side effect of a UI pass. Create-backup only needs readRegion(),
// already proven and already used this same way by exportNavigationToGpx().

const BACKUPS_DIR = `${RNFS.DocumentDirectoryPath}/backups`;

export interface BackupEntry {
  prefix: string;
  createdAt: number;
  legacy?: boolean;         // an Ambit1/2 backup (legacy-routes.bin / legacy-waypoints.json) - restorable
  legacyRoutes?: boolean;   // has a {prefix}_legacy-routes.bin
  legacyWaypoints?: boolean;// has a {prefix}_legacy-waypoints.json
}

// Ambit1/2 codenames - these use the legacy route region, not the SBEM nav regions.
const LEGACY_MODELS = ['Bluebird', 'Duck', 'Colibri', 'Greentit'];
const isLegacyModel = (model?: string) => !!model && LEGACY_MODELS.includes(model);

async function ensureDir(): Promise<void> {
  if (!(await RNFS.exists(BACKUPS_DIR))) {
    await RNFS.mkdir(BACKUPS_DIR);
  }
}

/** Reads the watch's navigation database and saves it locally. Ambit3/Traverse: Waypoints+Routes
 * SBEM regions (read-only, restore not built there). Ambit1/2: the legacy route region 0x041EB0
 * (restorable - see restoreNavBackup). `deviceModel` (from Home) selects the path. */
export async function createNavBackup(deviceModel?: string): Promise<void> {
  await ensureDir();
  const prefix = String(Date.now());
  if (isLegacyModel(deviceModel)) {
    // Back up routes AND waypoints (POIs) - the watch's whole nav database, matching desktop scope.
    const rb = await backupLegacyRoutes();            // self-connects; null when no routes
    if (rb) await RNFS.writeFile(`${BACKUPS_DIR}/${prefix}_legacy-routes.bin`, bytesToBase64(rb.bytes), 'base64');
    const wps = await backupLegacyWaypoints();        // self-connects; null when no waypoints
    if (wps) await RNFS.writeFile(`${BACKUPS_DIR}/${prefix}_legacy-waypoints.json`, JSON.stringify(wps), 'utf8');
    if (!rb && !wps) throw new Error('This watch has no routes or POIs to back up.');
    return;
  }
  await connect();
  try {
    const bases = await readNavBases();
    const [waypointsB64, routesB64] = await Promise.all([
      readRegion(bases.waypointBase, bases.waypointSize),
      readRegion(bases.routeBase, bases.routeSize),
    ]);
    await RNFS.writeFile(`${BACKUPS_DIR}/${prefix}_waypoints.bin`, waypointsB64, 'base64');
    await RNFS.writeFile(`${BACKUPS_DIR}/${prefix}_routes.bin`, routesB64, 'base64');
  } finally {
    await disconnect().catch(() => {});
  }
}

/** Restore a legacy route backup to the connected Ambit1/2 (destructive: replaces its routes).
 * Only legacy backups are restorable - Ambit3 restore needs a raw SBEM region write that doesn't
 * exist yet (see this file's header). */
export async function restoreNavBackup(prefix: string): Promise<{ routeCount: number; waypointCount: number }> {
  const routePath = `${BACKUPS_DIR}/${prefix}_legacy-routes.bin`;
  const wpPath = `${BACKUPS_DIR}/${prefix}_legacy-waypoints.json`;
  const hasRoutes = await RNFS.exists(routePath);
  const hasWaypoints = await RNFS.exists(wpPath);
  if (!hasRoutes && !hasWaypoints) {
    throw new Error('This backup cannot be restored (not a legacy nav backup).');
  }
  let waypointCount = 0;
  let routeCount = 0;
  // Waypoints first: their write clears the whole nav list (restoreLegacyWaypoints is route-safe on
  // its own), then restore the backed-up routes so the final state is exactly what was saved.
  if (hasWaypoints) {
    const wps: LegacyWaypoint[] = JSON.parse(await RNFS.readFile(wpPath, 'utf8'));
    waypointCount = (await restoreLegacyWaypoints(wps)).count;
  }
  if (hasRoutes) {
    const bytes = base64ToBytes(await RNFS.readFile(routePath, 'base64'));
    routeCount = (await restoreLegacyRoutes(bytes)).routeCount;
  }
  return { routeCount, waypointCount };
}

/** Every backup created so far, newest first - grouped by the shared timestamp prefix. */
export async function listNavBackups(): Promise<BackupEntry[]> {
  await ensureDir();
  const files = await RNFS.readDir(BACKUPS_DIR);
  // prefix -> {sbem, legacyRoutes, legacyWaypoints}
  const seen = new Map<string, { sbem: boolean; legacyRoutes: boolean; legacyWaypoints: boolean }>();
  const get = (p: string) => {
    let e = seen.get(p);
    if (!e) { e = { sbem: false, legacyRoutes: false, legacyWaypoints: false }; seen.set(p, e); }
    return e;
  };
  for (const f of files) {
    const m = /^(\d+)_(waypoints|routes)\.bin$/.exec(f.name);
    if (m) { get(m[1]).sbem = true; continue; }
    const lr = /^(\d+)_legacy-routes\.bin$/.exec(f.name);
    if (lr) { get(lr[1]).legacyRoutes = true; continue; }
    const lw = /^(\d+)_legacy-waypoints\.json$/.exec(f.name);
    if (lw) { get(lw[1]).legacyWaypoints = true; }
  }
  return Array.from(seen.entries())
    .map(([prefix, e]) => ({
      prefix,
      createdAt: parseInt(prefix, 10),
      legacy: e.legacyRoutes || e.legacyWaypoints,
      legacyRoutes: e.legacyRoutes,
      legacyWaypoints: e.legacyWaypoints,
    }))
    .sort((a, b) => b.createdAt - a.createdAt);
}

export function backupsFolderPath(): string {
  return BACKUPS_DIR;
}

/**
 * "Backup database to folder" (André, 2026-08-16) - the keyless replacement for the cloud-OAuth
 * upload. Reads the same two nav regions createNavBackup() does, bundles them into one file, and
 * hands it to the system "Save as" picker so the user can drop it in any folder - point it at a
 * Dropbox/OneDrive/Drive sync folder and it syncs, no keys, no sign-in.
 *
 * One bundled file (not the two raw .bin) so it's a single tap through one picker; Android backup
 * is export-only anyway (no raw-region write exists here - see this file's header), so this is a
 * safety copy, with both regions recoverable from the base64 inside. Throws SAVE_AS_CANCELLED if
 * the user backs out of the picker - callers treat that as a no-op, not an error.
 */
export async function backupNavToFile(): Promise<void> {
  await connect();
  try {
    // Per-device region bases from the watch's own 0x0b21 map (not the hardcoded Ambit3
    // offsets), so a Traverse backs up its real Waypoints/Routes regions.
    const bases = await readNavBases();
    const [waypointsB64, routesB64] = await Promise.all([
      readRegion(bases.waypointBase, bases.waypointSize),
      readRegion(bases.routeBase, bases.routeSize),
    ]);
    const bundle = JSON.stringify({
      format: 'ambit-nav-backup',
      version: 1,
      createdAt: Date.now(),
      routes_b64: routesB64,
      waypoints_b64: waypointsB64,
    });
    const d = new Date();
    const pad = (n: number) => String(n).padStart(2, '0');
    const name = `Sommet-nav-backup-${d.getFullYear()}${pad(d.getMonth() + 1)}${pad(d.getDate())}`
      + `-${pad(d.getHours())}${pad(d.getMinutes())}.ambitbak`;
    const tmp = `${RNFS.CachesDirectoryPath}/${name}`;
    await RNFS.writeFile(tmp, bundle, 'utf8');
    try {
      await saveFileAs(tmp, name, 'application/octet-stream');
    } finally {
      RNFS.unlink(tmp).catch(() => {});
    }
  } finally {
    await disconnect().catch(() => {});
  }
}
