import { readLegacyRegion, writeLegacyRegion, connect, disconnect, isBleTransportActive, getDeviceInfo } from '../native/AmbitUsbModule';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { saveWatchBackup } from './WatchBackup';
import { Display, decodeDisplayList } from './CustomModesReader';
import { SPORT_MODE_ROWS } from './SportModeRows';

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
// Bit 1 is set whenever the belt or any pod is used: every pods value in both captures.
const SENSOR_SEARCH = 0x0002;
const SENSOR_BITS = 0x0001 | 0x0040 | 0x0080 | 0x0100 | 0x0800; // belt, power, cadence, foot, bike

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
const latin1Name = (b: Uint8Array) => Array.from(cString(b), c => String.fromCharCode(c)).join('');

/**
 * The sport-mode region `data` (as read off the watch) with one mode's settings changed - the
 * same bytes as tools/legacy_sport_modes.py patch_region. `modeIndex` is the index in
 * readLegacySportModes()' output. Every other byte is left as read; the length is unchanged.
 *   name     blob 0, 16 bytes latin-1, NUL-padded. On the Ambit2 the mode's own group (0x0210 whose
 *            one 0x0214 is the mode's place in 0x0100, named like it) is renamed too, in its 24-byte
 *            0x0212 - SuuntoLink's rename "Indoor training" -> "Treino Forção" did both.
 *   HR       on: max/min (+ use 1 on the Ambit2). Off: max = min = 0 (+ use 0), as SuuntoLink writes.
 *   useHw    belt bit 0, power 6, cadence 7, foot 8, bike 11. Bit 1 (0x0002) is set whenever the
 *            belt or any pod is used, in every pods value of both captures; when the belt or a pod
 *            is switched, bit 1 follows that rule on every model (André 2026-11-26, Q14 a).
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
    if ((old ^ v) & SENSOR_BITS) v = (v & SENSOR_BITS) ? (v | SENSOR_SEARCH) : (v & ~SENSOR_SEARCH); // the belt or a pod was switched
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

/** Read-modify-write of the region's first 10240 bytes (SuuntoLink's extent): read, `patch` them,
 * write them back with the 0x0b18 commit tail and read them again to check. A copy of the region
 * as read is kept on the phone first (backups/LegacySportModes/). Returns the bytes read back. */
async function rewriteLegacyRegion(patch: (data: Uint8Array) => Uint8Array): Promise<Uint8Array> {
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
    const out = patch(data);
    if (sameBytes(out, data)) return data; // nothing to change
    await saveWatchBackup('LegacySportModes', b64, (await getDeviceInfo()).serial ?? '');
    const ok = await writeLegacyRegion(REGION_ADDR, bytesToBase64(out), SPORT_MODE_COMMIT_EXTRA);
    if (!ok) throw new Error('The watch did not accept the sport-mode write.');
    const back = base64ToBytes(await readLegacyRegion(REGION_ADDR, REGION_WRITE_BYTES)); // same connection
    // A display edit moves the used extent; the bytes a shorter list freed were written 0xFF.
    const check = Math.max(used, regionUsed(out));
    if (!sameBytes(back.subarray(0, check), out.subarray(0, check))) {
      throw new Error('The watch did not read back what was written (a copy is in backups/LegacySportModes).');
    }
    return back;
  } finally {
    if (!overBle) await disconnect().catch(() => {});
  }
}

/**
 * Edit one Ambit1/2 sport mode's fields and write region 0x2000 back. `modeIndex` is the index in
 * readLegacySportModes()' output. Returns the re-read mode list so the caller can confirm the watch
 * applied it.
 */
export async function writeLegacySportMode(modeIndex: number, patch: LegacyModePatch): Promise<LegacyMode[]> {
  return parseRegion(await rewriteLegacyRegion(data => patchLegacySportModeRegion(data, modeIndex, patch)));
}

// ─── Displays and the sport-mode menu (2026-11-26, watch-protocol/10 L5, L2) ──────────────────────
// The same record format as the Ambit3's display list, so it decodes with CustomModesReader's
// decodeDisplayList (a screen of the wearer's is a display of type 10, the built-in views come
// after them). The edits are tools/legacy_sport_modes.py patch_displays, byte for byte (that file
// says which SuuntoLink capture each change is replayed from; tools/test_legacy_display_edit.py
// replays them and __tests__/LegacyDisplays.test.ts checks this port gives the same bytes):
//   0x0106 { 0x0107 [u16 layout][u16 type], one 0x0108 per row: { 0x0109 [u16 row][u16 item],
//            0x010A [u16 value]... } }
// A row with item 0 and 0x010A values is the multi-value row. Every enclosing length (0x0105, the
// mode's 0x0101, 0x0100, the root) moves by the size change and the rest of the region is 0xFF.
// The screen offers a row change (as it does on the Ambit3); add / remove / layout are the
// desktop's, through the same bytes.
const TAG_DISPLAYS = 0x0105;
const TAG_DISPLAY = 0x0106;
const TAG_LAYOUT = 0x0107;
const TAG_ROW_BOX = 0x0108;
const TAG_ROW = 0x0109;
const TAG_VIEW = 0x010a;
const USER_DISPLAY = 0x000a;
const MAX_DISPLAYS = 8;      // SuuntoLink getMaxDisplays() for Bluebird/Duck/Colibri/Greentit
const MAX_ROW_VALUES = 5;    // sportmode_rows.json maxValuesPerMultiRow; the captures' longest
export const LEGACY_LAYOUT_IDS: Record<string, number> = { '3-row': 260, '2-row': 261, '1-row': 262, graph: 257 };
type Row = [number, number, number[]];
// A new display's rows, as SuuntoLink wrote every display it created in both captures.
const NEW_DISPLAY_ROWS: Record<number, Row[]> = {
  260: [[0, 10, []], [1, 11, []], [2, 0, [5]]],
  261: [[0, 10, []], [1, 0, [5]]],
  262: [[0, 10, []]],
  257: [[0, 6, []], [1, 32, []], [2, 5, []]],
};
export const LEGACY_ROW_LABELS = ['Top', 'Center', 'Bottom'];

