import RNFS from 'react-native-fs';
import { connect, disconnect, readRegion, saveFileAs, getDeviceInfo } from '../native/AmbitUsbModule';
import { readNavBases } from './MemoryMap';
import { isAmbit12 } from './AmbitSettingsService';
import { backupLegacyRoutes, restoreLegacyRoutes } from './AmbitLegacyNav';
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
  legacy?: boolean;     // an Ambit1/2 route-region backup ({prefix}_legacy-routes.bin) - restorable
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
    const rb = await backupLegacyRoutes();          // self-connects; null when no routes
    if (!rb) throw new Error('This watch has no routes to back up.');
    await RNFS.writeFile(`${BACKUPS_DIR}/${prefix}_legacy-routes.bin`, bytesToBase64(rb.bytes), 'base64');
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
export async function restoreNavBackup(prefix: string): Promise<{ routeCount: number }> {
  const path = `${BACKUPS_DIR}/${prefix}_legacy-routes.bin`;
  if (!(await RNFS.exists(path))) throw new Error('This backup cannot be restored (not a legacy route backup).');
  const bytes = base64ToBytes(await RNFS.readFile(path, 'base64'));
  return restoreLegacyRoutes(bytes);
}

/** Every backup created so far, newest first - grouped by the shared timestamp prefix. */
export async function listNavBackups(): Promise<BackupEntry[]> {
  await ensureDir();
  const files = await RNFS.readDir(BACKUPS_DIR);
  const prefixes = new Map<string, boolean>(); // prefix -> legacy?
  for (const f of files) {
    const m = /^(\d+)_(waypoints|routes)\.bin$/.exec(f.name);
    if (m) { if (!prefixes.has(m[1])) prefixes.set(m[1], false); continue; }
    const l = /^(\d+)_legacy-routes\.bin$/.exec(f.name);
    if (l) prefixes.set(l[1], true);
  }
  return Array.from(prefixes.entries())
    .map(([prefix, legacy]) => ({ prefix, createdAt: parseInt(prefix, 10), legacy }))
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
