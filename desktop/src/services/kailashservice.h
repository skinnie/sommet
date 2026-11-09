#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QUrl>
#include <QVariantList>
#include <QVariantMap>

// Kailash ("Hoopoe") has no sport-mode logbook or ExerciseLog PMEM region the way Ambit3
// does - real request 2026-08-08 ("Kailash doesn't have sports modes... if possible activity
// download"). Wraps backend/server.py's /api/kailash/history and /api/kailash/tracklog
// (tools/kailash_history.py / kailash_tracklog.py --json, both live-verified against real
// hardware this same session) the same thin-HTTP-client way DeviceService/ActivityService
// already do. Kept as its own class rather than folded into either of those: neither
// existing concept (device identity, GPX/FIT activities) actually fits what this data is -
// visited-cities travel stats, plus a passive GPS track with no FIT/altitude data.
class KailashService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)

    // /api/kailash/history - the watch's own "7R" button screen data.
    Q_PROPERTY(bool historyOk READ historyOk NOTIFY historyChanged)
    Q_PROPERTY(int citiesVisited READ citiesVisited NOTIFY historyChanged)
    Q_PROPERTY(int countriesVisited READ countriesVisited NOTIFY historyChanged)
    Q_PROPERTY(QString lastKnownCountry READ lastKnownCountry NOTIFY historyChanged)
    Q_PROPERTY(QString lastKnownTime READ lastKnownTime NOTIFY historyChanged)
    Q_PROPERTY(double lastKnownLatitude READ lastKnownLatitude NOTIFY historyChanged)
    Q_PROPERTY(double lastKnownLongitude READ lastKnownLongitude NOTIFY historyChanged)
    Q_PROPERTY(bool hasLastKnownLocation READ hasLastKnownLocation NOTIFY historyChanged)
    Q_PROPERTY(int travellingDays READ travellingDays NOTIFY historyChanged)
    Q_PROPERTY(double travelledDistanceMeters READ travelledDistanceMeters NOTIFY historyChanged)
    Q_PROPERTY(double furthestFromHomeMeters READ furthestFromHomeMeters NOTIFY historyChanged)
    // Each entry: {when, durationSeconds, distanceMeters, maxSpeed} - the real "activity
    // mode" logbook bundled in the same DeviceHistory reply; see kailash_history.py's own
    // docstring for how this was found (this project had separately been unable to locate
    // it as its own flash region).
    Q_PROPERTY(QVariantList sessions READ sessions NOTIFY historyChanged)
    // Real, 2026-08-09 ("Add a world map with the points were kailash has been") - every
    // real visited place (lat/lon/country), not just the last one - see
    // kailash_history.py's own comment on why this only recently stopped being an
    // overwrite-the-last-one bug (never had a visible symptom against a watch with just
    // one real visited place so far). Each entry: {lat, lon, country}.
    Q_PROPERTY(QVariantList visitedPlaces READ visitedPlaces NOTIFY historyChanged)

    // /api/kailash/tracklog - one activity per real DeviceHistory session, correlated by
    // timestamp against TrackLog's own GPS points (see kailash_tracklog.py's
    // split_into_activities() docstring - real, 2026-08-09, replacing the single bundled-
    // everything activity this used to be). Same field shape ActivityService::activities()
    // entries already use, so ActivityCard/MapView need no new QML code to show them.
    Q_PROPERTY(bool trackLogOk READ trackLogOk NOTIFY trackLogChanged)
    Q_PROPERTY(QVariantList trackLogActivities READ trackLogActivities NOTIFY trackLogChanged)
    // Real, 2026-08-09 ("activities, they take a while to load...any chance of fixing?") -
    // `loading` above is shared with refreshHistory() and clears as soon as that fast
    // request finishes, well before this slow one does, so ActivitiesPage had no way to
    // show "still fetching GPS tracks" without it looking done-but-actually-not. Tracked
    // separately so it stays true for this request's own real ~1.3MB-flash-read duration.
    Q_PROPERTY(bool trackLogLoading READ trackLogLoading NOTIFY trackLogLoadingChanged)

    // Real, 2026-08-09 ("I believe you put a POI icon for home, name home and identify the
    // city by coordinates") - the watch's real HomeLocation setting (sml.DeviceSettings.
    // HomeLocation, see sommet_kailash_home_location_field memory) already gets read
    // into SettingsWriteService.settings; QML passes those two coordinates in here rather
    // than this class re-fetching settings itself (it has nothing to do with settings
    // otherwise). Reverse-geocoded via the same real Nominatim call WeatherService's own
    // fetchPlaceName() already uses and already proved compliant with (real User-Agent
    // header, OSM's own usage policy) - not a new mechanism, the same one reused.
    Q_PROPERTY(bool hasHomeLocation READ hasHomeLocation NOTIFY homeLocationChanged)
    Q_PROPERTY(double homeLatitude READ homeLatitude NOTIFY homeLocationChanged)
    Q_PROPERTY(double homeLongitude READ homeLongitude NOTIFY homeLocationChanged)
    Q_PROPERTY(QString homeCity READ homeCity NOTIFY homeLocationChanged)

