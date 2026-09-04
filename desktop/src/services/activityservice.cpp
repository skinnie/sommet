#include "activityservice.h"

#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QDateTime>
#include <QHash>
#include <QNetworkReply>
#include <QHttpMultiPart>
#include <QNetworkRequest>
#include <QSet>
#include <QSettings>
#include <QSqlError>
#include <QSqlQuery>
#include <QStandardPaths>
#include <QUrl>
#include <QUrlQuery>
#include <QXmlStreamReader>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

ActivityService::ActivityService(QObject *parent) : QObject(parent)
{
    openDatabase();
}

void ActivityService::setLoading(bool value)
{
    if (m_loading == value)
        return;
    m_loading = value;
    emit loadingChanged();
}

void ActivityService::setLastError(const QString &message)
{
    m_lastError = message;
    emit lastErrorChanged();
}

// Parses exactly the shape tools/exercise_log.py's to_gpx() produces - see this class's own
// header comment. Deliberately narrow (no namespace-prefix handling, no alternate GPX
// dialects) since this only ever reads GPX this project generated itself, not arbitrary
// third-party files - that's a different, real need (Routes' "Import GPX" in Step 8) with
// its own, more defensive parser, not this one.
QVariantMap ActivityService::parseGpx(const QString &gpxText)
{
    QVariantMap result;
    result[QStringLiteral("name")] = QString();
    result[QStringLiteral("durationSeconds")] = 0;
    result[QStringLiteral("distanceMeters")] = 0;
    result[QStringLiteral("ascentMeters")] = 0;
    // kcal straight off the watch (see exercise_log.py's own comment on the unit). 0 means
    // "not recorded" - an older GPX in the cache predates this field entirely, and the UI
    // hides the figure rather than claiming the move cost nothing.
    result[QStringLiteral("energyKcal")] = 0;
    result[QStringLiteral("sportTypeRaw")] = -1;
    result[QStringLiteral("startTime")] = QString();
    // Richer summary metrics carried through from the watch log header (exercise_log.py).
    // -1 / 0 mean "not recorded" so the UI can hide the figure rather than show a false 0.
    result[QStringLiteral("avgHr")] = 0;
    result[QStringLiteral("maxHr")] = 0;
    result[QStringLiteral("avgCadence")] = 0;
    result[QStringLiteral("maxCadence")] = 0;
    result[QStringLiteral("avgSpeedMh")] = 0;     // metres/hour, watch's own unit
    result[QStringLiteral("maxSpeedMh")] = 0;
    result[QStringLiteral("descentMeters")] = 0;
    result[QStringLiteral("recoverySeconds")] = 0;
    result[QStringLiteral("peakTrainingEffect")] = 0;   // value*10 (35 -> 3.5)
    result[QStringLiteral("poolLengths")] = 0;
    result[QStringLiteral("maxAltitudeMeters")] = 0;
    // HRV (rMSSD, ms) computed by exercise_log.py from this move's raw R-R (needs a Smart
    // Sensor belt); 0 = none recorded. hrvResting flags a lie-still HRV test (a 5+5 or similar)
    // vs a workout that merely logged R-R - only resting ones feed the Health HRV series.
    // hrvOrthoDrop is the standing-vs-lying RMSSD drop % for a 5+5 (0 when not an orthostatic).
    result[QStringLiteral("hrvRmssd")] = 0;
    result[QStringLiteral("hrvResting")] = 0;
    result[QStringLiteral("hrvOrthoDrop")] = 0;

    QVariantList track;
    QXmlStreamReader xml(gpxText);
    QString currentTag;
    QString pendingLat, pendingLon, pendingEle;
    bool inExtensions = false;

    while (!xml.atEnd()) {
        const auto token = xml.readNext();
        if (token == QXmlStreamReader::StartElement) {
            const QString tag = xml.name().toString();
            if (tag == QStringLiteral("extensions")) {
                inExtensions = true;
            } else if (tag == QStringLiteral("trkpt")) {
                pendingLat = xml.attributes().value(QStringLiteral("lat")).toString();
                pendingLon = xml.attributes().value(QStringLiteral("lon")).toString();
                pendingEle.clear();
            }
            currentTag = tag;
        } else if (token == QXmlStreamReader::EndElement) {
            const QString tag = xml.name().toString();
            if (tag == QStringLiteral("extensions"))
                inExtensions = false;
            if (tag == QStringLiteral("trkpt")) {
                QVariantMap point;
                point[QStringLiteral("lat")] = pendingLat.toDouble();
                point[QStringLiteral("lon")] = pendingLon.toDouble();
                point[QStringLiteral("ele")] = pendingEle.toDouble();
                track.append(point);
            }
            currentTag.clear();
        } else if (token == QXmlStreamReader::Characters && !xml.isWhitespace()) {
            const QString text = xml.text().toString();
            if (currentTag == QStringLiteral("name")) {
                result[QStringLiteral("name")] = text;
            } else if (currentTag == QStringLiteral("ele")) {
                pendingEle = text;
            } else if (currentTag == QStringLiteral("time")
                       && result[QStringLiteral("startTime")].toString().isEmpty()) {
                result[QStringLiteral("startTime")] = text;
            } else if (inExtensions && currentTag == QStringLiteral("duration")) {
                result[QStringLiteral("durationSeconds")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("distance")) {
                result[QStringLiteral("distanceMeters")] = text.toDouble();
            } else if (inExtensions && currentTag == QStringLiteral("ascent")) {
                result[QStringLiteral("ascentMeters")] = text.toDouble();
            } else if (inExtensions && currentTag == QStringLiteral("energy")) {
                result[QStringLiteral("energyKcal")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("sport_type")) {
                result[QStringLiteral("sportTypeRaw")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("avg_hr")) {
                result[QStringLiteral("avgHr")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("max_hr")) {
                result[QStringLiteral("maxHr")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("avg_cadence")) {
                result[QStringLiteral("avgCadence")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("max_cadence")) {
                result[QStringLiteral("maxCadence")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("avg_speed")) {
                result[QStringLiteral("avgSpeedMh")] = text.toDouble();
            } else if (inExtensions && currentTag == QStringLiteral("max_speed")) {
                result[QStringLiteral("maxSpeedMh")] = text.toDouble();
            } else if (inExtensions && currentTag == QStringLiteral("descent")) {
                result[QStringLiteral("descentMeters")] = text.toDouble();
            } else if (inExtensions && currentTag == QStringLiteral("recovery_time")) {
                result[QStringLiteral("recoverySeconds")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("peak_training_effect")) {
                result[QStringLiteral("peakTrainingEffect")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("pool_lengths")) {
                result[QStringLiteral("poolLengths")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("max_altitude")) {
                result[QStringLiteral("maxAltitudeMeters")] = text.toDouble();
            } else if (inExtensions && currentTag == QStringLiteral("hrv_rmssd")) {
                result[QStringLiteral("hrvRmssd")] = text.toDouble();
            } else if (inExtensions && currentTag == QStringLiteral("hrv_resting")) {
                result[QStringLiteral("hrvResting")] = text.toInt();
            } else if (inExtensions && currentTag == QStringLiteral("hrv_ortho_drop")) {
                result[QStringLiteral("hrvOrthoDrop")] = text.toDouble();
            }
        }
    }

    // Derived: average pace (seconds per km). Prefer the watch's own avg speed; fall back to
    // distance/duration. 0 when there's no distance to pace against.
    const double distM = result.value(QStringLiteral("distanceMeters")).toDouble();
    const double durS = result.value(QStringLiteral("durationSeconds")).toDouble();
    const double avgSpeedMh = result.value(QStringLiteral("avgSpeedMh")).toDouble();
    double paceSecPerKm = 0;
    if (avgSpeedMh > 0)
        paceSecPerKm = 3600.0 * 1000.0 / avgSpeedMh;   // (s/h)*(m/km) / (m/h) = s/km
    else if (distM > 0 && durS > 0)
        paceSecPerKm = durS / (distM / 1000.0);
    result[QStringLiteral("paceSecPerKm")] = paceSecPerKm;

    // If the watch didn't record an average speed (older GPX, or a sport that doesn't), derive
    // it from distance/duration so the Avg-speed column still has something to show. The watch's
    // own value (moving average) is preferred when present.
    if (avgSpeedMh <= 0 && distM > 0 && durS > 0)
        result[QStringLiteral("avgSpeedMh")] = distM / (durS / 3600.0);

    result[QStringLiteral("track")] = track;
    return result;
}

void ActivityService::refresh()
{
    setLoading(true);
    setLastError(QString());
    // Read EVERY connected watch, each scoped to its own USB serial, so two watches of the same
    // model (two Ambit3 Peaks) don't collapse into one identity and clobber each other's history
    // (André, 2026-09-04). Falls back to the single pinned read when the device list isn't
    // available (BLE, or an older/erroring backend).
    refreshAllWatches();
}

void ActivityService::refreshAllWatches()
{
    const QUrl url(kBackendBase + QStringLiteral("/api/devices"));
    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const bool netOk = reply->error() == QNetworkReply::NoError;
        const auto root = netOk ? QJsonDocument::fromJson(reply->readAll()).object()
                                : QJsonObject{};
        const auto watches = root.value(QStringLiteral("watches")).toArray();
        if (!netOk || !root.value(QStringLiteral("ok")).toBool() || watches.isEmpty()) {
            // No enumerable USB watches (BLE handshake, no watch, or backend error): keep the
            // original single-read behaviour, which also covers the BLE path server-side.
            requestActivities(dbKnownCount(m_lastDevice), false);
            return;
        }
        m_pendingWatchReads = watches.size();
        for (const auto &wv : watches) {
            const auto w = wv.toObject();
            readWatchActivities(w.value(QStringLiteral("productId")).toInt(),
                                w.value(QStringLiteral("serial")).toString());
        }
    });
}

void ActivityService::readWatchActivities(int productId, const QString &serial,
                                          bool retriedFromZero)
{
    // Identity for this physical watch: its serial when we have one (unique even between two
    // same-model watches), else the product-id hex as a fallback. The backend echoes this same
    // string back as the response "device", so rows key to it consistently.
    const QString tag = !serial.isEmpty()
        ? serial
        : QStringLiteral("0x") + QString::number(productId, 16);
    QString path = QStringLiteral("/api/activities?known_count=%1&device=%2")
                       .arg(retriedFromZero ? 0 : dbKnownCount(tag)).arg(productId);
    if (!serial.isEmpty())
        path += QStringLiteral("&serial=") + QString::fromUtf8(QUrl::toPercentEncoding(serial));

    QNetworkReply *reply = m_network.get(QNetworkRequest(QUrl(kBackendBase + path)));
    connect(reply, &QNetworkReply::finished, this,
            [this, reply, productId, serial, tag, retriedFromZero] {
        reply->deleteLater();
        auto finishOne = [this] {
            if (--m_pendingWatchReads <= 0) {
                m_pendingWatchReads = 0;
                setLoading(false);
                m_ok = true;                 // an empty result is a valid "no activities" state
                m_showingCachedData = false;
                dbLoadAll();                 // one reload + de-dupe after every watch is in
                emit activitiesChanged();
            }
        };
        if (reply->error() != QNetworkReply::NoError) { finishOne(); return; }
        const auto root = QJsonDocument::fromJson(reply->readAll()).object();
        if (!root.value(QStringLiteral("ok")).toBool()) { finishOne(); return; }

        // Watch log wrapped/reset since our cache was built: our cached indices for this watch no
        // longer mean the same moves. Re-read this one watch from scratch, once.
        const int totalEntries = root.value(QStringLiteral("total_entries")).toInt();
        if (!retriedFromZero && totalEntries < dbKnownCount(tag)) {
            dbClear(tag);
            readWatchActivities(productId, serial, /*retriedFromZero=*/true);
            return;                          // this slot's finishOne fires on the retry's reply
        }

        const QString device = root.value(QStringLiteral("device")).toString();
        const auto rawList = root.value(QStringLiteral("activities")).toArray();
        for (const auto &rawValue : rawList) {
            const auto rawObj = rawValue.toObject();
            const int index = rawObj.value(QStringLiteral("index")).toInt();
            const QString gpxText = rawObj.value(QStringLiteral("gpx")).toString();
            const QString fitBase64 = rawObj.value(QStringLiteral("fit_base64")).toString();
            const QVariantMap parsed = parseGpx(gpxText);
            const QJsonValue ruleOutputs = rawObj.value(QStringLiteral("rule_outputs"));
            const QString ruleOutputsJson = ruleOutputs.isObject()
                ? QString::fromUtf8(QJsonDocument(ruleOutputs.toObject()).toJson(
                      QJsonDocument::Compact))
                : QString();
            dbInsert(index, device, parsed, gpxText, fitBase64, ruleOutputsJson);
        }
        finishOne();
    });
}

QVariantMap ActivityService::intervalsStreamMap() const
{
    const QString raw = QSettings().value(
        QStringLiteral("intervals/streamMap")).toString();
    if (raw.isEmpty())
        return {};
    const auto doc = QJsonDocument::fromJson(raw.toUtf8());
    return doc.isObject() ? doc.object().toVariantMap() : QVariantMap{};
}

QString ActivityService::intervalsStreamFor(const QString &app) const
{
    return intervalsStreamMap().value(app).toString();
}

void ActivityService::setIntervalsStreamFor(const QString &app, const QString &stream)
{
    QVariantMap map = intervalsStreamMap();
    // Empty / "custom" (the default) means "developer field only" - drop the key entirely so
    // the request carries no mapping for this app.
    if (stream.isEmpty() || stream == QStringLiteral("custom"))
        map.remove(app);
    else
        map.insert(app, stream);
    QSettings().setValue(QStringLiteral("intervals/streamMap"),
                         QString::fromUtf8(QJsonDocument(QJsonObject::fromVariantMap(map))
                                               .toJson(QJsonDocument::Compact)));
}

void ActivityService::requestActivities(int knownCount, bool alreadyRetried)
{
    // Experimental "mark synced workouts as synced" toggle (DeviceService persists it to
    // this same QSettings key; read here rather than coupling the two services). When on,
    // ask the backend to write the watch's per-move synced flag after this read. Off by
    // default - see DeviceService::markSyncedEnabled's header comment.
    const bool markSynced =
        QSettings().value(QStringLiteral("experimental/markSynced"), false).toBool();
    QString path = QStringLiteral("/api/activities?known_count=%1").arg(knownCount);
    if (markSynced)
        path += QStringLiteral("&mark_synced=1");
    // Optional per-app "send this logged Suunto App output to intervals.icu as <native stream>"
    // mapping (off by default; the custom developer field is always emitted regardless). Stored
    // as a JSON object {appName: stream}; passed to exercise_log.py as repeated ?map=APP=STREAM
    // so the generated FIT already carries the native stream for those apps.
    const auto streamMap = intervalsStreamMap();
    for (auto it = streamMap.constBegin(); it != streamMap.constEnd(); ++it) {
        const QString pair = it.key() + QStringLiteral("=") + it.value().toString();
        path += QStringLiteral("&map=") + QString::fromUtf8(
            QUrl::toPercentEncoding(pair));
    }
    const QUrl url(kBackendBase + path);
    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply, knownCount, alreadyRetried] {
        reply->deleteLater();

        if (reply->error() != QNetworkReply::NoError) {
            setLoading(false);
            m_ok = dbLoadAll();
            if (!m_ok)
                setLastError(reply->errorString());
            emit activitiesChanged();
            return;
        }

        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto root = doc.object();
        const bool liveOk = root.value(QStringLiteral("ok")).toBool();
        if (!liveOk) {
            setLoading(false);
            m_ok = dbLoadAll();
            if (!m_ok)
                setLastError(root.value(QStringLiteral("stderr")).toString());
            emit activitiesChanged();
            return;
        }

        // Real total entry count straight from the watch (exercise_log.py's own
        // master.json, see server.py's own comment) - if it's LESS than what we already
        // knew, the watch's log wrapped/reset since our database was built, so our cached
        // indices no longer mean the same activities. One automatic retry from scratch
        // (known_count 0) rather than silently mixing old and new data under the same idx.
        // Which watch this response is about (backend device_key()). If it differs from the
        // watch our known_count was computed against, the backend may have skipped the wrong
        // activities - re-fetch once, scoped to the real watch, before touching the cache.
        const QString device = root.value(QStringLiteral("device")).toString();
        if (!alreadyRetried && knownCount > 0 && !device.isEmpty()
            && device != m_lastDevice) {
            m_lastDevice = device;
            requestActivities(dbKnownCount(device), true);
            return;
        }
        m_lastDevice = device;

        const int totalEntries = root.value(QStringLiteral("total_entries")).toInt();
        if (totalEntries < knownCount && !alreadyRetried) {
            dbClear(device);            // only this watch's rows; other watches' histories stay
            requestActivities(0, true);
            return;
        }

        const auto rawList = root.value(QStringLiteral("activities")).toArray();
        for (const auto &rawValue : rawList) {
            const auto rawObj = rawValue.toObject();
            const int index = rawObj.value(QStringLiteral("index")).toInt();
            const QString gpxText = rawObj.value(QStringLiteral("gpx")).toString();
            const QString fitBase64 = rawObj.value(QStringLiteral("fit_base64")).toString();
            const QVariantMap parsed = parseGpx(gpxText);
            // Logged Suunto App outputs (ruleoutput1..5), when present: stored verbatim as the
            // backend's compact JSON, re-emitted to QML as `ruleOutputs` from the cache.
            const QJsonValue ruleOutputs = rawObj.value(QStringLiteral("rule_outputs"));
            const QString ruleOutputsJson = ruleOutputs.isObject()
                ? QString::fromUtf8(QJsonDocument(ruleOutputs.toObject()).toJson(
                      QJsonDocument::Compact))
                : QString();
            dbInsert(index, device, parsed, gpxText, fitBase64, ruleOutputsJson);
        }

        setLoading(false);
        // The fetch succeeded, so ok is true even if the list is empty: an empty result is
        // "no recorded activities on the watch", a valid state the page renders as such - NOT
        // "couldn't load". Real, 2026-08-16: a reset/empty watch (a freshly-flashed Kailash,
        // whose ExerciseLog region is absent) came back with 0 activities, and because
        // dbLoadAll() returns false for an empty database, ok flipped to false and the page
        // showed the error banner. Load the (possibly empty) rows, but don't let that decide ok.
        dbLoadAll();
        m_ok = true;
        m_showingCachedData = false;
        emit activitiesChanged();

        // Auto-export new watch moves to intervals.icu when the scope opts in (suunto/all).
        // exportToIntervals() only uploads rows not already marked exported, so this is a
        // no-op when nothing is new. eTrex auto-export is handled on GarminService's own sync.
        const QString scope = intervalsExportScope();
        if (scope == QStringLiteral("suunto") || scope == QStringLiteral("all"))
            exportToIntervals();
    });
}

void ActivityService::openDatabase()
{
    // A named connection (not the default one) - QML_SINGLETON means exactly one instance
    // of this class ever exists, but naming it anyway avoids the classic Qt trap where a
    // second addDatabase() call with the default connection name silently steals the first.
    m_db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), QStringLiteral("activities"));
    const QString dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(dir);
    m_db.setDatabaseName(dir + QStringLiteral("/activities.db"));
    if (!m_db.open()) {
        setLastError(m_db.lastError().text());
        return;
    }
    QSqlQuery q(m_db);
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS activities ("
        "idx INTEGER PRIMARY KEY, name TEXT, duration_s INTEGER, distance_m REAL, "
        "ascent_m REAL, energy_kcal INTEGER, sport_type_raw INTEGER, start_time TEXT, "
        "track_json TEXT, gpx_text TEXT, fit_base64 TEXT)"));
    // Migration for caches created before logged-Suunto-App outputs (ruleoutput1..5) existed:
    // add the column if absent. ALTER fails harmlessly with "duplicate column name" on an
    // already-migrated DB, so the ignored error is by design (same no-op-on-exists idiom).
    q.exec(QStringLiteral("ALTER TABLE activities ADD COLUMN rule_outputs_json TEXT"));
    // Blended imports (André, 2026-08-18): where a row came from. NULL/'watch' = read off the
    // watch (the default and everything that already exists); 'intervals' = pulled from
    // intervals.icu. external_id is the remote id, used to de-dup on re-import. Both ALTERs are
    // no-ops (ignored "duplicate column name") on an already-migrated DB.
    q.exec(QStringLiteral("ALTER TABLE activities ADD COLUMN source TEXT"));
    q.exec(QStringLiteral("ALTER TABLE activities ADD COLUMN external_id TEXT"));
    // The device/app a move was recorded on (André, 2026-08-24) - meaningful for imports
    // (Garmin, Zwift, a phone app…); watch moves leave it empty (the watch is implied).
    q.exec(QStringLiteral("ALTER TABLE activities ADD COLUMN device TEXT"));
    // Whether a watch activity has been uploaded to intervals.icu (export), so we don't
    // re-upload it every sync. 1 = uploaded. Imports never set this.
    q.exec(QStringLiteral("ALTER TABLE activities ADD COLUMN exported INTEGER"));
    // Multi-watch keying (André, 2026-08-26: "can we interchange watches?"). The table was
    // keyed by idx alone, so watch B's index-0 REPLACE'd watch A's index-0. Re-key to
    // (idx, device) so several watches' histories coexist. Detect the old idx-only PK via
    // pragma and rebuild once. Watch rows that predate the device tag (device NULL/empty) are
    // dropped here and re-read from the watch on the next sync; imports (device set, negative
    // idx) copy across unchanged.
    {
        bool tableExists = false, deviceInPk = false;
        QSqlQuery info(m_db);
        info.exec(QStringLiteral("PRAGMA table_info(activities)"));
        while (info.next()) {
            tableExists = true;
            if (info.value(1).toString() == QStringLiteral("device")
                && info.value(5).toInt() > 0)
                deviceInPk = true;
        }
        if (tableExists && !deviceInPk) {
            q.exec(QStringLiteral("BEGIN TRANSACTION"));
            q.exec(QStringLiteral(
                "CREATE TABLE activities_rekey ("
                "idx INTEGER, name TEXT, duration_s INTEGER, distance_m REAL, ascent_m REAL, "
                "energy_kcal INTEGER, sport_type_raw INTEGER, start_time TEXT, track_json TEXT, "
                "gpx_text TEXT, fit_base64 TEXT, rule_outputs_json TEXT, source TEXT, "
                "external_id TEXT, device TEXT, exported INTEGER, PRIMARY KEY(idx, device))"));
            q.exec(QStringLiteral(
                "INSERT INTO activities_rekey SELECT idx, name, duration_s, distance_m, "
                "ascent_m, energy_kcal, sport_type_raw, start_time, track_json, gpx_text, "
                "fit_base64, rule_outputs_json, source, external_id, device, exported "
                "FROM activities WHERE device IS NOT NULL AND device != ''"));
            q.exec(QStringLiteral("DROP TABLE activities"));
            q.exec(QStringLiteral("ALTER TABLE activities_rekey RENAME TO activities"));
            q.exec(QStringLiteral("COMMIT"));
        }
    }
    // Deleted-activity tombstones (André, 2026-08-25). One row per deleted activity, keyed by
    // start-time|name (the same identity dedupeActivities() collapses on), so a deleted move
    // stays gone across every future watch re-sync and intervals/Garmin re-import - the watch's
    // circular log has no delete of its own, so this is the only durable way to keep it gone.
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS deleted_activities (key TEXT PRIMARY KEY)"));

    // One-time migration to serial-based watch identity (2026-09-04): watch activities used to
    // be tagged by product-id hex ("0x1b"); they are now tagged by the watch's USB serial so two
    // same-model watches stay distinct. Old product-id-tagged watch rows would otherwise show as
    // duplicates of the freshly re-read serial-tagged ones (different device -> both survive the
    // de-dupe). Clear those old rows ONCE; they re-read from the watch on the next sync. Imports
    // (device 'intervals'/'garmin'/…) and already-serial rows are untouched. Matched by the
    // product-id-hex shape: "0x" + up to four hex digits.
    if (!QSettings().value(QStringLiteral("activities/serialTagMigrationDone"), false).toBool()) {
        q.exec(QStringLiteral(
            "DELETE FROM activities WHERE device GLOB '0x[0-9a-fA-F]*' "
            "AND length(device) <= 6"));
        QSettings().setValue(QStringLiteral("activities/serialTagMigrationDone"), true);
    }

    loadTombstones();
}

// Stable identity for a deleted activity - the same start-time|name key dedupeActivities()
// uses to collapse the same move arriving from more than one source, so tombstoning it hides
// every copy (watch, intervals, Garmin) at once.
QString ActivityService::tombstoneKey(const QString &startTime, const QString &name)
{
    return startTime + QLatin1Char('|') + name;
}

void ActivityService::loadTombstones()
{
    m_tombstones.clear();
    if (!m_db.isOpen())
        return;
    QSqlQuery q(QStringLiteral("SELECT key FROM deleted_activities"), m_db);
    while (q.next())
        m_tombstones.insert(q.value(0).toString());
}

int ActivityService::dbKnownCount(const QString &device)
{
    if (!m_db.isOpen())
        return 0;
    // Only watch rows drive "how many the app already has" - imported rows use negative idx
    // and must not inflate this (they'd make the watch skip reading real activities). Scoped
    // to the given device so each watch's known_count is its own (multi-watch keying).
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "SELECT MAX(idx) FROM activities WHERE (source IS NULL OR source = 'watch') "
        "AND device = ?"));
    q.addBindValue(device);
    q.exec();
    if (q.next())
        return q.value(0).toInt();  // NULL (empty table) -> QVariant().toInt() == 0
    return 0;
}

void ActivityService::dbClear(const QString &device)
{
    if (!m_db.isOpen())
        return;
    // Only THIS watch's own rows - imported intervals.icu activities and other watches' moves
    // must survive a log-wrap re-read (this runs when the selected watch's log reset since our
    // cache).
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "DELETE FROM activities WHERE (source IS NULL OR source = 'watch') AND device = ?"));
    q.addBindValue(device);
    q.exec();
}

