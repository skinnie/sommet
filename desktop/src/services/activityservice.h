#pragma once

#include <QJsonArray>
#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QSqlDatabase>
#include <QVariantList>

// Step 7. Wraps backend/server.py's /api/activities (raw GPX/FIT per recorded move) and
// parses the GPX side into structured fields - the backend deliberately doesn't parse GPX
// itself (see that endpoint's own comment: it just re-exposes what exercise_log.py already
// wrote to disk), so that parsing happens here, once, rather than once per QML page that
// wants activity data.
//
// Parses against the *real*, known GPX shape `tools/exercise_log.py`'s own `to_gpx()`
// produces (checked directly, not guessed): <trk><name>, <extensions><duration/distance/
// ascent/sport_type>, and a <trkseg> of <trkpt lat lon><ele><time>.
//
// Honest gap, not fabricated: `sport_type` in that GPX is a raw byte
// (`header["activity_type"]` in exercise_log.py) that is never decoded to a real sport name
// anywhere in this project's own tooling - so this class exposes it as a raw number and
// nothing in the UI picks a sport-specific icon from it yet. Inventing a mapping here would
// be guessing at something this project has explicitly never verified.
//
// Real, 2026-08-11 (performance audit: "is this creating a database... or just reading
// live?", comparing against Android's real SQLite-backed diff sync). The answer used to be
// "reads live, every time" - refresh() re-fetched, re-decoded, and re-parsed the watch's
// ENTIRE recorded history on every single Activities page visit (this class's own previous
// header comment reasoned explicitly against a database: "a DB would be solving a problem
// that doesn't exist here" - true when this was written, false once the perf audit measured
// what "every page visit re-decodes everything ever recorded" actually costs on rule 5's old
// hardware). This is that reversal, done honestly rather than silently: a real SQLite
// database (QStandardPaths::AppDataLocation + "/activities.db", one `activities` table,
// `idx` primary key), same spirit as Android's `ambitsync.db`. refresh() now sends the
// database's own highest known idx to the backend as `known_count` - exercise_log.py's own
// new `--known-count` skips decoding (not re-reading flash for) activities already in the
// database, and the backend only returns genuinely new ones. Already-known activities are
// read straight back out of the database (including their real GPX/FIT bytes, so Export
// still works exactly as before) - zero network payload and zero GPX re-parse for anything
// that hasn't changed. Replaces the old per-file activities_cache/ entirely (gpxText/
// fitBase64 are now DB columns, not separate files) - same offline-fallback role (used
// whenever the live call fails), same track/points array cached (as compact JSON) so
// reading from the database never re-runs the GPX XML parser either.
class ActivityService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(bool ok READ ok NOTIFY activitiesChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    // True when activities[] came from the local database (no live watch read succeeded
    // this time) rather than a real, just-now read off the watch.
    Q_PROPERTY(bool showingCachedData READ showingCachedData NOTIFY activitiesChanged)
    // Each entry: {name, durationSeconds, distanceMeters, ascentMeters, sportTypeRaw,
    // startTime (ISO string, from the first track point, empty if none),
    // track: [{lat, lon, ele}, ...], gpxText, fitBase64}
    Q_PROPERTY(QVariantList activities READ activities NOTIFY activitiesChanged)

public:
    explicit ActivityService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    bool ok() const { return m_ok; }
    QString lastError() const { return m_lastError; }
    bool showingCachedData() const { return m_showingCachedData; }
    QVariantList activities() const { return m_activities; }

    Q_INVOKABLE void refresh();

    // Optional per-app "send this logged Suunto App output to intervals.icu as this native
    // stream" choice (empty/"custom" = default, developer field only). Persisted in QSettings;
    // applied to the FIT the backend generates on the next refresh. See requestActivities.
    Q_INVOKABLE QString intervalsStreamFor(const QString &app) const;
    Q_INVOKABLE void setIntervalsStreamFor(const QString &app, const QString &stream);

    // Pull activities FROM intervals.icu into the local DB (André, 2026-08-18) - for moves not
    // recorded on the watch (Zwift, manual, other devices). oldestDays<=0 pulls everything;
    // otherwise the last N days. Imported rows are marked source="intervals" so they blend into
    // the lists but stay tellable-apart, and they never touch the watch-sync known-count.
    Q_INVOKABLE void importFromIntervals(int oldestDays);

    // Export (upload) the watch's own activities TO intervals.icu as FIT files (André,
    // 2026-08-24). Only rows we haven't already uploaded; each is marked once it lands.
    Q_INVOKABLE void exportToIntervals();

    // Export-scope selector (André, 2026-08-24): which activities get pushed to intervals.icu.
    // "manual" = per-activity only (nothing auto); "suunto" = watch moves; "etrex" = Garmin
    // eTrex device moves (GPX); "all" = suunto+etrex. Never the intervals imports themselves.
    // Persisted in QSettings intervals/exportScope. Auto-export (for suunto/all) runs after a
    // sync; eTrex auto-export is driven from GarminService.
    Q_INVOKABLE QString intervalsExportScope() const;
    Q_INVOKABLE void setIntervalsExportScope(const QString &scope);

    // Export one specific activity (the Upload tab's per-activity button) - works for any
    // activity shown in the detail view: a watch move sends its FIT (carrying the logged-app
    // streams), an eTrex move its GPX. idx<0 / no DB row is fine (nothing to mark).
    Q_INVOKABLE void exportActivityToIntervals(const QString &name, const QString &fitBase64,
                                               const QString &gpxText);

signals:
    void loadingChanged();
    void activitiesChanged();
    void lastErrorChanged();
    void importFinished(int count);
    void importError(const QString &message);
    void exportFinished(int uploaded, int failed);
    void exportError(const QString &message);

private:
    QNetworkAccessManager m_network;
    QSqlDatabase m_db;
    bool m_loading = false;
    bool m_ok = false;
    bool m_showingCachedData = false;
    QString m_lastError;
    QVariantList m_activities;

    void setLoading(bool value);
    void setLastError(const QString &message);
    static QVariantMap parseGpx(const QString &gpxText);

    void openDatabase();
    int dbKnownCount();
    void dbClear();
    void dbInsert(int index, const QVariantMap &parsed, const QString &gpxText,
                  const QString &fitBase64, const QString &ruleOutputsJson);
    bool dbLoadAll();
    void importActivitiesInto(const QJsonArray &activities);
    void uploadOneToIntervals(int idx, const QByteArray &fit,
                              const QString &athlete, const QString &key);
    int m_exportPending = 0;
    int m_exportUploaded = 0;
    int m_exportFailed = 0;
    void requestActivities(int knownCount, bool alreadyRetried);
    QVariantMap intervalsStreamMap() const;
};
