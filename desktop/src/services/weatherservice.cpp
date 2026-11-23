#include "weatherservice.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QSettings>
#include <QUrlQuery>

WeatherService::WeatherService(QObject *parent) : QObject(parent)
{
    // Background auto-refresh, real request 2026-08-07: every 10 minutes, independent of
    // whoever else calls refresh() (HomePage.qml on load, Settings after a location change).
    m_refreshTimer.setInterval(10 * 60 * 1000);
    connect(&m_refreshTimer, &QTimer::timeout, this, &WeatherService::refresh);
    m_refreshTimer.start();

    // A place set by hand survives a restart: the IP address can be another city (Faro for a
    // Vodafone line in Leiria, André 2026-11-23), and a desktop has no GPS to correct it.
    const QSettings settings;
    if (settings.value(QStringLiteral("weather/manual"), false).toBool()) {
        m_latitude = settings.value(QStringLiteral("weather/latitude"), m_latitude).toDouble();
        m_longitude = settings.value(QStringLiteral("weather/longitude"), m_longitude).toDouble();
        m_manualLocation = true;
    }
}

void WeatherService::setManualLocation(double latitude, double longitude)
{
    QSettings settings;
    settings.setValue(QStringLiteral("weather/manual"), true);
    settings.setValue(QStringLiteral("weather/latitude"), latitude);
    settings.setValue(QStringLiteral("weather/longitude"), longitude);
    m_manualLocation = true;
    m_latitude = latitude;
    m_longitude = longitude;
    emit locationChanged();
    refresh();
}

void WeatherService::useAutomaticLocation()
{
    QSettings settings;
    settings.remove(QStringLiteral("weather/manual"));
    settings.remove(QStringLiteral("weather/latitude"));
    settings.remove(QStringLiteral("weather/longitude"));
    m_manualLocation = false;
    emit locationChanged();
    detectLocationFromIp();
}

void WeatherService::setLoading(bool value)
{
    if (m_loading == value)
        return;
    m_loading = value;
    emit loadingChanged();
}

void WeatherService::setAvailable(bool value)
{
    if (m_available == value)
        return;
    m_available = value;
    emit availableChanged();
}

void WeatherService::markFetchedOnce()
{
    if (m_hasFetchedOnce)
        return;
    m_hasFetchedOnce = true;
    emit hasFetchedOnceChanged();
}

void WeatherService::setLatitude(double value)
{
    if (qFuzzyCompare(m_latitude, value))
        return;
    m_latitude = value;
    emit locationChanged();
}

void WeatherService::setLongitude(double value)
{
    if (qFuzzyCompare(m_longitude, value))
        return;
    m_longitude = value;
    emit locationChanged();
}

void WeatherService::refresh()
{
    setLoading(true);

    QUrl url(QStringLiteral("https://api.open-meteo.com/v1/forecast"));
    QUrlQuery query;
    query.addQueryItem(QStringLiteral("latitude"), QString::number(m_latitude));
    query.addQueryItem(QStringLiteral("longitude"), QString::number(m_longitude));
    query.addQueryItem(QStringLiteral("current"),
                        QStringLiteral("temperature_2m,weather_code,wind_speed_10m"));
    query.addQueryItem(QStringLiteral("daily"),
                        QStringLiteral("temperature_2m_max,temperature_2m_min,weather_code"));
    query.addQueryItem(QStringLiteral("timezone"), QStringLiteral("auto"));
    query.addQueryItem(QStringLiteral("forecast_days"), QStringLiteral("3"));
    url.setQuery(query);

    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoading(false);

        // Any failure here, network or malformed response, just leaves `available` false;
        // WeatherCard.qml shows a plain "you're offline" message rather than any raw error.
        if (reply->error() != QNetworkReply::NoError) {
            setAvailable(false);
            markFetchedOnce();
            return;
        }

        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto root = doc.object();
        const auto current = root.value(QStringLiteral("current")).toObject();
        const auto daily = root.value(QStringLiteral("daily")).toObject();
        const auto dailyTime = daily.value(QStringLiteral("time")).toArray();
        const auto dailyHigh = daily.value(QStringLiteral("temperature_2m_max")).toArray();
        const auto dailyLow = daily.value(QStringLiteral("temperature_2m_min")).toArray();
        const auto dailyCode = daily.value(QStringLiteral("weather_code")).toArray();

        if (current.isEmpty() || dailyTime.isEmpty()) {
            setAvailable(false);
            markFetchedOnce();
            return;
        }

        m_currentTemperature = current.value(QStringLiteral("temperature_2m")).toDouble();
        m_currentWeatherCode = current.value(QStringLiteral("weather_code")).toInt();
        m_windSpeed = current.value(QStringLiteral("wind_speed_10m")).toDouble();
        m_todayHigh = dailyHigh.isEmpty() ? 0 : dailyHigh.at(0).toDouble();
        m_todayLow = dailyLow.isEmpty() ? 0 : dailyLow.at(0).toDouble();

        m_forecast.clear();
        for (int i = 0; i < dailyTime.size(); ++i) {
            QVariantMap day;
            day[QStringLiteral("date")] = dailyTime.at(i).toString();
            day[QStringLiteral("high")] = i < dailyHigh.size() ? dailyHigh.at(i).toDouble() : 0;
            day[QStringLiteral("low")] = i < dailyLow.size() ? dailyLow.at(i).toDouble() : 0;
            day[QStringLiteral("code")] = i < dailyCode.size() ? dailyCode.at(i).toInt() : -1;
            m_forecast.append(day);
        }

        setAvailable(true);
        markFetchedOnce();
        emit dataChanged();
    });

    fetchPlaceName();
}

