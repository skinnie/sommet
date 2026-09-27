#include "activityweatherservice.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QStandardPaths>
#include <QTimeZone>
#include <QUrlQuery>
#include <cmath>

namespace {
constexpr double kPi = 3.14159265358979323846;
double rad(double d) { return d * kPi / 180.0; }

double haversineM(double la1, double lo1, double la2, double lo2)
{
    const double p1 = rad(la1), p2 = rad(la2), dp = rad(la2 - la1), dl = rad(lo2 - lo1);
    const double a = std::sin(dp / 2) * std::sin(dp / 2)
                   + std::cos(p1) * std::cos(p2) * std::sin(dl / 2) * std::sin(dl / 2);
    return 2 * 6371000.0 * std::asin(std::min(1.0, std::sqrt(a)));
}

double bearingDeg(double la1, double lo1, double la2, double lo2)
{
    const double p1 = rad(la1), p2 = rad(la2), dl = rad(lo2 - lo1);
    const double y = std::sin(dl) * std::cos(p2);
    const double x = std::cos(p1) * std::sin(p2) - std::sin(p1) * std::cos(p2) * std::cos(dl);
    return std::fmod(std::atan2(y, x) * 180.0 / kPi + 360.0, 360.0);
}

// Same split as WeatherRoute.ts windRelation(): < 60° off the nose = headwind, > 120° = tailwind.
int windRelation(double windFromDeg, double headingDeg)
{
    const double diff = std::fabs(std::fmod(std::fmod(windFromDeg - headingDeg + 180.0, 360.0) + 360.0, 360.0) - 180.0);
    return diff < 60 ? 0 : diff > 120 ? 2 : 1;   // 0 head, 1 cross, 2 tail
}

QString compass(double deg)
{
    static const char *names[] = {"N", "NE", "E", "SE", "S", "SW", "W", "NW"};
    return QString::fromLatin1(names[int(std::floor(std::fmod(deg + 22.5 + 360.0, 360.0) / 45.0)) % 8]);
}

QPair<double, double> firstPoint(const QVariantList &track)
{
    for (const auto &v : track) {
        const auto m = v.toMap();
        const double lat = m.value(QStringLiteral("lat")).toDouble();
        const double lon = m.value(QStringLiteral("lon")).toDouble();
        if (lat != 0.0 || lon != 0.0) return {lat, lon};
    }
    return {qQNaN(), qQNaN()};
}
} // namespace

ActivityWeatherService::ActivityWeatherService(QObject *parent) : QObject(parent)
{
    loadCache();
}

QString ActivityWeatherService::cachePath() const
{
    return QStandardPaths::writableLocation(QStandardPaths::AppDataLocation)
           + QStringLiteral("/activity-weather.json");
}

void ActivityWeatherService::loadCache()
{
    QFile f(cachePath());
    if (!f.open(QIODevice::ReadOnly)) return;
    const auto obj = QJsonDocument::fromJson(f.readAll()).object();
    for (auto it = obj.constBegin(); it != obj.constEnd(); ++it)
        m_cache.insert(it.key(), it.value().toObject().toVariantMap());
}

void ActivityWeatherService::saveArchived()
{
    QJsonObject obj;
    for (auto it = m_cache.constBegin(); it != m_cache.constEnd(); ++it)
        if (it.value().value(QStringLiteral("source")).toString() == QStringLiteral("archive"))
            obj.insert(it.key(), QJsonObject::fromVariantMap(it.value()));
    QDir().mkpath(QFileInfo(cachePath()).absolutePath());
    QFile f(cachePath());
    if (f.open(QIODevice::WriteOnly | QIODevice::Truncate))
        f.write(QJsonDocument(obj).toJson(QJsonDocument::Compact));
}

QVariantMap ActivityWeatherService::request(const QString &key, const QString &startIso,
                                            int durationS, const QVariantList &track)
{
    const QDateTime start = QDateTime::fromString(startIso, Qt::ISODate);
    const auto [lat, lon] = firstPoint(track);
    if (key.isEmpty() || !start.isValid() || std::isnan(lat)) return {};   // indoor / no GPS: nothing to ask
    const qint64 startSecs = start.toSecsSinceEpoch();
    const qint64 endSecs = startSecs + qMax(0, durationS);
    // Archive (ERA5) lags ~5 days; before that only the forecast API knows the recent past.
    const bool archived = QDateTime::currentSecsSinceEpoch() - endSecs > 6 * 86400;

    const auto hit = m_cache.constFind(key);
    if (hit != m_cache.constEnd()
        && (hit->value(QStringLiteral("source")).toString() == QStringLiteral("archive") || !archived))
        return *hit;
    if (m_inFlight.value(key)) return {};
    m_inFlight.insert(key, true);

    QUrl url(archived ? QStringLiteral("https://archive-api.open-meteo.com/v1/archive")
                      : QStringLiteral("https://api.open-meteo.com/v1/forecast"));
    QUrlQuery q;
    q.addQueryItem(QStringLiteral("latitude"), QString::number(lat, 'f', 4));
    q.addQueryItem(QStringLiteral("longitude"), QString::number(lon, 'f', 4));
    q.addQueryItem(QStringLiteral("hourly"), QStringLiteral(
        "temperature_2m,apparent_temperature,precipitation,wind_speed_10m,wind_direction_10m,"
        "wind_gusts_10m,weather_code"));
    q.addQueryItem(QStringLiteral("timezone"), QStringLiteral("GMT"));
    q.addQueryItem(QStringLiteral("start_date"), QDateTime::fromSecsSinceEpoch(startSecs, QTimeZone::UTC).date().toString(Qt::ISODate));
    q.addQueryItem(QStringLiteral("end_date"), QDateTime::fromSecsSinceEpoch(endSecs, QTimeZone::UTC).date().toString(Qt::ISODate));
    url.setQuery(q);
    QNetworkRequest req(url);
    req.setRawHeader("User-Agent", "Sommet/1.0");
    QNetworkReply *reply = m_net.get(req);
    connect(reply, &QNetworkReply::finished, this, [=, this]() {
        reply->deleteLater();
        m_inFlight.remove(key);
        if (reply->error() != QNetworkReply::NoError) return;   // offline: the strip just stays hidden
        const auto hourly = QJsonDocument::fromJson(reply->readAll()).object()
                                .value(QStringLiteral("hourly")).toObject().toVariantMap();
        QVariantMap w = summarize(hourly, startSecs, endSecs, track);
        if (w.isEmpty()) return;
        w.insert(QStringLiteral("source"), archived ? QStringLiteral("archive") : QStringLiteral("forecast"));
        m_cache.insert(key, w);
        if (archived) saveArchived();
        emit ready(key, w);
    });
    return {};
}

