// Bryton Aero 60 planned-workout FIT: decode + encode, pure TS (no RN deps).
//
// This is the byte-for-byte twin of the desktop tools/bryton_workout.py + bryton_from_intervals.py
// (both proven byte-exact and hardware-verified against the device). The Aero 60 stores each
// planned workout as one tiny standard-FIT file in System/Plan/Cycling/*.fit: a file_id (type 5),
// one `workout` message (global 26) and one `workout_step` (global 27) per step. Conventions
// (all confirmed on André's device, 2026-09-24):
//   unit -> workout_step target_type (field 3): speed=0, cadence=3, ftp=245, lthr=248, mhr=249
//   interval mode -> duration_type (field 1): time=0 (ms), distance=1 (cm)
//   %-units (ftp/mhr/lthr) encode as percent+100000; cadence = rpm; speed = mm/s TRUNCATED
//   intensity (field 7): work=0, recovery=1, warmup=2, cooldown=3
//   Range vs Target = low!=high vs low==high. Repeats are stored FLATTENED.
//
// Ordering that matters for byte-exactness (learned from the device): the file emits file_id def+
// data, then BOTH the workout def and the workout_step def, THEN the workout data + step data.

const PROFILE_VERSION = 2167;
const MANUFACTURER = 255;
const FILE_TYPE_WORKOUT = 5;
const FIT_EPOCH = 631065600;

const E = 0x00, U8 = 0x02, U16 = 0x84, U32 = 0x86, STR = 0x07;

const CRC_TABLE = [
  0x0000, 0xcc01, 0xd801, 0x1400, 0xf001, 0x3c00, 0x2800, 0xe401,
  0xa001, 0x6c00, 0x7800, 0xb401, 0x5000, 0x9c01, 0x8801, 0x4400,
];
function fitCrc(bytes: number[] | Uint8Array): number {
  let crc = 0;
  for (const byte of bytes) {
    let tmp = CRC_TABLE[crc & 0x0f];
    crc = ((crc >> 4) ^ tmp ^ CRC_TABLE[byte & 0x0f]) & 0xffff;
    tmp = CRC_TABLE[crc & 0x0f];
    crc = ((crc >> 4) ^ tmp ^ CRC_TABLE[(byte >> 4) & 0x0f]) & 0xffff;
  }
  return crc & 0xffff;
}

export type BrytonUnit = 'ftp' | 'mhr' | 'lthr' | 'speed' | 'cadence';
export type BrytonIntensity = 'warmup' | 'work' | 'recovery' | 'cooldown';
export type BrytonMode = 'time' | 'distance';

export interface BrytonStep {
  intensity: BrytonIntensity;
  duration: number; // seconds (time) or metres (distance)
  low: number;      // human units: % / rpm / km-h
  high: number;
}
export interface BrytonWorkout {
  name: string;
  unit: BrytonUnit;
  basedOn?: 'range' | 'target';
  intervalMode: BrytonMode;
  timeCreated?: number; // FIT seconds; default now
  steps: BrytonStep[];
}

const UNIT_TO_TARGET: Record<BrytonUnit, number> = { speed: 0, cadence: 3, ftp: 245, lthr: 248, mhr: 249 };
const TARGET_TO_UNIT: Record<number, BrytonUnit> = { 0: 'speed', 3: 'cadence', 245: 'ftp', 248: 'lthr', 249: 'mhr' };
const INTENSITY_TO_CODE: Record<BrytonIntensity, number> = { work: 0, recovery: 1, warmup: 2, cooldown: 3 };
const CODE_TO_INTENSITY: Record<number, BrytonIntensity> = { 0: 'work', 1: 'recovery', 2: 'warmup', 3: 'cooldown' };
const PCT_UNITS = new Set<BrytonUnit>(['ftp', 'mhr', 'lthr']);