export type LegacyDisplayEdit =
  | { op: 'add'; type: string }
  | { op: 'remove'; display: number }
  | { op: 'setType'; display: number; type: string }
  | { op: 'setRow'; display: number; row: string; values: number[] };

const le16 = (v: number) => [v & 0xff, (v >> 8) & 0xff];
const tlvOf = (tag: number, body: number[]) => [...le16(tag), ...le16(body.length), ...body];

function displayRecord(layout: number, rows: Row[], kind = USER_DISPLAY): number[] {
  let body = tlvOf(TAG_LAYOUT, [...le16(layout), ...le16(kind)]);
  for (const [row, item, values] of rows) {
    const box = [...tlvOf(TAG_ROW, [...le16(row), ...le16(item)]), ...values.flatMap(v => tlvOf(TAG_VIEW, le16(v)))];
    body = [...body, ...tlvOf(TAG_ROW_BOX, box)];
  }
  return tlvOf(TAG_DISPLAY, body);
}

/** A 0x0106 record -> layout, type and rows, or null when it is not the captured shape. */
function parseDisplay(rec: Uint8Array): { layout: number; type: number; rows: Row[] } | null {
  if (rec.length < 4 || u16(rec, 0) !== TAG_DISPLAY || 4 + u16(rec, 2) !== rec.length) return null;
  const kids = children(rec, 4, rec.length);
  if (!kids.length || kids[0].tag !== TAG_LAYOUT || kids[0].len !== 4) return null;
  const out = { layout: u16(rec, kids[0].off), type: u16(rec, kids[0].off + 2), rows: [] as Row[] };
  for (const k of kids.slice(1)) {
    const cells = children(rec, k.off, k.off + k.len);
    if (k.tag !== TAG_ROW_BOX || !cells.length || cells[0].tag !== TAG_ROW || cells[0].len !== 4) return null;
    if (cells.slice(1).some(c => c.tag !== TAG_VIEW || c.len !== 2)) return null;
    out.rows.push([u16(rec, cells[0].off), u16(rec, cells[0].off + 2), cells.slice(1).map(c => u16(rec, c.off))]);
  }
  if (!sameBytes(Uint8Array.from(displayRecord(out.layout, out.rows, out.type)), rec)) return null;
  return out;
}

/** Header offsets of the 0x0100 list and of the mode's 0x0101, and its 0x0105 body (or null). */
function modeRecords(data: Uint8Array, modeIndex: number) {
  const blobs = modeBlobs(data);
  if (modeIndex < 0 || modeIndex >= blobs.length) {
    throw new Error(`Sport mode ${modeIndex} not found (region has ${blobs.length}).`);
  }
  const blob = blobs[modeIndex].off;
  for (const c of children(data, 4, regionUsed(data))) {
    if (c.tag !== 0x0100) continue;
    for (const m of children(data, c.off, c.off + c.len)) {
      if (m.tag === 0x0101 && m.off <= blob && blob < m.off + m.len) {
        const disp = children(data, m.off, m.off + m.len).find(k => k.tag === TAG_DISPLAYS);
        return { modes: c.off - 4, mode: m.off - 4, displays: disp ?? null };
      }
    }
  }
  throw new Error('The sport mode is not inside the region\'s mode list.');
}

function displayIndex(edit: { op: string; display?: number }, count: number): number {
  const i = Number(edit.display);
  if (!Number.isInteger(i)) throw new Error(`${edit.op}: which display?`);
  if (i < 0 || i >= count) throw new Error(`${edit.op}: display ${i} not found (the mode has ${count}).`);
  return i;
}

function layoutOf(edit: { op: string; type?: string }): number {
  const layout = LEGACY_LAYOUT_IDS[String(edit.type ?? '').trim().toLowerCase()];
  if (layout === undefined) throw new Error(`${edit.op}: unknown layout ${JSON.stringify(edit.type)}.`);
  return layout;
}

