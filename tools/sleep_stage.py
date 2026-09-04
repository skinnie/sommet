#!/usr/bin/env python3
"""Turn a Polar Verity Sense overnight offline recording into a night of HRV + resting HR.

The Verity Sense records raw PPG (photoplethysmogram, ~55-135 Hz, 3-4 channels) and ACC while
you sleep with just the band - nothing has to stay connected. The band stores HR/PPI only for a
LIVE stream, so overnight we get the raw PPG and derive the beats ourselves:

    PPG channels -> pick best channel -> band-pass 0.5-4 Hz -> adaptive peak pick -> R-R (ms)
      (ACC magnitude gates out the movement seconds so a toss doesn't fake a beat)
    -> hrv.py's clean_rr / hrv_summary (the SAME math the morning strap reading uses)
    -> windowed across the night: overnight RMSSD, resting HR, and the HR/HRV trend curve.

Phase 1 (this file) stops at "overnight HRV + resting-HR trend". Sleep STAGING (wake/light/deep/
REM) is Phase 2 - it hangs off the same per-window R-R + ACC features produced here (`windows`
in the output already carries what a stager needs), so nothing here has to change for it.

We deliberately hand hrv_summary the RAW derived R-R in milliseconds and let clean_rr do the
median-seeded ectopic correction - no pre-trim of the first beat, no pre-smoothing of the series
(that is the documented way to feed it; pre-cleaning double-corrects and biases RMSSD).

Input is a recording JSON (the shape the PolarSleep Android module writes; see --selftest for a
generated example):
    {"start_time": "2026-09-04T21:45:00", "ppg_hz": 55, "acc_hz": 52,
     "ppg": [[c0,c1,c2,(c3)], ...],   # one row per PPG sample, µV-ish ints
     "acc": [[x,y,z], ...]}           # one row per ACC sample, mg (optional)

Everything runs on numpy alone (no scipy) so it works anywhere the other tools do.
"""
import argparse
import json
import math
import os
import sys

import numpy as np

# Reuse the strap session's HRV math + the ONE definition of the physiologic bounds. Never
# redefine these here - importing keeps sleep and the morning reading on identical thresholds.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from hrv import clean_rr, hrv_summary, RR_MIN_MS, RR_MAX_MS, ECTOPIC_RATIO  # noqa: E402

# --- PPG band + peak-pick constants -------------------------------------------------------
HR_BAND_LO_HZ = 0.5    # 30 bpm - below this is baseline wander, not a pulse
HR_BAND_HI_HZ = 4.0    # 240 bpm - above this is sensor/quantisation noise
MIN_BEAT_MS = RR_MIN_MS  # peaks closer than this can't be two real beats (shares hrv's 200-bpm cap)
# ACC motion gate: seconds whose ACC magnitude standard deviation (in g) exceeds this are "moving"
# and their beats are dropped. Resting wrist/upper-arm noise sits well under this; a toss blows past.
ACC_MOTION_STD_G = 0.06
WINDOW_S = 300         # 5-minute HRV windows across the night (RMSSD is defined on ~min+ spans)
RESTING_HR_PCTILE = 10  # "resting HR" = the 10th-percentile window HR (the sustained overnight floor)


def _bandpass_fft(x, fs, lo, hi):
    """Zero-phase band-pass by masking the FFT - clean and dependency-free for offline data.

    Real-time DSP would need an IIR filter; offline we have the whole night in memory, so a
    forward/inverse rFFT with a [lo,hi] Hz box (plus a small raised-cosine edge to avoid ringing)
    is simpler and has no phase lag to bias the peak positions."""
    x = np.asarray(x, dtype=np.float64)
    n = x.shape[0]
    if n < 4:
        return x - x.mean() if n else x
    freqs = np.fft.rfftfreq(n, d=1.0 / fs)
    X = np.fft.rfft(x - x.mean())
    mask = np.zeros_like(freqs)
    band = (freqs >= lo) & (freqs <= hi)
    mask[band] = 1.0
    # soften the two band edges over ~0.1 Hz so the inverse transform doesn't ring
    edge = 0.1
    for f0 in (lo, hi):
        near = np.abs(freqs - f0) < edge
        mask[near] = 0.5 * (1 - np.cos(np.pi * (edge - np.abs(freqs[near] - f0)) / edge))
    mask[~band & (mask == 0)] = 0.0
    return np.fft.irfft(X * mask, n=n)