public:
    explicit KailashService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    QString lastError() const { return m_lastError; }

    bool historyOk() const { return m_historyOk; }
    int citiesVisited() const { return m_citiesVisited; }
    int countriesVisited() const { return m_countriesVisited; }
    QString lastKnownCountry() const { return m_lastKnownCountry; }
    QString lastKnownTime() const { return m_lastKnownTime; }
    double lastKnownLatitude() const { return m_lastKnownLatitude; }
    double lastKnownLongitude() const { return m_lastKnownLongitude; }
    bool hasLastKnownLocation() const { return m_hasLastKnownLocation; }
    int travellingDays() const { return m_travellingDays; }
    double travelledDistanceMeters() const { return m_travelledDistanceMeters; }
    double furthestFromHomeMeters() const { return m_furthestFromHomeMeters; }
    QVariantList sessions() const { return m_sessions; }
    QVariantList visitedPlaces() const { return m_visitedPlaces; }

    bool trackLogOk() const { return m_trackLogOk; }
    QVariantList trackLogActivities() const { return m_trackLogActivities; }
    bool trackLogLoading() const { return m_trackLogLoading; }

    bool hasHomeLocation() const { return m_hasHomeLocation; }
    double homeLatitude() const { return m_homeLatitude; }
    double homeLongitude() const { return m_homeLongitude; }
    QString homeCity() const { return m_homeCity; }

    // GET /api/kailash/history - real, read-only (a single 0x1200 SBEM query), safe any time.
    Q_INVOKABLE void refreshHistory();
    // GET /api/kailash/tracklog - real, read-only, but a real ~1.3MB flash read (slow - the
    // backend's own timeout for this one is longer than usual). Not called automatically
    // alongside refreshHistory(); the UI triggers it explicitly so a quick Home refresh
    // doesn't always pay this cost.
    Q_INVOKABLE void refreshTrackLog();
    // Real Nominatim reverse-geocode of the watch's own HomeLocation coordinates (caller
    // supplies them - see this property's own header comment for why). No watch access at
    // all, a plain internet call - safe to call as soon as the coordinates are known.
    Q_INVOKABLE void refreshHomeLocation(double latitude, double longitude);

signals:
    void loadingChanged();
    void lastErrorChanged();
    void historyChanged();
    void trackLogChanged();
    void homeLocationChanged();
    void trackLogLoadingChanged();

private:
    QNetworkAccessManager m_network;
    bool m_loading = false;
    QString m_lastError;

    bool m_historyOk = false;
    int m_citiesVisited = 0;
    int m_countriesVisited = 0;
    QString m_lastKnownCountry;
    QString m_lastKnownTime;
    double m_lastKnownLatitude = 0;
    double m_lastKnownLongitude = 0;
    bool m_hasLastKnownLocation = false;
    int m_travellingDays = 0;
    double m_travelledDistanceMeters = 0;
    double m_furthestFromHomeMeters = 0;
    QVariantList m_sessions;
    QVariantList m_visitedPlaces;

    bool m_trackLogOk = false;
    QVariantList m_trackLogActivities;
    bool m_trackLogLoading = false;

    bool m_hasHomeLocation = false;
    double m_homeLatitude = 0;
    double m_homeLongitude = 0;
    QString m_homeCity;

    void setLoading(bool value);
    void setTrackLogLoading(bool value);
    void setLastError(const QString &message);

    static QUrl backendUrl(const QString &path);
};