void ActivityService::dbInsert(int index, const QString &device, const QVariantMap &parsed,
                                const QString &gpxText, const QString &fitBase64,
                                const QString &ruleOutputsJson)
{
    if (!m_db.isOpen())
        return;
    const QJsonDocument trackDoc(QJsonArray::fromVariantList(parsed.value(
        QStringLiteral("track")).toList()));

    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT OR REPLACE INTO activities "
        "(idx, device, name, duration_s, distance_m, ascent_m, energy_kcal, sport_type_raw, "
        " start_time, track_json, gpx_text, fit_base64, rule_outputs_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"));
    q.addBindValue(index);
    q.addBindValue(device);
    q.addBindValue(parsed.value(QStringLiteral("name")));
    q.addBindValue(parsed.value(QStringLiteral("durationSeconds")));
    q.addBindValue(parsed.value(QStringLiteral("distanceMeters")));
    q.addBindValue(parsed.value(QStringLiteral("ascentMeters")));
    q.addBindValue(parsed.value(QStringLiteral("energyKcal")));
    q.addBindValue(parsed.value(QStringLiteral("sportTypeRaw")));
    q.addBindValue(parsed.value(QStringLiteral("startTime")));
    q.addBindValue(QString::fromUtf8(trackDoc.toJson(QJsonDocument::Compact)));
    q.addBindValue(gpxText);
    q.addBindValue(fitBase64);
    q.addBindValue(ruleOutputsJson.isEmpty() ? QVariant() : ruleOutputsJson);
    q.exec();
}

