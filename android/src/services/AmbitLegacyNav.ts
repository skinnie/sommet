import { readLegacyRegion, writeLegacyRegion, writeLegacyWaypoints, readLegacyNav, connect, disconnect, isBleTransportActive } from '../native/AmbitUsbModule';
import { base64ToBytes, bytesToBase64 } from './Base64';

// Ambit1/2 (Bluebird) route-region BACKUP + RESTORE (2026-08-27, André: "legacy nav backup +
// restore"). The legacy family has no SBEM 0x0b21 memory map, so the generic NavBackupService
// (which reads region bases from that map) can't touch it - this reads/writes the route region
// directly. Ground truth: docs/ambit1_route_region.md + tools/legacy_route.py + the real
// SuuntoLink<->watch capture assets/pcap/ambit2_suuntolink_settings_sportmodes.pcap.
//
// Route region 0x041EB0, nested layout: [32 B head][route table][points @ +2432]. The head's u32
// at offset 8 is the total routepoint count; each point is 8 bytes, so the USED extent is
// 2432 + point_count*8 (matches the pcap's 15992 B for 1695 points exactly). We back up / restore
// exactly that extent - the watch REJECTS an over-long write (seen on the sport-mode region).
//
// Restore writes via the native writeLegacyRegion (0x0b16 chunks + 0x0b18 COMMIT tail). The route
// region's commit tail `extra` is the constant 0xFFFFFA1A - proven content-independent across two
// captures with different watches/routes (see [[ambit_app_legacy_write_commit_tail]]).

const ROUTE_REGION = 0x041eb0;
const HEAD_MAGIC = 0x3008;       // openambit "unknown1, always 12296"
const POINTS_OFFSET = 2432;      // 0x042830 - 0x041EB0
const POINT_LEN = 8;
const ROUTE_COMMIT_EXTRA = 0xfffffa1a;
const MAX_REGION = 0x30000;      // hard cap well under the gap to the next region (0x0704E0)

