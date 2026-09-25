import { concat, fitCrc16, le16, le32 } from './MageneBle';
import { readProfile, withMagene } from './MageneDevice';
import { packets, pbInt32, pbMsg, pbSint32, transferFile } from './MageneTransfer';

// Send a structured workout to the Magene C406 - the TypeScript port of tools/magene_workout.py
// (HW-verified there 2026-09-25). Input is the project workout schema (the calendar's), in
// absolute watts; time steps only, power/cadence targets (HR/speed have no C406 equivalent).

const INTENSITY: Record<string, number> = { work: 0, recovery: 1, warmup: 2, cooldown: 3 };
const PHASE: Record<string, string> = {
  warmup: 'warmup', cooldown: 'cooldown', recovery: 'recovery', rest: 'recovery',
  interval: 'work', work: 'work', active: 'work', steady: 'work',
};
const POWER_W = 1;

export interface MageneInterval { seconds: number; intensity: number; power: number; powerUnit: number; cadence: number }

function flatten(steps: any[]): any[] {
  const out: any[] = [];
  for (let i = 0; i < steps.length; i++) {
    const st = steps[i];
    const tn = st?.type?.typeName;
    if (tn === 'repeatStart') {
      const count = Math.max(1, Number(st.type.value || 1));
      let depth = 1, j = i + 1;
      while (j < steps.length && depth > 0) {
        const t = steps[j]?.type?.typeName;
        if (t === 'repeatStart') depth++;
        if (t === 'repeatEnd') depth--;
        j++;
      }
      const inner = flatten(steps.slice(i + 1, j - 1));
      for (let k = 0; k < count; k++) out.push(...inner);
      i = j - 1;
    } else if (tn !== 'repeatEnd') {
      out.push(st);
    }
  }
  return out;
}

function mid(target: any, name: string): number {
  if (!target || target.targetName !== name) return 0;
  const r = target.valueRange || {};
  if (r.min == null && r.max == null) return target.value != null ? Math.round(Number(target.value)) : 0;
  const lo = Number(r.min ?? r.max), hi = Number(r.max ?? r.min);
  return Math.round((lo + hi) / 2);
}

export function toIntervals(workout: any): MageneInterval[] {
  const steps = flatten(workout?.steps || []);
  if (!steps.length) throw new Error('workout has no steps');
  return steps.map(st => {
    const d = st.duration || {};
    if (d.durationName === 'distance') throw new Error('the C406 takes time-based steps only; this workout has a distance step');
    const seconds = Math.round(Number(d.value || 0));
    if (seconds <= 0) throw new Error('a step has no duration');
    const phase = PHASE[st?.type?.typeName] || 'work';
    return { seconds, intensity: INTENSITY[phase] ?? 0, power: mid(st.target, 'power'), powerUnit: POWER_W, cadence: mid(st.target, 'cadence') };
  });
}

export function encodeWorkout(iv: MageneInterval[]): Uint8Array {
  const secs = iv.map(i => i.seconds);
  const infor = [
    ...pbInt32(1, iv.length), ...pbInt32(2, 1), ...pbInt32(3, Math.min(...secs)), ...pbInt32(4, Math.max(...secs)),
    ...pbSint32(5, 0), ...pbSint32(6, Math.max(...iv.map(i => i.power))),
    ...pbSint32(7, 0), ...pbSint32(8, Math.max(...iv.map(i => i.cadence))),
    ...pbSint32(9, 0), ...pbSint32(10, 0),
  ];
  const body: number[] = pbMsg(1, infor);
  for (const i of iv) {
    const duration = [...pbInt32(1, 1), ...pbInt32(2, i.seconds), ...pbInt32(3, i.seconds >= 60 ? 1 : 0)];
    const power = [...pbInt32(1, 4), ...pbInt32(2, i.powerUnit), ...pbSint32(3, i.power)];
    const cadence = [...pbInt32(1, 3), ...pbInt32(2, 0), ...pbSint32(3, i.cadence)];
    body.push(...pbMsg(2, [...pbMsg(1, duration), ...pbMsg(2, power), ...pbMsg(2, cadence), ...pbInt32(3, i.intensity)]));
  }
  return new Uint8Array(body);
}

/** The app's NP (TrainFormulaUtil.getNP_PowerList_Double) and TSS - see magene_workout.tss. */
export function tss(iv: MageneInterval[], ftp: number): number {
  ftp = Math.trunc(ftp || 0);
  if (ftp <= 0) return 0;
  const series: number[] = [];
  for (const i of iv) {
    const w = i.powerUnit === 0 ? Math.floor((ftp * i.power) / 100) : i.power;
    for (let s = 0; s < i.seconds; s++) series.push(w);
  }
  const n = series.length - 30;
  if (n <= 0) return 0;
  const pre = [0];
  for (const v of series) pre.push(pre[pre.length - 1] + v);
  let acc = 0;
  for (let i = 0; i < n; i++) acc += ((pre[series.length - i] - pre[series.length - 30 - i]) / 30) ** 4;
  const np = Math.round((acc / n) ** 0.25 * 1e4) / 1e4;
  return (series.length * np * (np / ftp)) / ftp / 36;
}

function utf8(s: string): number[] {
  return Array.from(unescape(encodeURIComponent(s)), c => c.charCodeAt(0));
}

export function workoutInfo(id: number, tssValue: number, totalSeconds: number, crc: number, name: string): Uint8Array {
  let nb = utf8(name);
  while (nb.length > 255) { name = name.slice(0, -1); nb = utf8(name); }
  return concat([0x40, 0x88], le32(id), le16(Math.ceil(tssValue * 10) & 0xffff), le32(totalSeconds), le16(crc), [nb.length], nb);
}

export async function sendWorkout(address: string, workout: any, name?: string): Promise<{ ok: boolean; error?: string }> {
  const iv = toIntervals(workout);
  const file = encodeWorkout(iv);
  const crc = fitCrc16(file);
  const total = iv.reduce((a, i) => a + i.seconds, 0);
  return withMagene(address, async mtu => {
    const ftp = (await readProfile())?.ftp || 0; // the TSS shown uses the device's own FTP
    const info = workoutInfo(Math.floor(Date.now() / 1000) & 0x7fffffff, tss(iv, ftp), total, crc,
      name || workout?.name || 'Workout');
    const res = await transferFile(info, packets(file, mtu));
    return { ok: res.error === null, error: res.error ?? undefined };
  });
}