function setRow(rec: number[], edit: { row: string; values: number[] }): number[] {
  const disp = parseDisplay(Uint8Array.from(rec));
  if (!disp) throw new Error('setRow: this display is not in the layout the captures show; not changed.');
  const rowNbr = LEGACY_ROW_LABELS.map(l => l.toLowerCase()).indexOf(String(edit.row ?? '').trim().toLowerCase());
  const rows = disp.rows.filter(r => r[0] === rowNbr);
  if (rowNbr < 0 || rows.length !== 1) throw new Error(`setRow: this display has no ${JSON.stringify(edit.row)} row.`);
  const values = (edit.values ?? []).map(Number);
  if (values.some(v => !Number.isInteger(v))) throw new Error('setRow: the values must be field numbers.');
  if (values.some(v => !(v > 0 && v < 0xfffe))) throw new Error('setRow: a value of 0 (empty) or above 0xFFFD is in no capture.');
  const row = rows[0];
  if (row[1] === 0 && row[2].length) {            // the multi-value row
    if (values.length < 1 || values.length > MAX_ROW_VALUES) throw new Error(`setRow: this row holds 1 to ${MAX_ROW_VALUES} values.`);
    row[2] = values;
  } else if (row[1] !== 0 && !row[2].length) {    // a one-value row
    if (values.length !== 1) throw new Error('setRow: this row shows one value.');
    row[1] = values[0];
  } else {
    throw new Error('setRow: an empty row is in no capture; not changed.');
  }
  return displayRecord(disp.layout, disp.rows, disp.type);
}

/**
 * The sport-mode region `data` with one mode's displays edited - the same bytes as
 * tools/legacy_sport_modes.py patch_displays. `modeIndex` is the index in readLegacySportModes()'
 * output; the edits apply in order, each display index counting the mode's own screens as they are
 * after the edits before it. Throws for anything the captures do not show.
 */
export function patchLegacyDisplayRegion(data: Uint8Array, modeIndex: number, edits: LegacyDisplayEdit[]): Uint8Array {
  const where = modeRecords(data, modeIndex);
  if (!where.displays) throw new Error('This sport mode has no display list.');
  const { off: dbody, len: dlen } = where.displays;
  const records: number[][] = [];
  for (const c of children(data, dbody, dbody + dlen)) {
    if (c.tag !== TAG_DISPLAY) throw new Error(`Record 0x${c.tag.toString(16)} in the display list is in no capture; nothing changed.`);
    records.push(Array.from(data.subarray(c.off - 4, c.off + c.len)));
  }
  if (records.reduce((n, r) => n + r.length, 0) !== dlen) throw new Error('The display list does not parse to its end; nothing changed.');
  const kinds = records.map(r => parseDisplay(Uint8Array.from(r))?.type);
  let nUser = 0;
  while (nUser < kinds.length && kinds[nUser] === USER_DISPLAY) nUser++;
  if (kinds.slice(nUser).includes(USER_DISPLAY)) {
    throw new Error('This mode\'s own screens are not all before the built-in ones; nothing changed.');
  }
  const user = records.slice(0, nUser);
  const builtin = records.slice(nUser);
  for (const edit of edits) {
    if (edit.op === 'add') {
      const layout = layoutOf(edit);
      if (user.length >= MAX_DISPLAYS) throw new Error(`add: this watch holds at most ${MAX_DISPLAYS} displays per mode.`);
      user.push(displayRecord(layout, NEW_DISPLAY_ROWS[layout]));
    } else if (edit.op === 'remove') {
      const i = displayIndex(edit, user.length);
      if (user.length <= 1) throw new Error('remove: a mode keeps at least one display.');
      user.splice(i, 1);
    } else if (edit.op === 'setType') {
      const i = displayIndex(edit, user.length);
      const layout = layoutOf(edit);
      user[i] = displayRecord(layout, NEW_DISPLAY_ROWS[layout]);
    } else if (edit.op === 'setRow') {
      const i = displayIndex(edit, user.length);
      user[i] = setRow(user[i], edit);
    } else {
      throw new Error(`Unknown display edit ${JSON.stringify((edit as { op?: string }).op)}.`);
    }
  }
  const body = [...user, ...builtin].flat();
  const delta = body.length - dlen;
  const used = regionUsed(data);
  const total = dbody + body.length + (used - dbody - dlen);
  if (total > data.length) throw new Error(`The sport modes would need ${total} of the region's ${data.length} bytes.`);
  const out = new Uint8Array(data.length).fill(0xff);
  out.set(data.subarray(0, dbody));
  out.set(body, dbody);
  out.set(data.subarray(dbody + dlen, used), dbody + body.length);
  for (const header of [dbody - 4, where.mode, where.modes, 0]) {
    const v = Math.max(0, Math.min(0xffff, u16(out, header + 2) + delta));
    out[header + 2] = v & 0xff;
    out[header + 3] = v >> 8;
  }
  return out;
}

/** Edit one Ambit1/2 mode's displays and write region 0x2000 back (the same read-modify-write as
 * writeLegacySportMode). Returns the re-read region decoded. */
export async function writeLegacyDisplays(modeIndex: number, edits: LegacyDisplayEdit[]): Promise<LegacyRegion> {
  return decodeLegacyRegion(await rewriteLegacyRegion(data => patchLegacyDisplayRegion(data, modeIndex, edits)));
}

/** One entry of the watch's sport-mode menu (a 0x0210 group): several legs = a multisport. */
export interface LegacyGroup {
  name: string;
  activityId: number;
  legs: number[];              // places in the 0x0100 mode list
  legNames: (string | null)[];
  isMultisport: boolean;
}

export interface LegacyRegion {
  modes: LegacyMode[];
  displays: Display[][];       // per mode, same order as `modes`
  groups: LegacyGroup[];
  /** What may be deleted and SuuntoLink's 10-mode count (legacyMenu), when read off the watch. */
  menu?: LegacyMenu;
}

/** The region decoded: the named modes (as readLegacySportModes), each one's displays, and the menu
 * groups (tools/legacy_sport_modes.py sport_mode_groups), the multisports among them. */