function u16(b: Uint8Array, o: number): number { return b[o] | (b[o + 1] << 8); }
function u32(b: Uint8Array, o: number): number {
  return (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;
}

export interface LegacyRouteBackup { bytes: Uint8Array; routeCount: number; pointCount: number }

/** Read the route region's USED extent off the connected watch. Returns null if there are no
 * routes (an empty/unwritten region - nothing to back up). Assumes a connection is open. */
async function readLegacyRouteRegion(): Promise<LegacyRouteBackup | null> {
  const headB64 = await readLegacyRegion(ROUTE_REGION, 64);
  if (!headB64) throw new Error('Could not read the route region.');
  const head = base64ToBytes(headB64);
  if (head.length < 32 || u16(head, 0) !== HEAD_MAGIC) return null; // no routes on this watch
  const routeCount = u16(head, 4);
  const pointCount = u32(head, 8);
  const used = POINTS_OFFSET + pointCount * POINT_LEN;
  if (used <= POINTS_OFFSET || used > MAX_REGION) return null; // implausible - treat as empty
  const b64 = await readLegacyRegion(ROUTE_REGION, used);
  const bytes = base64ToBytes(b64);
  if (bytes.length < used) throw new Error(`Short route read (${bytes.length}/${used}).`);
  return { bytes: bytes.subarray(0, used), routeCount, pointCount };
}

/** Back up the connected Ambit1/2's routes (opens its own short-lived USB connection). Returns
 * the raw region bytes to save, or null when the watch has no routes. */
export async function backupLegacyRoutes(): Promise<LegacyRouteBackup | null> {
  const overBle = isBleTransportActive();
  if (!overBle) await connect();
  try {
    return await readLegacyRouteRegion();
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

/** Restore previously backed-up route bytes to the watch (destructive: replaces all routes).
 * Writes the exact backed-up extent + the 0x0b18 commit tail. Verifies by re-reading the head. */
export async function restoreLegacyRoutes(bytes: Uint8Array): Promise<{ routeCount: number }> {
  if (bytes.length < 32 || u16(bytes, 0) !== HEAD_MAGIC) {
    throw new Error('That backup is not a valid route region.');
  }
  const overBle = isBleTransportActive();
  if (!overBle) await connect();
  try {
    const ok = await writeLegacyRegion(ROUTE_REGION, bytesToBase64(bytes), ROUTE_COMMIT_EXTRA);
    if (!ok) throw new Error('The watch did not accept the route write.');
    // verify: re-read the head, confirm the route count matches what we wrote
    const back = base64ToBytes(await readLegacyRegion(ROUTE_REGION, 64));
    if (back.length < 32 || u16(back, 0) !== HEAD_MAGIC || u16(back, 4) !== u16(bytes, 4)) {
      throw new Error('Route region did not read back as written.');
    }
    return { routeCount: u16(bytes, 4) };
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

// ─── Waypoints (POIs) ────────────────────────────────────────────────────────
// Legacy waypoints are NOT a flash region - they live in the command-accessible nav list
// (readLegacyNav / 0x0b02/0x0b03). Write is the command path (native writeLegacyWaypoints:
// 0x0b1b write_start, 0x0b04 nav_memory_delete, 0x0b05 per waypoint). Because 0x0b04 clears the
// whole nav list and can drop the route region too, restoreLegacyWaypoints snapshots + re-writes
// the routes around the waypoint write, so restoring POIs never destroys the watch's routes.

export interface LegacyWaypoint {
  name: string;
  routeName: string;
  lat_e7: number;
  lon_e7: number;
  type: number;                 // raw device type (0..17); round-tripped unchanged
  ctime_year?: number; ctime_month?: number; ctime_day?: number;
  ctime_hour?: number; ctime_minute?: number; ctime_second?: number;
}

const WP_RECORD_LEN = 48;

function putLatin1(out: Uint8Array, off: number, s: string, max: number): void {
  const n = Math.min(s.length, max - 1); // leave room for a NUL within the field
  for (let i = 0; i < n; i++) out[off + i] = s.charCodeAt(i) & 0xff;
}

/** Encode waypoints to the flat 48-byte-per-record blob the native writeLegacyWaypoints expects. */
export function encodeWaypoints(wps: LegacyWaypoint[]): Uint8Array {
  const out = new Uint8Array(wps.length * WP_RECORD_LEN);
  const dv = new DataView(out.buffer);
  wps.forEach((w, i) => {
    const o = i * WP_RECORD_LEN;
    putLatin1(out, o + 0, w.name ?? '', 16);
    putLatin1(out, o + 16, w.routeName ?? '', 16);
    dv.setInt32(o + 32, w.lat_e7 | 0, true);
    dv.setInt32(o + 36, w.lon_e7 | 0, true);
    out[o + 40] = w.type & 0xff;
    dv.setUint16(o + 41, (w.ctime_year ?? 0) & 0xffff, true);
    out[o + 43] = (w.ctime_month ?? 0) & 0xff;
    out[o + 44] = (w.ctime_day ?? 0) & 0xff;
    out[o + 45] = (w.ctime_hour ?? 0) & 0xff;
    out[o + 46] = (w.ctime_minute ?? 0) & 0xff;
    out[o + 47] = (w.ctime_second ?? 0) & 0xff;
  });
  return out;
}

/** Back up the connected Ambit1/2's waypoints (POIs). Opens its own short-lived connection.
 * Returns the waypoint list to save, or null when the watch has none. */
export async function backupLegacyWaypoints(): Promise<LegacyWaypoint[] | null> {
  const overBle = isBleTransportActive();
  if (!overBle) await connect();
  try {
    const nav = JSON.parse(await readLegacyNav());
    const wps: any[] = nav?.waypoints ?? [];
    if (wps.length === 0) return null;
    return wps.map((w) => ({
      name: String(w.name ?? ''),
      routeName: String(w.routeName ?? ''),
      lat_e7: Number(w.lat_e7) | 0,
      lon_e7: Number(w.lon_e7) | 0,
      type: Number(w.type) & 0xff,
      ctime_year: Number(w.ctime_year ?? 0),
      ctime_month: Number(w.ctime_month ?? 0),
      ctime_day: Number(w.ctime_day ?? 0),
      ctime_hour: Number(w.ctime_hour ?? 0),
      ctime_minute: Number(w.ctime_minute ?? 0),
      ctime_second: Number(w.ctime_second ?? 0),
    }));
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

/** Restore waypoints (POIs) to the connected Ambit1/2 (destructive: replaces all POIs). Route-safe:
 * snapshots the route region, writes the waypoints (which clears the whole nav list), then re-writes
 * the routes so they survive. Verifies the waypoint count read back. */
export async function restoreLegacyWaypoints(wps: LegacyWaypoint[]): Promise<{ count: number }> {
  const overBle = isBleTransportActive();
  if (!overBle) await connect();
  try {
    // 1. snapshot current routes (the waypoint write's 0x0b04 may clear them)
    const routeSnap = await readLegacyRouteRegion().catch(() => null);
    // 2. write the waypoints (write_start + nav_memory_delete + per-waypoint)
    const n = await writeLegacyWaypoints(bytesToBase64(encodeWaypoints(wps)));
    if (n < 0) throw new Error('The watch did not accept the waypoint write.');
    // 3. re-write the routes we snapshotted, so restoring POIs never destroys routes
    if (routeSnap) {
      const ok = await writeLegacyRegion(ROUTE_REGION, bytesToBase64(routeSnap.bytes), ROUTE_COMMIT_EXTRA);
      if (!ok) throw new Error('POIs restored, but re-writing the routes failed - check the watch.');
    }
    // 4. verify: re-read the nav list, confirm the waypoint count matches
    const back = JSON.parse(await readLegacyNav());
    const got = (back?.waypoints ?? []).length;
    if (got !== wps.length) throw new Error(`Waypoints did not read back (${got}/${wps.length}).`);
    return { count: wps.length };
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}