export function targetToRaw(unit: BrytonUnit, value: number): number {
  if (PCT_UNITS.has(unit)) return 100000 + Math.round(value);
  if (unit === 'cadence') return Math.round(value);
  if (unit === 'speed') return Math.trunc((value / 3.6) * 1000); // km/h -> mm/s, TRUNCATED
  throw new Error(`unknown unit ${unit}`);
}
export function rawToTarget(unit: BrytonUnit, raw: number): number {
  if (PCT_UNITS.has(unit)) return raw - 100000;
  if (unit === 'cadence') return raw;
  if (unit === 'speed') return Math.round((raw * 3.6) / 1000 * 10) / 10;
  throw new Error(`unknown unit ${unit}`);
}
function durationToRaw(mode: BrytonMode, value: number): number {
  return mode === 'time' ? Math.round(value * 1000) : Math.round(value * 100);
}
function rawToDuration(mode: BrytonMode, raw: number): number {
  return mode === 'time' ? raw / 1000 : raw / 100;
}

// ─── binary helpers ───────────────────────────────────────────────────────────
function u8(b: number[], v: number) { b.push(v & 0xff); }
function u16(b: number[], v: number) { b.push(v & 0xff, (v >>> 8) & 0xff); }
function u32(b: number[], v: number) { b.push(v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff); }
function fixedStr(b: number[], s: string, size: number) {
  // Names on the device are ASCII ("Step Name", workout names); one byte per char, NUL-padded to
  // `size` with a guaranteed trailing NUL, exactly like the Python encoder.
  const n = Math.min(s.length, size - 1);
  for (let i = 0; i < n; i++) b.push(s.charCodeAt(i) & 0xff);
  for (let i = n; i < size; i++) b.push(0);
}
function writeDef(b: number[], local: number, global: number, fields: [number, number, number][]) {
  u8(b, 0x40 | local); u8(b, 0); u8(b, 0); u16(b, global); u8(b, fields.length);
  for (const [num, size, bt] of fields) { u8(b, num); u8(b, size); u8(b, bt); }
}

export function encodeBrytonWorkout(w: BrytonWorkout): Uint8Array {
  const unit = w.unit;
  const targetType = UNIT_TO_TARGET[unit];
  const durType = w.intervalMode === 'time' ? 0 : 1;
  const tc = w.timeCreated ?? (Math.floor(Date.now() / 1000) - FIT_EPOCH);

  const data: number[] = [];
  // file_id (local 0, global 0)
  writeDef(data, 0, 0, [[0, 1, E], [1, 2, U16], [4, 4, U32]]);
  u8(data, 0); u8(data, FILE_TYPE_WORKOUT); u16(data, MANUFACTURER); u32(data, tc >>> 0);
  // workout (local 0, global 26) + workout_step def (local 2, global 27) BEFORE any data
  writeDef(data, 0, 26, [[8, 16, STR], [6, 2, U16], [4, 1, E]]);
  writeDef(data, 2, 27, [[0, 10, STR], [2, 4, U32], [4, 4, U32], [5, 4, U32], [6, 4, U32],
                         [254, 2, U16], [1, 1, E], [3, 1, E], [7, 1, E]]);
  // workout data
  u8(data, 0); fixedStr(data, w.name || 'Workout', 16); u16(data, w.steps.length); u8(data, 2);
  // steps
  w.steps.forEach((st, i) => {
    const low = targetToRaw(unit, st.low);
    const high = targetToRaw(unit, st.high ?? st.low);
    u8(data, 2); fixedStr(data, 'Step Name', 10);
    u32(data, durationToRaw(w.intervalMode, st.duration));
    u32(data, 0); u32(data, low); u32(data, high);
    u16(data, i); u8(data, durType); u8(data, targetType); u8(data, INTENSITY_TO_CODE[st.intensity]);
  });

  const hdr: number[] = [];
  u8(hdr, 14); u8(hdr, 0x10); u16(hdr, PROFILE_VERSION); u32(hdr, data.length);
  for (const c of [0x2e, 0x46, 0x49, 0x54]) hdr.push(c); // ".FIT"
  u16(hdr, fitCrc(hdr));
  const crc = fitCrc(data);
  return new Uint8Array([...hdr, ...data, crc & 0xff, (crc >>> 8) & 0xff]);
}

