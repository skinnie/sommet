import { readLegacyRegion, writeLegacyRegion, connect, disconnect, isBleTransportActive, getDeviceInfo } from '../native/AmbitUsbModule';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { saveWatchBackup } from './WatchBackup';

// Ambit 1/2 (Bluebird) sport-mode READ - the one thing openambit can't do for this family
// (it only blind-writes). Ported from the desktop tools/legacy_sport_modes.py, validated
// byte-for-byte against a real Ambit2. The Ambit3 CustomModes path (readCustomModesRaw, a
// 12288-byte SBEM region) does NOT apply here and throws on an Ambit2 - this reads region
// 0x2000's nested TLV instead (readRegion is generic 0x0b17, no new native code).
//
// Region 0x2000, nested [u16 tag][u16 len][body]: 0x0003 root / 0x0100 modes / 0x0101 one
// mode / 0x0102 its settings blob / 0x0105-0x010a displays / 0x0200,0x0210 multisport
// groups. The settings blob's length picks the field table: 90 bytes = the Ambit2
// (openambit's ambit_sport_mode_settings_t), 76 = the Ambit1 (docs/ambit1_sport_mode_format.md
// section 4). Until 2026-11-26 the 90-byte table was read and written on Ambit1 blobs too: the
// pods came out of the GPS interval and an autolap edit landed in the low HR limit.

const REGION_ADDR = 0x2000;
const REGION_BYTES = 16384;
const TAG_SETTINGS = 0x0102;
const CONTAINERS = new Set([
  0x0003, 0x0100, 0x0101, 0x0105, 0x0106, 0x0107, 0x0108, 0x0109, 0x0200, 0x0210,
]);
const POD = { hrBelt: 0x0003, powerPod: 0x0040, cadencePod: 0x0080, footPod: 0x0100, bikePod: 0x0800 };

const AMBIT1_BLOB = 76;
const AMBIT2_BLOB = 90;
/** Offsets of the fields Sommet reads and edits, by settings-blob length. Same table as
 * tools/legacy_sport_modes.py (_FIELDS, _FIELDS_AMBIT1, LAYOUTS). */
const LAYOUTS: Record<number, {
  pods: number; altiBaro: number; gps: number; rec: number; autolap: number;
  hrMax: number; hrMin: number; useHr: number | null; modeId: number | null; hrBelt: number;
}> = {
  [AMBIT1_BLOB]: { pods: 18, altiBaro: 20, gps: 22, rec: 24, autolap: 26, hrMax: 28, hrMin: 30,
    useHr: null, modeId: null, hrBelt: 0x0001 },
  [AMBIT2_BLOB]: { pods: 22, altiBaro: 24, gps: 26, rec: 28, autolap: 30, hrMax: 32, hrMin: 34,
    useHr: 36, modeId: 18, hrBelt: 0x0003 },
};

export interface LegacyMode {
  name: string;
  activityId: number;
  modeId: number;
  gpsInterval: number;
  recordingInterval: number;
  autolapM: number;
  altiBaroMode: number;
  heartrateMin: number;
  heartrateMax: number;
  useHrLimits: boolean;
  useHw: number; // raw hrbelt_and_pods bitfield (maps to ExerciseMode.useHw)
  hrBelt: boolean;
  footPod: boolean;
  bikePod: boolean;
  powerPod: boolean;
  cadencePod: boolean;
}

function u16(b: Uint8Array, o: number): number {
  return b[o] | (b[o + 1] << 8);
}

function decodeSettings(blob: Uint8Array): LegacyMode {
  let name = '';
  for (let i = 0; i < 16 && blob[i]; i++) name += String.fromCharCode(blob[i]); // latin1
  // Any length other than the Ambit1's is read with the Ambit2 table (the old behaviour).
  const lay = LAYOUTS[blob.length === AMBIT1_BLOB ? AMBIT1_BLOB : AMBIT2_BLOB];
  const at = (o: number) => (o + 2 <= blob.length ? u16(blob, o) : 0);
  const pods = at(lay.pods);
  const hrMax = at(lay.hrMax), hrMin = at(lay.hrMin);
  return {
    name,
    activityId: u16(blob, 16),
    modeId: lay.modeId === null ? 0 : at(lay.modeId),
    altiBaroMode: at(lay.altiBaro),
    gpsInterval: at(lay.gps),
    recordingInterval: at(lay.rec),
    autolapM: at(lay.autolap),
    heartrateMax: hrMax,
    heartrateMin: hrMin,
    // The Ambit1 has no use_heartrate_limits field: limits of 0/0 are its "off".
    useHrLimits: lay.useHr === null ? (hrMax !== 0 || hrMin !== 0) : at(lay.useHr) !== 0,
    useHw: pods,
    hrBelt: (pods & lay.hrBelt) !== 0,
    powerPod: (pods & POD.powerPod) !== 0,
    cadencePod: (pods & POD.cadencePod) !== 0,
    footPod: (pods & POD.footPod) !== 0,
    bikePod: (pods & POD.bikePod) !== 0,
  };
}

