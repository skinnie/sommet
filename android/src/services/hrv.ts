// Time-domain HRV math for a BLE heart-rate strap that reports R-R intervals (COOSPO HW9, Polar,
// most chest straps). Direct TypeScript port of the desktop's tools/hrv.py — same Task-Force 1996
// formulas (RMSSD/SDNN/pNN50/mean-HR) and the same median-seeded artifact correction — so mobile
// and desktop compute identical numbers from the same beats. No dependencies; shared by iOS+Android.
//
// R-R comes from the standard Heart Rate Measurement characteristic (0x2A37): see
// parseHrMeasurement() for the SIG byte layout. RR values are milliseconds (ints).

export const RR_MIN_MS = 300; // 200 bpm — faster is a decode/double-count artifact
export const RR_MAX_MS = 2000; // 30 bpm — slower is a dropped-beat artifact
export const ECTOPIC_RATIO = 0.2; // drop a beat that deviates >20% from the running reference
const NN50_MS = 50;

/** R-R intervals (ms) carried in one 0x2A37 notification (may be empty). */
export function parseHrMeasurement(data: Uint8Array): number[] {
  if (!data || data.length === 0) return [];
  const flags = data[0];
  let i = 1;
  i += flags & 0x01 ? 2 : 1; // HR value: uint16 or uint8
  if (flags & 0x08) i += 2; // Energy Expended present -> uint16
  const rr: number[] = [];
  if (flags & 0x10) {
    // RR-Interval(s) present, uint16 LE in 1/1024 s
    while (i + 1 < data.length) {
      const raw = data[i] | (data[i + 1] << 8);
      rr.push(Math.round((raw * 1000) / 1024));
      i += 2;
    }
  }
  return rr;
}

function median(xs: number[]): number {
  const s = [...xs].sort((a, b) => a - b);
  const n = s.length;
  if (n === 0) return 0;
  return n % 2 ? s[(n - 1) / 2] : (s[n / 2 - 1] + s[n / 2]) / 2;
}

export interface CleanReport {
  nRaw: number;
  nOutOfRange: number;
  nEctopic: number;
  nUsed: number;
  removedPct: number;
}

/**
 * Artifact-correct an R-R series. First drop physiologically impossible intervals, then drop
 * ectopic beats that deviate >ratio from a running reference SEEDED ON THE MEDIAN (not the first
 * beat) — a real strap often opens with a spurious partial interval, and seeding on that would
 * delete the whole recording (the exact bug fixed in hrv.py). The reference tracks each accepted
 * beat so slow HR drift is kept and real respiratory HRV isn't clipped.
 */
export function cleanRr(
  rr: number[],
  rrMin = RR_MIN_MS,
  rrMax = RR_MAX_MS,
  ectopicRatio = ECTOPIC_RATIO,
): { cleaned: number[]; report: CleanReport } {
  const ints = rr.map((x) => Math.round(x));
  const nRaw = ints.length;
  const ranged = ints.filter((x) => x >= rrMin && x <= rrMax);
  const nOutOfRange = nRaw - ranged.length;

  let cleaned: number[];
  let nEctopic = 0;
  if (ranged.length < 2) {
    cleaned = ranged.slice();
  } else {
    let ref = median(ranged); // robust seed: a bad first/last beat can't cascade
    cleaned = [];
    for (const x of ranged) {
      if (Math.abs(x - ref) > ectopicRatio * ref) {
        nEctopic += 1;
        continue;
      }
      cleaned.push(x);
      ref = x; // track slow HR drift
    }
  }
  const nUsed = cleaned.length;
  return {
    cleaned,
    report: {
      nRaw,
      nOutOfRange,
      nEctopic,
      nUsed,
      removedPct: nRaw ? Math.round((1000 * (nRaw - nUsed)) / nRaw) / 10 : 0,
    },
  };
}

export function rmssd(rr: number[]): number | null {
  if (rr.length < 2) return null;
  let sum = 0;
  for (let i = 1; i < rr.length; i++) {
    const d = rr[i] - rr[i - 1];
    sum += d * d;
  }
  return Math.sqrt(sum / (rr.length - 1));
}

export function sdnn(rr: number[]): number | null {
  if (rr.length < 2) return null;
  const mean = rr.reduce((a, b) => a + b, 0) / rr.length;
  const varr = rr.reduce((a, x) => a + (x - mean) * (x - mean), 0) / (rr.length - 1);
  return Math.sqrt(varr);
}

export function pnn50(rr: number[], threshold = NN50_MS): number | null {
  if (rr.length < 2) return null;
  let n = 0;
  for (let i = 1; i < rr.length; i++) if (Math.abs(rr[i] - rr[i - 1]) > threshold) n += 1;
  return (100 * n) / (rr.length - 1);
}

export function meanHr(rr: number[]): number | null {
  if (rr.length === 0) return null;
  return 60000 / (rr.reduce((a, b) => a + b, 0) / rr.length);
}

export interface HrvSummary {
  nBeats: number;
  durationS: number;
  rmssdMs: number | null;
  sdnnMs: number | null;
  pnn50Pct: number | null;
  meanHrBpm: number | null;
  lnRmssd: number | null;
  correction: CleanReport | null;
}

const r1 = (x: number | null) => (x == null ? null : Math.round(x * 10) / 10);

/** Full time-domain summary from raw R-R (ms). Applies artifact correction by default. */
export function hrvSummary(rr: number[], correct = true): HrvSummary {
  let report: CleanReport | null = null;
  let series = rr;
  if (correct) {
    const c = cleanRr(rr);
    series = c.cleaned;
    report = c.report;
  }
  const r = rmssd(series);
  return {
    nBeats: series.length,
    durationS: series.length ? Math.round(series.reduce((a, b) => a + b, 0) / 100) / 10 : 0,
    rmssdMs: r1(r),
    sdnnMs: r1(sdnn(series)),
    pnn50Pct: r1(pnn50(series)),
    meanHrBpm: r1(meanHr(series)),
    lnRmssd: r ? Math.round(Math.log(r) * 100) / 100 : null,
    correction: report,
  };
}