void WeatherService::fetchPlaceName()
{
    QUrl url(QStringLiteral("https://nominatim.openstreetmap.org/reverse"));
    QUrlQuery query;
    query.addQueryItem(QStringLiteral("lat"), QString::number(m_latitude));
    query.addQueryItem(QStringLiteral("lon"), QString::number(m_longitude));
    query.addQueryItem(QStringLiteral("format"), QStringLiteral("json"));
    query.addQueryItem(QStringLiteral("zoom"), QStringLiteral("10"));  // city/town level
    url.setQuery(query);

    QNetworkRequest request(url);
    // Nominatim's usage policy requires a real identifying User-Agent, not the Qt default -
    // see https://operations.osmfoundation.org/policies/nominatim/.
    request.setHeader(QNetworkRequest::UserAgentHeader, QStringLiteral("Sommet/2.0"));

    QNetworkReply *reply = m_network.get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            return;  // same "just don't show it" rule as weather data itself
        }
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto address = doc.object().value(QStringLiteral("address")).toObject();
        // Nominatim's address breakdown has no single "the name" field - city/town/village
        // is the real hierarchy for how populated places are actually classified there, in
        // that fallback order, matching what a weather card should show regardless of
        // whether the coordinate lands in a big city or a small village.
        const QString name = [&]() {
            for (const auto &key : {"city", "town", "village", "municipality", "county"}) {
                const auto value = address.value(QLatin1String(key)).toString();
                if (!value.isEmpty())
                    return value;
            }
            return QString();
        }();
        if (m_placeName != name) {
            m_placeName = name;
            emit placeNameChanged();
        }
    });
}

void WeatherService::detectLocationFromIp()
{
    if (m_manualLocation) {               // a place set by hand wins
        refresh();
        return;
    }
    tryIpLocation(0);
}

// IP-based location over HTTPS (2026-10-22). It used ip-api.com, whose free tier is plain HTTP
// only: the Android app can't use that (Android blocks plain HTTP), so its weather always fell
// back to the default. Both apps now ask the same HTTPS services, free and without a key:
// ipapi.co first, get.geojs.io if that fails or is rate-limited. City-level at best, from the
// IP's registered location - not true GPS.
void WeatherService::tryIpLocation(int provider)
{
    static const char *const kServices[] = {
        "https://ipapi.co/json/",
        "https://get.geojs.io/v1/ip/geo.json",
    };
    // Every service failed: a failed IP lookup still has to end in a real refresh() call with
    // whatever coordinates are already set (the static central-Europe fallback, or wherever the
    // user last pointed it via Settings) - otherwise hasFetchedOnce would never become true on a
    // genuinely offline launch, and WeatherCard.qml would stay hidden forever instead of showing
    // its real offline message.
    if (provider >= int(sizeof(kServices) / sizeof(kServices[0]))) {
        refresh();
        return;
    }
    QNetworkRequest request(QUrl(QString::fromLatin1(kServices[provider])));
    request.setHeader(QNetworkRequest::UserAgentHeader, QStringLiteral("Sommet/2.0"));
    QNetworkReply *reply = m_network.get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply, provider] {
        reply->deleteLater();
        if (reply->error() == QNetworkReply::NoError) {
            const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
            // ipapi.co sends the coordinates as numbers, get.geojs.io as strings.
            const auto number = [](const QJsonValue &v, bool *ok) {
                if (v.isDouble()) {
                    *ok = true;
                    return v.toDouble();
                }
                return v.toString().toDouble(ok);
            };
            bool latOk = false, lonOk = false;
            const double lat = number(obj.value(QStringLiteral("latitude")), &latOk);
            const double lon = number(obj.value(QStringLiteral("longitude")), &lonOk);
            if (m_manualLocation) {           // set by hand while the lookup was on its way
                refresh();
                return;
            }
            if (latOk && lonOk && !obj.value(QStringLiteral("error")).toBool()) {
                setLatitude(lat);
                setLongitude(lon);
                refresh();
                return;
            }
        }
        tryIpLocation(provider + 1);
    });
}