bool ActivityService::dbLoadAll()
{
    m_activities.clear();
    if (!m_db.isOpen())
        return false;

    QSqlQuery q(QStringLiteral(
        "SELECT idx, name, duration_s, distance_m, ascent_m, energy_kcal, sport_type_raw, "
        "start_time, track_json, gpx_text, fit_base64, rule_outputs_json, source, device "
        // By date (newest first) rather than log index, so imported intervals.icu moves blend
        // in chronologically with watch moves instead of clumping by idx. Real watch rows all
        // carry an ISO start_time (sortable as text); the idx tiebreak keeps a stable order.
        "FROM activities ORDER BY start_time DESC, idx DESC"),
        m_db);
    while (q.next()) {
        // Skip anything the user has deleted (tombstoned) - however it got back into the table
        // (a watch re-sync re-inserts every move; an import re-adds its rows), a deleted move
        // never re-appears in the list. Keyed by start-time|name, matching dedupeActivities().
        if (!m_tombstones.isEmpty()
                && m_tombstones.contains(tombstoneKey(q.value(7).toString(), q.value(1).toString())))
            continue;
        QVariantMap parsed;
        parsed[QStringLiteral("index")] = q.value(0).toInt();
        parsed[QStringLiteral("name")] = q.value(1).toString();
        parsed[QStringLiteral("durationSeconds")] = q.value(2).toInt();
        parsed[QStringLiteral("distanceMeters")] = q.value(3).toDouble();
        parsed[QStringLiteral("ascentMeters")] = q.value(4).toDouble();
        parsed[QStringLiteral("energyKcal")] = q.value(5).toInt();
        parsed[QStringLiteral("sportTypeRaw")] = q.value(6).toInt();
        parsed[QStringLiteral("startTime")] = q.value(7).toString();
        const auto trackDoc = QJsonDocument::fromJson(q.value(8).toString().toUtf8());
        parsed[QStringLiteral("track")] = trackDoc.array().toVariantList();
        parsed[QStringLiteral("gpxText")] = q.value(9).toString();
        parsed[QStringLiteral("fitBase64")] = q.value(10).toString();
        // "watch" (or NULL for pre-migration rows) vs "intervals" - QML shows a small marker on
        // imported ones so you can tell them apart while they blend into the same lists.
        parsed[QStringLiteral("source")] = q.value(12).toString().isEmpty()
            ? QStringLiteral("watch") : q.value(12).toString();
        parsed[QStringLiteral("device")] = q.value(13).toString();
        // Logged Suunto App outputs (ruleoutput1..5) - {slot: {label, times, values}} - handed
        // to QML as `ruleOutputs` for the per-app graph. Absent on moves recorded without app
        // logging (older caches too, where the column is NULL): left unset.
        const QString ruleOutputsJson = q.value(11).toString();
        if (!ruleOutputsJson.isEmpty()) {
            const auto doc = QJsonDocument::fromJson(ruleOutputsJson.toUtf8());
            if (doc.isObject())
                parsed[QStringLiteral("ruleOutputs")] = doc.object().toVariantMap();
        }
        // The richer metrics (HR/cadence/speed/pace/descent/…) aren't stored as their own DB
        // columns - re-parse the cached GPX text for them so the configurable Activities
        // columns work offline too, without a schema migration. Cheap: a local in-memory
        // string parse, and the extras just merge onto the fast DB core fields above.
        const QString gpx = q.value(9).toString();
        if (!gpx.isEmpty()) {
            const QVariantMap extra = parseGpx(gpx);
            for (const QString &key : {
                     QStringLiteral("avgHr"), QStringLiteral("maxHr"),
                     QStringLiteral("avgCadence"), QStringLiteral("maxCadence"),
                     QStringLiteral("avgSpeedMh"), QStringLiteral("maxSpeedMh"),
                     QStringLiteral("descentMeters"), QStringLiteral("recoverySeconds"),
                     QStringLiteral("peakTrainingEffect"), QStringLiteral("poolLengths"),
                     QStringLiteral("maxAltitudeMeters"), QStringLiteral("paceSecPerKm"),
                     QStringLiteral("hrvRmssd"), QStringLiteral("hrvResting"),
                     QStringLiteral("hrvOrthoDrop") }) {
                parsed[key] = extra.value(key);
            }
        }
        m_activities.append(parsed);
    }
    dedupeActivities();
    updateWatchHrvStore();
    m_showingCachedData = !m_activities.isEmpty();
    return !m_activities.isEmpty();
}