export function decodeLegacyRegion(data: Uint8Array): LegacyRegion {
  const modes = parseRegion(data);
  const blobs = modeBlobs(data);
  const used = regionUsed(data);
  const places: string[] = [];
  const displays: Display[][] = blobs.map(() => []);
  for (const c of children(data, 4, used)) {
    if (c.tag !== 0x0100) continue;
    for (const m of children(data, c.off, c.off + c.len).filter(k => k.tag === 0x0101)) {
      const kids = children(data, m.off, m.off + m.len);
      const settings = kids.find(k => k.tag === TAG_SETTINGS && k.len >= NAME_LEN);
      places.push(settings ? latin1Name(data.subarray(settings.off, settings.off + NAME_LEN)) : '');
      const k = blobs.findIndex(b => m.off <= b.off && b.off < m.off + m.len);
      const disp = kids.find(x => x.tag === TAG_DISPLAYS);
      if (k >= 0 && disp) displays[k] = decodeDisplayList(data, disp.off, disp.len);
    }
  }
  const groups: LegacyGroup[] = [];
  for (const c of children(data, 4, used)) {
    if (c.tag !== 0x0200) continue;
    for (const g of children(data, c.off, c.off + c.len)) {
      if (g.tag !== 0x0210) continue;
      const group: LegacyGroup = { name: '', activityId: 0, legs: [], legNames: [], isMultisport: false };
      for (const k of children(data, g.off, g.off + g.len)) {
        if (k.tag === 0x0212) group.name = latin1Name(data.subarray(k.off, k.off + k.len));
        else if (k.tag === 0x0213 && k.len === 2) group.activityId = u16(data, k.off);
        else if (k.tag === 0x0214 && k.len === 2) group.legs.push(u16(data, k.off));
      }
      if (!group.name) continue;
      group.legNames = group.legs.map(i => (i >= 0 && i < places.length ? places[i] : null));
      group.isMultisport = group.legs.length > 1;
      groups.push(group);
    }
  }
  return { modes, displays, groups };
}

/** Read the connected Ambit1/2's sport modes, their displays and the menu groups, in one read,
 * with what may be deleted (the rules are the same on the four models; the record length tells
 * the Ambit1 from the Ambit2 family). */
export async function readLegacySportModeRegion(): Promise<LegacyRegion> {
  const b64 = await readLegacyRegion(REGION_ADDR, REGION_BYTES);
  if (!b64) return { modes: [], displays: [], groups: [] };
  return withMenu(base64ToBytes(b64));
}

function withMenu(data: Uint8Array): LegacyRegion {
  const region = decodeLegacyRegion(data);
  try {
    const ambit1 = modeBlobs(data).every(b => b.len === AMBIT1_BLOB);
    return { ...region, menu: legacyMenu(data, ambit1 ? 'Bluebird' : 'Duck') };
  } catch {
    return region;
  }
}

// ─── Creating and deleting a sport mode (2026-11-26, watch-protocol/10 L3) ───────────────────────
// tools/legacy_sport_modes.py create_mode / delete_mode / delete_multisport, byte for byte (that
// file says where each byte comes from: SuuntoLink's four captured creates and its captured
// Triathlon delete, replayed by tools/test_legacy_sport_mode_manage.py; __tests__/
// LegacySportModeManage.test.ts checks this port gives the same bytes). In short:
//   create  appended to 0x0100: 0x0101 { 0x0102 settings, 0x01FF [settings time][display time],
//           0x0105 SuuntoLink's seven new-mode displays }; on the Ambit2 family its menu entry
//           appended to 0x0200: 0x0210 { 0x0212 name (24), 0x0213 sport, 0x0214 place,
//           0x02FE lowest free id, 0x02FF time }. Settings from the sport's SuuntoLink defaults.
//   delete  a mode with its own menu entry (never a multisport's leg, a mode without an entry or
//           the last one), or a multisport with the modes only it used; legs above a removed mode
//           move down by one.
const TAG_STAMP = 0x01ff;
const MAX_SPORT_MODES = 10;                 // sport_mode.js getMaxSportModes, all four models
export const LEGACY_VARIANTS: Record<string, number> = { Bluebird: AMBIT1_BLOB, Duck: AMBIT2_BLOB, Colibri: AMBIT2_BLOB, Greentit: AMBIT2_BLOB };
/** ui/input.js movescountActivity: the sports SuuntoLink's create list offers. */
export const LEGACY_SELECTABLE_ACTIVITIES = new Set([
  1, 2, 3, 4, 5, 6, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 20, 21, 22, 23, 24, 25, 26,
  27, 28, 29, 30, 31, 33, 34, 35, 36, 37, 38, 39, 40, 58, 61, 62, 63, 64, 65, 66, 67, 68, 69,
  70, 71, 72, 73, 74, 75, 76, 78, 79, 80, 81, 82, 83, 84, 85, 86, 87, 88, 89, 90, 91, 92, 93,
  94, 95, 96, 97, 98, 99,
]);
const FUSED_SPEED_ACTIVITIES = new Set([3, 25, 28, 40, 75, 81, 82, 93]);   // getFusedSpeedActivities
const SWIM_ACTIVITIES = new Set([6, 83]);
const SUPPORTS: Record<string, { alti: boolean; foot: boolean; bike: boolean; power: boolean; cadence: boolean }> = {
  Bluebird: { alti: true, foot: true, bike: true, power: true, cadence: true },
  Duck: { alti: true, foot: true, bike: true, power: true, cadence: true },
  Colibri: { alti: false, foot: true, bike: true, power: true, cadence: true },
  Greentit: { alti: false, foot: true, bike: false, power: false, cadence: false },
};
export const LEGACY_NEW_MODE_DISPLAYS: number[] = ([
  [260, 10, [[0, 10, []], [1, 11, []], [2, 0, [5]]]],
  [273, 4, [[0, 8, []], [1, 8, []], [2, 0, [16, 1, 65534]]]],
  [291, 5, [[0, 8, []], [1, 40, []], [2, 0, [16, 8, 1, 65534]]]],
  [290, 6, [[0, 24, []], [1, 25, []], [2, 0, [50, 26, 16]]]],
  [257, 65, [[1, 195, []], [2, 196, []], [3, 197, []]]],
  [336, 7, []],
  [260, 50, [[0, 62, []], [1, 61, []], [2, 0, [5, 10, 21, 11, 28]]]],
] as [number, number, Row[]][]).flatMap(([layout, kind, rows]) => displayRecord(layout, rows, kind));
const le32 = (v: number) => [v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff];
const u32 = (b: Uint8Array, o: number) => (b[o] | (b[o + 1] << 8) | (b[o + 2] << 16) | (b[o + 3] << 24)) >>> 0;

