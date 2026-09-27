#include "sleepscore.h"

#include <algorithm>
#include <cmath>

namespace SleepScore {
namespace {
double gauss(double x, double mu, double sigma) { return std::exp(-(x - mu) * (x - mu) / (2 * sigma * sigma)); }

double linear(double v, double xMin, double xMax, double yMin, double yMax)
{
    if (xMin == xMax) return yMax;
    const double c = xMin < xMax ? std::clamp(v, xMin, xMax) : std::clamp(v, xMax, xMin);
    return yMin + (c - xMin) / (xMax - xMin) * (yMax - yMin);
}

double scoreDuration(double min)
{
    const double h = min / 60.0;
    if (h > 10.5) return 0;
    if (h >= 7 && h <= 9) return 1;
    return gauss(h, h < 7 ? 7 : 9, 1);
}
double scoreEfficiency(double pct) { return 1.0 / (1.0 + std::exp(-50.0 * (pct / 100.0 - 0.9))); }
double scoreWaso(double min) { const double w = std::max(min - 20.0, 0.0) / 30.0; return 1.0 / (1.0 + w * w); }
double lightPenalty(double pct) { return pct > 65 ? gauss(pct, 65, 7) : 1.0; }

double scoreArchitecture(double durationMin, double deepPct, double remPct, double lightPct)
{
    const double n3 = deepPct / 100.0 * durationMin, rem = remPct / 100.0 * durationMin;
    const double aN3 = std::min(1.0, n3 / 90.0) * gauss(n3, 90, 40);
    const double aRem = std::min(1.0, rem / 100.0) * gauss(rem, 100, 40);
    return std::clamp((0.45 * aN3 + 0.45 * aRem) * lightPenalty(lightPct) + 0.1, 0.0, 1.0);
}

double scoreTiming(double onset, double durationMin)
{
    const double ms = midSleepHour(onset, durationMin);
    double s = gauss(ms, 3.5, 1);
    if (ms > 5.5) s *= gauss(ms, 5.5, 0.5);
    return std::clamp(s, 0.0, 1.0);
}
} // namespace

double midSleepHour(double onsetHourLocal, double durationMin)
{
    double ms = (onsetHourLocal > 12 ? onsetHourLocal - 24 : onsetHourLocal) + durationMin / 120.0;
    while (ms < 0) ms += 24;
    while (ms >= 24) ms -= 24;
    return ms;
}

std::optional<double> midSleepSd(const QVector<double> &h)
{
    if (h.size() < 2) return std::nullopt;
    // Unwrap around the first night so 23:50 and 00:10 are 20 min apart, not 23.7 h.
    QVector<double> xs;
    for (double v : h) { double d = v - h[0]; if (d > 12) d -= 24; if (d < -12) d += 24; xs.append(h[0] + d); }
    double mean = 0; for (double x : xs) mean += x; mean /= xs.size();
    double var = 0; for (double x : xs) var += (x - mean) * (x - mean);
    return std::sqrt(var / xs.size());
}

std::optional<Result> compute(const Night &n)
{
    if (!n.durationMin) return std::nullopt;   // no duration: nothing honest to score
    const double D = scoreDuration(*n.durationMin);
    std::optional<double> se, waso;
    if (n.efficiencyPct) se = scoreEfficiency(*n.efficiencyPct);
    if (n.wasoMin) waso = scoreWaso(*n.wasoMin);
    // No efficiency/WASO: Train Libre's fallback - light-sleep share as a fragmentation proxy.
    const double C = se && waso ? 0.5 * *se + 0.5 * *waso
                   : se ? *se : waso ? *waso : 0.9 * lightPenalty(n.lightPct.value_or(0)) + 0.1 * D;
    std::optional<double> A, T, R;
    if (n.deepPct && n.remPct) A = scoreArchitecture(*n.durationMin, *n.deepPct, *n.remPct, n.lightPct.value_or(0));
    if (n.onsetHourLocal) T = scoreTiming(*n.onsetHourLocal, *n.durationMin);
    if (n.midSleepSdHours && n.regularityDays >= 5) R = 1.0 / (1.0 + *n.midSleepSdHours * *n.midSleepSdHours);

    const std::pair<double, std::optional<double>> parts[] = {{0.30, D}, {0.20, C}, {0.25, A}, {0.15, T}, {0.10, R}};
    double w = 0, sum = 0;
    for (const auto &[wi, s] : parts) if (s) { w += wi; sum += wi * *s; }

    Result r;
    double mult = 1.0;
    auto take = [&](double m, const char *why) { if (m < mult) { mult = m; r.bottleneck = QString::fromLatin1(why); } };
    if (n.remPct) take(linear(*n.remPct / 100.0 * *n.durationMin, 40, 60, 0.65, 1), "rem");
    if (n.deepPct) take(linear(*n.deepPct / 100.0 * *n.durationMin, 40, 70, 0.60, 1), "n3");
    take(linear(*n.durationMin / 60.0, 5, 6.5, 0.5, 1), "tst");
    if (n.onsetHourLocal) take(linear(midSleepHour(*n.onsetHourLocal, *n.durationMin), 7.5, 5.5, 0.55, 1), "timing");

    r.score = std::clamp(sum / w * 100.0 * mult, 0.0, 100.0);
    r.completeness = std::min(1.0, w);
    return r;
}

} // namespace SleepScore