void ActivityService::updateWatchHrvStore()
{
    // m_activities is ordered newest-first, so the first row we see for a given calendar date
    // is that day's latest measurement - which we keep if a day happens to hold more than one
    // resting test. Only resting tests (hrvResting == 1) with a real rMSSD qualify; workout
    // R-R never reaches the Health series.
    QJsonArray out;
    QSet<QString> seenDates;
    for (const QVariant &v : std::as_const(m_activities)) {
        const QVariantMap a = v.toMap();
        if (a.value(QStringLiteral("hrvResting")).toInt() != 1)
            continue;
        const double rmssd = a.value(QStringLiteral("hrvRmssd")).toDouble();
        if (rmssd <= 0)
            continue;
        // start_time is ISO (e.g. 2026-08-24T07:00:00Z); the date is its first 10 chars.
        const QString date = a.value(QStringLiteral("startTime")).toString().left(10);
        if (date.size() != 10 || seenDates.contains(date))
            continue;
        seenDates.insert(date);
        out.append(QJsonObject{{QStringLiteral("date"), date},
                               {QStringLiteral("value"), rmssd}});
    }
    QSettings().setValue(QStringLiteral("health/watchHrv"),
                         QString::fromUtf8(QJsonDocument(out).toJson(QJsonDocument::Compact)));
}

void ActivityService::dedupeActivities()
{
    // Cross-source de-duplication (André, 2026-08-24): the same real move can arrive from
    // several sources - a Garmin Edge ride shows up both in a direct Garmin import AND via the
    // intervals.icu aggregator; a Karoo ride reaches Suunto and onward. Collapse rows that share
    // a start minute, keeping the highest-priority source. Priority favours DIRECT sources over
    // the intervals aggregator, so turning intervals off (the stated goal) loses nothing that a
    // direct source already provides. Watch-native moves (empty source) always win.
    //   watch(empty) > garmin > suunto > eltrex/garmin-usb > intervals > other
    // Device-aware (André, 2026-09-04: "two Ambit3 plugged, same sport, only one loads... some
    // filter for identical/duplicates?"). Two DIFFERENT watches recording the same minute (a
    // Peak and a Sport on the same ride) are two real, separate recordings - keep BOTH. What we
    // still collapse is the SAME move arriving from more than one source: a watch move plus its
    // Garmin/intervals copy, or two aggregator copies. So: a start-minute that has any watch move
    // keeps every distinct watch device and drops the non-watch copies; a start-minute with no
    // watch move keeps the single highest-priority source.
    //   watch(empty) > garmin > suunto > etrex/garmin-usb > intervals > other
    auto priority = [](const QString &src) -> int {
        if (src.isEmpty() || src == QStringLiteral("watch")) return 100;
        if (src == QStringLiteral("garmin")) return 80;
        if (src == QStringLiteral("suunto")) return 70;
        if (src == QStringLiteral("etrex")) return 60;
        if (src == QStringLiteral("intervals")) return 20;
        return 10;
    };
    auto minuteKey = [](const QVariantMap &a) -> QString {
        const QString s = a.value(QStringLiteral("startTime")).toString();
        return s.size() >= 16 ? s.left(16) : s;   // "YYYY-MM-DDTHH:MM"
    };
    auto isWatch = [](const QVariantMap &a) -> bool {
        const QString src = a.value(QStringLiteral("source")).toString();
        return src.isEmpty() || src == QStringLiteral("watch");
    };

    // Pass 1: which start-minutes have at least one watch-native move.
    QSet<QString> minutesWithWatch;
    for (const QVariant &v : std::as_const(m_activities)) {
        const QVariantMap a = v.toMap();
        const QString key = minuteKey(a);
        if (!key.isEmpty() && isWatch(a))
            minutesWithWatch.insert(key);
    }

    // Pass 2: keep, in the existing (date-sorted) order.
    QSet<QString> keptWatchDevPerMinute;   // "minute|device" already kept
    QHash<QString, int> bestNonWatchIdx;   // minute -> index in kept (only when no watch move)
    QVariantList kept;
    for (const QVariant &v : std::as_const(m_activities)) {
        const QVariantMap a = v.toMap();
        const QString key = minuteKey(a);
        if (key.isEmpty()) {               // no start time -> can't match, always keep
            kept.append(v);
            continue;
        }
        if (isWatch(a)) {
            // One row per (minute, device): different watches both kept; a genuine re-read of
            // the same watch's same move collapses.
            const QString devKey = key + QLatin1Char('|')
                                 + a.value(QStringLiteral("device")).toString();
            if (keptWatchDevPerMinute.contains(devKey))
                continue;
            keptWatchDevPerMinute.insert(devKey);
            kept.append(v);
        } else {
            if (minutesWithWatch.contains(key))
                continue;                  // a watch move owns this minute -> drop the copy
            const int prio = priority(a.value(QStringLiteral("source")).toString());
            if (!bestNonWatchIdx.contains(key)) {
                bestNonWatchIdx.insert(key, kept.size());
                kept.append(v);
            } else {
                const int idx = bestNonWatchIdx.value(key);
                if (prio > priority(kept.at(idx).toMap()
                                    .value(QStringLiteral("source")).toString()))
                    kept[idx] = v;
            }
        }
    }
    m_activities = kept;
}

