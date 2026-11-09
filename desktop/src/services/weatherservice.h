#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QTimer>
#include <QVariantList>

// docs/reference/sommet-spec.md, "Weather": Open-Meteo (no API key, simple REST, good European
// forecasts). Calls Open-Meteo directly - unlike DeviceService, this has nothing to do with
// the watch or the Python backend, so there's no reason to route it through
// desktop/backend/server.py.
//
// Refreshes itself on a 10-minute timer in the background (real request, 2026-08-07) so the
// card stays current without the user having to reopen the app. "If weather retrieval fails"
// used to mean "hide the card, no popup, no error"; the real request superseded that with a
// plain, friendly offline message shown in the card instead - see `hasFetchedOnce` and
// WeatherCard.qml.
//
// "WeatherService should allow changing the location source without UI modifications" -
// `latitude`/`longitude` are plain writable properties for exactly that reason: a future
// Settings screen (manual location) or a future watch-GPS source both just set these two
// properties and call refresh() again. No page needs to know which source is active.
class WeatherService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(bool available READ available NOTIFY availableChanged)
    // False until the very first refresh() call finishes (success or failure). WeatherCard.qml
    // uses this to stay blank on the very first app launch, before there's been any real
    // attempt, rather than flashing the offline message for a moment.
    Q_PROPERTY(bool hasFetchedOnce READ hasFetchedOnce NOTIFY hasFetchedOnceChanged)
    Q_PROPERTY(double latitude READ latitude WRITE setLatitude NOTIFY locationChanged)
    Q_PROPERTY(double longitude READ longitude WRITE setLongitude NOTIFY locationChanged)
    // Reverse-geocoded from latitude/longitude via OSM's own Nominatim (no API key, same
    // provider this app's map tiles already come from and are already attributed to) -
    // Open-Meteo itself has no place-name lookup, only coordinates. Empty string if the
    // lookup hasn't completed or failed - same "just hide it, no error" rule as weather
    // data itself, see refresh()'s own comment.
    Q_PROPERTY(QString placeName READ placeName NOTIFY placeNameChanged)
    Q_PROPERTY(double currentTemperature READ currentTemperature NOTIFY dataChanged)
    Q_PROPERTY(int currentWeatherCode READ currentWeatherCode NOTIFY dataChanged)
    Q_PROPERTY(double windSpeed READ windSpeed NOTIFY dataChanged)
    Q_PROPERTY(double todayHigh READ todayHigh NOTIFY dataChanged)
    Q_PROPERTY(double todayLow READ todayLow NOTIFY dataChanged)
    // Each entry: {date: "yyyy-MM-dd", high: number, low: number, code: int} - 3 entries,
    // today included, matching "Three-day forecast."
    Q_PROPERTY(QVariantList forecast READ forecast NOTIFY dataChanged)

public:
    explicit WeatherService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    bool available() const { return m_available; }
    bool hasFetchedOnce() const { return m_hasFetchedOnce; }
    double latitude() const { return m_latitude; }
    double longitude() const { return m_longitude; }
    void setLatitude(double value);
    void setLongitude(double value);
    double currentTemperature() const { return m_currentTemperature; }
    int currentWeatherCode() const { return m_currentWeatherCode; }
    double windSpeed() const { return m_windSpeed; }
    double todayHigh() const { return m_todayHigh; }
    double todayLow() const { return m_todayLow; }
    QVariantList forecast() const { return m_forecast; }
    QString placeName() const { return m_placeName; }

    Q_INVOKABLE void refresh();
    // Real "use this computer's location" - desktops have no GPS, so this is IP-based
    // geolocation (ipapi.co, then get.geojs.io; free, no key, HTTPS) rather than anything
    // satellite-based. Sets
    // latitude/longitude from the result and calls refresh(); leaves them untouched on
    // failure rather than snapping to some default.
    Q_INVOKABLE void detectLocationFromIp();

signals:
    void loadingChanged();
    void availableChanged();
    void hasFetchedOnceChanged();
    void locationChanged();
    void dataChanged();
    void placeNameChanged();

private:
    // One IP-location service after another until one answers (see detectLocationFromIp).
    void tryIpLocation(int provider);

    QNetworkAccessManager m_network;
    QTimer m_refreshTimer;
    bool m_loading = false;
    bool m_available = false;
    bool m_hasFetchedOnce = false;

    // Default: roughly central Europe - a real "no location configured yet" default is
    // Settings' job (Step 11: "User configured location"); this is a placeholder that at
    // least returns real weather rather than 0,0 (the Gulf of Guinea).
    double m_latitude = 48.85;
    double m_longitude = 2.35;

    double m_currentTemperature = 0;
    int m_currentWeatherCode = -1;
    double m_windSpeed = 0;
    double m_todayHigh = 0;
    double m_todayLow = 0;
    QVariantList m_forecast;
    QString m_placeName;

    void setLoading(bool value);
    void setAvailable(bool value);
    void markFetchedOnce();
    void fetchPlaceName();
};