/** The settings SuuntoLink's create form saves for a new mode of this sport on this model. */
export function legacyNewModeFields(variant: string, activityId: number) {
  const sup = SUPPORTS[variant];
  const d = SPORT_MODE_ROWS.activityDefaults[variant]?.[String(activityId)];
  if (!sup || !d) throw new Error(`SuuntoLink has no defaults for sport ${activityId} on the ${variant}.`);
  const locked = variant !== 'Bluebird' && SWIM_ACTIVITIES.has(activityId);
  let pods = d.hrBelt && !locked ? 0x0001 : 0;
  if (d.powerPod && sup.power && !locked) pods |= 0x0040;
  if (d.cadencePod && sup.cadence && !locked) pods |= 0x0080;
  if (d.footPod && sup.foot && !locked) pods |= 0x0100;
  if (d.bikePod && sup.bike && !locked) pods |= 0x0800;
  if (pods) pods |= SENSOR_SEARCH;
  if (FUSED_SPEED_ACTIVITIES.has(activityId)) pods |= 0x0004;
  return { pods, alti: sup.alti && !locked ? d.altiBaroProfile : 0, gps: d.gpsInterval, rec: d.recordingInterval };
}

function newModeSettings(variant: string, name: string, activityId: number, modeId: number): number[] {
  const f = legacyNewModeFields(variant, activityId);
  const len = LEGACY_VARIANTS[variant];
  const out = new Array(len).fill(0);
  latin1(name, NAME_LEN).forEach((c, i) => { out[i] = c; });
  const put = (at: number, v: number) => { out[at] = v & 0xff; out[at + 1] = (v >> 8) & 0xff; };
  put(16, activityId);
  const offs = len === AMBIT1_BLOB ? [18, 20, 22, 24, 38] : [22, 24, 26, 28, 46];
  [f.pods, f.alti, f.gps, f.rec, 99].forEach((v, i) => put(offs[i], v));
  if (len === AMBIT2_BLOB) {
    le32(modeId).forEach((c, i) => { out[18 + i] = c; });
    put(84, 0x00ff);
    put(86, 0x00ff);
  }
  return out;
}

interface ModeRec { at: number; len: number; name: string; blobLen: number; blobOff: number }
interface GroupRec { at: number; len: number; name: string; legs: number[]; legsAt: number[]; id: number | null }

function modeList(data: Uint8Array): { at: number; modes: ModeRec[] } {
  for (const c of children(data, 4, regionUsed(data))) {
    if (c.tag !== 0x0100) continue;
    const modes: ModeRec[] = [];
    for (const m of children(data, c.off, c.off + c.len)) {
      if (m.tag !== 0x0101) continue;
      const s = children(data, m.off, m.off + m.len).find(k => k.tag === TAG_SETTINGS);
      modes.push({ at: m.off - 4, len: m.len + 4, blobLen: s ? s.len : 0, blobOff: s ? s.off : -1,
        name: s && s.len >= NAME_LEN ? latin1Name(data.subarray(s.off, s.off + NAME_LEN)) : '' });
    }
    return { at: c.off - 4, modes };
  }
  throw new Error('The sport-mode region has no mode list.');
}

function groupList(data: Uint8Array): { at: number | null; groups: GroupRec[] } {
  for (const c of children(data, 4, regionUsed(data))) {
    if (c.tag !== 0x0200) continue;
    const groups: GroupRec[] = [];
    for (const g of children(data, c.off, c.off + c.len)) {
      if (g.tag !== 0x0210) continue;
      const rec: GroupRec = { at: g.off - 4, len: g.len + 4, name: '', legs: [], legsAt: [], id: null };
      for (const k of children(data, g.off, g.off + g.len)) {
        if (k.tag === 0x0212) rec.name = latin1Name(data.subarray(k.off, k.off + k.len));
        else if (k.tag === 0x0214 && k.len === 2) { rec.legs.push(u16(data, k.off)); rec.legsAt.push(k.off); }
        else if (k.tag === 0x02fe && k.len === 4) rec.id = u32(data, k.off);
      }
      groups.push(rec);
    }
    return { at: c.off - 4, groups };
  }
  return { at: null, groups: [] };
}