void ActivityService::deleteActivity(const QVariantMap &activity)
{
    if (!m_db.isOpen())
        openDatabase();

    const int idx = activity.value(QStringLiteral("index"), -1).toInt();

    // Resolve the row's real identity from the DB - the QML activity map carries `index` but not
    // external_id/source, and idx is the primary key, so this reads the one exact row.
    QString source, extId, start, name;
    {
        QSqlQuery sel(m_db);
        sel.prepare(QStringLiteral(
            "SELECT source, external_id, start_time, name FROM activities WHERE idx = ?"));
        sel.addBindValue(idx);
        if (sel.exec() && sel.next()) {
            source = sel.value(0).toString();
            extId = sel.value(1).toString();
            start = sel.value(2).toString();
            name = sel.value(3).toString();
        }
    }
    // Fall back to the map's own fields if the row isn't in the DB (e.g. the shown copy was a
    // deduped winner from a source whose row is gone) - the tombstone still needs a real key.
    if (start.isEmpty())
        start = activity.value(QStringLiteral("startTime")).toString();
    if (name.isEmpty())
        name = activity.value(QStringLiteral("name")).toString();
    if (source.isEmpty())
        source = activity.value(QStringLiteral("source")).toString();

    // 1) Tombstone it, so it stays gone across every future watch re-sync / import.
    const QString key = tombstoneKey(start, name);
    m_tombstones.insert(key);
    QSqlQuery ins(m_db);
    ins.prepare(QStringLiteral("INSERT OR IGNORE INTO deleted_activities (key) VALUES (?)"));
    ins.addBindValue(key);
    ins.exec();

    // 2) Remove the local row(s) - by idx AND by the shared key, so a deduped duplicate from
    //    another source goes too (deleting the whole activity, not just the copy on screen).
    QSqlQuery del(m_db);
    del.prepare(QStringLiteral(
        "DELETE FROM activities WHERE idx = ? OR (start_time = ? AND name = ?)"));
    del.addBindValue(idx);
    del.addBindValue(start);
    del.addBindValue(name);
    del.exec();

    // 3) When it came from intervals.icu, delete it there too - permanent, per André's choice.
    //    /api/v1/activity/{id} is intervals' single-activity endpoint (same Basic API_KEY auth
    //    as every other intervals call here); a 404 means it is already gone, which is fine.
    if (source == QStringLiteral("intervals") && !extId.isEmpty()) {
        QSettings settings;
        const QString apiKey =
            settings.value(QStringLiteral("connections/intervals_icu/apiKey")).toString();
        if (!apiKey.isEmpty()) {
            QNetworkRequest req(QUrl(
                QStringLiteral("https://intervals.icu/api/v1/activity/%1").arg(extId)));
            const QByteArray basic = QByteArrayLiteral("API_KEY:") + apiKey.toUtf8();
            req.setRawHeader("Authorization", "Basic " + basic.toBase64());
            QNetworkReply *reply = m_network.deleteResource(req);
            connect(reply, &QNetworkReply::finished, this, [this, reply]() {
                const int status =
                    reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
                if (reply->error() != QNetworkReply::NoError
                        && status != 200 && status != 204 && status != 404)
                    setLastError(tr("Removed here, but deleting it on intervals.icu failed: %1")
                                 .arg(reply->errorString()));
                reply->deleteLater();
            });
        }
    }

    // 4) Drop it from the in-memory list now (no full reload needed) and tell the UI.
    for (int i = m_activities.size() - 1; i >= 0; --i) {
        const QVariantMap a = m_activities.at(i).toMap();
        if (tombstoneKey(a.value(QStringLiteral("startTime")).toString(),
                         a.value(QStringLiteral("name")).toString()) == key)
            m_activities.removeAt(i);
    }
    emit activityDeleted(name);
    emit activitiesChanged();
}

void ActivityService::backfillIntervalsTracks(int maxCount)
{
    if (m_trackBusy) return;               // a run is already walking the queue
    if (!m_db.isOpen()) openDatabase();
    if (!m_db.isOpen()) return;

    // Only rows that genuinely need it: an intervals import, a real distance (so it plausibly
    // has positions - indoor trainer rides have none), and no track stored yet. Newest first,
    // because that is what the user is actually looking at.
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "SELECT external_id FROM activities "
        "WHERE source = 'intervals' AND COALESCE(external_id,'') <> '' "
        "  AND COALESCE(distance_m,0) > 0 "
        "  AND COALESCE(track_json,'') IN ('', '[]') "
        "ORDER BY start_time DESC LIMIT ?"));
    q.addBindValue(maxCount > 0 ? maxCount : 200);
    if (!q.exec()) return;

    m_trackQueue.clear();
    while (q.next())
        m_trackQueue.append(q.value(0).toString());
    if (m_trackQueue.isEmpty()) {
        emit trackBackfillFinished(0, 0);
        return;
    }
    m_trackTotal = m_trackQueue.size();
    m_trackDone = 0;
    m_trackFilled = 0;
    m_trackBusy = true;
    fetchNextTrack();
}

void ActivityService::fetchNextTrack()
{
    if (m_trackQueue.isEmpty()) {
        m_trackBusy = false;
        // How many still need a track after this run, so the caller can decide to continue.
        int remaining = 0;
        if (m_db.isOpen()) {
            QSqlQuery c(QStringLiteral(
                "SELECT COUNT(*) FROM activities WHERE source = 'intervals' "
                "AND COALESCE(external_id,'') <> '' AND COALESCE(distance_m,0) > 0 "
                "AND COALESCE(track_json,'') IN ('', '[]')"), m_db);
            if (c.next()) remaining = c.value(0).toInt();
        }
        if (m_trackFilled > 0) {
            dbLoadAll();                    // republish activities[] with the new tracks
            emit activitiesChanged();
        }
        emit trackBackfillFinished(m_trackFilled, remaining);
        return;
    }

    const QString id = m_trackQueue.takeFirst();
    QSettings settings;
    const QString key =
        settings.value(QStringLiteral("connections/intervals_icu/apiKey")).toString();
    if (key.isEmpty()) { m_trackQueue.clear(); fetchNextTrack(); return; }

    // types=latlng,altitude - the only two streams a map trace needs. Verified against the real
    // API (2026-08-25): the response is an ARRAY of stream objects, and for "latlng" the
    // positions are split across TWO parallel arrays - `data` holds the latitudes and `data2`
    // the longitudes (NOT [lat,lon] pairs, which is the obvious wrong assumption).
    QNetworkRequest req(QUrl(QStringLiteral(
        "https://intervals.icu/api/v1/activity/%1/streams?types=latlng,altitude").arg(id)));
    const QByteArray basic = QByteArrayLiteral("API_KEY:") + key.toUtf8();
    req.setRawHeader("Authorization", "Basic " + basic.toBase64());
    // Same Cloudflare workaround as the other intervals calls: an empty UA is 1010-banned.
    req.setRawHeader("User-Agent",
                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Sommet/1.0");

    QNetworkReply *reply = m_network.get(req);
    connect(reply, &QNetworkReply::finished, this, [this, reply, id]() {
        reply->deleteLater();
        ++m_trackDone;

        if (reply->error() == QNetworkReply::NoError) {
            const QJsonArray streams = QJsonDocument::fromJson(reply->readAll()).array();
            QJsonArray lat, lon, alt;
            for (const QJsonValue &v : streams) {
                const QJsonObject s = v.toObject();
                const QString type = s.value(QStringLiteral("type")).toString();
                if (type == QLatin1String("latlng")) {
                    lat = s.value(QStringLiteral("data")).toArray();
                    lon = s.value(QStringLiteral("data2")).toArray();
                } else if (type == QLatin1String("altitude")) {
                    alt = s.value(QStringLiteral("data")).toArray();
                }
            }
            // Build the same {lat, lon, ele} shape parseGpx() produces, so every existing
            // consumer (MapView, ActivityCard's preview, the detail map) works unchanged.
            QJsonArray track;
            const int n = qMin(lat.size(), lon.size());
            for (int i = 0; i < n; ++i) {
                if (lat.at(i).isNull() || lon.at(i).isNull()) continue;   // real gaps in a trace
                QJsonObject p{{QStringLiteral("lat"), lat.at(i).toDouble()},
                              {QStringLiteral("lon"), lon.at(i).toDouble()}};
                if (i < alt.size() && !alt.at(i).isNull())
                    p.insert(QStringLiteral("ele"), alt.at(i).toDouble());
                track.append(p);
            }
            if (!track.isEmpty()) {
                QSqlQuery up(m_db);
                up.prepare(QStringLiteral(
                    "UPDATE activities SET track_json = ? WHERE external_id = ?"));
                up.addBindValue(QString::fromUtf8(
                    QJsonDocument(track).toJson(QJsonDocument::Compact)));
                up.addBindValue(id);
                if (up.exec()) ++m_trackFilled;
            }
        }
        // Deliberately continues past a failed activity rather than aborting the whole run -
        // one bad/streamless activity should not stop the rest of the backfill.
        emit trackBackfillProgress(m_trackDone, m_trackTotal);
        fetchNextTrack();
    });
}

void ActivityService::importFromIntervals(int oldestDays)
{
    const QSettings settings;
    const QString athlete =
        settings.value(QStringLiteral("connections/intervals_icu/athleteId")).toString();
    const QString key =
        settings.value(QStringLiteral("connections/intervals_icu/apiKey")).toString();
    if (athlete.isEmpty() || key.isEmpty()) {
        emit importError(tr("Connect Intervals.icu in Settings first."));
        return;
    }

    QUrl url(QStringLiteral("https://intervals.icu/api/v1/athlete/%1/activities").arg(athlete));
    // intervals.icu requires BOTH oldest and newest (a date range) or it 422s. "Everything"
    // (oldestDays<=0) uses a far-past oldest; otherwise the last N days. newest is today.
    QUrlQuery query;
    const QDate today = QDate::currentDate();
    const QDate oldest = oldestDays > 0 ? today.addDays(-oldestDays) : QDate(2005, 1, 1);
    query.addQueryItem(QStringLiteral("oldest"), oldest.toString(Qt::ISODate));
    query.addQueryItem(QStringLiteral("newest"), today.toString(Qt::ISODate));
    url.setQuery(query);
    QNetworkRequest req(url);
    const QByteArray basic = QByteArrayLiteral("API_KEY:") + key.toUtf8();
    req.setRawHeader("Authorization", "Basic " + basic.toBase64());
    // Same Cloudflare workaround as GearService: the empty default UA is 1010-banned.
    req.setRawHeader("User-Agent",
                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Sommet/1.0");

    setLoading(true);
    QNetworkReply *reply = m_network.get(req);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        setLoading(false);
        if (reply->error() != QNetworkReply::NoError) {
            emit importError(reply->errorString());
            return;
        }
        const QJsonDocument doc = QJsonDocument::fromJson(reply->readAll());
        if (!doc.isArray()) {
            emit importError(tr("Unexpected response from Intervals.icu."));
            return;
        }
        importActivitiesInto(doc.array());
    });
}

