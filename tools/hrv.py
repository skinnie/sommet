#!/usr/bin/env python3
"""Computes HRV (heart-rate-variability) figures from the raw R-R / inter-beat intervals the
Ambit3 logs when a Suunto Smart Sensor belt (R-R capable) is worn during a move.

WHY this file exists: the Ambit3 never stored an HRV *number*. The "HRV tests" people
remember are community Suunto Apps ("5+5 HRV", "HRV", "HRV 2" in
assets/mac/.../suunto-apps/index.json) - the 5+5 one is literally just a stopwatch that
guides the orthostatic protocol (5 min lying, press Lap, 5 min standing) and tells you to
upload the recording to Kubios. The watch computes nothing; it only logs the raw R-R
intervals into the move. Those we already decode in exercise_log.py:
    sample_type 3 / episodic 0x06  ->  {"type": "ibi", "ibi": [u16 ms, ...]}
plus the phase boundary the protocol relies on:
    episodic 0x09                  ->  {"type": "lapinfo", ...}
So the HRV value is ours to compute, and it is plain textbook math on those millisecond
intervals - no firmware write, no reverse engineering, nothing on the wire.

FORMULAS are the standard time-domain HRV definitions from the Task Force of the European
Society of Cardiology / North American Society of Pacing and Electrophysiology (1996),
"Heart rate variability: standards of measurement, physiological interpretation, and clinical
use", Circulation 93(5):1043-1065 - the same ones Kubios/Firstbeat/Movescount report. Per
this project's standing rule (prefer a known published formula over custom sensor math), none
of this is invented:
  * RMSSD  = sqrt(mean(  (RR[i] - RR[i-1])^2  ))          root mean square of successive diffs
  * SDNN   = sample standard deviation of the RR series
  * pNN50  = % of successive RR diffs whose magnitude exceeds 50 ms
  * meanHR = 60000 / mean(RR)
  * lnRMSSD= natural log of RMSSD (the everyday "readiness score" scale; many apps also show
             ln(RMSSD) * 20 so the number sits in a friendlier 0-100-ish range)

ARTIFACT CORRECTION before any of the above: R-R streams contain missed/extra beats. We drop
intervals outside a physiological window (default 300-2000 ms, i.e. 30-200 bpm) and ectopic
beats where an interval jumps more than a set fraction (default 20%) from its predecessor -
the conventional Kubios-style pre-processing. Correction is reported (how many removed) so a
noisy recording is never silently turned into a confident number.

ORTHOSTATIC test: the 5+5 protocol produces two phases split by the single Lap marker. Lying
(parasympathetic, high RMSSD) then standing (sympathetic activation, RMSSD drops, HR rises).
The size of that drop is the recovery/readiness signal: a well-recovered autonomic system
holds its HRV up on standing; a fatigued one collapses it. We report both phase summaries and
the deltas (standing - lying) plus the RMSSD drop as a percentage.

The math functions below take plain lists of integer milliseconds, so they can be exercised
against a synthetic array with no dump and no hardware - see `--self-test` and `--demo`. The
`--from <dump>` path decodes a real ExerciseLog region via exercise_log.py and runs the same
functions on the move's real `ibi` samples.

    ./tools/hrv.py --self-test                 # verify the formulas against known values
    ./tools/hrv.py --demo                       # run on a built-in synthetic 5+5 recording
    ./tools/hrv.py --from /tmp/dump_ExerciseLog.bin [--move N]   # real move
"""

import argparse
import json
import math
import statistics

# Physiological / artifact-correction defaults (see module docstring).
RR_MIN_MS = 300      # 200 bpm - anything faster is a decode/double-count artifact
RR_MAX_MS = 2000     # 30 bpm - anything slower is a dropped-beat artifact
ECTOPIC_RATIO = 0.20  # drop a beat that jumps >20% from the previous accepted interval
NN50_MS = 50          # the classic pNN50 threshold


