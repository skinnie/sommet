#pragma once
#include <QHash>
#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QVariantList>
#include <QVariantMap>

// The weather a move was actually done in (André, 2026-09-27: "2 super nice" - the idea from
// OpenAthlete's weather.processor.ts, which attaches Open-Meteo history to each activity; formula
// and API choice only, their code is AGPL and was not copied).
//
// One Open-Meteo call per move, at the track's first point, for the hours the move spans:
// the historical archive (archive-api.open-meteo.com, ERA5 reanalysis) once the move is older
// than its ~5-day lag, the forecast API's recent past before that. Archive answers never change,
// so they're cached on disk (activity-weather.json, keyed like the gear tally by start time);
// a recent-past answer is only kept in memory and re-asked once the archive has it.
//
// Headwind/crosswind/tailwind share along the track uses the same 60°/120° split as the race
// planner's weather (tools/weather_route.py / android WeatherRoute.ts windRelation()).
class ActivityWeatherService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON
public:
    explicit ActivityWeatherService(QObject *parent = nullptr);

    // Returns the cached answer at once when there is one (and emits nothing); otherwise an
    // empty map, and ready(key, weather) follows when Open-Meteo answers. `track` is the move's
    // [{lat, lon}, ...]; its first point is where the weather is asked for.
    Q_INVOKABLE QVariantMap request(const QString &key, const QString &startIso, int durationS,
                                    const QVariantList &track);

    // Pure summary of an Open-Meteo `hourly` object over [startSecs, endSecs] (UTC), plus the
    // wind relation shares along `track`. Public + static so it is testable without network.
    static QVariantMap summarize(const QVariantMap &hourly, qint64 startSecs, qint64 endSecs,
                                 const QVariantList &track);

signals:
    void ready(const QString &key, const QVariantMap &weather);

private:
    QNetworkAccessManager m_net;
    QHash<QString, QVariantMap> m_cache;     // disk (archive) + memory (recent past)
    QHash<QString, bool> m_inFlight;
    QString cachePath() const;
    void loadCache();
    void saveArchived();
};
