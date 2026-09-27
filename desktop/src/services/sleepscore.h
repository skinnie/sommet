#pragma once
#include <QString>
#include <QVector>
#include <optional>

// Sleep score - a port of Train Libre's Sleep Health Score engine (SHS v3.5,
// lib/features/sleep/domain/scoring/sleep_scoring_engine.dart, GPL-3.0 like this app,
// https://github.com/rfivesix/train-libre). André, 2026-09-27: "5 yes why not".
// Twin of android/src/services/SleepScore.ts (unit-tested there) - keep the two in step.
//
// Five domains weighted 30/20/25/15/10 - duration, continuity, architecture (deep/REM), circadian
// timing, regularity - renormalised over what the source provides, then multiplied by the worst
// bottleneck. Garmin feeds all five; intervals.icu only the night's duration.
namespace SleepScore {

struct Night {
    std::optional<double> durationMin, efficiencyPct, wasoMin;
    std::optional<double> lightPct, deepPct, remPct;
    std::optional<double> onsetHourLocal;      // 0..24 local clock of sleep onset
    std::optional<double> midSleepSdHours;     // spread of mid-sleep over the previous nights
    int regularityDays = 0;
};

struct Result {
    double score = 0;          // 0..100
    double completeness = 0;   // share of the domain weights the source could feed
    QString bottleneck;        // "rem" | "n3" | "tst" | "timing" | ""
};

std::optional<Result> compute(const Night &n);
double midSleepHour(double onsetHourLocal, double durationMin);
std::optional<double> midSleepSd(const QVector<double> &midSleepHours);

} // namespace SleepScore
