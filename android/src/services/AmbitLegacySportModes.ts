import { readLegacyRegion, writeLegacyRegion, connect, disconnect, isBleTransportActive, getDeviceInfo } from '../native/AmbitUsbModule';
import { base64ToBytes, bytesToBase64 } from './Base64';
import { saveWatchBackup } from './WatchBackup';
import { Display, decodeDisplayList } from './CustomModesReader';

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
const latin1Name = (b: Uint8Array) => Array.from(cString(b), c => String.fromCharCode(c)).join('');

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
}

/** The region decoded: the named modes (as readLegacySportModes), each one's displays, and the menu
 * groups (tools/legacy_sport_modes.py sport_mode_groups). The multisports are read only: no capture
 * shows how SuuntoLink builds one. */
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

/** Read the connected Ambit1/2's sport modes, their displays and the menu groups, in one read. */
export async function readLegacySportModeRegion(): Promise<LegacyRegion> {
  const b64 = await readLegacyRegion(REGION_ADDR, REGION_BYTES);
  if (!b64) return { modes: [], displays: [], groups: [] };
  return decodeLegacyRegion(base64ToBytes(b64));
}