/** SuuntoLink's countSportModes: max(single entries + modes only a multisport uses, all entries). */
export function legacyUsage(data: Uint8Array): { used: number; max: number; multisport: number } {
  const { modes } = modeList(data);
  const { at, groups } = groupList(data);
  if (at === null) return { used: modes.filter(m => m.name).length, max: MAX_SPORT_MODES, multisport: 0 };
  const singles = groups.filter(g => g.legs.length === 1);
  const own = new Set(singles.map(g => g.legs[0]));
  const hidden = new Set(groups.filter(g => g.legs.length > 1).flatMap(g => g.legs).filter(l => !own.has(l)));
  return { used: Math.max(singles.length + hidden.size, groups.length), max: MAX_SPORT_MODES,
    multisport: groups.length - singles.length };
}

const lowestFree = (used: Set<number | null>) => { let n = 1; while (used.has(n)) n++; return n; };

/** Header offsets of the container records strictly enclosing the record at `off`. */
function enclosing(data: Uint8Array, off: number): number[] {
  const out: number[] = [];
  let start = 0, end = regionUsed(data);
  for (;;) {
    const hit = children(data, start, end).find(c => c.off - 4 < off && off < c.off + c.len && CONTAINERS.has(c.tag));
    if (!hit) return out;
    out.push(hit.off - 4);
    start = hit.off; end = hit.off + hit.len;
  }
}

/** `data` with ranges cut and records inserted, every enclosing length fixed, the rest 0xFF. */
function rewriteRecords(data: Uint8Array, cuts: [number, number][], inserts: [number, number[], number[]][]): Uint8Array {
  const used = regionUsed(data);
  const edits: { off: number; removed: number; added: number[]; headers: number[] }[] = [
    ...cuts.map(([off, len]) => ({ off, removed: len, added: [] as number[], headers: enclosing(data, off) })),
    ...inserts.map(([off, added, headers]) => ({ off, removed: 0, added, headers })),
  ];
  const out = Array.from(data.subarray(0, used));
  const delta = new Map<number, number>();
  for (const e of edits) for (const h of e.headers) delta.set(h, (delta.get(h) ?? 0) + e.added.length - e.removed);
  for (const [h, d] of delta) {
    const v = Math.max(0, Math.min(0xffff, u16(data, h + 2) + d));
    out[h + 2] = v & 0xff; out[h + 3] = v >> 8;
  }
  for (const e of [...edits].sort((a, b) => b.off - a.off)) out.splice(e.off, e.removed, ...e.added);
  if (out.length > data.length) throw new Error(`The sport modes would need ${out.length} of the region's ${data.length} bytes.`);
  const res = new Uint8Array(data.length).fill(0xff);
  res.set(out);
  return res;
}

function checkFamily(data: Uint8Array, variant: string) {
  if (!(variant in LEGACY_VARIANTS)) throw new Error(`${variant} is not an Ambit1 or Ambit2.`);
  const lens = new Set(modeList(data).modes.filter(m => m.name).map(m => m.blobLen));
  if (lens.size && (lens.size !== 1 || !lens.has(LEGACY_VARIANTS[variant]))) {
    throw new Error(`The region's settings records are not the ${variant}'s: nothing changed.`);
  }
}

/** The region with one new sport mode, as SuuntoLink creates one. `now` is a Unix time or the
 * [settings, display, menu-entry] times of SuuntoLink's three writes. */
export function createLegacyModeRegion(data: Uint8Array, name: string, activityId: number, variant: string,
                                       now?: number | [number, number, number]): Uint8Array {
  checkFamily(data, variant);
  const raw = String(name ?? '').trim();
  if (!raw) throw new Error('A sport mode needs a name.');
  if (Array.from(raw).length > NAME_LEN) throw new Error(`A sport mode name holds ${NAME_LEN} characters on this watch.`);
  const { at: modesAt, modes } = modeList(data);
  if (modes.some(m => m.name === raw)) throw new Error(`The watch already has a sport mode named "${raw}".`);
  if (!LEGACY_SELECTABLE_ACTIVITIES.has(activityId)) throw new Error(`Sport ${activityId} is not one SuuntoLink offers for a sport mode.`);
  const use = legacyUsage(data);
  if (use.used >= use.max) throw new Error(`${use.used}/${use.max} sport modes used: delete one first.`);
  const t = now ?? Math.floor(Date.now() / 1000);
  const [tSet, tDisp, tMenu] = Array.isArray(t) ? t : [t, t, t];
  const ids = new Set<number | null>(modes.filter(m => m.blobLen === AMBIT2_BLOB).map(m => u32(data, m.blobOff + 18)));
  const record = tlvOf(0x0101, [...tlvOf(TAG_SETTINGS, newModeSettings(variant, raw, activityId, lowestFree(ids))),
    ...tlvOf(TAG_STAMP, [...le32(tSet), ...le32(tDisp)]), ...tlvOf(TAG_DISPLAYS, LEGACY_NEW_MODE_DISPLAYS)]);
  const inserts: [number, number[], number[]][] = [[modesAt + 4 + u16(data, modesAt + 2), record, [modesAt, 0]]];
  if (variant !== 'Bluebird') {
    const { at: groupsAt, groups } = groupList(data);
    if (groupsAt === null) throw new Error('This Ambit2 region has no sport-mode menu (0x0200): nothing changed.');
    const entry = tlvOf(0x0210, [...tlvOf(0x0212, Array.from(latin1(raw, GROUP_NAME_LEN))),
      ...tlvOf(0x0213, le16(activityId)), ...tlvOf(0x0214, le16(modes.length)),
      ...tlvOf(0x02fe, le32(lowestFree(new Set(groups.map(g => g.id))))), ...tlvOf(0x02ff, le32(tMenu))]);
    inserts.push([groupsAt + 4 + u16(data, groupsAt + 2), entry, [groupsAt, 0]]);
  }
  return rewriteRecords(data, [], inserts);
}