def clean_rr(rr, rr_min=RR_MIN_MS, rr_max=RR_MAX_MS, ectopic_ratio=ECTOPIC_RATIO):
    """Artifact-correct a raw R-R series (list of ms ints). Returns (clean_rr, report).

    Two passes, both standard: first drop physiologically impossible intervals (outside
    [rr_min, rr_max]); then, walking the survivors in order, drop any interval that differs
    from a running reference by more than `ectopic_ratio` - a missed beat shows up as one long
    interval, an extra beat as one short one, and either way the jump is far larger than real
    beat-to-beat variation.

    The running reference is SEEDED with the series median, not the first interval, and only
    updates to each *accepted* beat. Seeding on the median matters: a real belt trace often
    opens with a spurious partial interval as the sensor locks on (a 307 ms first beat was seen
    on André's first real 5+5, 2026-08-25). Seeding on that bad first value made every genuine
    ~800 ms beat look like a >20% jump and deleted 99.7% of the recording. The median is immune
    to a single bad endpoint; updating the reference to each accepted beat then tracks the slow
    HR drift of a resting recording so real respiratory HRV (RSA) is preserved, not clipped.
    `report` carries the counts so callers can refuse to trust an over-corrected run."""
    rr = [int(x) for x in rr]
    n_raw = len(rr)
    ranged = [x for x in rr if rr_min <= x <= rr_max]
    n_out_of_range = n_raw - len(ranged)

    n_ectopic = 0
    if len(ranged) < 2:
        cleaned = list(ranged)
    else:
        ref = statistics.median(ranged)   # robust seed: a bad first/last beat can't cascade
        cleaned = []
        for x in ranged:
            if abs(x - ref) > ectopic_ratio * ref:
                n_ectopic += 1
                continue
            cleaned.append(x)
            ref = x                        # track slow HR drift so RSA isn't clipped

    report = {
        "n_raw": n_raw,
        "n_out_of_range": n_out_of_range,
        "n_ectopic": n_ectopic,
        "n_used": len(cleaned),
        "removed_pct": round(100.0 * (n_raw - len(cleaned)) / n_raw, 1) if n_raw else 0.0,
    }
    return cleaned, report


def rmssd(rr):
    """Root mean square of successive differences (ms). Needs >= 2 intervals."""
    if len(rr) < 2:
        return None
    diffs = [rr[i] - rr[i - 1] for i in range(1, len(rr))]
    return math.sqrt(sum(d * d for d in diffs) / len(diffs))


def sdnn(rr):
    """Sample standard deviation of the R-R series (ms). Needs >= 2 intervals."""
    if len(rr) < 2:
        return None
    mean = sum(rr) / len(rr)
    var = sum((x - mean) ** 2 for x in rr) / (len(rr) - 1)  # sample (n-1), per Task Force
    return math.sqrt(var)


def pnn50(rr, threshold_ms=NN50_MS):
    """Percent of successive R-R diffs whose magnitude exceeds `threshold_ms`. Needs >= 2."""
    if len(rr) < 2:
        return None
    diffs = [abs(rr[i] - rr[i - 1]) for i in range(1, len(rr))]
    return 100.0 * sum(1 for d in diffs if d > threshold_ms) / len(diffs)


def mean_hr(rr):
    """Mean heart rate (bpm) from the mean R-R interval. Needs >= 1."""
    if not rr:
        return None
    return 60000.0 / (sum(rr) / len(rr))


def hrv_summary(rr, correct=True):
    """Full time-domain summary for one R-R series (list of ms ints).

    Returns a dict of RMSSD/SDNN/pNN50/mean HR/ln(RMSSD) plus the correction report and the
    series' own summed duration. Every metric is None when there are too few beats to define
    it - the caller decides how to present "not enough data" rather than getting a fake 0."""
    report = None
    if correct:
        rr, report = clean_rr(rr)
    r = rmssd(rr)
    return {
        "n_beats": len(rr),
        "duration_s": round(sum(rr) / 1000.0, 1) if rr else 0.0,
        "rmssd_ms": round(r, 1) if r is not None else None,
        "sdnn_ms": round(sdnn(rr), 1) if sdnn(rr) is not None else None,
        "pnn50_pct": round(pnn50(rr), 1) if pnn50(rr) is not None else None,
        "mean_hr_bpm": round(mean_hr(rr), 1) if mean_hr(rr) is not None else None,
        # ln(RMSSD) is the daily-readiness scale; *20 is the common friendly 0-100-ish form.
        "ln_rmssd": round(math.log(r), 2) if r else None,
        "ln_rmssd_x20": round(20.0 * math.log(r), 1) if r else None,
        "correction": report,
    }


def orthostatic(lying_rr, standing_rr):
    """Orthostatic (5+5) result: per-phase summaries plus the lying->standing deltas.

    RMSSD normally falls and HR rises on standing; the magnitude of the RMSSD fall is the
    recovery signal (a bigger collapse = more sympathetic dominance = less recovered). Deltas
    are standing - lying, so `rmssd_delta_ms` is normally negative; `rmssd_drop_pct` states the
    same fall as a positive percentage of the lying value for a friendlier headline."""
    lying = hrv_summary(lying_rr)
    standing = hrv_summary(standing_rr)
    out = {"lying": lying, "standing": standing,
           "rmssd_delta_ms": None, "rmssd_drop_pct": None, "hr_delta_bpm": None}
    if lying["rmssd_ms"] is not None and standing["rmssd_ms"] is not None:
        out["rmssd_delta_ms"] = round(standing["rmssd_ms"] - lying["rmssd_ms"], 1)
        if lying["rmssd_ms"] > 0:
            out["rmssd_drop_pct"] = round(
                100.0 * (lying["rmssd_ms"] - standing["rmssd_ms"]) / lying["rmssd_ms"], 1)
    if lying["mean_hr_bpm"] is not None and standing["mean_hr_bpm"] is not None:
        out["hr_delta_bpm"] = round(standing["mean_hr_bpm"] - lying["mean_hr_bpm"], 1)
    return out


