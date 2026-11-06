// Garmin Edge 500 athlete profile, read and patched byte for byte - the TypeScript twin of the
// desktop's tools/edge500_profile.py (hardware-checked on a real Edge 500, fw 3.30, 2026-11-05).
// Pure: bytes in, bytes out; Edge500Usb.ts moves the files over USB.
//
// Where the values live:
//   * Garmin/Settings/Settings.fit (FIT file type 2), mesg 3 user_profile:
//       1 gender (0 female, 1 male) · 2 age · 3 height (cm, u8) · 4 weight (kg x10, u16)
//       8 resting_heart_rate
//   * Garmin/Sports/Cycling.fit (FIT file type 3), mesg 7 zones_target:
//       1 max_heart_rate · 2 threshold_heart_rate (LTHR) · 3 functional_threshold_power (FTP)
//       5 hr_calc_type · 7 pwr_calc_type (1 = zones are % of max HR / % of FTP)
//     plus mesg 8 hr_zone (1 = high_bpm) and mesg 9 power_zone (1 = high_value).
//
// A write never touches the live files: a COPY is patched in place (every field we change is
// already in the file's definition, fixed size, so nothing shifts), the FIT CRC is recomputed, and
// the copy goes into Garmin/NewFiles, which the Edge reads when it leaves USB mode. When the zones
// are "% of max HR" / "% of FTP" (calc type 1), the zone limits are scaled by new/old so they keep
// the percentages the device shows.

export const SETTINGS_PATH = 'Garmin/Settings/Settings.fit';
export const CYCLING_PATH = 'Garmin/Sports/Cycling.fit';
export const NEWFILES_DIR = 'Garmin/NewFiles';

const USER_PROFILE = 3, ZONES_TARGET = 7, HR_ZONE = 8, POWER_ZONE = 9;

export type Edge500Field = 'gender' | 'age' | 'height' | 'weight' | 'rest_hr' | 'max_hr' | 'lthr' | 'ftp';
type FileKey = 'settings' | 'cycling';

// name -> [file, mesg, field, scale] (same order as the Python FIELDS dict)
const FIELDS: Record<Edge500Field, [FileKey, number, number, number]> = {
  gender:  ['settings', USER_PROFILE, 1, 1],
  age:     ['settings', USER_PROFILE, 2, 1],
  height:  ['settings', USER_PROFILE, 3, 1],     // cm
  weight:  ['settings', USER_PROFILE, 4, 10],    // kg, stored x10
  rest_hr: ['settings', USER_PROFILE, 8, 1],
  max_hr:  ['cycling', ZONES_TARGET, 1, 1],
  lthr:    ['cycling', ZONES_TARGET, 2, 1],
  ftp:     ['cycling', ZONES_TARGET, 3, 1],
};

export const BOUNDS: Record<Edge500Field, [number, number]> = {
  gender: [0, 1], age: [5, 120], height: [80, 250], weight: [20, 250],
  max_hr: [100, 240], lthr: [80, 230], rest_hr: [30, 120], ftp: [30, 600],
};

type Code = 'B' | 'b' | 'H' | 'h' | 'I' | 'i';
// FIT base type -> [code, size]
const BASE: Record<number, [Code, number]> = {
  0x00: ['B', 1], 0x01: ['b', 1], 0x02: ['B', 1], 0x0a: ['B', 1],
  0x83: ['h', 2], 0x84: ['H', 2], 0x8b: ['H', 2],
  0x85: ['i', 4], 0x86: ['I', 4], 0x8c: ['I', 4],
};
const INVALID: Partial<Record<Code, number>> = { B: 0xff, H: 0xffff, b: 0x7f, h: 0x7fff, I: 0xffffffff };

const CRC = [0x0000, 0xcc01, 0xd801, 0x1400, 0xf001, 0x3c00, 0x2800, 0xe401,
  0xa001, 0x6c00, 0x7800, 0xb401, 0x5000, 0x9c01, 0x8801, 0x4400];

export function fitCrc(data: Uint8Array, crc = 0): number {
  for (let i = 0; i < data.length; i++) {
    const b = data[i];
    let t = CRC[crc & 0xf];
    crc = ((crc >> 4) & 0x0fff) ^ t ^ CRC[b & 0xf];
    t = CRC[crc & 0xf];
    crc = ((crc >> 4) & 0x0fff) ^ t ^ CRC[(b >> 4) & 0xf];
  }
  return crc & 0xffff;
}

/** Python's round(): halves go to the even neighbour (round(2.5) == 2). */
export function pyRound(x: number): number {
  const f = Math.floor(x);
  const d = x - f;
  if (d < 0.5) return f;
  if (d > 0.5) return f + 1;
  return f % 2 === 0 ? f : f + 1;
}

