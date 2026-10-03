// Twin of tools/race_alertness.py: the two-process sleep model (Borbely) personalised to the rider's
// usual sleep. Published parameters, never fitted (see the Python header). Parity-tested.
import { NDT, hourOf } from './pyCompat';

export const TAU_RISE_H = 18.2;
export const TAU_FALL_H = 4.2;
export const S_AFTER_NORMAL_SLEEP = 0.15;
export const W_S = 0.7, W_C = 0.3;
export const MAX_LOSS = 0.20;
export const Z_WORST = 0.97;
export const DANGER_ALERTNESS = 35.0;
export const TIRED_ALERTNESS = 45.0;

const pyMod = (a: number, n: number) => ((a % n) + n) % n;   // Python % (sign of the divisor)

export class Alertness {
  bed_h: number; wake_h: number; peak_h: number; z_ref: number; s: number;
  min_alertness = 100.0;
  min_at: NDT | null = null;

  constructor(bedH = 22.0, wakeH = 6.0, awakeHAtStart: number | null = null, start: NDT | null = null) {
    this.bed_h = pyMod(Number(bedH), 24.0);
    this.wake_h = pyMod(Number(wakeH), 24.0);
    this.peak_h = pyMod(this.wake_h + 10.0, 24.0);
    const dayH = pyMod(this.bed_h - this.wake_h, 24.0);
    const sBed = 1.0 - (1.0 - S_AFTER_NORMAL_SLEEP) * Math.exp(-dayH / TAU_RISE_H);
    this.z_ref = W_S * sBed - W_C * this.cAtHour(this.bed_h);
    let awake = awakeHAtStart;
    if (awake === null || awake === undefined) {
      if (start !== null && start !== undefined) {
        const a = pyMod(hourOf(start) - this.wake_h, 24.0);
        awake = a <= dayH + 3.0 ? a : 0.0;
      } else awake = 0.0;
    }
    this.s = 1.0 - (1.0 - S_AFTER_NORMAL_SLEEP) * Math.exp(-awake / TAU_RISE_H);
  }

  cAtHour(h: number): number {
    return Math.cos(2.0 * Math.PI * pyMod(h - this.peak_h, 24.0) / 24.0);
  }
  z(dt: NDT): number { return W_S * this.s - W_C * this.cAtHour(hourOf(dt)); }
  alertness(dt: NDT): number { return Math.max(0.0, Math.min(100.0, 100.0 * (1.0 - this.z(dt)))); }
  speedFactor(dt: NDT): number {
    const span = Math.max(1e-6, Z_WORST - this.z_ref);
    const loss = MAX_LOSS * Math.max(0.0, Math.min(1.0, (this.z(dt) - this.z_ref) / span));
    return 1.0 - loss;
  }
  awake(seconds: number): void { this.s = 1.0 - (1.0 - this.s) * Math.exp(-seconds / 3600.0 / TAU_RISE_H); }
  sleep(seconds: number): void { this.s *= Math.exp(-seconds / 3600.0 / TAU_FALL_H); }
  record(dt: NDT): void {
    const a = this.alertness(dt);
    if (a < this.min_alertness) { this.min_alertness = a; this.min_at = dt; }
  }
}

export function band(a: number): 'fine' | 'tired' | 'dangerous' {
  return a >= TIRED_ALERTNESS ? 'fine' : (a >= DANGER_ALERTNESS ? 'tired' : 'dangerous');
}