// ─── decode ─────────────────────────────────────────────────────────────────
export function decodeBrytonWorkout(bytes: Uint8Array): BrytonWorkout {
  if (bytes[8] !== 0x2e || bytes[9] !== 0x46) throw new Error('not a FIT file');
  const hdrSz = bytes[0];
  const dsize = bytes[4] | (bytes[5] << 8) | (bytes[6] << 16) | (bytes[7] << 24);
  let pos = hdrSz;
  const end = hdrSz + dsize;
  const defs: Record<number, { global: number; fields: [number, number, number][] }> = {};
  let name = '', timeCreated: number | undefined;
  const raw: { idx: number; dtype: number; dval: number; ttype: number; low: number; high: number; inten: number }[] = [];

  const rd = (off: number, size: number) => {
    let v = 0;
    for (let i = 0; i < size; i++) v |= bytes[off + i] << (8 * i);
    return v >>> 0;
  };
  while (pos < end) {
    const rh = bytes[pos++];
    if (rh & 0x40) {
      const local = rh & 0x0f;
      const global = bytes[pos + 2] | (bytes[pos + 3] << 8);
      const nf = bytes[pos + 4]; pos += 5;
      const fields: [number, number, number][] = [];
      for (let i = 0; i < nf; i++) { fields.push([bytes[pos], bytes[pos + 1], bytes[pos + 2]]); pos += 3; }
      defs[local] = { global, fields };
    } else {
      const local = rh & 0x0f;
      const { global, fields } = defs[local];
      const rec: Record<number, number | string> = {};
      for (const [num, size, bt] of fields) {
        if ((bt & 0x1f) === 0x07) {
          let s = '';
          for (let i = 0; i < size; i++) { const c = bytes[pos + i]; if (c === 0) break; s += String.fromCharCode(c); }
          rec[num] = s;
        } else rec[num] = rd(pos, size);
        pos += size;
      }
      if (global === 0) timeCreated = rec[4] as number;
      else if (global === 26) name = (rec[8] as string) ?? '';
      else if (global === 27)
        raw.push({ idx: rec[254] as number, dtype: rec[1] as number, dval: rec[2] as number,
                   ttype: rec[3] as number, low: rec[5] as number, high: rec[6] as number, inten: rec[7] as number });
    }
  }
  raw.sort((a, b) => a.idx - b.idx);
  if (!raw.length) throw new Error('no steps');
  const unit = TARGET_TO_UNIT[raw[0].ttype];
  const mode: BrytonMode = raw[0].dtype === 0 ? 'time' : 'distance';
  let basedOn: 'range' | 'target' = 'target';
  const steps: BrytonStep[] = raw.map(s => {
    if (s.low !== s.high) basedOn = 'range';
    return { intensity: CODE_TO_INTENSITY[s.inten], duration: rawToDuration(mode, s.dval),
             low: rawToTarget(unit, s.low), high: rawToTarget(unit, s.high) };
  });
  return { name, unit, basedOn, intervalMode: mode, timeCreated, steps };
}

// ─── convert the project workout schema (absolute W/bpm) -> a Bryton-native workout ───
// Mirrors tools/bryton_from_intervals.py. `thresholds` are the device's own FTP/MaxHR/LTHR so the
// % the watch resolves matches what was planned. Un-mappable targets fall back to a per-phase band.
export interface Thresholds { ftp?: number; maxHr?: number; lthr?: number; }
const PHASE_TO_INTENSITY: Record<string, BrytonIntensity> = {
  warmup: 'warmup', cooldown: 'cooldown', recovery: 'recovery', rest: 'recovery',
  interval: 'work', work: 'work', active: 'work', steady: 'work',
};
const TARGET_FAMILY: Record<string, 'power' | 'hr' | 'speed' | 'cadence'> = {
  power: 'power', hr: 'hr', pace: 'speed', speed: 'speed', cadence: 'cadence',
};
const DEFAULT_PCT: Record<BrytonIntensity, [number, number]> = {
  warmup: [50, 60], recovery: [45, 55], cooldown: [45, 55], work: [85, 95],
};

