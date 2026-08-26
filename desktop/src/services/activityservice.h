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

    // Delete one activity (André, 2026-08-25). Removes it from the local database AND remembers
    // it (a tombstone keyed by start-time|name) so re-syncing the watch or re-importing from
    // intervals/Garmin never brings it back - the watch's own log is circular and cannot be
    // deleted, so a tombstone is the only way a deleted watch move stays gone. When the activity
    // came FROM intervals.icu (source == "intervals"), it is ALSO deleted there permanently, per
    // André's choice. The row is identified by its `index` (the DB idx primary key).
    Q_INVOKABLE void deleteActivity(const QVariantMap &activity);

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

    // Backfill the GPS track for already-imported intervals.icu activities (André, 2026-08-25:
    // "we have activities with distance (running, so outside! so gps!) and they say no gps").
    // importFromIntervals only ever fetched the activity-LIST JSON, which carries summary
    // numbers and no positions at all - so every imported outdoor move was stored with an empty
    // track and rendered as "No GPS track" even though intervals.icu had the real trace.
    //
    // The per-activity stream endpoint is a SEPARATE call, so this cannot be folded into the
    // list request: it is one GET per activity. With thousands of rows that is far too many to
    // fire at once, so this walks a queue ONE request at a time, newest-first, and only for rows
    // that actually need it (source='intervals', a real distance, no track yet). `maxCount`
    // bounds a single run so a first backfill is progressive rather than a multi-thousand-call
    // stampede; call it again to continue where it left off.
    Q_INVOKABLE void backfillIntervalsTracks(int maxCount);

    // Import activities FROM Garmin Connect into the local DB (André, 2026-08-24) - the cloud
    // account (a Garmin watch/Edge), distinct from the eTrex USB path in GarminService. Fetched
    // via the backend's /api/garmin/activities (tools/garmin_sync.py owns the OAuth). Rows are
    // tagged source="garmin"; a pull-only refresh, watch rows untouched.
    Q_INVOKABLE void importFromGarmin(int days);

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

    // Export one activity to Garmin Connect (the Upload tab's Garmin button). Goes through the
    // backend (tools/garmin_sync.py --upload owns the OAuth). Garmin dedups by start time.
    Q_INVOKABLE void exportActivityToGarmin(const QString &name, const QString &fitBase64,
                                            const QString &gpxText);

    // Bulk-export to Garmin Connect, mirroring the intervals export scope. exportToGarmin()
    // pushes watch moves (their FIT); exportActivitiesToGarmin() pushes a passed list (eTrex,
    // GPX). Both dedup by a stored per-activity key so re-running is a no-op (Garmin also dedups
    // by start time on its side).
    Q_INVOKABLE void exportToGarmin();
    Q_INVOKABLE void exportActivitiesToGarmin(const QVariantList &activities);

    // Bulk-export a list of activity maps (each {name, startTime, fitBase64?, gpxText?}) that
    // aren't already exported. Used for eTrex moves (which live in GarminService, GPX-only);
    // dedup is by a stable per-activity key kept in QSettings, so re-running is a no-op.
    Q_INVOKABLE void exportActivitiesToIntervals(const QVariantList &activities);

signals:
    void loadingChanged();
    void activitiesChanged();
    void lastErrorChanged();
    void importFinished(int count);
    void importError(const QString &message);
    // Emitted after an activity is removed locally (the intervals.icu delete, when it applies,
    // is fire-and-forget - a cloud failure is surfaced via lastError, not this signal).
    void activityDeleted(const QString &name);
    // GPS backfill progress (see backfillIntervalsTracks): `done`/`total` for this run, and a
    // final count of how many rows actually gained a real track.
    void trackBackfillProgress(int done, int total);
    void trackBackfillFinished(int filled, int remaining);
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
    // Per-watch scoping (André, 2026-08-26): activities are keyed by (device, idx) so several
    // watches' histories coexist instead of one watch's index-N replacing another's. device is
    // the backend's device_key() (product-id hex). m_lastDevice is the device the last fetch
    // was about, used to compute the next known_count against the right watch.
    int dbKnownCount(const QString &device);
    void dbClear(const QString &device);
    QString m_lastDevice;
    // Deleted-activity tombstones (start-time|name). Loaded once from the `deleted_activities`
    // table in openDatabase(); dbLoadAll() skips any row whose key is in here, so a deleted
    // move never re-appears however it gets re-inserted (watch re-sync, intervals/Garmin
    // re-import). deleteActivity() adds to both the table and this set.
    // GPS backfill state (see backfillIntervalsTracks). One in-flight request at a time: the
    // queue holds the external_ids still to fetch, and fetchNextTrack() pops one, stores its
    // track_json, then chains to the next.
    QStringList m_trackQueue;
    int m_trackTotal = 0;
    int m_trackDone = 0;
    int m_trackFilled = 0;
    bool m_trackBusy = false;
    void fetchNextTrack();

    QSet<QString> m_tombstones;
    void loadTombstones();
    static QString tombstoneKey(const QString &startTime, const QString &name);
    void dbInsert(int index, const QString &device, const QVariantMap &parsed,
                  const QString &gpxText, const QString &fitBase64,
                  const QString &ruleOutputsJson);
    bool dbLoadAll();
    // Extracts the resting-HRV readings (5+5 / lie-still tests, hrvResting == 1) from the
    // loaded activities and persists them to QSettings health/watchHrv as [{date,value}], where
    // HealthService merges them into the Health page's HRV series as the "watch" source. Kept
    // decoupled via QSettings (same pattern as manual entries) rather than a direct service ref.
    void updateWatchHrvStore();
    void dedupeActivities();
    void importActivitiesInto(const QJsonArray &activities);
    void importGarminActivitiesInto(const QJsonArray &activities);
    void uploadOneToIntervals(int idx, const QByteArray &fit,
                              const QString &athlete, const QString &key);
    // Generic single-file uploader used by the per-activity export (FIT or GPX). idx<0 means
    // "not a DB row" - success isn't recorded against any activity.
    void uploadFileToIntervals(int idx, const QByteArray &data, const QString &contentType,
                               const QString &filename, const QString &athlete,
                               const QString &key);
    // Upload one activity to Garmin via the backend; counts into the m_export* tally.
    void uploadToGarmin(const QByteArray &data, bool isFit);
    int m_exportPending = 0;
    int m_exportUploaded = 0;
    int m_exportFailed = 0;
    void requestActivities(int knownCount, bool alreadyRetried);
    QVariantMap intervalsStreamMap() const;
};