/** Python's '%.Nf' % x: like toFixed, but an exact tie rounds to the even digit. */
export function pyFixed(x: number, digits: number): string {
  const s = x.toFixed(digits);
  const long = Math.abs(x).toFixed(Math.min(100, digits + 60));
  const dot = long.indexOf('.');
  const rest = long.slice(dot + 1 + digits);
  if (!/^50*$/.test(rest)) return s;                            // not an exact tie
  const kept = long.slice(0, dot + 1 + digits).replace(/\.$/, '');
  const last = Number(kept[kept.length - 1]);
  if (last % 2 === 1) return s;                                 // toFixed went up: odd -> even
  return (x < 0 || Object.is(x, -0) ? '-' : '') + kept;         // stay on the even digit
}

interface Loc { off: number; code: Code; little: boolean }
interface Rec { mesg: number; fields: Map<number, Loc> }

/** Every data message with where its fixed-size scalar fields sit. Settings/sport files carry
 *  no compressed-timestamp records; one would throw. */
export function scan(data: Uint8Array): Rec[] {
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const hs = data[0];
  const end = hs + dv.getUint32(4, true);
  if (String.fromCharCode(data[8], data[9], data[10], data[11]) !== '.FIT' || end + 2 > data.length) {
    throw new Error('not a FIT file');
  }
  let p = hs;
  const defs = new Map<number, { g: number; little: boolean; fl: [number, number, number][]; dev: number }>();
  const out: Rec[] = [];
  while (p < end) {
    const h = data[p]; p += 1;
    if (h & 0x80) throw new Error('compressed-timestamp record in a settings file');
    const local = h & 0x0f;
    if (h & 0x40) {
      const little = data[p + 1] === 0;
      const g = dv.getUint16(p + 2, little);
      const n = data[p + 4]; p += 5;
      const fl: [number, number, number][] = [];
      for (let i = 0; i < n; i++) fl.push([data[p + 3 * i], data[p + 3 * i + 1], data[p + 3 * i + 2]]);
      p += 3 * n;
      let dev = 0;
      if (h & 0x20) {
        const nd = data[p]; p += 1;
        for (let i = 0; i < nd; i++) dev += data[p + 3 * i + 1];
        p += 3 * nd;
      }
      defs.set(local, { g, little, fl, dev });
      continue;
    }
    const def = defs.get(local);
    if (!def) throw new Error(`data record for undefined local type ${local}`);
    const rec: Rec = { mesg: def.g, fields: new Map() };
    for (const [num, size, bt] of def.fl) {
      const code = BASE[bt];
      if (code && code[1] === size) rec.fields.set(num, { off: p, code: code[0], little: def.little });
      p += size;
    }
    p += def.dev;
    out.push(rec);
  }
  return out;
}

function get(data: Uint8Array, loc: Loc): number {
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  switch (loc.code) {
    case 'B': return dv.getUint8(loc.off);
    case 'b': return dv.getInt8(loc.off);
    case 'H': return dv.getUint16(loc.off, loc.little);
    case 'h': return dv.getInt16(loc.off, loc.little);
    case 'I': return dv.getUint32(loc.off, loc.little);
    default: return dv.getInt32(loc.off, loc.little);
  }
}

function put(data: Uint8Array, loc: Loc, v: number) {
  const dv = new DataView(data.buffer, data.byteOffset, data.byteLength);
  const range: Record<Code, [number, number]> = {
    B: [0, 0xff], b: [-0x80, 0x7f], H: [0, 0xffff], h: [-0x8000, 0x7fff], I: [0, 0xffffffff], i: [-0x80000000, 0x7fffffff],
  };
  const [lo, hi] = range[loc.code];
  if (!Number.isInteger(v) || v < lo || v > hi) throw new Error(`value ${v} does not fit the field`);
  switch (loc.code) {
    case 'B': dv.setUint8(loc.off, v); break;
    case 'b': dv.setInt8(loc.off, v); break;
    case 'H': dv.setUint16(loc.off, v, loc.little); break;
    case 'h': dv.setInt16(loc.off, v, loc.little); break;
    case 'I': dv.setUint32(loc.off, v, loc.little); break;
    default: dv.setInt32(loc.off, v, loc.little);
  }
}

const first = (recs: Rec[], mesg: number) => recs.find(r => r.mesg === mesg) ?? null;
const valid = (v: number, code: Code) => v !== INVALID[code];

export interface Edge500Profile {
  gender: number | null; age: number | null; height: number | null; weight: number | null;
  rest_hr: number | null; max_hr: number | null; lthr: number | null; ftp: number | null;
  hr_zone_type: number | null; power_zone_type: number | null;
  hr_zones: number[]; power_zones: number[];
}