// intervals.icu (Strava-style) activity "type" -> this app's canonical ActivityTypes name, so
// an imported move gets the right sport icon and reads consistently with watch moves (which put
// the sport in the name too). Unknown types fall back to the raw type, then "Unspecified sport"
// resolves a generic badge. André, 2026-08-24: "assign imported activities with the good icons".
// A readable name for the intervals.icu upload source connector, when there's no device_name.
static QString friendlySource(const QString &source)
{
    static const QHash<QString, QString> map = {
        {QStringLiteral("GARMIN_CONNECT"), QStringLiteral("Garmin")},
        {QStringLiteral("STRAVA"), QStringLiteral("Strava")},
        {QStringLiteral("ZWIFT"), QStringLiteral("Zwift")},
        {QStringLiteral("WAHOO"), QStringLiteral("Wahoo")},
        {QStringLiteral("POLAR"), QStringLiteral("Polar")},
        {QStringLiteral("SUUNTO"), QStringLiteral("Suunto")},
        {QStringLiteral("COROS"), QStringLiteral("COROS")},
        {QStringLiteral("UPLOAD"), QStringLiteral("Manual upload")},
        {QStringLiteral("MANUAL"), QStringLiteral("Manual entry")},
    };
    const QString mapped = map.value(source);
    if (!mapped.isEmpty())
        return mapped;
    return source;  // unknown connector: show it verbatim rather than nothing
}

static QString sportNameForIntervalsType(const QString &type)
{
    static const QHash<QString, QString> map = {
        {QStringLiteral("Run"), QStringLiteral("Running")},
        {QStringLiteral("TrailRun"), QStringLiteral("Trail running")},
        {QStringLiteral("VirtualRun"), QStringLiteral("Treadmill")},
        {QStringLiteral("Ride"), QStringLiteral("Cycling")},
        {QStringLiteral("VirtualRide"), QStringLiteral("Indoor cycling")},
        {QStringLiteral("GravelRide"), QStringLiteral("Cycling")},
        {QStringLiteral("EBikeRide"), QStringLiteral("Cycling")},
        {QStringLiteral("MountainBikeRide"), QStringLiteral("Mountain biking")},
        {QStringLiteral("Walk"), QStringLiteral("Walking")},
        {QStringLiteral("Hike"), QStringLiteral("Hiking")},
        {QStringLiteral("Swim"), QStringLiteral("Pool swimming")},
        {QStringLiteral("OpenWaterSwim"), QStringLiteral("Openwater swimming")},
        {QStringLiteral("Rowing"), QStringLiteral("Indoor rowing")},
        {QStringLiteral("Kayaking"), QStringLiteral("Kayaking")},
        {QStringLiteral("StandUpPaddling"), QStringLiteral("Standup paddling")},
        {QStringLiteral("WeightTraining"), QStringLiteral("Weight training")},
        {QStringLiteral("Workout"), QStringLiteral("Indoor training")},
        {QStringLiteral("Elliptical"), QStringLiteral("Crosstrainer")},
        {QStringLiteral("Yoga"), QStringLiteral("Yoga / pilates")},
        {QStringLiteral("NordicSki"), QStringLiteral("Cross-country skiing")},
        {QStringLiteral("BackcountrySki"), QStringLiteral("Ski touring")},
        {QStringLiteral("AlpineSki"), QStringLiteral("Alpine skiing")},
        {QStringLiteral("Snowboard"), QStringLiteral("Snowboarding")},
        {QStringLiteral("Snowshoe"), QStringLiteral("Snow shoeing")},
        {QStringLiteral("Golf"), QStringLiteral("Golf")},
        {QStringLiteral("Tennis"), QStringLiteral("Tennis")},
        {QStringLiteral("Soccer"), QStringLiteral("Soccer / football")},
        {QStringLiteral("Climbing"), QStringLiteral("Climbing")},
        {QStringLiteral("RockClimbing"), QStringLiteral("Climbing")},
        {QStringLiteral("Rowing"), QStringLiteral("Indoor rowing")},
        {QStringLiteral("Canoeing"), QStringLiteral("Canoeing")},
        {QStringLiteral("Badminton"), QStringLiteral("Badminton")},
        {QStringLiteral("Skateboard"), QStringLiteral("Unspecified sport")},
        {QStringLiteral("Surfing"), QStringLiteral("Surfing")},
        {QStringLiteral("Windsurf"), QStringLiteral("Windsurfing")},
        {QStringLiteral("Kitesurf"), QStringLiteral("Kitesurfing / kiting")},
        {QStringLiteral("Sail"), QStringLiteral("Sailing")},
    };
    const QString mapped = map.value(type);
    if (!mapped.isEmpty())
        return mapped;
    return type.isEmpty() ? QStringLiteral("Unspecified sport") : type;
}

void ActivityService::importActivitiesInto(const QJsonArray &arr)
{
    if (!m_db.isOpen()) {
        emit importError(tr("Local activity store isn't open."));
        return;
    }
    // Pull-only refresh: replace the previous intervals.icu snapshot wholesale (de-dup by
    // simply clearing it first), then re-insert. Watch rows are never touched.
    // One transaction around the whole refresh - without it each of the (often thousands of)
    // inserts is its own fsync, which took tens of seconds and held the DB lock the whole time.
    m_db.transaction();
    QSqlQuery del(m_db);
    del.exec(QStringLiteral("DELETE FROM activities WHERE source = 'intervals'"));

    int idx = -1;  // imported rows use negative idx so they never collide with watch log indexes
    int count = 0;
    for (const QJsonValue &v : arr) {
        const QJsonObject o = v.toObject();
        const QString extId = o.value(QStringLiteral("id")).toVariant().toString();
        // Store the mapped SPORT as the name so the badge shows the right icon and it reads
        // like a watch move. (The intervals.icu free-text title isn't kept - the sport is what
        // every other activity in the app shows.)
        const QString type = o.value(QStringLiteral("type")).toString();
        const QString name = sportNameForIntervalsType(type);
        const QString start = o.value(QStringLiteral("start_date_local")).toString();
        int duration = o.value(QStringLiteral("moving_time")).toInt();
        if (duration == 0)
            duration = o.value(QStringLiteral("elapsed_time")).toInt();
        const double distance = o.value(QStringLiteral("distance")).toDouble();
        const double ascent = o.value(QStringLiteral("total_elevation_gain")).toDouble();
        const int calories = o.value(QStringLiteral("calories")).toInt();
        // Skip junk/test entries - under a minute AND under 100 m (planned/manual/test uploads
        // with no real recorded activity, André 2026-08-24: "it was tests for our app"). An
        // activity needs at least ~1 min OR ~100 m to count; these live on intervals.icu so the
        // filter has to run on every import, not just a one-off local delete.
        if (duration < 60 && distance < 100.0)
            continue;
        // Prefer the real device name; fall back to a friendly form of the source connector.
        QString device = o.value(QStringLiteral("device_name")).toString();
        if (device.isEmpty())
            device = friendlySource(o.value(QStringLiteral("source")).toString());

        QSqlQuery ins(m_db);
        ins.prepare(QStringLiteral(
            "INSERT INTO activities "
            "(idx, name, duration_s, distance_m, ascent_m, energy_kcal, sport_type_raw, "
            " start_time, source, external_id, device) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'intervals', ?, ?)"));
        ins.addBindValue(idx--);
        ins.addBindValue(name);
        ins.addBindValue(duration);
        ins.addBindValue(distance);
        ins.addBindValue(ascent);
        ins.addBindValue(calories);
        ins.addBindValue(start);
        ins.addBindValue(extId);
        ins.addBindValue(device);
        ins.exec();
        ++count;
    }
    m_db.commit();
    dbLoadAll();
    emit activitiesChanged();
    emit importFinished(count);
    // Pull the GPS traces for what was just imported (André, 2026-08-25 - imported outdoor
    // moves used to show "No GPS track" because the list endpoint carries no positions). Bounded
    // per run so this stays a background trickle rather than thousands of calls at once; the
    // remaining count comes back on trackBackfillFinished for a caller that wants to continue.
    backfillIntervalsTracks(150);
}