function parseRegion(data: Uint8Array): LegacyMode[] {
  const modes: LegacyMode[] = [];
  const walk = (b: Uint8Array) => {
    let o = 0;
    while (o + 4 <= b.length) {
      const tag = u16(b, o);
      const ln = u16(b, o + 2);
      const body = b.subarray(o + 4, o + 4 + ln);
      if (body.length !== ln) break; // truncated tail
      // The settings blob is 90 bytes on the Ambit2 but only 76 on the Ambit1 (five capabilities
      // dropped - see tools/legacy_sport_modes.py). Accept both; the old >=90 gate silently
      // dropped every Ambit1 mode (André, 2026-08-27: "ambit 1 sports mode android don't work").
      if (tag === TAG_SETTINGS && body.length >= 38) modes.push(decodeSettings(body));
      else if (CONTAINERS.has(tag) && ln >= 4) walk(body);
      o += 4 + ln;
      if (tag === 0 && ln === 0) break;
    }
  };
  walk(data);
  return modes.filter(m => m.name);
}

/** Read the connected Ambit1/2's sport modes off region 0x2000. The watch must be connected. */
export async function readLegacySportModes(): Promise<LegacyMode[]> {
  const b64 = await readLegacyRegion(REGION_ADDR, REGION_BYTES);
  if (!b64) return [];
  return parseRegion(base64ToBytes(b64));
}

// ─── WRITE (2026-08-27, André: "go for both"; Ambit1 layout and SuuntoLink's extent 2026-11-26) ──
// Editing a field is a read-modify-write: read the region, patch the target mode's 0x0102 settings
// blob in place, write it back (native writeLegacyRegion does the 0x0b16 chunks + the 0x0b18
// commit tail), re-read to confirm. Everything except the patched fields is preserved byte-for-byte.
// Seen working on André's Ambit2, 2026-08-27 (autolap 0 -> 250, kept across an app restart).
//
// The 0x0b18 commit tail is MANDATORY: without it the watch acks the 0x0b16 chunks but discards
// them (they read back in-session then revert after a reconnect). Its `extra` word is 0xffffffff
// for this region. Both SuuntoLink captures (Ambit2: assets/pcap/ambit2_suuntolink_settings_
// sportmodes.pcap, 159 writes; Ambit1: 2026-08-23-ambit1-suuntolink/zzambit1full.pcap, 79) write
// exactly the region's first 10240 bytes (0x2000-0x47FF) every time, so that is what is read,
// patched and written here (it was whatever a 16384-byte read returned). SuuntoLink also puts a
// Unix time in the mode's 0x01FF record on every save; that is its own bookkeeping and is left as
// read, as this writer always did. Each byte below is replayed against those captures by
// tools/test_legacy_sport_mode_edit.py; this file and that one give the same bytes
// (__tests__/LegacySportModes.test.ts, same synthetic region and FNV-1a). The desktop writes them
// through tools/legacy_link.py sport_mode_edit.
const SPORT_MODE_COMMIT_EXTRA = 0xffffffff;
const REGION_WRITE_BYTES = 10240;
const NAME_LEN = 16;
const GROUP_NAME_LEN = 24;

export interface LegacyModePatch {
  name?: string;            // <=16 chars, latin1, null-padded
  autolapM?: number;
  heartrateMin?: number;
  heartrateMax?: number;
  useHrLimits?: boolean;
  useHw?: number;           // raw hrbelt_and_pods bitfield
}

/** Absolute byte offset (into the region) and length of each named mode's 0x0102 settings blob,
 * in the SAME order readLegacySportModes returns them (nameless blobs filtered out identically). */