/** The profile from the two files (tools/edge500_profile.py read()). */
export function readEdge500Profile(settings: Uint8Array, cycling: Uint8Array): Edge500Profile {
  const files: Record<FileKey, Uint8Array> = { settings, cycling };
  const recs: Record<FileKey, Rec[]> = { settings: scan(settings), cycling: scan(cycling) };
  const prof: any = {};
  for (const name of Object.keys(FIELDS) as Edge500Field[]) {
    const [fk, mesg, fnum, scale] = FIELDS[name];
    const loc = first(recs[fk], mesg)?.fields.get(fnum);
    if (!loc) { prof[name] = null; continue; }
    const v = get(files[fk], loc);
    prof[name] = valid(v, loc.code) ? (scale !== 1 ? Number(pyFixed(v / scale, 1)) : v) : null;
  }
  const zt = first(recs.cycling, ZONES_TARGET);
  const z5 = zt?.fields.get(5), z7 = zt?.fields.get(7);
  prof.hr_zone_type = z5 ? get(cycling, z5) : null;
  prof.power_zone_type = z7 ? get(cycling, z7) : null;
  prof.hr_zones = recs.cycling.filter(r => r.mesg === HR_ZONE && r.fields.has(1)).map(r => get(cycling, r.fields.get(1)!));
  prof.power_zones = recs.cycling.filter(r => r.mesg === POWER_ZONE && r.fields.has(1)).map(r => get(cycling, r.fields.get(1)!));
  return prof as Edge500Profile;
}

function scaleZones(buf: Uint8Array, recs: Rec[], mesg: number, old: number, nw: number, cap: number) {
  if (!old || !nw || old === nw) return;
  for (const r of recs) {
    const loc = r.mesg === mesg ? r.fields.get(1) : undefined;
    if (!loc) continue;
    const v = get(buf, loc);
    if (v === 0 || v === 0xff || v === 0xffff || v >= cap) continue;  // zone 0 floor / open top zone
    put(buf, loc, Math.max(1, Math.min(cap - 1, pyRound((v * nw) / old))));
  }
}

export interface Edge500Build {
  settings: Uint8Array | null;   // null = unchanged, nothing to write
  cycling: Uint8Array | null;
  changed: Partial<Record<Edge500Field, { old: number; new: number }>>;
}

/** Patched copies of the two files (tools/edge500_profile.py build()). Pure; never mutates the
 *  input. Values already on the device are skipped, so an all-equal change writes nothing. */
export function buildEdge500Profile(settings: Uint8Array, cycling: Uint8Array,
                                    changes: Partial<Record<Edge500Field, number>>): Edge500Build {
  for (const [name, value] of Object.entries(changes) as [Edge500Field, number][]) {
    if (!(name in FIELDS)) throw new Error(`unknown field ${name}`);
    const [lo, hi] = BOUNDS[name];
    const v = Number(value);
    if (!(lo <= v && v <= hi)) throw new Error(`${name}=${value} out of range ${lo}..${hi}`);
  }
  const bufs: Record<FileKey, Uint8Array> = { settings: new Uint8Array(settings), cycling: new Uint8Array(cycling) };
  const recs: Record<FileKey, Rec[]> = { settings: scan(bufs.settings), cycling: scan(bufs.cycling) };
  const touched = new Set<FileKey>();
  const changed: Edge500Build['changed'] = {};
  for (const [name, value] of Object.entries(changes) as [Edge500Field, number][]) {
    const [fk, mesg, fnum, scale] = FIELDS[name];
    const loc = first(recs[fk], mesg)?.fields.get(fnum);
    if (!loc) throw new Error(`${name}: field ${mesg}.${fnum} not in this device's file`);
    const oldRaw = get(bufs[fk], loc);
    const newRaw = pyRound(Number(value) * scale);
    if (oldRaw === newRaw) continue;
    put(bufs[fk], loc, newRaw);
    touched.add(fk);
    changed[name] = { old: scale !== 1 ? oldRaw / scale : oldRaw, new: Number(value) };
    const zt = first(recs.cycling, ZONES_TARGET);
    const z5 = zt?.fields.get(5), z7 = zt?.fields.get(7);
    if (name === 'max_hr' && z5 && get(bufs.cycling, z5) === 1) scaleZones(bufs.cycling, recs.cycling, HR_ZONE, oldRaw, newRaw, 255);
    if (name === 'ftp' && z7 && get(bufs.cycling, z7) === 1) scaleZones(bufs.cycling, recs.cycling, POWER_ZONE, oldRaw, newRaw, 2000);
  }
  const out: Record<FileKey, Uint8Array | null> = { settings: null, cycling: null };
  for (const fk of ['settings', 'cycling'] as FileKey[]) {
    if (!touched.has(fk)) continue;
    const b = bufs[fk];
    const dv = new DataView(b.buffer, b.byteOffset, b.byteLength);
    const end = b[0] + dv.getUint32(4, true);
    dv.setUint16(end, fitCrc(b.subarray(0, end)), true);
    scan(b);                                 // re-parse: must still be a well-formed FIT
    out[fk] = b;
  }
  return { settings: out.settings, cycling: out.cycling, changed };
}