# ---- glue to the real decoder (exercise_log.py) -----------------------------------------

def rr_from_samples(samples):
    """Concatenate every `ibi` sample's intervals into one R-R series, in log order.

    `samples` is exactly what exercise_log.walk_entries()/read_entry_at() yields for a move
    (already time-corrected). Each ibi sample carries a list of u16 millisecond intervals; we
    flatten them in the order they were recorded, which is the order HRV needs."""
    rr = []
    for s in samples:
        if s.get("type") == "ibi":
            rr.extend(int(x) for x in s.get("ibi", []))
    return rr


def rr_phases_by_lap(samples):
    """Split the move's R-R series at each `lapinfo` marker -> list of per-phase R-R lists.

    The 5+5 protocol is one lap: everything before it is the lying phase, everything after is
    standing, so this returns two lists for a clean 5+5 recording. A move with no laps returns
    a single phase holding the whole series; a move with more laps returns more phases (the
    orthostatic reading below just uses the first two)."""
    phases = [[]]
    for s in samples:
        t = s.get("type")
        if t == "ibi":
            phases[-1].extend(int(x) for x in s.get("ibi", []))
        elif t == "lapinfo":
            phases.append([])
    # Drop empty leading/trailing phases (a lap pressed before any beat, etc.).
    phases = [p for p in phases if p]
    return phases or [[]]


def hrv_from_move(samples):
    """Top-level: given one move's samples, return whole-move HRV and, when the move has at
    least two R-R phases (i.e. a Lap was pressed, as the 5+5 test requires), the orthostatic
    lying-vs-standing result built from the first two phases."""
    whole = hrv_summary(rr_from_samples(samples))
    phases = rr_phases_by_lap(samples)
    result = {"whole_move": whole, "n_phases": len(phases), "orthostatic": None}
    if len(phases) >= 2:
        result["orthostatic"] = orthostatic(phases[0], phases[1])
    return result


# ---- synthetic data for offline testing --------------------------------------------------

def _synthetic_rr(mean_ms, sd_ms, n, seed=0):
    """A deterministic pseudo-random R-R series around `mean_ms` with ~`sd_ms` variability -
    a tiny LCG so results are reproducible without importing `random` or numpy. Enough to
    exercise the pipeline; not a physiological model."""
    rr = []
    state = seed * 2654435761 + 12345
    for _ in range(n):
        state = (1103515245 * state + 12345) & 0x7FFFFFFF
        u = state / 0x7FFFFFFF  # 0..1
        rr.append(int(mean_ms + (u - 0.5) * 2.0 * sd_ms))
    return rr


def synthetic_5plus5():
    """A stand-in 5+5 recording as an exercise_log-style samples list: a lying phase with high
    HRV, a lapinfo marker, then a standing phase with clearly lower HRV and a faster HR - so
    the orthostatic drop is unmistakable. Values are chopped into several ibi samples to prove
    the concatenation across samples works, exactly as a real move stores them."""
    lying = _synthetic_rr(mean_ms=1000, sd_ms=60, n=300, seed=1)    # ~60 bpm, high variability
    standing = _synthetic_rr(mean_ms=800, sd_ms=20, n=375, seed=2)  # ~75 bpm, low variability
    samples = []

    def emit(series, chunk=64):
        for i in range(0, len(series), chunk):
            samples.append({"type": "ibi", "ibi": series[i:i + chunk]})

    emit(lying)
    samples.append({"type": "lapinfo", "event_type": 0})
    emit(standing)
    return samples


