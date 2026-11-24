#include "weatherservice.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QProcess>
#include <QRegularExpression>
#include <QSettings>
#include <algorithm>
#include <cmath>
#include <QUrlQuery>

WeatherService::WeatherService(QObject *parent) : QObject(parent)
{
    // Background auto-refresh, real request 2026-08-07: every 10 minutes, independent of
    // whoever else calls refresh() (HomePage.qml on load, Settings after a location change).
    m_refreshTimer.setInterval(10 * 60 * 1000);
    connect(&m_refreshTimer, &QTimer::timeout, this, &WeatherService::refresh);
    m_refreshTimer.start();
    // A failed fetch is tried again after a minute rather than at the next 10-minute refresh
    // (André, 2026-11-24: "offline" on the X230 though it was not, after a start short on memory).
    m_retryTimer.setSingleShot(true);
    m_retryTimer.setInterval(60 * 1000);
    connect(&m_retryTimer, &QTimer::timeout, this, &WeatherService::refresh);

    // A place set by hand survives a restart: the IP address can be another city (Faro for a
    // Vodafone line in Leiria, André 2026-11-23), and a desktop has no GPS to correct it.
    const QSettings settings;
    m_wifiLocation = settings.value(QStringLiteral("weather/wifi"), false).toBool();
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

void WeatherService::setWifiLocation(bool on)
{
    if (m_wifiLocation == on)
        return;
    m_wifiLocation = on;
    QSettings().setValue(QStringLiteral("weather/wifi"), on);
    emit wifiLocationChanged();
    if (on && !m_manualLocation)
        detectLocationFromIp();
}

namespace {
const char kBeaconDb[] = "https://api.beacondb.net/v1/geolocate";
const char kLookupAgent[] = "Sommet/2.0 (open-source watch app)";

struct Heard { QString bssid; int dbm; QString ssid; };

// nmcli -t: "AA\:BB\:CC\:DD\:EE\:FF:72:Name" (signal 0-100).
QList<Heard> parseNmcli(const QString &out)
{
    QList<Heard> list;
    for (QString line : out.split(QLatin1Char('\n'), Qt::SkipEmptyParts)) {
        line.replace(QStringLiteral("\\:"), QStringLiteral("\x01"));
        const QStringList f = line.split(QLatin1Char(':'));
        if (f.size() < 3)
            continue;
        list.append({ QString(f[0]).replace(QChar(1), QLatin1Char(':')), f[1].toInt() / 2 - 100,
                      f.mid(2).join(QLatin1Char(':')).replace(QChar(1), QLatin1Char(':')) });
    }
    return list;
}

// netsh wlan show networks mode=bssid: "SSID 1 : Name", "BSSID 1 : aa:bb:..", "Signal : 85%"
// (the labels are translated; the MAC, the % and the word SSID are not).
QList<Heard> parseNetsh(const QString &out)
{
    static const QRegularExpression mac(QStringLiteral("([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}"));
    static const QRegularExpression pct(QStringLiteral("(\\d+)%"));
    static const QRegularExpression ssid(QStringLiteral("^\\s*SSID\\s+\\d+\\s*:\\s*(.*)$"));
    QList<Heard> list;
    QString name;
    for (const QString &line : out.split(QLatin1Char('\n'))) {
        const auto s = ssid.match(line);
        if (s.hasMatch()) { name = s.captured(1).trimmed(); continue; }
        const auto m = mac.match(line);
        if (m.hasMatch() && line.contains(QStringLiteral("BSSID"))) { list.append({ m.captured(0), -100, name }); continue; }
        const auto p = pct.match(line);
        if (p.hasMatch() && !list.isEmpty() && list.last().dbm == -100)
            list.last().dbm = p.captured(1).toInt() / 2 - 100;
    }
    return list;
}

// What of it may be sent: the same rule as the Android app (DeviceLocation.ts usableNetworks).
QJsonArray usableNetworks(QList<Heard> heard)
{
    static const QRegularExpression macRe(QStringLiteral("^([0-9a-fA-F]{2}:){5}[0-9a-fA-F]{2}$"));
    std::sort(heard.begin(), heard.end(), [](const Heard &a, const Heard &b) { return a.dbm > b.dbm; });
    QJsonArray out;
    for (const Heard &h : heard) {
        if (!macRe.match(h.bssid).hasMatch() || h.ssid.toLower().endsWith(QStringLiteral("_nomap")))
            continue;
        if (h.bssid.left(2).toInt(nullptr, 16) & 0x02)          // an address a device made up
            continue;
        out.append(QJsonObject{ { QStringLiteral("macAddress"), h.bssid.toLower() },
                                { QStringLiteral("signalStrength"), h.dbm } });
        if (out.size() >= 20)
            break;
    }
    return out;
}
} // namespace

void WeatherService::tryWifiLocation()
{
    auto *proc = new QProcess(this);
#ifdef Q_OS_WIN
    const bool windows = true;
    proc->start(QStringLiteral("netsh"), { QStringLiteral("wlan"), QStringLiteral("show"),
                                           QStringLiteral("networks"), QStringLiteral("mode=bssid") });
#else
    const bool windows = false;
    proc->start(QStringLiteral("nmcli"), { QStringLiteral("-t"), QStringLiteral("-f"),
                                           QStringLiteral("BSSID,SIGNAL,SSID"), QStringLiteral("dev"),
                                           QStringLiteral("wifi"), QStringLiteral("list") });
#endif
    QTimer::singleShot(10000, proc, [proc] { proc->kill(); });
    connect(proc, &QProcess::finished, this, [this, proc, windows] {
        proc->deleteLater();
        const QString out = QString::fromLocal8Bit(proc->readAllStandardOutput());
        const QJsonArray networks = usableNetworks(windows ? parseNetsh(out) : parseNmcli(out));
        if (networks.size() < 2) {                 // too few to send: the IP lookup instead
            tryIpLocation(0);
            return;
        }
        QNetworkRequest request{QUrl(QString::fromLatin1(kBeaconDb))};
        request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
        request.setHeader(QNetworkRequest::UserAgentHeader, QString::fromLatin1(kLookupAgent));
        const QByteArray body = QJsonDocument(QJsonObject{
            { QStringLiteral("considerIp"), false }, { QStringLiteral("wifiAccessPoints"), networks } })
            .toJson(QJsonDocument::Compact);
        QNetworkReply *reply = m_network.post(request, body);
        connect(reply, &QNetworkReply::finished, this, [this, reply] {
            reply->deleteLater();
            const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
            const auto loc = obj.value(QStringLiteral("location")).toObject();
            if (m_manualLocation) { refresh(); return; }
            if (reply->error() == QNetworkReply::NoError && !obj.contains(QStringLiteral("fallback"))
                && loc.contains(QStringLiteral("lat")) && loc.contains(QStringLiteral("lng"))) {
                // Rounded to about 1 km: the weather services need no more.
                setLatitude(std::round(loc.value(QStringLiteral("lat")).toDouble() * 100) / 100);
                setLongitude(std::round(loc.value(QStringLiteral("lng")).toDouble() * 100) / 100);
                refresh();
                return;
            }
            tryIpLocation(0);
        });
    });
    connect(proc, &QProcess::errorOccurred, this, [this, proc](QProcess::ProcessError e) {
        if (e == QProcess::FailedToStart) { proc->deleteLater(); tryIpLocation(0); }
    });
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
            qWarning("[weather] fetch failed: %s", qPrintable(reply->errorString()));
            setAvailable(false);
            markFetchedOnce();
            m_retryTimer.start();
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
            qWarning("[weather] answer without a forecast");
            setAvailable(false);
            markFetchedOnce();
            m_retryTimer.start();
            return;
        }
        m_retryTimer.stop();

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
    if (m_wifiLocation) {
        tryWifiLocation();
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
    // BeaconDB first, with nothing but the address it sees: it placed a Vodafone line in Leiria
    // near Leiria, where ipapi.co says Faro (2026-11-23). Its IP data is DB-IP's (CC BY 4.0).
    static const char *const kServices[] = {
        kBeaconDb,
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
    request.setHeader(QNetworkRequest::UserAgentHeader, QString::fromLatin1(kLookupAgent));
    QNetworkReply *reply = nullptr;
    if (provider == 0) {
        request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
        reply = m_network.post(request, QByteArrayLiteral("{\"wifiAccessPoints\":[]}"));
    } else {
        reply = m_network.get(request);
    }
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
            // BeaconDB: {location: {lat, lng}}; the others: {latitude, longitude}.
            const auto loc = obj.value(QStringLiteral("location")).toObject();
            const double lat = provider == 0 ? number(loc.value(QStringLiteral("lat")), &latOk)
                                             : number(obj.value(QStringLiteral("latitude")), &latOk);
            const double lon = provider == 0 ? number(loc.value(QStringLiteral("lng")), &lonOk)
                                             : number(obj.value(QStringLiteral("longitude")), &lonOk);
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