def _pick_channel(ppg):
    """Choose the PPG channel with the strongest pulsatile signal (highest band-passed variance).

    Verity exposes 3-4 optical channels; which one is cleanest depends on skin/fit/position, so
    pick per-recording rather than hard-coding channel 0."""
    ppg = np.asarray(ppg, dtype=np.float64)
    if ppg.ndim == 1:
        return ppg
    best, best_var = 0, -1.0
    for c in range(ppg.shape[1]):
        v = float(np.var(ppg[:, c]))
        if v > best_var:
            best, best_var = c, v
    return ppg[:, best]


def _detect_peaks(sig, fs):
    """Adaptive local-maxima peak pick with a physiologic refractory gap.

    A running threshold (fraction of a sliding RMS) rejects the troughs; a minimum inter-peak
    spacing (MIN_BEAT_MS) rejects dicrotic-notch double counts. Returns peak sample indices."""
    sig = np.asarray(sig, dtype=np.float64)
    n = sig.shape[0]
    if n < 3:
        return np.array([], dtype=int)
    min_gap = max(1, int(round(fs * MIN_BEAT_MS / 1000.0)))
    # sliding RMS (~2 s) as an adaptive amplitude reference
    win = max(1, int(round(fs * 2.0)))
    power = sig * sig
    csum = np.concatenate([[0.0], np.cumsum(power)])
    rms = np.sqrt(np.maximum(
        (csum[np.minimum(np.arange(n) + win, n)] - csum[np.maximum(np.arange(n) - win, 0)])
        / (np.minimum(np.arange(n) + win, n) - np.maximum(np.arange(n) - win, 0)), 1e-12))
    thr = 0.3 * rms  # accept peaks above 30% of local RMS
    # candidate local maxima above threshold
    up = (sig[1:-1] > sig[:-2]) & (sig[1:-1] >= sig[2:]) & (sig[1:-1] > thr[1:-1])
    cand = np.where(up)[0] + 1
    # enforce refractory gap, keeping the taller of any two that are too close
    peaks = []
    for i in cand:
        if peaks and (i - peaks[-1]) < min_gap:
            if sig[i] > sig[peaks[-1]]:
                peaks[-1] = i
        else:
            peaks.append(i)
    return np.array(peaks, dtype=int)


def _motion_mask(acc, acc_hz, n_seconds):
    """Per-second boolean: True where the band was moving (ACC magnitude std > ACC_MOTION_STD_G).

    Returned length == n_seconds; when there's no ACC we report 'not moving' everywhere (all
    False) so a recording without ACC still yields HRV, just without motion rejection."""
    mask = np.zeros(n_seconds, dtype=bool)
    if acc is None or len(acc) == 0:
        return mask
    acc = np.asarray(acc, dtype=np.float64)
    mag = np.sqrt((acc ** 2).sum(axis=1)) / 1000.0  # mg -> g
    for s in range(n_seconds):
        a, b = int(s * acc_hz), int((s + 1) * acc_hz)
        seg = mag[a:b]
        if seg.size and float(np.std(seg)) > ACC_MOTION_STD_G:
            mask[s] = True
    return mask


def ppg_to_rr(ppg, ppg_hz, acc=None, acc_hz=None):
    """PPG (+ optional ACC) -> (rr_ms list, beat_times_s list, motion_dropped_beats int).

    R-R are RAW here (no clean_rr): peak-to-peak deltas in ms, with beats whose interval falls in
    a movement second removed. Feed the returned rr straight to hrv_summary."""
    sig = _bandpass_fft(_pick_channel(ppg), ppg_hz, HR_BAND_LO_HZ, HR_BAND_HI_HZ)
    peaks = _detect_peaks(sig, ppg_hz)
    if peaks.size < 2:
        return [], [], 0
    t = peaks / float(ppg_hz)                     # beat times, seconds from recording start
    rr = np.diff(t) * 1000.0                       # ms
    n_seconds = int(math.ceil(t[-1])) + 1
    moving = _motion_mask(acc, acc_hz, n_seconds)
    keep, times, dropped = [], [], 0
    for i, gap in enumerate(rr):
        sec = int(t[i + 1])                        # the second the interval closes in
        if sec < n_seconds and moving[sec]:
            dropped += 1
            continue
        keep.append(float(gap))
        times.append(float(t[i + 1]))
    return keep, times, dropped