QVariantMap ActivityWeatherService::summarize(const QVariantMap &hourly, qint64 startSecs,
                                              qint64 endSecs, const QVariantList &track)
{
    const auto times = hourly.value(QStringLiteral("time")).toList();
    auto col = [&](const char *name) { return hourly.value(QString::fromLatin1(name)).toList(); };
    const auto temp = col("temperature_2m"), feels = col("apparent_temperature"),
               rain = col("precipitation"), wind = col("wind_speed_10m"),
               dir = col("wind_direction_10m"), gust = col("wind_gusts_10m"), code = col("weather_code");

    // Every hour the move touches: from the hour it started in to the hour it ended in.
    const qint64 from = startSecs - startSecs % 3600, to = endSecs;
    double tMin = 1e9, tMax = -1e9, feelsSum = 0, rainSum = 0, windSum = 0, gustMax = 0, u = 0, v = 0;
    int n = 0, worstCode = -1;
    for (int i = 0; i < times.size(); ++i) {
        QDateTime t = QDateTime::fromString(times[i].toString(), Qt::ISODate);
        t.setTimeZone(QTimeZone::UTC);
        const qint64 s = t.toSecsSinceEpoch();
        if (s < from || s > to || temp.value(i).isNull()) continue;
        const double tc = temp.value(i).toDouble(), ws = wind.value(i).toDouble(), wd = dir.value(i).toDouble();
        tMin = qMin(tMin, tc); tMax = qMax(tMax, tc);
        feelsSum += feels.value(i).toDouble();
        // precipitation[i] is the PRECEDING hour's total; count the ones inside the move.
        if (s > from) rainSum += rain.value(i).toDouble();
        windSum += ws;
        gustMax = qMax(gustMax, gust.value(i).toDouble());
        u += ws * std::sin(rad(wd)); v += ws * std::cos(rad(wd));   // speed-weighted mean direction
        worstCode = qMax(worstCode, code.value(i).toInt());         // WMO codes rise with severity
        ++n;
    }
    if (n == 0) return {};
    const double windFrom = std::fmod(std::atan2(u, v) * 180.0 / kPi + 360.0, 360.0);
    QVariantMap w{
        {QStringLiteral("tempMin"), tMin}, {QStringLiteral("tempMax"), tMax},
        {QStringLiteral("feels"), feelsSum / n}, {QStringLiteral("rainMm"), rainSum},
        {QStringLiteral("windKmh"), windSum / n}, {QStringLiteral("gustKmh"), gustMax},
        {QStringLiteral("windFromDeg"), windFrom}, {QStringLiteral("windCompass"), compass(windFrom)},
        {QStringLiteral("code"), worstCode},
    };

    // Share of the distance ridden into / across / with the wind (calm air: skip, it's noise).
    double dist[3] = {0, 0, 0};
    if (windSum / n >= 5.0) {
        double pla = qQNaN(), plo = qQNaN();
        for (const auto &p : track) {
            const auto m = p.toMap();
            const double la = m.value(QStringLiteral("lat")).toDouble(), lo = m.value(QStringLiteral("lon")).toDouble();
            if (la == 0.0 && lo == 0.0) continue;
            if (!std::isnan(pla)) {
                const double d = haversineM(pla, plo, la, lo);
                if (d > 0.5) dist[windRelation(windFrom, bearingDeg(pla, plo, la, lo))] += d;
            }
            pla = la; plo = lo;
        }
    }
    const double total = dist[0] + dist[1] + dist[2];
    if (total > 200) {
        w.insert(QStringLiteral("headShare"), dist[0] / total);
        w.insert(QStringLiteral("crossShare"), dist[1] / total);
        w.insert(QStringLiteral("tailShare"), dist[2] / total);
    }
    return w;
}