// Garmin activity typeKey -> this app's canonical sport name (same idea as
// sportNameForIntervalsType, but Garmin's keys). Falls back to a readable form of the key.
static QString sportNameForGarminType(const QString &typeKey)
{
    static const QHash<QString, QString> map = {
        {QStringLiteral("running"), QStringLiteral("Running")},
        {QStringLiteral("trail_running"), QStringLiteral("Trail running")},
        {QStringLiteral("treadmill_running"), QStringLiteral("Running")},
        {QStringLiteral("cycling"), QStringLiteral("Cycling")},
        {QStringLiteral("road_biking"), QStringLiteral("Cycling")},
        {QStringLiteral("mountain_biking"), QStringLiteral("Mountain biking")},
        {QStringLiteral("indoor_cycling"), QStringLiteral("Indoor cycling")},
        {QStringLiteral("walking"), QStringLiteral("Walking")},
        {QStringLiteral("hiking"), QStringLiteral("Trekking")},
        {QStringLiteral("lap_swimming"), QStringLiteral("Pool swimming")},
        {QStringLiteral("open_water_swimming"), QStringLiteral("Openwater swim")},
        {QStringLiteral("strength_training"), QStringLiteral("Gym training")},
        {QStringLiteral("fitness_equipment"), QStringLiteral("Indoor training")},
        {QStringLiteral("mountaineering"), QStringLiteral("Mountaineering")},
        {QStringLiteral("resort_skiing_snowboarding"), QStringLiteral("Alpine skiing")},
        {QStringLiteral("cross_country_skiing"), QStringLiteral("Cross country skiing")},
    };
    const QString mapped = map.value(typeKey);
    if (!mapped.isEmpty())
        return mapped;
    QString s = typeKey;
    s.replace(QLatin1Char('_'), QLatin1Char(' '));
    return s.isEmpty() ? QStringLiteral("Unspecified sport") : s;
}

void ActivityService::importFromGarmin(int days)
{
    const QUrl url(kBackendBase + QStringLiteral("/api/garmin/activities?days=%1")
                   .arg(days > 0 ? days : 3650));
    setLoading(true);
    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        setLoading(false);
        if (reply->error() != QNetworkReply::NoError) {
            emit importError(reply->errorString());
            return;
        }
        const auto o = QJsonDocument::fromJson(reply->readAll()).object();
        if (!o.value(QStringLiteral("ok")).toBool(false)) {
            emit importError(o.value(QStringLiteral("needLogin")).toBool(false)
                ? tr("Sign in to Garmin on the Weight page first.")
                : o.value(QStringLiteral("error")).toString(tr("Garmin activity import failed.")));
            return;
        }
        importGarminActivitiesInto(o.value(QStringLiteral("activities")).toArray());
    });
}

void ActivityService::importGarminActivitiesInto(const QJsonArray &arr)
{
    if (!m_db.isOpen()) {
        emit importError(tr("Local activity store isn't open."));
        return;
    }
    m_db.transaction();
    QSqlQuery del(m_db);
    del.exec(QStringLiteral("DELETE FROM activities WHERE source = 'garmin'"));

    int idx = -1000000;   // separate negative range from intervals imports, avoid idx collisions
    int count = 0;
    for (const QJsonValue &v : arr) {
        const QJsonObject o = v.toObject();
        const int duration = o.value(QStringLiteral("duration")).toInt();
        const double distance = o.value(QStringLiteral("distance")).toDouble();
        if (duration < 60 && distance < 100.0)   // skip test/junk, same rule as intervals import
            continue;
        QSqlQuery ins(m_db);
        ins.prepare(QStringLiteral(
            "INSERT INTO activities "
            "(idx, name, duration_s, distance_m, ascent_m, energy_kcal, sport_type_raw, "
            " start_time, source, external_id, device) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'garmin', ?, ?)"));
        ins.addBindValue(idx--);
        ins.addBindValue(sportNameForGarminType(o.value(QStringLiteral("typeKey")).toString()));
        ins.addBindValue(duration);
        ins.addBindValue(distance);
        ins.addBindValue(o.value(QStringLiteral("ascent")).toDouble());
        ins.addBindValue(o.value(QStringLiteral("calories")).toInt());
        ins.addBindValue(o.value(QStringLiteral("start")).toString());
        ins.addBindValue(o.value(QStringLiteral("id")).toVariant().toString());
        ins.addBindValue(o.value(QStringLiteral("device")).toString());
        ins.exec();
        ++count;
    }
    m_db.commit();
    dbLoadAll();
    emit activitiesChanged();
    emit importFinished(count);
}

void ActivityService::exportToIntervals()
{
    const QSettings settings;
    const QString athlete =
        settings.value(QStringLiteral("connections/intervals_icu/athleteId")).toString();
    const QString key =
        settings.value(QStringLiteral("connections/intervals_icu/apiKey")).toString();
    if (athlete.isEmpty() || key.isEmpty()) {
        emit exportError(tr("Connect Intervals.icu in Settings first."));
        return;
    }
    if (!m_db.isOpen()) {
        emit exportError(tr("Local activity store isn't open."));
        return;
    }
    // Watch activities with a FIT we haven't already uploaded. Imports (source='intervals')
    // came FROM intervals.icu, so they're never pushed back.
    QList<QPair<int, QByteArray>> items;
    QSqlQuery q(QStringLiteral(
        "SELECT idx, fit_base64 FROM activities "
        "WHERE (source IS NULL OR source = 'watch') AND fit_base64 IS NOT NULL "
        "AND fit_base64 <> '' AND (exported IS NULL OR exported = 0)"), m_db);
    while (q.next()) {
        const QByteArray fit = QByteArray::fromBase64(q.value(1).toString().toLatin1());
        if (!fit.isEmpty())
            items.append({q.value(0).toInt(), fit});
    }
    if (items.isEmpty()) {
        emit exportFinished(0, 0);  // nothing new to push
        return;
    }
    setLoading(true);
    m_exportPending = items.size();
    m_exportUploaded = 0;
    m_exportFailed = 0;
    for (const auto &item : items)
        uploadOneToIntervals(item.first, item.second, athlete, key);
}

