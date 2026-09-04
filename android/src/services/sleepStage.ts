// On-device twin of tools/sleep_stage.py: turn a Polar Verity Sense overnight offline recording
// (raw PPG + ACC) into a night of HRV + resting HR, entirely on the phone so André can sleep with
// just the band and process in the morning with nothing else connected.
//
//   PPG channels -> pick best channel -> band-pass -> adaptive peak pick -> R-R (ms)
//     (ACC magnitude gates out the movement seconds so a toss doesn't fake a beat)
//   -> hrv.ts cleanRr/hrvSummary (the SAME math the morning strap reading uses, hrv.ts<->hrv.py)
//   -> windowed across the night: overnight RMSSD, resting HR, and the per-window trend curve.
//
// Difference from the Python reference: the phone processes a whole night (~1.5M samples), so the
// band-pass here is an O(n) time-domain filter (moving-average detrend + smooth) rather than the
// desktop's full-night FFT — same idea, phone-affordable. Each side is validated against a
// known-truth synthetic night independently, so they don't have to be bit-identical, just correct.
//
// Phase 1 stops at "overnight HRV + resting-HR trend"; the per-window rows already carry the R-R +
// motion features a Phase-2 sleep stager will consume, so nothing here changes for it.
//
// We hand hrvSummary the RAW derived R-R in ms and let cleanRr do the median-seeded ectopic
// correction — no pre-trim, no pre-smooth (the documented way; pre-cleaning double-corrects).

import {
  hrvSummary,
  RR_MIN_MS,
  RR_MAX_MS,
  type HrvSummary,
} from './hrv';

// PPG band + peak-pick constants (mirror sleep_stage.py)
const MIN_BEAT_MS = RR_MIN_MS; // peaks closer than this can't be two real beats (200-bpm cap)
const ACC_MOTION_STD_G = 0.06; // per-second ACC magnitude std (g) above which we drop the beats
const WINDOW_S = 300; // 5-minute HRV windows
const RESTING_HR_PCTILE = 10; // resting HR = 10th-percentile window HR (the sustained floor)

export interface SleepWindow {
  startS: number;
  rmssdMs: number | null;
  meanHrBpm: number | null;
  nBeats: number;
  sdnnMs: number | null;
}

export interface SleepOvernight {
  overnightRmssdMs: number | null;
  overnightLnRmssdX20: number | null;
  restingHrBpm: number | null;
  minHrBpm: number | null;
  meanHrBpm: number | null;
  nWindows: number;
  recordingSpanMin: number;
}

export interface SleepResult {
  ok: boolean;
  error?: string;
  startTime?: string;
  source: string;
  beatsUsed: number;
  beatsDroppedMotion: number;
  overnight: SleepOvernight;
  overallHrv: HrvSummary;
  windows: SleepWindow[];
}

export interface Recording {
  start_time?: string;
  ppg_hz?: number;
  acc_hz?: number;
  ppg: number[][]; // one row per PPG sample = its channel list
  acc?: number[][]; // one row per ACC sample = [x,y,z] in mg
}

// Choose the PPG channel with the strongest pulsatile signal (highest variance).
function pickChannel(ppg: number[][]): Float64Array {
  const n = ppg.length;
  if (n === 0) return new Float64Array(0);
  const nch = ppg[0].length;
  let best = 0;
  let bestVar = -1;
  for (let c = 0; c < nch; c++) {
    let mean = 0;
    for (let i = 0; i < n; i++) mean += ppg[i][c];
    mean /= n;
    let v = 0;
    for (let i = 0; i < n; i++) {
      const d = ppg[i][c] - mean;
      v += d * d;
    }
    if (v > bestVar) {
      bestVar = v;
      best = c;
    }
  }
  const out = new Float64Array(n);
  for (let i = 0; i < n; i++) out[i] = ppg[i][best];
  return out;
}

// Prefix-sum moving average (window = half-widths ±w). O(n).
function movingAvg(x: Float64Array, w: number): Float64Array {
  const n = x.length;
  const out = new Float64Array(n);
  const csum = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) csum[i + 1] = csum[i] + x[i];
  for (let i = 0; i < n; i++) {
    const a = Math.max(0, i - w);
    const b = Math.min(n, i + w + 1);
    out[i] = (csum[b] - csum[a]) / (b - a);
  }
  return out;
}