function deleteRecords(data: Uint8Array, groupsGone: GroupRec[], placesGone: number[]): Uint8Array {
  const { modes } = modeList(data);
  const { groups } = groupList(data);
  const out = Uint8Array.from(data);
  for (const g of groups) {
    if (groupsGone.some(x => x.at === g.at)) continue;
    g.legs.forEach((leg, i) => {
      const v = leg - placesGone.filter(p => p < leg).length;
      out[g.legsAt[i]] = v & 0xff; out[g.legsAt[i] + 1] = v >> 8;
    });
  }
  return rewriteRecords(out, [...placesGone.map(p => [modes[p].at, modes[p].len] as [number, number]),
    ...groupsGone.map(g => [g.at, g.len] as [number, number])], []);
}

/** The region without one sport mode (and its menu entry), with SuuntoLink's refusals. */
export function deleteLegacyModeRegion(data: Uint8Array, name: string, variant: string): Uint8Array {
  checkFamily(data, variant);
  const { modes } = modeList(data);
  const place = modes.findIndex(m => m.name === name);
  if (place < 0) throw new Error(`No sport mode named "${name}" on the watch.`);
  const { at, groups } = groupList(data);
  if (at === null) {
    if (modes.filter(m => m.name).length <= 1) throw new Error('The watch keeps at least one sport mode.');
    return deleteRecords(data, [], [place]);
  }
  const users = groups.filter(g => g.legs.length > 1 && g.legs.includes(place)).map(g => g.name);
  if (users.length) throw new Error(`"${name}" is used by the multisport ${users.join(', ')}: delete that first.`);
  const own = groups.filter(g => g.legs.length === 1 && g.legs[0] === place);
  if (!own.length) throw new Error(`"${name}" has no entry of its own in the watch menu: SuuntoLink does not delete it.`);
  if (groups.length - own.length < 1) throw new Error('The watch keeps at least one sport mode.');
  return deleteRecords(data, own, [place]);
}

/** The region without one multisport and the modes only it used (Ambit2 capture: Triathlon). */
export function deleteLegacyMultisportRegion(data: Uint8Array, name: string, variant: string): Uint8Array {
  checkFamily(data, variant);
  const { groups } = groupList(data);
  const gone = groups.find(g => g.name === name && g.legs.length > 1);
  if (!gone) throw new Error(`No multisport named "${name}" on the watch.`);
  if (groups.length <= 1) throw new Error('The watch keeps at least one sport mode.');
  const still = new Set(groups.filter(g => g !== gone).flatMap(g => g.legs));
  const places = [...new Set(gone.legs)].filter(l => !still.has(l)).sort((a, b) => a - b);
  return deleteRecords(data, [gone], places);
}

// ─── Creating a multisport (2026-11-26, watch-protocol/10 L2) ────────────────────────────────────
// tools/legacy_sport_modes.py create_multisport, byte for byte (that file says where each byte
// comes from: the Ambit2 capture's write 1 -> 2, SuuntoLink's editor making a Triathlon, replayed by
// tools/test_legacy_sport_mode_manage.py; __tests__/LegacySportModeManage.test.ts checks this port
// gives the same bytes). One entry appended to 0x0200: 0x0210 { 0x0212 name (24), 0x0213 sport, one
// 0x0214 per leg in the order picked, 0x02FE lowest free id, 0x02FF time }. SuuntoLink's limits:
// Ambit2 and 2S only, Multisport / Triathlon / Adventure racing, 2 to 6 legs from the modes with an
// entry of their own and those only a multisport uses, 2 multisports, 10 sport modes. Not edited.
export const LEGACY_MULTISPORT_ACTIVITIES = [2, 19, 61];       // activity.js getMultisportPhrases
export const LEGACY_MULTISPORT_VARIANTS = new Set(['Duck', 'Colibri']);
const MAX_MULTISPORT_MODES = 2;
const MIN_LEGS = 2, MAX_LEGS = 6;

/** The modes SuuntoLink's leg picker offers: those with a menu entry of their own, then those only
 * a multisport uses (the factory Transition). */
export function legacyMultisportLegChoices(data: Uint8Array): string[] {
  const { modes } = modeList(data);
  const { groups } = groupList(data);
  const own = new Set(groups.filter(g => g.legs.length === 1).map(g => g.legs[0]));
  const used = new Set(groups.filter(g => g.legs.length > 1).flatMap(g => g.legs));
  const places = [...modes.keys()].filter(p => own.has(p))
    .concat([...modes.keys()].filter(p => used.has(p) && !own.has(p)));
  return places.map(p => modes[p].name).filter(n => n);
}

