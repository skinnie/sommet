#include "activityservice.h"

#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QDateTime>
#include <QHash>
#include <QNetworkReply>
#include <QNetworkRequest>
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
    requestActivities(dbKnownCount(), false);
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
        const int totalEntries = root.value(QStringLiteral("total_entries")).toInt();
        if (totalEntries < knownCount && !alreadyRetried) {
            dbClear();
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
            dbInsert(index, parsed, gpxText, fitBase64, ruleOutputsJson);
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
}

int ActivityService::dbKnownCount()
{
    if (!m_db.isOpen())
        return 0;
    // Only watch rows drive "how many the app already has" - imported rows use negative idx
    // and must not inflate this (they'd make the watch skip reading real activities).
    QSqlQuery q(QStringLiteral(
        "SELECT MAX(idx) FROM activities WHERE source IS NULL OR source = 'watch'"), m_db);
    if (q.next())
        return q.value(0).toInt();  // NULL (empty table) -> QVariant().toInt() == 0
    return 0;
}

void ActivityService::dbClear()
{
    if (!m_db.isOpen())
        return;
    QSqlQuery q(m_db);
    q.exec(QStringLiteral("DELETE FROM activities"));
}

void ActivityService::dbInsert(int index, const QVariantMap &parsed, const QString &gpxText,
                                const QString &fitBase64, const QString &ruleOutputsJson)
{
    if (!m_db.isOpen())
        return;
    const QJsonDocument trackDoc(QJsonArray::fromVariantList(parsed.value(
        QStringLiteral("track")).toList()));

    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT OR REPLACE INTO activities "
        "(idx, name, duration_s, distance_m, ascent_m, energy_kcal, sport_type_raw, "
        " start_time, track_json, gpx_text, fit_base64, rule_outputs_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"));
    q.addBindValue(index);
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
        "start_time, track_json, gpx_text, fit_base64, rule_outputs_json, source "
        // By date (newest first) rather than log index, so imported intervals.icu moves blend
        // in chronologically with watch moves instead of clumping by idx. Real watch rows all
        // carry an ISO start_time (sortable as text); the idx tiebreak keeps a stable order.
        "FROM activities ORDER BY start_time DESC, idx DESC"),
        m_db);
    while (q.next()) {
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
                     QStringLiteral("maxAltitudeMeters"), QStringLiteral("paceSecPerKm") }) {
                parsed[key] = extra.value(key);
            }
        }
        m_activities.append(parsed);
    }
    m_showingCachedData = !m_activities.isEmpty();
    return !m_activities.isEmpty();
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

        QSqlQuery ins(m_db);
        ins.prepare(QStringLiteral(
            "INSERT INTO activities "
            "(idx, name, duration_s, distance_m, ascent_m, energy_kcal, sport_type_raw, "
            " start_time, source, external_id) "
            "VALUES (?, ?, ?, ?, ?, ?, 0, ?, 'intervals', ?)"));
        ins.addBindValue(idx--);
        ins.addBindValue(name);
        ins.addBindValue(duration);
        ins.addBindValue(distance);
        ins.addBindValue(ascent);
        ins.addBindValue(calories);
        ins.addBindValue(start);
        ins.addBindValue(extId);
        ins.exec();
        ++count;
    }
    m_db.commit();
    dbLoadAll();
    emit activitiesChanged();
    emit importFinished(count);
}