// Time-domain band-pass for the pulse: subtract a long moving average (removes baseline wander
// < ~0.7 Hz) then smooth with a short one (removes noise > ~4 Hz). O(n), phone-friendly.
function bandpass(sig: Float64Array, fs: number): Float64Array {
  const n = sig.length;
  if (n < 4) return sig;
  const longW = Math.max(1, Math.round(fs * 0.6)); // ~1.2 s window -> HP ~0.7 Hz
  const shortW = Math.max(1, Math.round(fs / 12)); // ~0.08 s window -> LP ~4 Hz
  const base = movingAvg(sig, longW);
  const detr = new Float64Array(n);
  for (let i = 0; i < n; i++) detr[i] = sig[i] - base[i];
  return movingAvg(detr, shortW);
}

// Adaptive local-maxima peak pick with a physiologic refractory gap. Returns peak sample indices.
function detectPeaks(sig: Float64Array, fs: number): number[] {
  const n = sig.length;
  if (n < 3) return [];
  const minGap = Math.max(1, Math.round((fs * MIN_BEAT_MS) / 1000));
  const win = Math.max(1, Math.round(fs * 2)); // 2 s sliding RMS as an adaptive amplitude ref
  const power = new Float64Array(n);
  for (let i = 0; i < n; i++) power[i] = sig[i] * sig[i];
  const csum = new Float64Array(n + 1);
  for (let i = 0; i < n; i++) csum[i + 1] = csum[i] + power[i];
  const peaks: number[] = [];
  for (let i = 1; i < n - 1; i++) {
    const a = Math.max(0, i - win);
    const b = Math.min(n, i + win);
    const rms = Math.sqrt(Math.max((csum[b] - csum[a]) / (b - a), 1e-12));
    const thr = 0.3 * rms;
    if (sig[i] > sig[i - 1] && sig[i] >= sig[i + 1] && sig[i] > thr) {
      const last = peaks.length ? peaks[peaks.length - 1] : -minGap - 1;
      if (i - last < minGap) {
        if (sig[i] > sig[last]) peaks[peaks.length - 1] = i;
      } else {
        peaks.push(i);
      }
    }
  }
  return peaks;
}

// Per-second boolean: was the band moving (ACC magnitude std > threshold)? All-false when no ACC.
function motionMask(
  acc: number[][] | undefined,
  accHz: number,
  nSeconds: number,
): Uint8Array {
  const mask = new Uint8Array(nSeconds);
  if (!acc || acc.length === 0) return mask;
  const mag = new Float64Array(acc.length);
  for (let i = 0; i < acc.length; i++) {
    const [x, y, z] = acc[i];
    mag[i] = Math.sqrt(x * x + y * y + z * z) / 1000; // mg -> g
  }
  for (let s = 0; s < nSeconds; s++) {
    const a = Math.floor(s * accHz);
    const b = Math.floor((s + 1) * accHz);
    if (b <= a) continue;
    let mean = 0;
    for (let i = a; i < b && i < mag.length; i++) mean += mag[i];
    const cnt = Math.min(b, mag.length) - a;
    if (cnt <= 0) continue;
    mean /= cnt;
    let v = 0;
    for (let i = a; i < b && i < mag.length; i++) {
      const d = mag[i] - mean;
      v += d * d;
    }
    if (Math.sqrt(v / cnt) > ACC_MOTION_STD_G) mask[s] = 1;
  }
  return mask;
}

// PPG (+ optional ACC) -> raw R-R (ms), beat times (s), and count dropped for motion.
export function ppgToRr(
  ppg: number[][],
  ppgHz: number,
  acc?: number[][],
  accHz = 52,
): { rrMs: number[]; beatTimesS: number[]; droppedMotion: number } {
  const sig = bandpass(pickChannel(ppg), ppgHz);
  const peaks = detectPeaks(sig, ppgHz);
  if (peaks.length < 2) return { rrMs: [], beatTimesS: [], droppedMotion: 0 };
  const t = peaks.map((p) => p / ppgHz);
  const nSeconds = Math.ceil(t[t.length - 1]) + 1;
  const moving = motionMask(acc, accHz, nSeconds);
  const rrMs: number[] = [];
  const beatTimesS: number[] = [];
  let droppedMotion = 0;
  for (let i = 1; i < t.length; i++) {
    const sec = Math.floor(t[i]);
    if (sec < nSeconds && moving[sec]) {
      droppedMotion++;
      continue;
    }
    rrMs.push((t[i] - t[i - 1]) * 1000);
    beatTimesS.push(t[i]);
  }
  return { rrMs, beatTimesS, droppedMotion };
}