/** The region with one new multisport, as SuuntoLink's editor makes one. `legs` are mode names in
 * the order the watch steps through them. */
export function createLegacyMultisportRegion(data: Uint8Array, name: string, activityId: number, legs: string[],
                                             variant: string, now?: number): Uint8Array {
  checkFamily(data, variant);
  if (!LEGACY_MULTISPORT_VARIANTS.has(variant)) throw new Error(`The ${variant} has no multisport modes.`);
  const raw = String(name ?? '').trim();
  if (!raw) throw new Error('A multisport needs a name.');
  if (Array.from(raw).length > NAME_LEN) throw new Error(`A multisport name holds ${NAME_LEN} characters on this watch.`);
  if (!LEGACY_MULTISPORT_ACTIVITIES.includes(activityId)) {
    throw new Error(`Sport ${activityId} cannot hold several sports: pick Multisport, Triathlon or Adventure racing.`);
  }
  const { at: groupsAt, groups } = groupList(data);
  if (groupsAt === null) throw new Error('This Ambit2 region has no sport-mode menu (0x0200): nothing changed.');
  const { modes } = modeList(data);
  if (groups.some(g => g.name === raw) || modes.some(m => m.name === raw)) {
    throw new Error(`The watch already has a sport mode or multisport named "${raw}".`);
  }
  const use = legacyUsage(data);
  if (use.multisport >= MAX_MULTISPORT_MODES) throw new Error(`${use.multisport}/${MAX_MULTISPORT_MODES} multisport modes used: delete one first.`);
  if (use.used >= use.max) throw new Error(`${use.used}/${use.max} sport modes used: delete one first.`);
  if (legs.length < MIN_LEGS || legs.length > MAX_LEGS) {
    throw new Error(`A multisport takes ${MIN_LEGS} to ${MAX_LEGS} sport modes, not ${legs.length}.`);
  }
  const choices = new Set(legacyMultisportLegChoices(data));
  const places = legs.map(leg => {
    if (!choices.has(leg)) throw new Error(`"${leg}" is not a sport mode SuuntoLink offers as a leg.`);
    return modes.findIndex(m => m.name === leg);
  });
  const stamp = now ?? Math.floor(Date.now() / 1000);
  const entry = tlvOf(0x0210, [...tlvOf(0x0212, Array.from(latin1(raw, GROUP_NAME_LEN))),
    ...tlvOf(0x0213, le16(activityId)), ...places.flatMap(p => tlvOf(0x0214, le16(p))),
    ...tlvOf(0x02fe, le32(lowestFree(new Set(groups.map(g => g.id))))), ...tlvOf(0x02ff, le32(stamp))]);
  return rewriteRecords(data, [], [[groupsAt + 4 + u16(data, groupsAt + 2), entry, [groupsAt, 0]]]);
}

export interface LegacyMenuEntry { name: string; activityId: number; legNames: (string | null)[]; multisport: boolean; deletable: boolean; reason: string }
export interface LegacyMenu { used: number; max: number; multisport: number; entries: LegacyMenuEntry[]; legChoices: string[] }

/** The watch's sport modes and multisports with what may be deleted, and the count SuuntoLink keeps. */
export function legacyMenu(data: Uint8Array, variant: string): LegacyMenu {
  const why = (f: () => unknown) => { try { f(); return ''; } catch (e: any) { return e?.message ?? String(e); } };
  const region = decodeLegacyRegion(data);
  const entries: LegacyMenuEntry[] = region.modes.map(m => {
    const reason = why(() => deleteLegacyModeRegion(data, m.name, variant));
    return { name: m.name, activityId: m.activityId, legNames: [m.name], multisport: false, deletable: !reason, reason };
  });
  for (const g of region.groups.filter(x => x.isMultisport)) {
    const reason = why(() => deleteLegacyMultisportRegion(data, g.name, variant));
    entries.push({ name: g.name, activityId: g.activityId, legNames: g.legNames, multisport: true, deletable: !reason, reason });
  }
  return { ...legacyUsage(data), entries, legChoices: legacyMultisportLegChoices(data) };
}

/** Create one sport mode on the connected Ambit1/2 (one region read-modify-write). `variant` is
 * the watch's codename: it picks the record format and the settings SuuntoLink gives a new mode. */
export async function createLegacySportMode(name: string, activityId: number, variant: string): Promise<LegacyRegion> {
  return withMenu(await rewriteLegacyRegion(data => createLegacyModeRegion(data, name, activityId, variant)));
}

/** Create one multisport on the connected Ambit2 / 2S (one region read-modify-write). */
export async function createLegacyMultisport(name: string, activityId: number, legs: string[], variant: string): Promise<LegacyRegion> {
  return withMenu(await rewriteLegacyRegion(data => createLegacyMultisportRegion(data, name, activityId, legs, variant)));
}

/** Delete one sport mode, or with `multisport` one multisport, on the connected Ambit1/2. */
export async function deleteLegacySportMode(name: string, variant: string, multisport = false): Promise<LegacyRegion> {
  return withMenu(await rewriteLegacyRegion(data => (multisport
    ? deleteLegacyMultisportRegion(data, name, variant) : deleteLegacyModeRegion(data, name, variant))));
}
