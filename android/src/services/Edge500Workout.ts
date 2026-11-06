// A workout in the project schema -> a standard FIT workout (file type 5) for a Garmin Edge 500 -
// the TypeScript twin of the desktop's tools/edge500_workout.py (hardware-checked on a real Edge
// 500, fw 3.30, 2026-11-05). Pure; Edge500Usb.ts puts the file in Garmin/NewFiles, which the Edge
// reads when it leaves USB mode (it then shows under Training > Workouts, max 50).
//
// Input: the schema tools/bryton_from_intervals.py documents (steps with type/duration/target,
// targets ABSOLUTE: watts, bpm, m/s, rpm; repeatStart(N) ... repeatEnd). Garmin targets are
// absolute too, so no FTP/HR conversion is needed:
//   * workout (26): 4 sport = 2 cycling · 6 num_valid_steps · 8 wkt_name
//   * workout_step (27): 254 index · 1 duration_type (0 time ms, 1 distance cm, 5 open,
//     6 repeat-until-steps-complete) · 2 duration_value · 3 target_type (0 speed, 1 HR, 2 open,
//     3 cadence, 4 power) · 4 target_value (0 = custom range; repeat count on a repeat step) ·
//     5/6 custom low/high (HR bpm+100, power W+1000, speed mm/s, cadence rpm) ·
//     7 intensity (0 active, 1 rest, 2 warmup, 3 cooldown)
// Repeats stay repeats (one repeat step pointing back to the block start), as Garmin stores them.

import { fitCrc, pyRound } from './Edge500Profile';
import { stripName } from './Edge500Course';

const NAME_LEN = 16;
export const MAX_STEPS = 50;
const U32_INVALID = 0xffffffff;

const INTENSITY: Record<string, number> = { warmup: 2, cooldown: 3, recovery: 1, rest: 1 };
const TARGET: Record<string, number> = { power: 4, hr: 1, speed: 0, pace: 0, cadence: 3 };

type Step = [number, number, number, number, number, number, number]; // dt, dv, tt, tv, lo, hi, intensity

const own = (o: object, k: any) => typeof k === 'string' && Object.prototype.hasOwnProperty.call(o, k);
const truthy = (v: any) => !(v == null || v === 0 || v === '' || v === false || Number.isNaN(v));

function leaf(st: any): Step {
  const dur = st.duration || {};
  const dn = dur.durationName, dvv = dur.value;
  let dtype: number, dval: number;
  if (dn === 'time' && truthy(dvv)) { dtype = 0; dval = pyRound(Number(dvv) * 1000); }
  else if (dn === 'distance' && truthy(dvv)) { dtype = 1; dval = pyRound(Number(dvv) * 100); }
  else { dtype = 5; dval = U32_INVALID; }                     // open: ends on the lap button
  const tg = st.target || {};
  const vr = tg.valueRange || {};
  let ttype = 2, lo = U32_INVALID, hi = U32_INVALID;
  if (own(TARGET, tg.targetName) && vr.min != null && vr.max != null) {
    const [a, b] = [Number(vr.min), Number(vr.max)].sort((x, y) => x - y);
    const name = tg.targetName as string;
    ttype = TARGET[name];
    if (name === 'power') { lo = pyRound(a) + 1000; hi = pyRound(b) + 1000; }
    else if (name === 'hr') { lo = pyRound(a) + 100; hi = pyRound(b) + 100; }
    else if (name === 'speed' || name === 'pace') { lo = pyRound(a * 1000); hi = pyRound(b * 1000); }
    else { lo = pyRound(a); hi = pyRound(b); }
  }
  const tn = (st.type || {}).typeName;
  const inten = own(INTENSITY, tn) ? INTENSITY[tn] : 0;
  return [dtype, dval, ttype, ttype !== 2 ? 0 : U32_INVALID, lo, hi, inten];
}