function percentile(sorted: number[], p: number): number {
  if (sorted.length === 0) return NaN;
  const idx = (p / 100) * (sorted.length - 1);
  const lo = Math.floor(idx);
  const hi = Math.ceil(idx);
  if (lo === hi) return sorted[lo];
  return sorted[lo] + (sorted[hi] - sorted[lo]) * (idx - lo);
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const m = Math.floor(s.length / 2);
  return s.length % 2 ? s[m] : (s[m - 1] + s[m]) / 2;
}

// Window the night -> per-window HRV + HR, then overnight roll-ups (median RMSSD, low-pctile HR).
export function analyseNight(
  rrMs: number[],
  beatTimesS: number[],
  windowS = WINDOW_S,
): { overnight: SleepOvernight; windows: SleepWindow[]; overall: HrvSummary } {
  const windows: SleepWindow[] = [];
  if (beatTimesS.length) {
    const total = beatTimesS[beatTimesS.length - 1];
    const nWin = Math.floor(total / windowS) + 1;
    for (let w = 0; w < nWin; w++) {
      const lo = w * windowS;
      const hi = (w + 1) * windowS;
      const seg: number[] = [];
      for (let i = 0; i < beatTimesS.length; i++) {
        if (beatTimesS[i] >= lo && beatTimesS[i] < hi) seg.push(rrMs[i]);
      }
      if (seg.length < 20) continue;
      const s = hrvSummary(seg, true);
      windows.push({
        startS: lo,
        rmssdMs: s.rmssdMs,
        meanHrBpm: s.meanHrBpm,
        nBeats: s.nBeats,
        sdnnMs: s.sdnnMs,
      });
    }
  }
  const overall = hrvSummary(rrMs, true);
  const rmssds = windows.map((w) => w.rmssdMs).filter((x): x is number => x != null);
  const hrs = windows.map((w) => w.meanHrBpm).filter((x): x is number => x != null);
  const hrsSorted = [...hrs].sort((a, b) => a - b);
  const medRmssd = rmssds.length ? median(rmssds) : null;
  const overnight: SleepOvernight = {
    overnightRmssdMs: medRmssd != null ? round1(medRmssd) : null,
    overnightLnRmssdX20: medRmssd != null && medRmssd > 0 ? round1(20 * Math.log(medRmssd)) : null,
    restingHrBpm: hrs.length ? round1(percentile(hrsSorted, RESTING_HR_PCTILE)) : null,
    minHrBpm: hrs.length ? round1(hrsSorted[0]) : null,
    meanHrBpm: overall.meanHrBpm,
    nWindows: windows.length,
    recordingSpanMin: beatTimesS.length ? round1(beatTimesS[beatTimesS.length - 1] / 60) : 0,
  };
  return { overnight, windows, overall };
}

function round1(x: number): number {
  return Math.round(x * 10) / 10;
}

// Top level: a recording -> the full result (what the Sleep screen renders).
export function processRecording(rec: Recording): SleepResult {
  const ppg = rec.ppg || [];
  const ppgHz = rec.ppg_hz || 55;
  const accHz = rec.acc_hz || 52;
  const emptyHrv: HrvSummary = hrvSummary([], true);
  if (!ppg.length) {
    return {
      ok: false,
      error: 'recording has no PPG samples',
      source: 'polar_verity_sense_offline_ppg',
      beatsUsed: 0,
      beatsDroppedMotion: 0,
      overnight: analyseNight([], []).overnight,
      overallHrv: emptyHrv,
      windows: [],
    };
  }
  const { rrMs, beatTimesS, droppedMotion } = ppgToRr(ppg, ppgHz, rec.acc, accHz);
  if (rrMs.length < 20) {
    return {
      ok: false,
      error: `too few beats derived from PPG (${rrMs.length}) — check the recording`,
      source: 'polar_verity_sense_offline_ppg',
      beatsUsed: rrMs.length,
      beatsDroppedMotion: droppedMotion,
      overnight: analyseNight([], []).overnight,
      overallHrv: emptyHrv,
      windows: [],
    };
  }
  const { overnight, windows, overall } = analyseNight(rrMs, beatTimesS);
  return {
    ok: true,
    startTime: rec.start_time,
    source: 'polar_verity_sense_offline_ppg',
    beatsUsed: rrMs.length,
    beatsDroppedMotion: droppedMotion,
    overnight,
    overallHrv: overall,
    windows,
  };
}

// re-export the physiologic bounds so callers stay on the ONE definition
export { RR_MIN_MS, RR_MAX_MS };