export function schemaToBryton(workout: any, thr: Thresholds, hrUnit: BrytonUnit = 'mhr'): BrytonWorkout {
  // one-level flatten of repeatStart(N)/repeatEnd brackets, exactly like the Bryton app stores them
  const out: any[] = [];
  const src = workout.steps || [];
  for (let i = 0; i < src.length; i++) {
    const st = src[i];
    const tn = st?.type?.typeName;
    if (tn === 'repeatStart') {
      const count = Number(st.type.value || 1);
      const inner: any[] = [];
      let j = i + 1;
      for (; j < src.length && src[j]?.type?.typeName !== 'repeatEnd'; j++) inner.push(src[j]);
      for (let r = 0; r < count; r++) out.push(...inner);
      i = j;
    } else if (tn === 'repeatEnd') { /* skip */ }
    else out.push(st);
  }

  // pick unit from work steps
  const workFams: string[] = [];
  for (const st of out) {
    const phase = PHASE_TO_INTENSITY[st?.type?.typeName] || 'work';
    const fam = TARGET_FAMILY[st?.target?.targetName];
    if (phase === 'work' && fam) workFams.push(fam);
  }
  const fam = workFams.length ? workFams.sort((a, b) =>
    workFams.filter(x => x === b).length - workFams.filter(x => x === a).length)[0] : 'power';
  const unit: BrytonUnit = fam === 'hr' ? hrUnit : fam === 'speed' ? 'speed' : fam === 'cadence' ? 'cadence' : 'ftp';

  const modes = new Set(out.filter(s => s.duration).map(s => s.duration.durationName === 'distance' ? 'distance' : 'time'));
  if (modes.size > 1) throw new Error('workout mixes time and distance steps');
  const mode: BrytonMode = (modes.values().next().value as BrytonMode) || 'time';

  const bandFor = (target: any): [number, number] | null => {
    const f = TARGET_FAMILY[target?.targetName];
    const vr = target?.valueRange;
    if (!f || !vr || vr.min == null || vr.max == null) return null;
    let lo = Number(vr.min), hi = Number(vr.max);
    if (lo > hi) [lo, hi] = [hi, lo];
    if (unit === 'ftp') return f === 'power' && thr.ftp ? [Math.round((lo / thr.ftp) * 100), Math.round((hi / thr.ftp) * 100)] : null;
    if (unit === 'mhr') return f === 'hr' && thr.maxHr ? [Math.round((lo / thr.maxHr) * 100), Math.round((hi / thr.maxHr) * 100)] : null;
    if (unit === 'lthr') return f === 'hr' && thr.lthr ? [Math.round((lo / thr.lthr) * 100), Math.round((hi / thr.lthr) * 100)] : null;
    if (unit === 'speed') return f === 'speed' ? [Math.round(lo * 3.6 * 10) / 10, Math.round(hi * 3.6 * 10) / 10] : null;
    if (unit === 'cadence') return f === 'cadence' ? [Math.round(lo), Math.round(hi)] : null;
    return null;
  };

  const steps: BrytonStep[] = out.map(st => {
    const phase = PHASE_TO_INTENSITY[st?.type?.typeName] || 'work';
    const dur = st.duration?.value;
    const band = bandFor(st.target) || DEFAULT_PCT[phase];
    return { intensity: phase, duration: Number(dur || 0), low: band[0], high: band[1] };
  });
  return { name: workout.name || 'Workout', unit, basedOn: 'range', intervalMode: mode, steps };
}