def _self_test():
    """Check the formulas against hand-computable values. Returns True on pass."""
    ok = True

    # RMSSD of an exact ±50 ms alternation is exactly 50: diffs are all ±50, squared 2500,
    # mean 2500, sqrt 50.
    alt = [800, 850, 800, 850, 800, 850]
    r = rmssd(alt)
    if abs(r - 50.0) > 1e-9:
        print(f"FAIL rmssd: expected 50.0 got {r}"); ok = False

    # SDNN of that same series: values are 800/850 in equal count, mean 825, each deviates 25,
    # sum of squared deviations = 6*25^2 = 3750, sample variance = 3750/(6-1) = 750,
    # sqrt(750) = 27.386...
    s = sdnn(alt)
    if abs(s - math.sqrt(750.0)) > 1e-9:
        print(f"FAIL sdnn: expected {math.sqrt(750.0)} got {s}"); ok = False

    # Every successive diff is 50 ms, which is NOT > 50, so pNN50 is exactly 0.
    if abs(pnn50(alt) - 0.0) > 1e-9:
        print(f"FAIL pnn50 (boundary): expected 0.0 got {pnn50(alt)}"); ok = False
    # Bump the threshold's counterpart: diffs of 60 ms are > 50, so pNN50 is 100.
    if abs(pnn50([800, 860, 800, 860]) - 100.0) > 1e-9:
        print(f"FAIL pnn50: expected 100.0 got {pnn50([800, 860, 800, 860])}"); ok = False

    # mean_hr of a flat 1000 ms series is exactly 60 bpm.
    if abs(mean_hr([1000] * 10) - 60.0) > 1e-9:
        print(f"FAIL mean_hr: expected 60.0 got {mean_hr([1000]*10)}"); ok = False

    # clean_rr drops the impossible interval (2500 > RR_MAX) and the ectopic spike.
    cleaned, rep = clean_rr([800, 810, 2500, 805, 100000, 815])
    if 2500 in cleaned or 100000 in cleaned:
        print(f"FAIL clean_rr: impossible interval survived: {cleaned}"); ok = False
    if rep["n_out_of_range"] != 2:
        print(f"FAIL clean_rr report: expected 2 out-of-range got {rep}"); ok = False

    # Too-few-beats guards return None rather than crashing or faking a number.
    if rmssd([800]) is not None or hrv_summary([])["rmssd_ms"] is not None:
        print("FAIL short-series guard: expected None"); ok = False

    # Orthostatic on the synthetic 5+5: lying RMSSD must exceed standing, drop is positive,
    # and HR rises on standing.
    res = hrv_from_move(synthetic_5plus5())
    o = res["orthostatic"]
    if o is None:
        print("FAIL orthostatic: no phases split"); ok = False
    else:
        if not (o["lying"]["rmssd_ms"] > o["standing"]["rmssd_ms"]):
            print(f"FAIL orthostatic RMSSD ordering: {o['lying']['rmssd_ms']} vs "
                  f"{o['standing']['rmssd_ms']}"); ok = False
        if not (o["rmssd_drop_pct"] > 0):
            print(f"FAIL orthostatic drop%: expected >0 got {o['rmssd_drop_pct']}"); ok = False
        if not (o["hr_delta_bpm"] > 0):
            print(f"FAIL orthostatic HR delta: expected >0 got {o['hr_delta_bpm']}"); ok = False

    print("SELF-TEST: PASS" if ok else "SELF-TEST: FAIL")
    return ok


def main():
    ap = argparse.ArgumentParser(description="Compute HRV (RMSSD + orthostatic) from Ambit3 "
                                             "R-R / IBI data.")
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--from", dest="dump", help="ExerciseLog region dump (.bin) to decode")
    src.add_argument("--demo", action="store_true",
                     help="run on a built-in synthetic 5+5 recording (no hardware needed)")
    src.add_argument("--self-test", action="store_true",
                     help="verify the formulas against known values and exit")
    ap.add_argument("--move", type=int, default=0,
                    help="with --from: which move to analyse (0 = newest), default 0")
    args = ap.parse_args()

    if args.self_test:
        raise SystemExit(0 if _self_test() else 1)

    if args.demo:
        result = hrv_from_move(synthetic_5plus5())
        print(json.dumps(result, indent=2))
        return

    # --from: decode the real region and pull the requested move's samples.
    import exercise_log  # local import: only the real-dump path needs the decoder
    with open(args.dump, "rb") as f:
        data = f.read()
    moves = list(exercise_log.walk_entries(data))
    if not moves:
        raise SystemExit("no moves found in dump")
    if not (0 <= args.move < len(moves)):
        raise SystemExit(f"--move {args.move} out of range (0..{len(moves) - 1})")
    header, samples = moves[args.move]
    result = hrv_from_move(samples)
    result["move"] = {
        "name": header.get("activity_name"),
        "date": f'{header.get("year")}-{header.get("month"):02d}-{header.get("day"):02d}',
        "recovery_time_h": round(header.get("recovery_time_ms", 0) / 3600000.0, 1),
    }
    if result["whole_move"]["n_beats"] == 0:
        print("This move has no R-R (IBI) data - it was recorded without a Smart Sensor belt "
              "(or with a belt that does not transmit R-R). No HRV can be computed.")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