function modeBlobs(data: Uint8Array): { off: number; len: number }[] {
  const out: { off: number; len: number }[] = [];
  const walk = (start: number, end: number) => {
    let o = start;
    while (o + 4 <= end) {
      const tag = u16(data, o);
      const ln = u16(data, o + 2);
      const bodyStart = o + 4;
      const bodyEnd = bodyStart + ln;
      if (bodyEnd > end) break; // truncated tail
      if (tag === TAG_SETTINGS && ln >= 38) {
        if (data[bodyStart] !== 0) out.push({ off: bodyStart, len: ln });
      } else if (CONTAINERS.has(tag) && ln >= 4) {
        walk(bodyStart, bodyEnd);
      }
      o = bodyEnd;
      if (tag === 0 && ln === 0) break;
    }
  };
  walk(0, data.length);
  return out;
}

/** The records directly inside [start, end): tag, body offset, body length. */
function children(data: Uint8Array, start: number, end: number): { tag: number; off: number; len: number }[] {
  const out: { tag: number; off: number; len: number }[] = [];
  let o = start;
  while (o + 4 <= end) {
    const tag = u16(data, o), len = u16(data, o + 2);
    if (o + 4 + len > end) break;
    out.push({ tag, off: o + 4, len });
    o += 4 + len;
  }
  return out;
}

/** Bytes in use (root record + its length). Throws when this is not a sport-mode region. */
function regionUsed(data: Uint8Array): number {
  if (data.length < 4 || u16(data, 0) !== 0x0003) throw new Error('Not a sport-mode region (no 0x0003 root record).');
  const used = 4 + u16(data, 2);
  if (used > data.length) throw new Error(`Sport-mode region read short: ${data.length} of ${used} bytes.`);
  return used;
}

function latin1(name: string, size: number): Uint8Array {
  const out = new Uint8Array(size);
  // By code point, '?' for what latin-1 cannot hold: Python's encode("latin-1", "replace").
  Array.from(name).slice(0, NAME_LEN).forEach((ch, i) => {
    const c = ch.codePointAt(0) ?? 0x3f;
    out[i] = c > 0xff ? 0x3f : c;
  });
  return out;
}

const sameBytes = (a: Uint8Array, b: Uint8Array) => a.length === b.length && a.every((x, i) => x === b[i]);
const cString = (b: Uint8Array) => { const z = b.indexOf(0); return z < 0 ? b : b.subarray(0, z); };

/**
 * The sport-mode region `data` (as read off the watch) with one mode's settings changed - the
 * same bytes as tools/legacy_sport_modes.py patch_region. `modeIndex` is the index in
 * readLegacySportModes()' output. Every other byte is left as read; the length is unchanged.
 *   name     blob 0, 16 bytes latin-1, NUL-padded. On the Ambit2 the mode's own group (0x0210 whose
 *            one 0x0214 is the mode's place in 0x0100, named like it) is renamed too, in its 24-byte
 *            0x0212 - SuuntoLink's rename "Indoor training" -> "Treino Forção" did both.
 *   HR       on: max/min (+ use 1 on the Ambit2). Off: max = min = 0 (+ use 0), as SuuntoLink writes.
 *   useHw    the HR belt is bit 0 on the Ambit1 but bits 0+1 on the Ambit2 (SuuntoLink 0x0003 <->
 *            0x0000); the screen toggles bit 0, so on the Ambit2 bit 1 follows it.
 */