void ActivityService::uploadOneToIntervals(int idx, const QByteArray &fit,
                                           const QString &athlete, const QString &key)
{
    QHttpMultiPart *multiPart = new QHttpMultiPart(QHttpMultiPart::FormDataType);
    QHttpPart filePart;
    filePart.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/fit"));
    filePart.setHeader(QNetworkRequest::ContentDispositionHeader,
                       QStringLiteral("form-data; name=\"file\"; filename=\"activity.fit\""));
    filePart.setBody(fit);
    multiPart->append(filePart);

    QNetworkRequest req(QUrl(
        QStringLiteral("https://intervals.icu/api/v1/athlete/%1/activities").arg(athlete)));
    const QByteArray basic = QByteArrayLiteral("API_KEY:") + key.toUtf8();
    req.setRawHeader("Authorization", "Basic " + basic.toBase64());
    req.setRawHeader("User-Agent",
                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Sommet/1.0");

    QNetworkReply *reply = m_network.post(req, multiPart);
    multiPart->setParent(reply);  // freed with the reply
    connect(reply, &QNetworkReply::finished, this, [this, reply, idx]() {
        reply->deleteLater();
        const int status =
            reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        const bool ok = reply->error() == QNetworkReply::NoError
                        && (status == 200 || status == 201);
        if (ok) {
            ++m_exportUploaded;
            QSqlQuery up(m_db);
            up.prepare(QStringLiteral("UPDATE activities SET exported = 1 WHERE idx = ?"));
            up.addBindValue(idx);
            up.exec();
        } else {
            ++m_exportFailed;
        }
        if (--m_exportPending <= 0) {
            setLoading(false);
            emit exportFinished(m_exportUploaded, m_exportFailed);
        }
    });
}

QString ActivityService::intervalsExportScope() const
{
    return QSettings().value(QStringLiteral("intervals/exportScope"),
                             QStringLiteral("manual")).toString();
}

void ActivityService::setIntervalsExportScope(const QString &scope)
{
    QSettings().setValue(QStringLiteral("intervals/exportScope"), scope);
    // For a scope that includes watch moves, push what's outstanding right away (bulk export
    // is a no-op when there's nothing new). eTrex is driven from GarminService on its own sync.
    if (scope == QStringLiteral("suunto") || scope == QStringLiteral("all"))
        exportToIntervals();
}

void ActivityService::exportActivityToIntervals(const QString &name, const QString &fitBase64,
                                                const QString &gpxText)
{
    const QSettings settings;
    const QString athlete =
        settings.value(QStringLiteral("connections/intervals_icu/athleteId")).toString();
    const QString key =
        settings.value(QStringLiteral("connections/intervals_icu/apiKey")).toString();
    if (athlete.isEmpty() || key.isEmpty()) {
        emit exportError(tr("Connect Intervals.icu in Settings first."));
        return;
    }
    // Prefer the FIT (carries the logged-app streams); fall back to GPX for eTrex moves, which
    // have no FIT. The activity name (if any) becomes the upload's filename hint.
    const QByteArray fit = QByteArray::fromBase64(fitBase64.toLatin1());
    const QString base = name.isEmpty() ? QStringLiteral("activity")
                                        : QString(name).replace(QLatin1Char('"'), QString());
    setLoading(true);
    m_exportPending = 1;
    m_exportUploaded = 0;
    m_exportFailed = 0;
    if (!fit.isEmpty()) {
        uploadFileToIntervals(-1, fit, QStringLiteral("application/fit"),
                              base + QStringLiteral(".fit"), athlete, key);
    } else if (!gpxText.isEmpty()) {
        uploadFileToIntervals(-1, gpxText.toUtf8(), QStringLiteral("application/gpx+xml"),
                              base + QStringLiteral(".gpx"), athlete, key);
    } else {
        setLoading(false);
        m_exportPending = 0;
        emit exportError(tr("This activity has no FIT or GPX data to upload."));
    }
}

void ActivityService::exportActivitiesToIntervals(const QVariantList &activities)
{
    QSettings settings;
    const QString athlete =
        settings.value(QStringLiteral("connections/intervals_icu/athleteId")).toString();
    const QString key =
        settings.value(QStringLiteral("connections/intervals_icu/apiKey")).toString();
    if (athlete.isEmpty() || key.isEmpty()) {
        emit exportError(tr("Connect Intervals.icu in Settings first."));
        return;
    }
    // Per-activity dedup key (eTrex moves have no DB idx). Kept in QSettings; marked optimistically
    // when we start the upload so a repeated sync doesn't re-push the same file.
    QStringList done = settings.value(QStringLiteral("intervals/exportedKeys")).toStringList();
    struct Item { QByteArray data; QString contentType, filename; };
    QList<Item> items;
    for (const QVariant &v : activities) {
        const QVariantMap a = v.toMap();
        const QString akey = a.value(QStringLiteral("startTime")).toString()
                             + QLatin1Char('|') + a.value(QStringLiteral("name")).toString();
        if (done.contains(akey))
            continue;
        const QByteArray fit =
            QByteArray::fromBase64(a.value(QStringLiteral("fitBase64")).toString().toLatin1());
        const QString gpx = a.value(QStringLiteral("gpxText")).toString();
        const QString base = a.value(QStringLiteral("name")).toString().isEmpty()
            ? QStringLiteral("activity")
            : QString(a.value(QStringLiteral("name")).toString()).replace(QLatin1Char('"'), QString());
        if (!fit.isEmpty())
            items.append({fit, QStringLiteral("application/fit"), base + QStringLiteral(".fit")});
        else if (!gpx.isEmpty())
            items.append({gpx.toUtf8(), QStringLiteral("application/gpx+xml"),
                          base + QStringLiteral(".gpx")});
        else
            continue;
        done << akey;
    }
    if (items.isEmpty()) {
        emit exportFinished(0, 0);
        return;
    }
    settings.setValue(QStringLiteral("intervals/exportedKeys"), done);
    setLoading(true);
    m_exportPending = items.size();
    m_exportUploaded = 0;
    m_exportFailed = 0;
    for (const Item &it : items)
        uploadFileToIntervals(-1, it.data, it.contentType, it.filename, athlete, key);
}

void ActivityService::exportActivityToGarmin(const QString &name, const QString &fitBase64,
                                             const QString &gpxText)
{
    Q_UNUSED(name)
    if (fitBase64.isEmpty() && gpxText.isEmpty()) {
        emit exportError(tr("This activity has no FIT or GPX data to upload."));
        return;
    }
    QJsonObject payload;
    if (!fitBase64.isEmpty())
        payload.insert(QStringLiteral("fit_base64"), fitBase64);
    else
        payload.insert(QStringLiteral("gpx"), gpxText);
    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/garmin/upload")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    setLoading(true);
    QNetworkReply *reply = m_network.post(req, QJsonDocument(payload).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        setLoading(false);
        const auto o = QJsonDocument::fromJson(reply->readAll()).object();
        if (o.value(QStringLiteral("ok")).toBool(false))
            emit exportFinished(1, 0);
        else
            emit exportError(o.value(QStringLiteral("needLogin")).toBool(false)
                ? tr("Sign in to Garmin on the Weight page first.")
                : o.value(QStringLiteral("error")).toString(tr("Garmin upload failed.")));
    });
}

void ActivityService::uploadToGarmin(const QByteArray &data, bool isFit)
{
    QJsonObject payload;
    if (isFit)
        payload.insert(QStringLiteral("fit_base64"), QString::fromLatin1(data.toBase64()));
    else
        payload.insert(QStringLiteral("gpx"), QString::fromUtf8(data));
    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/garmin/upload")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(req, QJsonDocument(payload).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        const auto o = QJsonDocument::fromJson(reply->readAll()).object();
        if (o.value(QStringLiteral("ok")).toBool(false))
            ++m_exportUploaded;
        else
            ++m_exportFailed;
        if (--m_exportPending <= 0) {
            setLoading(false);
            emit exportFinished(m_exportUploaded, m_exportFailed);
        }
    });
}

void ActivityService::exportToGarmin()
{
    if (!m_db.isOpen()) {
        emit exportError(tr("Local activity store isn't open."));
        return;
    }
    QSettings settings;
    QStringList done = settings.value(QStringLiteral("garmin/exportedKeys")).toStringList();
    QList<QByteArray> fits;
    QSqlQuery q(QStringLiteral(
        "SELECT fit_base64, start_time, name FROM activities "
        "WHERE (source IS NULL OR source = 'watch') AND fit_base64 IS NOT NULL "
        "AND fit_base64 <> ''"), m_db);
    while (q.next()) {
        const QString key = q.value(1).toString() + QLatin1Char('|') + q.value(2).toString();
        if (done.contains(key))
            continue;
        const QByteArray fit = QByteArray::fromBase64(q.value(0).toString().toLatin1());
        if (fit.isEmpty())
            continue;
        fits.append(fit);
        done << key;
    }
    if (fits.isEmpty()) {
        emit exportFinished(0, 0);
        return;
    }
    settings.setValue(QStringLiteral("garmin/exportedKeys"), done);
    setLoading(true);
    m_exportPending = fits.size();
    m_exportUploaded = 0;
    m_exportFailed = 0;
    for (const QByteArray &fit : fits)
        uploadToGarmin(fit, /*isFit=*/true);
}

void ActivityService::exportActivitiesToGarmin(const QVariantList &activities)
{
    QSettings settings;
    QStringList done = settings.value(QStringLiteral("garmin/exportedKeys")).toStringList();
    struct Item { QByteArray data; bool isFit; };
    QList<Item> items;
    for (const QVariant &v : activities) {
        const QVariantMap a = v.toMap();
        const QString key = a.value(QStringLiteral("startTime")).toString()
                            + QLatin1Char('|') + a.value(QStringLiteral("name")).toString();
        if (done.contains(key))
            continue;
        const QByteArray fit =
            QByteArray::fromBase64(a.value(QStringLiteral("fitBase64")).toString().toLatin1());
        const QString gpx = a.value(QStringLiteral("gpxText")).toString();
        if (!fit.isEmpty())
            items.append({fit, true});
        else if (!gpx.isEmpty())
            items.append({gpx.toUtf8(), false});
        else
            continue;
        done << key;
    }
    if (items.isEmpty()) {
        emit exportFinished(0, 0);
        return;
    }
    settings.setValue(QStringLiteral("garmin/exportedKeys"), done);
    setLoading(true);
    m_exportPending = items.size();
    m_exportUploaded = 0;
    m_exportFailed = 0;
    for (const Item &it : items)
        uploadToGarmin(it.data, it.isFit);
}

void ActivityService::uploadFileToIntervals(int idx, const QByteArray &data,
                                            const QString &contentType, const QString &filename,
                                            const QString &athlete, const QString &key)
{
    QHttpMultiPart *multiPart = new QHttpMultiPart(QHttpMultiPart::FormDataType);
    QHttpPart filePart;
    filePart.setHeader(QNetworkRequest::ContentTypeHeader, contentType);
    filePart.setHeader(QNetworkRequest::ContentDispositionHeader,
                       QStringLiteral("form-data; name=\"file\"; filename=\"%1\"").arg(filename));
    filePart.setBody(data);
    multiPart->append(filePart);

    QNetworkRequest req(QUrl(
        QStringLiteral("https://intervals.icu/api/v1/athlete/%1/activities").arg(athlete)));
    const QByteArray basic = QByteArrayLiteral("API_KEY:") + key.toUtf8();
    req.setRawHeader("Authorization", "Basic " + basic.toBase64());
    req.setRawHeader("User-Agent",
                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Sommet/1.0");

    QNetworkReply *reply = m_network.post(req, multiPart);
    multiPart->setParent(reply);
    connect(reply, &QNetworkReply::finished, this, [this, reply, idx]() {
        reply->deleteLater();
        const int status =
            reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();
        const bool ok = reply->error() == QNetworkReply::NoError
                        && (status == 200 || status == 201);
        if (ok) {
            ++m_exportUploaded;
            if (idx >= 0 && m_db.isOpen()) {
                QSqlQuery up(m_db);
                up.prepare(QStringLiteral("UPDATE activities SET exported = 1 WHERE idx = ?"));
                up.addBindValue(idx);
                up.exec();
            }
        } else {
            ++m_exportFailed;
            // A per-file upload (idx<0) surfaces the reason; the bulk path already aggregates.
            if (idx < 0)
                emit exportError(status == 409
                    ? tr("intervals.icu already has this activity (duplicate).")
                    : reply->errorString());
        }
        if (--m_exportPending <= 0) {
            setLoading(false);
            emit exportFinished(m_exportUploaded, m_exportFailed);
        }
    });
}