def analyse_night(rr_ms, beat_times_s, window_s=WINDOW_S):
    """Window the night into `window_s` spans -> per-window HRV + HR, then overnight roll-ups.

    Each window's R-R go through hrv_summary (which runs clean_rr internally). Overnight RMSSD is
    the median of the window RMSSDs (robust to a couple of noisy windows); resting HR is the low
    percentile of window mean HR (the sustained floor, not a single lucky beat)."""
    windows = []
    if beat_times_s:
        total = beat_times_s[-1]
        n_win = int(total // window_s) + 1
        for w in range(n_win):
            lo, hi = w * window_s, (w + 1) * window_s
            seg = [rr_ms[i] for i, bt in enumerate(beat_times_s) if lo <= bt < hi]
            if len(seg) < 20:                      # too few beats to define a window's HRV
                continue
            s = hrv_summary(seg, correct=True)
            windows.append({
                "start_s": lo,
                "rmssd_ms": s["rmssd_ms"],
                "mean_hr_bpm": s["mean_hr_bpm"],
                "n_beats": s["n_beats"],
                "sdnn_ms": s["sdnn_ms"],
            })
    overall = hrv_summary(rr_ms, correct=True)
    rmssds = [w["rmssd_ms"] for w in windows if w["rmssd_ms"] is not None]
    hrs = [w["mean_hr_bpm"] for w in windows if w["mean_hr_bpm"] is not None]
    overnight = {
        "overnight_rmssd_ms": round(float(np.median(rmssds)), 1) if rmssds else None,
        "overnight_ln_rmssd_x20": round(20.0 * math.log(float(np.median(rmssds))), 1) if rmssds else None,
        "resting_hr_bpm": round(float(np.percentile(hrs, RESTING_HR_PCTILE)), 1) if hrs else None,
        "min_hr_bpm": round(min(hrs), 1) if hrs else None,
        "mean_hr_bpm": overall["mean_hr_bpm"],
        "n_windows": len(windows),
        "recording_span_min": round(beat_times_s[-1] / 60.0, 1) if beat_times_s else 0.0,
    }
    return overnight, windows, overall


def process_recording(rec):
    """Top level: a loaded recording dict -> the full result dict (what the backend returns)."""
    ppg = rec.get("ppg") or []
    ppg_hz = float(rec.get("ppg_hz") or 55.0)
    acc = rec.get("acc")
    acc_hz = float(rec.get("acc_hz") or 52.0)
    if not ppg:
        return {"ok": False, "error": "recording has no PPG samples"}
    rr, times, dropped = ppg_to_rr(ppg, ppg_hz, acc, acc_hz)
    if len(rr) < 20:
        return {"ok": False, "error": "too few beats derived from PPG (%d) - check the recording"
                % len(rr), "beats": len(rr)}
    overnight, windows, overall = analyse_night(rr, times)
    return {
        "ok": True,
        "start_time": rec.get("start_time"),
        "source": "polar_verity_sense_offline_ppg",
        "ppg_hz": ppg_hz,
        "beats_used": len(rr),
        "beats_dropped_motion": dropped,
        "overnight": overnight,
        "overall_hrv": overall,      # whole-night hrv_summary incl. its clean_rr correction report
        "windows": windows,          # per-5-min curve; also the Phase-2 sleep-staging feature rows
    }


# --- synthetic self-test (no band needed) -------------------------------------------------

def _make_synthetic(minutes=30, ppg_hz=55, base_hr=52.0, rmssd_ms=45.0, seed=1):
    """Build a believable overnight PPG+ACC recording with a KNOWN target HR/HRV, for validation.

    Beats are laid down from an R-R series (mean from base_hr, beat-to-beat jitter sized to the
    target RMSSD, plus a slow respiratory-sinus wave); each beat stamps a pulse wave into the PPG.
    A couple of movement bursts are injected into ACC to exercise the motion gate."""
    rng = np.random.default_rng(seed)
    n = int(minutes * 60 * ppg_hz)
    t = np.arange(n) / ppg_hz
    # R-R series: mean interval + RSA sine + white jitter tuned to hit ~rmssd_ms
    mean_rr = 60000.0 / base_hr
    beat_times, tcur = [], 0.5
    jitter = rmssd_ms / math.sqrt(2)  # successive-difference std -> ~ target RMSSD
    prev = mean_rr
    while tcur < minutes * 60 - 1:
        rsa = 25.0 * math.sin(2 * math.pi * 0.25 * tcur)     # respiratory sinus arrhythmia
        rr = mean_rr + rsa + rng.normal(0, jitter / math.sqrt(2))
        rr = max(RR_MIN_MS + 50, min(RR_MAX_MS - 50, rr))
        beat_times.append(tcur)
        tcur += rr / 1000.0
        prev = rr
    # stamp a pulse shape (skewed gaussian) at each beat into a 3-channel PPG
    ppg = np.zeros((n, 3))
    pulse_w = int(0.15 * ppg_hz)
    for bt in beat_times:
        c = int(bt * ppg_hz)
        for k in range(-pulse_w, 2 * pulse_w):
            idx = c + k
            if 0 <= idx < n:
                ppg[idx, 0] += 1000.0 * math.exp(-((k) ** 2) / (2 * (pulse_w / 2.0) ** 2))
    ppg[:, 1] = ppg[:, 0] * 0.7 + rng.normal(0, 30, n)       # weaker, noisier channel
    ppg[:, 2] = rng.normal(0, 200, n)                        # a dead/noise channel
    ppg[:, 0] += rng.normal(0, 40, n) + 500 * np.sin(2 * math.pi * 0.05 * t)  # baseline wander
    # ACC: mostly still (small noise) with two movement bursts
    acc_hz = 52
    m = int(minutes * 60 * acc_hz)
    acc = rng.normal(0, 8, (m, 3))
    acc[:, 2] += 1000                                        # gravity on Z
    for burst_min in (7, 19):
        a = int(burst_min * 60 * acc_hz)
        b = min(a + acc_hz * 20, m)
        if b > a:
            acc[a:b] += rng.normal(0, 150, (b - a, 3))
    return {
        "start_time": "2026-09-04T21:45:00",
        "ppg_hz": ppg_hz, "acc_hz": acc_hz,
        "ppg": ppg.astype(int).tolist(),
        "acc": acc.astype(int).tolist(),
        "_truth": {"base_hr": base_hr, "rmssd_ms": rmssd_ms, "n_beats": len(beat_times)},
    }


def _selftest():
    truth_hr, truth_rmssd = 52.0, 45.0
    rec = _make_synthetic(minutes=20, base_hr=truth_hr, rmssd_ms=truth_rmssd)
    res = process_recording(rec)
    print(json.dumps({k: v for k, v in res.items() if k != "windows"}, indent=2))
    if not res["ok"]:
        print("SELFTEST FAIL:", res.get("error"), file=sys.stderr)
        return 1
    hr = res["overnight"]["mean_hr_bpm"]
    rmssd = res["overnight"]["overnight_rmssd_ms"]
    truth_beats = rec["_truth"]["n_beats"]
    print("\n  truth:   HR ~%.0f bpm, RMSSD ~%.0f ms, %d beats" % (truth_hr, truth_rmssd, truth_beats))
    print("  derived: HR %.1f bpm, RMSSD %.1f ms, %d beats (%d dropped for motion)" % (
        hr, rmssd, res["beats_used"], res["beats_dropped_motion"]))
    ok = (abs(hr - truth_hr) <= 3.0
          and abs(res["beats_used"] + res["beats_dropped_motion"] - truth_beats) <= 0.1 * truth_beats)
    print("  VERDICT:", "PASS" if ok else "CHECK",
          "(HR within 3 bpm and beat count within 10%)" if ok else "(see deltas above)")
    return 0 if ok else 2


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("recording", nargs="?", help="recording JSON from the PolarSleep module")
    ap.add_argument("--json", action="store_true", help="emit the full result as JSON (default)")
    ap.add_argument("--selftest", action="store_true",
                    help="run the synthetic PPG->R-R->HRV validation (no band needed)")
    args = ap.parse_args()
    if args.selftest:
        return _selftest()
    if not args.recording:
        ap.error("give a recording JSON, or --selftest")
    with open(args.recording) as f:
        rec = json.load(f)
    print(json.dumps(process_recording(rec), indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