export function patchLegacySportModeRegion(data: Uint8Array, modeIndex: number, patch: LegacyModePatch): Uint8Array {
  const blobs = modeBlobs(data);
  if (modeIndex < 0 || modeIndex >= blobs.length) {
    throw new Error(`Sport mode ${modeIndex} not found (region has ${blobs.length}).`);
  }
  const { off: blob, len } = blobs[modeIndex];
  const lay = LAYOUTS[len];
  if (!lay) throw new Error(`Settings record of ${len} bytes: only the Ambit1 (76) and Ambit2 (90) layouts are known.`);
  const out = Uint8Array.from(data); // copy - patch this, leave everything else byte-exact
  const setU16 = (at: number, v: number) => {
    const x = Math.max(0, Math.min(65535, Math.round(v)));
    out[at] = x & 0xff;
    out[at + 1] = (x >> 8) & 0xff;
  };
  if (patch.autolapM !== undefined) setU16(blob + lay.autolap, patch.autolapM);
  if (patch.useHrLimits === false) {
    setU16(blob + lay.hrMax, 0);
    setU16(blob + lay.hrMin, 0);
  } else {
    if (patch.heartrateMax !== undefined) setU16(blob + lay.hrMax, patch.heartrateMax);
    if (patch.heartrateMin !== undefined) setU16(blob + lay.hrMin, patch.heartrateMin);
  }
  if (patch.useHrLimits !== undefined && lay.useHr !== null) setU16(blob + lay.useHr, patch.useHrLimits ? 1 : 0);
  if (patch.useHw !== undefined) {
    const old = u16(data, blob + lay.pods);
    let v = patch.useHw & 0xffff;
    if ((old ^ v) & 0x0001) v = (v & 0x0001) ? (v | lay.hrBelt) : (v & ~lay.hrBelt); // the HR belt was switched
    setU16(blob + lay.pods, v);
  }
  if (patch.name !== undefined) {
    const oldName = cString(data.subarray(blob, blob + NAME_LEN));
    out.set(latin1(patch.name, NAME_LEN), blob);
    // The mode's place in the 0x0100 list, and the groups of 0x0200 that name only it.
    const used = regionUsed(data);
    let place = -1;
    for (const c of children(data, 4, used)) {
      if (c.tag !== 0x0100) continue;
      children(data, c.off, c.off + c.len).filter(m => m.tag === 0x0101)
        .forEach((m, k) => { if (m.off <= blob && blob < m.off + m.len) place = k; });
    }
    for (const c of children(data, 4, used)) {
      if (c.tag !== 0x0200) continue;
      for (const g of children(data, c.off, c.off + c.len)) {
        if (g.tag !== 0x0210) continue;
        const kids = children(data, g.off, g.off + g.len);
        const nameRec = kids.find(k => k.tag === 0x0212);
        const members = kids.filter(k => k.tag === 0x0214 && k.len === 2).map(k => u16(data, k.off));
        if (nameRec && nameRec.len === GROUP_NAME_LEN && members.length === 1 && members[0] === place
            && sameBytes(cString(data.subarray(nameRec.off, nameRec.off + GROUP_NAME_LEN)), oldName)) {
          out.set(latin1(patch.name, GROUP_NAME_LEN), nameRec.off);
        }
      }
    }
  }
  return out;
}

/**
 * Edit one Ambit1/2 sport mode's fields and write region 0x2000 back. `modeIndex` is the index in
 * readLegacySportModes()' output. Returns the re-read mode list so the caller can confirm the watch
 * applied it. A copy of the region as read is kept on the phone first (backups/LegacySportModes/).
 */
export async function writeLegacySportMode(modeIndex: number, patch: LegacyModePatch): Promise<LegacyMode[]> {
  // Ambit1/2 are USB-only (no BLE), but keep the same transport guard the rest of the app uses:
  // over an already-open link don't re-connect, over USB open a short-lived connection for the
  // whole read-patch-write-verify cycle (the raw readLegacyRegion/writeLegacyRegion need g_device).
  const overBle = isBleTransportActive();
  if (!overBle) await connect();
  try {
    const b64 = await readLegacyRegion(REGION_ADDR, REGION_WRITE_BYTES);
    if (!b64) throw new Error('Could not read the sport-mode region.');
    const data = base64ToBytes(b64);
    if (data.length !== REGION_WRITE_BYTES) {
      throw new Error(`Read ${data.length} of the sport-mode region's ${REGION_WRITE_BYTES} bytes: nothing written.`);
    }
    const used = regionUsed(data);
    const out = patchLegacySportModeRegion(data, modeIndex, patch);
    if (sameBytes(out, data)) return parseRegion(data); // nothing to change
    await saveWatchBackup('LegacySportModes', b64, (await getDeviceInfo()).serial ?? '');
    const ok = await writeLegacyRegion(REGION_ADDR, bytesToBase64(out), SPORT_MODE_COMMIT_EXTRA);
    if (!ok) throw new Error('The watch did not accept the sport-mode write.');
    const back = base64ToBytes(await readLegacyRegion(REGION_ADDR, REGION_WRITE_BYTES)); // same connection
    if (!sameBytes(back.subarray(0, used), out.subarray(0, used))) {
      throw new Error('The watch did not read back what was written (a copy is in backups/LegacySportModes).');
    }
    return parseRegion(back);
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}