/** Schema steps -> FIT step tuples, repeats as Garmin repeat steps. */
export function stepsOf(workout: any): Step[] {
  const out: Step[] = [];
  const starts: [number, number][] = [];
  for (const st of workout.steps || []) {
    const tn = (st.type || {}).typeName;
    if (tn === 'repeatStart') {
      starts.push([out.length, Math.trunc(Number(st.type.value ?? 1))]);
    } else if (tn === 'repeatEnd') {
      if (!starts.length) throw new Error('repeatEnd without repeatStart');
      const [firstIdx, count] = starts.pop()!;
      out.push([6, firstIdx, 0xff, Math.max(1, count), U32_INVALID, U32_INVALID, 0xff]);
    } else {
      out.push(leaf(st));
    }
  }
  if (starts.length) throw new Error('repeatStart without repeatEnd');
  if (!out.length) throw new Error('workout has no steps');
  if (out.length > MAX_STEPS) throw new Error(`${out.length} steps; the Edge takes up to ${MAX_STEPS}`);
  return out;
}

export function workoutName(workout: any, name?: string | null): string {
  const n = stripName(name || workout.name || '').trim() || 'Workout';
  return Array.from(n).slice(0, NAME_LEN - 1).join('');
}

/** The file name the workout gets in Garmin/NewFiles. */
export function workoutFileName(workout: any, name?: string | null): string {
  return workoutName(workout, name).replace(/[^\p{L}\p{N}_\-]/gu, '_') + '.FIT';
}

function utf8(s: string): number[] {
  return Array.from(unescape(encodeURIComponent(s)), c => c.charCodeAt(0));
}

export function encodeEdge500Workout(workout: any, name?: string | null): Uint8Array {
  const steps = stepsOf(workout);
  const nm = utf8(workoutName(workout, name)).slice(0, NAME_LEN - 1);
  const body: number[] = [];
  const u8 = (v: number) => {
    if (!Number.isInteger(v) || v < 0 || v > 0xff) throw new Error(`value ${v} out of range`);
    body.push(v);
  };
  const u16 = (v: number) => {
    if (!Number.isInteger(v) || v < 0 || v > 0xffff) throw new Error(`value ${v} out of range`);
    body.push(v & 0xff, v >>> 8);
  };
  const u32 = (v: number) => {
    if (!Number.isInteger(v) || v < 0 || v > 0xffffffff) throw new Error(`value ${v} out of range`);
    body.push(v & 0xff, (v >>> 8) & 0xff, (v >>> 16) & 0xff, (v >>> 24) & 0xff);
  };
  // file_id: type 5, manufacturer 1 (Garmin), product 65534 (Garmin Connect, as its workouts)
  body.push(0x40, 0, 0); u16(0); u8(4);
  body.push(0, 1, 0x00, 1, 2, 0x84, 2, 2, 0x84, 3, 4, 0x8c);
  u8(0x00); u8(5); u16(1); u16(65534); u32(0);
  body.push(0x41, 0, 0); u16(26); u8(3);
  body.push(4, 1, 0x00, 6, 2, 0x84, 8, NAME_LEN, 0x07);
  u8(0x01); u8(2); u16(steps.length);
  for (let i = 0; i < NAME_LEN; i++) body.push(i < nm.length ? nm[i] : 0);
  body.push(0x42, 0, 0); u16(27); u8(8);
  body.push(254, 2, 0x84, 1, 1, 0x00, 2, 4, 0x86, 3, 1, 0x00, 4, 4, 0x86, 5, 4, 0x86, 6, 4, 0x86, 7, 1, 0x00);
  steps.forEach(([dt, dv, tt, tv, lo, hi, it], i) => {
    u8(0x02); u16(i); u8(dt); u32(dv); u8(tt); u32(tv); u32(lo); u32(hi); u8(it);
  });
  const out = new Uint8Array(14 + body.length + 2);
  const dv = new DataView(out.buffer);
  dv.setUint8(0, 14); dv.setUint8(1, 16); dv.setUint16(2, 2093, true); dv.setUint32(4, body.length, true);
  out.set([0x2e, 0x46, 0x49, 0x54], 8);                                    // ".FIT"
  dv.setUint16(12, fitCrc(out.subarray(0, 12)), true);
  out.set(body, 14);
  dv.setUint16(14 + body.length, fitCrc(out.subarray(0, 14 + body.length)), true);
  return out;
}
