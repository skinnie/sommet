#pragma once
// "Is this the same move?" - ONE rule for every place Sommet meets a move twice: the activity
// list, imports from bike computers, intervals.icu, Suunto, and the shared database. The same move
// arrives from several services (watch -> Suunto app -> Garmin Connect -> intervals.icu, or a
// bike computer and its intervals.icu copy), and the copies never agree exactly:
//   * start time: compared as UTC instants, never through the computer's time zone (watches,
//     FIT files and Suunto store UTC; intervals.icu's start_date is UTC, kept as start_utc beside
//     the local time the list shows). Within 3 minutes = same start. One exception, also
//     independent of any time zone: a copy EXACTLY a whole number of hours away (1-14 h, to within
//     5 s) whose distance - or duration - agrees closely. That is a clock stored in the wrong zone:
//     a watch move without GPS (a pool swim) keeps the watch's wall-clock time labelled UTC, and
//     some uploads shift by the zone (Tacx). Real separate moves never line up to 5 s.
//   * duration: one counts pauses, one doesn't (6154 s vs 2665 s for the same 1978 m walk).
//   * distance: the most reliable field - identical, or within a few percent across devices.
// Two DIFFERENT watches recording the same ride are two recordings, both kept.
//
// Plain C++ (no Qt) so tools/test_same_move.py compiles it and runs the shared cases
// (android/src/services/__tests__/sameMove.cases.json). The phone's twin is SameMove.ts and the
// Python one tools/same_move.py: change all three together.
#include <cstdlib>
#include <string>

struct SameMoveRow {
    long long start = -1;       // epoch seconds, UTC; < 0 = unknown
    int durationS = 0;          // 0 = unknown
    double distanceM = 0;       // 0 = unknown / indoor
    bool watch = false;         // read straight off a watch (not via a service)
    std::string device;         // which watch, for `watch` rows
};

inline bool sameMove(const SameMoveRow &a, const SameMoveRow &b)
{
    if (a.start < 0 || b.start < 0)
        return false;
    const long long gap = std::llabs(a.start - b.start);
    bool shifted = false;
    if (gap > 180) {
        const long long h = (gap + 1800) / 3600;           // nearest whole hour
        if (h < 1 || h > 14 || std::llabs(gap - h * 3600) > 5)
            return false;                                  // not the same start
        shifted = true;                                    // a clock in the wrong zone
    }
    if (a.watch && b.watch && !a.device.empty() && !b.device.empty() && a.device != b.device)
        return false;                                      // two watches, two recordings

    const bool da = a.distanceM > 100, db = b.distanceM > 100;
    if (da && db) {
        const double diff = a.distanceM > b.distanceM ? a.distanceM - b.distanceM
                                                      : b.distanceM - a.distanceM;
        const double big = a.distanceM > b.distanceM ? a.distanceM : b.distanceM;
        return diff <= 50 || diff / big <= (shifted ? 0.02 : 0.05);
    }
    if (shifted && (da || db))
        return false;                                      // shifted, distance on one side only
    if (a.durationS > 0 && b.durationS > 0) {
        const double r = double(a.durationS) / double(b.durationS);
        return shifted ? (r >= 0.97 && r <= 1.03) : (r >= 0.66 && r <= 1.5);
    }
    return !shifted;                                       // only the start is known
}
