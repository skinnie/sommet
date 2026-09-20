#include "gearservice.h"
#include "apppaths.h"

#include <cmath>
#include <QtMath>

#include <QDateTime>
#include <QDir>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QSettings>
#include <QSqlError>
#include <QSqlQuery>
#include <QStandardPaths>
#include <QUrl>
#include <QUrlQuery>
#include <QUuid>
#include <QtMath>

namespace {
const QString kBase = QStringLiteral("https://intervals.icu/api/v1");
const QString kAthleteKey = QStringLiteral("connections/intervals_icu/athleteId");
const QString kApiKeyKey = QStringLiteral("connections/intervals_icu/apiKey");

// intervals.icu gear `type` is free-form; only Bike/Shoes are top-level (the rest are parts).
bool isTopLevel(const QString &type)
{
    const QString t = type.toLower();
    return t == QStringLiteral("bike") || t == QStringLiteral("shoes") || t == QStringLiteral("shoe");
}

// Local due-ness: (gear total - reset baseline) / interval, max across the units a reminder sets.
// On desktop the gear total is the imported intervals distance/time (baseline); this still
// computes due-ness ourselves rather than trusting percent_used.
double reminderPercent(const QSqlQuery &r, double gearDistanceM, double gearTimeS, qint64 nowMs)
{
    double pct = 0.0;
    const double distInt = r.value(QStringLiteral("distance_m")).toDouble();
    const double timeInt = r.value(QStringLiteral("time_s")).toDouble();
    const int days = r.value(QStringLiteral("days")).toInt();
    const double startDist = r.value(QStringLiteral("starting_distance_m")).toDouble();
    const double startTime = r.value(QStringLiteral("starting_time_s")).toDouble();
    const qint64 lastReset = r.value(QStringLiteral("last_reset")).toLongLong();
    if (distInt > 0) pct = qMax(pct, qMax(0.0, gearDistanceM - startDist) / distInt * 100.0);
    if (timeInt > 0) pct = qMax(pct, qMax(0.0, gearTimeS - startTime) / timeInt * 100.0);
    if (days > 0 && lastReset > 0)
        pct = qMax(pct, double(nowMs - lastReset) / 86400000.0 / days * 100.0);
    return pct;
}

QString reminderLabel(const QSqlQuery &r)
{
    const double dist = r.value(QStringLiteral("distance_m")).toDouble();
    const double time = r.value(QStringLiteral("time_s")).toDouble();
    const int days = r.value(QStringLiteral("days")).toInt();
    const int acts = r.value(QStringLiteral("activities")).toInt();
    if (dist > 0) return QStringLiteral("%1 km").arg(qRound(dist / 1000.0));
    if (time > 0) return QStringLiteral("%1 h").arg(qRound(time / 3600.0));
    if (days > 0) return QStringLiteral("%1 d").arg(days);
    if (acts > 0) return QStringLiteral("%1×").arg(acts);
    return QStringLiteral("—");
}
} // namespace

GearService::GearService(QObject *parent) : QObject(parent)
{
    openDatabase();
    loadFromDb();
}

QString GearService::apiKey() const { return QSettings().value(kApiKeyKey).toString(); }
QString GearService::athleteId() const { return QSettings().value(kAthleteKey).toString(); }
bool GearService::connected() const { return !apiKey().isEmpty() && !athleteId().isEmpty(); }

void GearService::setLoading(bool v)
{
    if (m_loading == v) return;
    m_loading = v;
    emit loadingChanged();
}

void GearService::setLastError(const QString &e)
{
    m_lastError = e;
    emit lastErrorChanged();
}

void GearService::openDatabase()
{
    m_db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), QStringLiteral("gear"));
    const QString dir = AppPaths::databaseDir();   // user-chosen data location (Settings -> Database)
    QDir().mkpath(dir);
    m_db.setDatabaseName(dir + QStringLiteral("/gear.db"));
    if (!m_db.open()) {
        setLastError(m_db.lastError().text());
        return;
    }
    QSqlQuery q(m_db);
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS gear ("
        "id TEXT PRIMARY KEY, remote_id TEXT, parent_id TEXT, name TEXT, type TEXT, "
        "component INTEGER, distance_m REAL, time_s REAL, retired INTEGER, "
        "component_ids TEXT)"));
    q.exec(QStringLiteral("ALTER TABLE gear ADD COLUMN component_ids TEXT")); // migrate older DBs
    // Sommet Sync / local-first gear (#SYNC-4a): let desktop gear stop being a pull-only cache of
    // intervals.icu so it can live in the user's own shared store and outlive intervals.
    //  - updated_at/deleted: last-writer-wins + tombstone, same model as activities.
    //  - starting_distance_m/_time_s + baseline_at: the manually-entered mileage baseline and the
    //    moment it was set. Displayed total = baseline + rides attributed to this gear AFTER
    //    baseline_at (so a typed number is never lost and rides on top are never double-counted).
    // All additive no-op migrations (ignored "duplicate column" on an already-migrated DB).
    for (const char *col : {"updated_at INTEGER", "deleted INTEGER", "starting_distance_m REAL",
                            "starting_time_s REAL", "baseline_at INTEGER"})
        q.exec(QStringLiteral("ALTER TABLE gear ADD COLUMN %1").arg(QLatin1String(col)));
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS gear_reminder ("
        "id TEXT PRIMARY KEY, gear_id TEXT, name TEXT, distance_m REAL, time_s REAL, "
        "days INTEGER, activities INTEGER, starting_distance_m REAL, starting_time_s REAL, "
        "last_reset INTEGER)"));
    // Migrate a gear.db made by an earlier build whose gear_reminder predates these columns
    // (same class of bug fixed on Android's db.ts, 2026-08-18: import crashed with "table
    // gear_reminder has no column named distance_m"). Each ADD COLUMN is a harmless no-op error
    // when the column already exists.
    for (const char *col : {"distance_m REAL", "time_s REAL", "days INTEGER", "activities INTEGER",
                            "starting_distance_m REAL", "starting_time_s REAL", "last_reset INTEGER",
                            // Sommet Sync (#SYNC-4): reminders sync like gear - remote_id tells an
                            // intervals-sourced reminder from a local-only one (import prunes only
                            // the former); updated_at/deleted drive last-writer-wins + tombstones.
                            "remote_id TEXT", "updated_at INTEGER", "deleted INTEGER"})
        q.exec(QStringLiteral("ALTER TABLE gear_reminder ADD COLUMN %1").arg(QLatin1String(col)));
    // Default gear per decoded sport name, and the local usage ledger (manual per-activity gear).
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS gear_assignment (sport TEXT PRIMARY KEY, gear_id TEXT)"));
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS gear_exception ("
        "sport TEXT PRIMARY KEY, country TEXT, radius_km REAL, gear_id TEXT)"));
    q.exec(QStringLiteral(
        "CREATE TABLE IF NOT EXISTS activity_gear ("
        "activity_key TEXT PRIMARY KEY, gear_id TEXT, distance_m REAL, time_s REAL)"));
}

void GearService::send(const QByteArray &verb, const QString &path, const QJsonObject &body,
                       std::function<void(const QJsonDocument &)> onOk)
{
    if (!connected()) {
        setLastError(tr("Connect Intervals.icu in Settings first."));
        return;
    }
    setLoading(true);
    setLastError(QString());

    QNetworkRequest req(QUrl(QStringLiteral("%1/athlete/%2%3").arg(kBase, athleteId(), path)));
    const QByteArray basic = QByteArrayLiteral("API_KEY:") + apiKey().toUtf8();
    req.setRawHeader("Authorization", "Basic " + basic.toBase64());
    // Cloudflare in front of intervals.icu returns 1010 (banned) for QNetworkAccessManager's
    // empty default User-Agent — proven live 2026-08-18. A normal Mozilla-style UA passes.
    req.setRawHeader("User-Agent",
                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Sommet/1.0");
    QByteArray data;
    if (!body.isEmpty()) {
        data = QJsonDocument(body).toJson(QJsonDocument::Compact);
        req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    }

    QNetworkReply *reply = nullptr;
    if (verb == "GET") reply = m_net.get(req);
    else if (verb == "POST") reply = m_net.post(req, data);
    else if (verb == "PUT") reply = m_net.put(req, data);
    else if (verb == "DELETE") reply = m_net.deleteResource(req);
    else reply = m_net.sendCustomRequest(req, verb, data);

    connect(reply, &QNetworkReply::finished, this, [this, reply, onOk]() {
        reply->deleteLater();
        setLoading(false);
        if (reply->error() != QNetworkReply::NoError) {
            setLastError(reply->errorString());
            return;
        }
        onOk(QJsonDocument::fromJson(reply->readAll()));
    });
}

void GearService::importFromIntervals()
{
    send("GET", QStringLiteral("/gear"), {}, [this](const QJsonDocument &doc) {
        if (!doc.isArray()) {
            setLastError(tr("Unexpected response from Intervals.icu."));
            return;
        }
        const QJsonArray arr = doc.array();

        // Build the child -> parent map from every parent's component_ids.
        QHash<QString, QString> parentOf;
        for (const QJsonValue &v : arr) {
            const QJsonObject o = v.toObject();
            const QString id = o.value(QStringLiteral("id")).toVariant().toString();
            for (const QJsonValue &c : o.value(QStringLiteral("component_ids")).toArray())
                parentOf.insert(c.toVariant().toString(), id);
        }

        // Local-first MERGE (#SYNC-4a), not a wipe: keep the local store (so gear that lives only
        // here - never pushed to intervals, or synced from the NAS - survives), update the rows
        // intervals still has, and remove only the intervals-sourced rows intervals no longer has
        // (a real remote delete). storeGear() upserts and preserves the local mileage baseline.
        QStringList remoteIds;
        QStringList remoteReminderIds;
        int count = 0;
        for (const QJsonValue &v : arr) {
            const QVariantMap g = v.toObject().toVariantMap();
            const QString id = g.value(QStringLiteral("id")).toString();
            remoteIds << id;
            for (const QVariant &rv : g.value(QStringLiteral("reminders")).toList())
                remoteReminderIds << rv.toMap().value(QStringLiteral("id")).toString();
            storeGear(g, parentOf.value(id));
            ++count;
        }
        // Prune intervals-sourced rows that vanished remotely (deleted on intervals). A row is
        // "intervals-sourced" when it has a remote_id; local-only rows (remote_id NULL/'') stay.
        const auto pruneMissing = [this](const QString &table, const QString &col,
                                         const QStringList &keep) {
            QStringList q;
            for (int i = 0; i < keep.size(); ++i) q << QStringLiteral("?");
            const QString ph = q.join(QLatin1Char(','));
            QSqlQuery del(m_db);
            del.prepare(QStringLiteral("DELETE FROM %1 WHERE remote_id IS NOT NULL AND remote_id != '' "
                                       "AND %2 NOT IN (%3)").arg(table, col, ph.isEmpty() ? QStringLiteral("''") : ph));
            for (const QString &k : keep) del.addBindValue(k);
            del.exec();
        };
        pruneMissing(QStringLiteral("gear"), QStringLiteral("id"), remoteIds);
        pruneMissing(QStringLiteral("gear_reminder"), QStringLiteral("id"), remoteReminderIds);

        loadFromDb();
        emit importFinished(count);
    });
}

QString GearService::componentIdsJson(const QString &gearId) const
{
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("SELECT component_ids FROM gear WHERE id = ?"));
    q.addBindValue(gearId);
    q.exec();
    return q.next() ? q.value(0).toString() : QString();
}

// ── Editing (write-through to intervals.icu, then re-import to refresh) ─────────

void GearService::addGear(const QString &name, const QString &type)
{
    if (connected()) {
        // Intervals is connected: let it assign the id (the proven path). importFromIntervals()
        // now merges rather than wipes, and pushes to the NAS afterwards.
        send("POST", QStringLiteral("/gear"),
             QJsonObject{{"name", name}, {"type", type}, {"component", false}},
             [this](const QJsonDocument &) { importFromIntervals(); sommetGearSyncNow(); });
        return;
    }
    // No intervals: create the gear locally with our own id; it flows to the NAS on the next sync.
    if (!m_db.isOpen())
        return;
    const QString id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO gear (id, remote_id, parent_id, name, type, component, distance_m, time_s, "
        "retired, updated_at, deleted, starting_distance_m, starting_time_s, baseline_at) "
        "VALUES (?, '', '', ?, ?, 0, 0, 0, 0, ?, 0, 0, 0, 0)"));
    q.addBindValue(id);
    q.addBindValue(name);
    q.addBindValue(type);
    q.addBindValue(now);
    q.exec();
    loadFromDb();
    sommetGearSyncNow();
}

void GearService::addComponent(const QString &parentId, const QString &name, const QString &type)
{
    if (connected()) {
        const QString stored = componentIdsJson(parentId);
        send("POST", QStringLiteral("/gear"),
             QJsonObject{{"name", name}, {"type", type}, {"component", true}},
             [this, parentId, stored](const QJsonDocument &doc) {
                 const QString newId = doc.object().value(QStringLiteral("id")).toVariant().toString();
                 if (newId.isEmpty() || parentId.isEmpty()) { importFromIntervals(); sommetGearSyncNow(); return; }
                 QJsonArray ids = QJsonDocument::fromJson(stored.toUtf8()).array();
                 ids.append(newId);
                 send("PUT", QStringLiteral("/gear/%1").arg(parentId),
                      QJsonObject{{"component_ids", ids}},
                      [this](const QJsonDocument &) { importFromIntervals(); sommetGearSyncNow(); });
             });
        return;
    }
    // No intervals: create the part locally as a child gear (parent_id links it; parts list by
    // parent_id). It flows to the NAS on the next sync, exactly like adding a bike.
    if (!m_db.isOpen())
        return;
    const QString id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO gear (id, remote_id, parent_id, name, type, component, distance_m, time_s, "
        "retired, updated_at, deleted, starting_distance_m, starting_time_s, baseline_at) "
        "VALUES (?, '', ?, ?, ?, 1, 0, 0, 0, ?, 0, 0, 0, 0)"));
    q.addBindValue(id);
    q.addBindValue(parentId);
    q.addBindValue(name);
    q.addBindValue(type);
    q.addBindValue(now);
    q.exec();
    // Keep the parent's component_ids list consistent (used by the intervals side + as a link).
    QJsonArray ids = QJsonDocument::fromJson(componentIdsJson(parentId).toUtf8()).array();
    ids.append(id);
    QSqlQuery pq(m_db);
    pq.prepare(QStringLiteral("UPDATE gear SET component_ids = ?, updated_at = ? WHERE id = ?"));
    pq.addBindValue(QString::fromUtf8(QJsonDocument(ids).toJson(QJsonDocument::Compact)));
    pq.addBindValue(now);
    pq.addBindValue(parentId);
    pq.exec();
    loadFromDb();
    sommetGearSyncNow();
}

// Local-first edits (#SYNC-4): apply the change to our own DB first (stamping updated_at so it
// flows to the NAS), refresh, then - only if intervals.icu is connected - mirror it there too.
// This makes gear editing work with intervals, with the NAS, with both, or with neither.
void GearService::localGearField(const QString &id, const QString &column, const QVariant &value)
{
    if (!m_db.isOpen())
        return;
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("UPDATE gear SET %1 = ?, updated_at = ? WHERE id = ?").arg(column));
    q.addBindValue(value);
    q.addBindValue(QDateTime::currentMSecsSinceEpoch());
    q.addBindValue(id);
    q.exec();
    loadFromDb();
    sommetGearSyncNow();
}

void GearService::renameGear(const QString &id, const QString &name)
{
    localGearField(id, QStringLiteral("name"), name);
    if (connected())
        send("PUT", QStringLiteral("/gear/%1").arg(id), QJsonObject{{"name", name}},
             [](const QJsonDocument &) {});
}

void GearService::setRetired(const QString &id, bool retired)
{
    localGearField(id, QStringLiteral("retired"), retired ? 1 : 0);
    if (connected())
        send("PUT", QStringLiteral("/gear/%1").arg(id), QJsonObject{{"retired", retired}},
             [](const QJsonDocument &) {});
}

void GearService::removeGear(const QString &id)
{
    // Tombstone locally (the gear and its parts) so it hides at once and the NAS sync propagates
    // the delete - whether or not intervals is connected.
    if (m_db.isOpen()) {
        QSqlQuery q(m_db);
        q.prepare(QStringLiteral(
            "UPDATE gear SET deleted = 1, updated_at = ? WHERE id = ? OR parent_id = ?"));
        q.addBindValue(QDateTime::currentMSecsSinceEpoch());
        q.addBindValue(id);
        q.addBindValue(id);
        q.exec();
    }
    loadFromDb();
    sommetGearSyncNow();
    if (connected())
        send("DELETE", QStringLiteral("/gear/%1").arg(id), {}, [](const QJsonDocument &) {});
}

void GearService::addReminder(const QString &gearId, const QString &name,
                              double km, double hours, int days, int activities)
{
    if (connected()) {
        send("POST", QStringLiteral("/gear/%1/reminder").arg(gearId),
             QJsonObject{{"name", name}, {"distance", km * 1000.0}, {"time", hours * 3600.0},
                         {"days", days}, {"activities", activities}},
             [this](const QJsonDocument &) { importFromIntervals(); sommetGearSyncNow(); });
        return;
    }
    if (!m_db.isOpen())
        return;
    const QString id = QUuid::createUuid().toString(QUuid::WithoutBraces);
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO gear_reminder (id, gear_id, name, distance_m, time_s, days, activities, "
        "starting_distance_m, starting_time_s, last_reset, remote_id, updated_at, deleted) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, 0, 0, 0, '', ?, 0)"));
    q.addBindValue(id);
    q.addBindValue(gearId);
    q.addBindValue(name);
    q.addBindValue(km * 1000.0);
    q.addBindValue(hours * 3600.0);
    q.addBindValue(days);
    q.addBindValue(activities);
    q.addBindValue(QDateTime::currentMSecsSinceEpoch());
    q.exec();
    loadFromDb();
    sommetGearSyncNow();
}

void GearService::removeReminder(const QString &gearId, const QString &reminderId)
{
    if (m_db.isOpen()) {
        QSqlQuery q(m_db);
        q.prepare(QStringLiteral(
            "UPDATE gear_reminder SET deleted = 1, updated_at = ? WHERE id = ?"));
        q.addBindValue(QDateTime::currentMSecsSinceEpoch());
        q.addBindValue(reminderId);
        q.exec();
    }
    loadFromDb();
    sommetGearSyncNow();
    if (connected())
        send("DELETE", QStringLiteral("/gear/%1/reminder/%2").arg(gearId, reminderId), {},
             [](const QJsonDocument &) {});
}

// ── Local distance tally + manual per-activity gear (D2-a, all local) ──────────

void GearService::setAssignment(const QString &sport, const QString &gearId)
{
    QSqlQuery q(m_db);
    if (gearId.isEmpty()) {
        q.prepare(QStringLiteral("DELETE FROM gear_assignment WHERE sport = ?"));
        q.addBindValue(sport);
    } else {
        q.prepare(QStringLiteral("INSERT OR REPLACE INTO gear_assignment (sport, gear_id) VALUES (?, ?)"));
        q.addBindValue(sport);
        q.addBindValue(gearId);
    }
    q.exec();
    loadFromDb();
}

QString GearService::defaultGearForSport(const QString &sport) const
{
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("SELECT gear_id FROM gear_assignment WHERE sport = ? LIMIT 1"));
    q.addBindValue(sport);
    q.exec();
    return q.next() ? q.value(0).toString() : QString();
}

// Country centroids for the exception geofence (André, 2026-08-18). A curated set - Europe in
// full plus common travel destinations - each an approximate country centre. "In <country>
// within <radius> km" is a circle around this point; not a border polygon, which is why the
// radius is generous (up to 1000 km). Extend freely.
QVariantList GearService::countries() const
{
    struct C { const char *name; double lat; double lon; };
    static const C table[] = {
        {"Portugal",39.5,-8.0},{"Spain",40.3,-3.7},{"France",46.6,2.4},{"Italy",42.8,12.8},
        {"Germany",51.1,10.4},{"United Kingdom",54.0,-2.5},{"Ireland",53.2,-8.0},
        {"Netherlands",52.2,5.3},{"Belgium",50.6,4.6},{"Switzerland",46.8,8.2},
        {"Austria",47.6,14.1},{"Andorra",42.5,1.6},{"Luxembourg",49.8,6.1},
        {"Denmark",56.0,9.5},{"Norway",62.0,9.0},{"Sweden",62.0,15.0},{"Finland",64.0,26.0},
        {"Poland",52.0,19.0},{"Czechia",49.8,15.5},{"Slovakia",48.7,19.7},
        {"Slovenia",46.1,14.8},{"Croatia",45.1,15.2},{"Greece",39.0,22.0},
        {"Hungary",47.2,19.4},{"Romania",45.9,25.0},{"Bulgaria",42.7,25.5},
        {"Iceland",64.9,-19.0},{"Morocco",31.8,-7.0},{"Cape Verde",16.0,-24.0},
        {"Turkey",39.0,35.0},{"United States",39.5,-98.5},{"Canada",56.0,-106.0},
        {"Brazil",-10.0,-52.0},{"Argentina",-34.0,-64.0},{"Australia",-25.0,134.0},
        {"New Zealand",-41.0,174.0},{"South Africa",-29.0,24.0},{"Japan",36.2,138.3},
        {"Thailand",15.0,101.0},{"United Arab Emirates",24.0,54.0},
    };
    QVariantList out;
    for (const C &c : table) {
        QVariantMap m;
        m.insert(QStringLiteral("name"), QString::fromUtf8(c.name));
        m.insert(QStringLiteral("lat"), c.lat);
        m.insert(QStringLiteral("lon"), c.lon);
        out.append(m);
    }
    return out;
}

void GearService::setException(const QString &sport, const QString &country,
                               double radiusKm, const QString &gearId)
{
    QSqlQuery q(m_db);
    if (country.isEmpty() || gearId.isEmpty()) {
        q.prepare(QStringLiteral("DELETE FROM gear_exception WHERE sport = ?"));
        q.addBindValue(sport);
    } else {
        q.prepare(QStringLiteral(
            "INSERT OR REPLACE INTO gear_exception (sport, country, radius_km, gear_id) "
            "VALUES (?, ?, ?, ?)"));
        q.addBindValue(sport);
        q.addBindValue(country);
        q.addBindValue(radiusKm);
        q.addBindValue(gearId);
    }
    q.exec();
    loadFromDb();
}

void GearService::clearException(const QString &sport)
{
    setException(sport, QString(), 0.0, QString());
}

QVariantMap GearService::exceptionFor(const QString &sport) const
{
    return m_exceptions.value(sport).toMap();
}

QString GearService::gearForActivity(const QString &sport, double lat, double lon) const
{
    const QString def = defaultGearForSport(sport);
    const QVariantMap ex = m_exceptions.value(sport).toMap();
    if (ex.isEmpty() || std::isnan(lat) || std::isnan(lon))
        return def;  // no exception, or an indoor/location-less activity -> default

    double clat = 0.0, clon = 0.0;
    bool found = false;
    const QString country = ex.value(QStringLiteral("country")).toString();
    for (const QVariant &c : countries()) {
        const QVariantMap m = c.toMap();
        if (m.value(QStringLiteral("name")).toString() == country) {
            clat = m.value(QStringLiteral("lat")).toDouble();
            clon = m.value(QStringLiteral("lon")).toDouble();
            found = true;
            break;
        }
    }
    if (!found) return def;

    // Haversine great-circle distance (km) from the activity start to the country centroid.
    const double R = 6371.0;
    const double dLat = qDegreesToRadians(lat - clat);
    const double dLon = qDegreesToRadians(lon - clon);
    const double a = std::sin(dLat / 2) * std::sin(dLat / 2)
        + std::cos(qDegreesToRadians(clat)) * std::cos(qDegreesToRadians(lat))
          * std::sin(dLon / 2) * std::sin(dLon / 2);
    const double distKm = R * 2.0 * std::atan2(std::sqrt(a), std::sqrt(1.0 - a));
    return distKm <= ex.value(QStringLiteral("radiusKm")).toDouble()
        ? ex.value(QStringLiteral("gearId")).toString()
        : def;
}

void GearService::attributeActivity(const QString &key, const QString &gearId,
                                    double distanceM, double timeS)
{
    QSqlQuery q(m_db);
    if (gearId.isEmpty()) {
        q.prepare(QStringLiteral("DELETE FROM activity_gear WHERE activity_key = ?"));
        q.addBindValue(key);
    } else {
        q.prepare(QStringLiteral(
            "INSERT OR REPLACE INTO activity_gear (activity_key, gear_id, distance_m, time_s) "
            "VALUES (?, ?, ?, ?)"));
        q.addBindValue(key);
        q.addBindValue(gearId);
        q.addBindValue(distanceM);
        q.addBindValue(timeS);
    }
    q.exec();
    loadFromDb();
}

void GearService::clearActivity(const QString &key)
{
    attributeActivity(key, QString(), 0, 0);
}

QString GearService::activityGearId(const QString &key) const
{
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("SELECT gear_id FROM activity_gear WHERE activity_key = ? LIMIT 1"));
    q.addBindValue(key);
    q.exec();
    return q.next() ? q.value(0).toString() : QString();
}

void GearService::storeGear(const QVariantMap &g, const QString &parentId)
{
    const QString id = g.value(QStringLiteral("id")).toString();
    const QJsonArray compIds = QJsonArray::fromStringList(
        [&] { QStringList out; for (const QVariant &c : g.value(QStringLiteral("component_ids")).toList())
                  out << c.toString(); return out; }());

    // Upsert (not INSERT OR REPLACE, which would wipe the whole row): update only the
    // intervals-owned fields, leaving the local-first columns (starting_distance_m/_time_s/
    // baseline_at) untouched so an import can never erase a manually-entered mileage baseline.
    // updated_at is bumped so the change flows to the shared store.
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "INSERT INTO gear "
        "(id, remote_id, parent_id, name, type, component, distance_m, time_s, retired, "
        " component_ids, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(id) DO UPDATE SET remote_id=excluded.remote_id, parent_id=excluded.parent_id, "
        "name=excluded.name, type=excluded.type, component=excluded.component, "
        "distance_m=excluded.distance_m, time_s=excluded.time_s, retired=excluded.retired, "
        "component_ids=excluded.component_ids, updated_at=excluded.updated_at"));
    q.addBindValue(id);
    q.addBindValue(id); // remote_id == id (numeric intervals id)
    q.addBindValue(parentId);
    q.addBindValue(g.value(QStringLiteral("name")).toString());
    q.addBindValue(g.value(QStringLiteral("type")).toString());
    q.addBindValue(g.value(QStringLiteral("component")).toBool() ? 1 : 0);
    q.addBindValue(g.value(QStringLiteral("distance")).toDouble());
    q.addBindValue(g.value(QStringLiteral("time")).toDouble());
    q.addBindValue(g.value(QStringLiteral("retired")).toBool() ? 1 : 0);
    q.addBindValue(QString::fromUtf8(QJsonDocument(compIds).toJson(QJsonDocument::Compact)));
    q.addBindValue(QDateTime::currentMSecsSinceEpoch());
    q.exec();

    for (const QVariant &rv : g.value(QStringLiteral("reminders")).toList()) {
        const QVariantMap r = rv.toMap();
        const QString rid = r.value(QStringLiteral("id")).toString();
        const QString lastReset = r.value(QStringLiteral("last_reset")).toString();
        const QDateTime dt = QDateTime::fromString(lastReset, Qt::ISODateWithMs);
        // Upsert (don't wipe a locally-tombstoned reminder's `deleted`); set remote_id = id so the
        // import prune tells this intervals reminder from a local-only one, and bump updated_at.
        QSqlQuery rq(m_db);
        rq.prepare(QStringLiteral(
            "INSERT INTO gear_reminder "
            "(id, gear_id, name, distance_m, time_s, days, activities, "
            " starting_distance_m, starting_time_s, last_reset, remote_id, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET gear_id=excluded.gear_id, name=excluded.name, "
            "distance_m=excluded.distance_m, time_s=excluded.time_s, days=excluded.days, "
            "activities=excluded.activities, starting_distance_m=excluded.starting_distance_m, "
            "starting_time_s=excluded.starting_time_s, last_reset=excluded.last_reset, "
            "remote_id=excluded.remote_id, updated_at=excluded.updated_at"));
        rq.addBindValue(rid);
        rq.addBindValue(id);
        rq.addBindValue(r.value(QStringLiteral("name")).toString());
        rq.addBindValue(r.value(QStringLiteral("distance")).toDouble());
        rq.addBindValue(r.value(QStringLiteral("time")).toDouble());
        rq.addBindValue(r.value(QStringLiteral("days")).toInt());
        rq.addBindValue(r.value(QStringLiteral("activities")).toInt());
        rq.addBindValue(r.value(QStringLiteral("starting_distance")).toDouble());
        rq.addBindValue(r.value(QStringLiteral("starting_time")).toDouble());
        rq.addBindValue(dt.isValid() ? dt.toMSecsSinceEpoch() : 0);
        rq.addBindValue(rid);   // remote_id = id (intervals-sourced)
        rq.addBindValue(QDateTime::currentMSecsSinceEpoch());
        rq.exec();
    }
}

void GearService::loadFromDb()
{
    m_gears.clear();
    m_assignments.clear();
    m_dueCount = 0;
    m_soonCount = 0;
    if (!m_db.isOpen()) {
        emit gearsChanged();
        return;
    }
    const qint64 now = QDateTime::currentMSecsSinceEpoch();

    QSqlQuery aq(QStringLiteral("SELECT sport, gear_id FROM gear_assignment"), m_db);
    while (aq.next())
        m_assignments.insert(aq.value(0).toString(), aq.value(1).toString());

    m_exceptions.clear();
    QSqlQuery eq(QStringLiteral("SELECT sport, country, radius_km, gear_id FROM gear_exception"), m_db);
    while (eq.next()) {
        QVariantMap ex;
        ex.insert(QStringLiteral("country"), eq.value(1).toString());
        ex.insert(QStringLiteral("radiusKm"), eq.value(2).toDouble());
        ex.insert(QStringLiteral("gearId"), eq.value(3).toString());
        m_exceptions.insert(eq.value(0).toString(), ex);
    }

    QSqlQuery q(QStringLiteral(
        "SELECT id, remote_id, parent_id, name, type, component, distance_m, time_s, retired, "
        "starting_distance_m, starting_time_s, baseline_at "
        "FROM gear WHERE deleted IS NULL OR deleted = 0 ORDER BY retired ASC, name ASC"), m_db);
    while (q.next()) {
        const QString id = q.value(QStringLiteral("id")).toString();
        const qint64 baselineAt = q.value(QStringLiteral("baseline_at")).toLongLong();
        // The mileage baseline: the manually-entered starting number when the user has set one
        // (baseline_at > 0), otherwise the intervals.icu total as before. Rides recorded AFTER the
        // baseline moment are added on top (below), so a typed number is never lost or double-counted.
        const double distanceM = baselineAt > 0
            ? q.value(QStringLiteral("starting_distance_m")).toDouble()
            : q.value(QStringLiteral("distance_m")).toDouble();
        const double timeS = baselineAt > 0
            ? q.value(QStringLiteral("starting_time_s")).toDouble()
            : q.value(QStringLiteral("time_s")).toDouble();

        QVariantList reminders;
        QSqlQuery rq(m_db);
        rq.prepare(QStringLiteral(
            "SELECT id, name, distance_m, time_s, days, activities, starting_distance_m, "
            "starting_time_s, last_reset FROM gear_reminder "
            "WHERE gear_id = ? AND (deleted IS NULL OR deleted = 0)"));
        rq.addBindValue(id);
        rq.exec();
        while (rq.next()) {
            const double pct = reminderPercent(rq, distanceM, timeS, now);
            if (pct >= 100.0) ++m_dueCount;
            else if (pct >= 90.0) ++m_soonCount;
            QVariantMap rm;
            rm.insert(QStringLiteral("id"), rq.value(QStringLiteral("id")).toString());
            rm.insert(QStringLiteral("name"), rq.value(QStringLiteral("name")).toString());
            rm.insert(QStringLiteral("label"), reminderLabel(rq));
            rm.insert(QStringLiteral("percent"), qRound(pct));
            rm.insert(QStringLiteral("due"), pct >= 100.0);
            rm.insert(QStringLiteral("soon"), pct >= 90.0 && pct < 100.0);
            reminders.append(rm);
        }

        QVariantMap gm;
        gm.insert(QStringLiteral("id"), id);
        gm.insert(QStringLiteral("parentId"), q.value(QStringLiteral("parent_id")).toString());
        gm.insert(QStringLiteral("name"), q.value(QStringLiteral("name")).toString());
        gm.insert(QStringLiteral("type"), q.value(QStringLiteral("type")).toString());
        gm.insert(QStringLiteral("component"), q.value(QStringLiteral("component")).toInt() != 0);
        gm.insert(QStringLiteral("topLevel"), isTopLevel(q.value(QStringLiteral("type")).toString()));
        // Rides attributed to this gear, added on top of the baseline. When a manual baseline is
        // set, count only rides recorded AFTER the moment it was set (activity_key is the move's
        // start time), so rides already included in the typed number aren't double-counted. An
        // unparseable/blank key is counted (safe default). With no manual baseline, sum them all.
        double addedM = 0.0;
        QSqlQuery aq2(m_db);
        aq2.prepare(QStringLiteral("SELECT activity_key, distance_m FROM activity_gear WHERE gear_id = ?"));
        aq2.addBindValue(id);
        aq2.exec();
        while (aq2.next()) {
            if (baselineAt > 0) {
                const QDateTime dt = QDateTime::fromString(aq2.value(0).toString(), Qt::ISODate);
                if (dt.isValid() && dt.toMSecsSinceEpoch() < baselineAt)
                    continue;   // ride predates the baseline - already in the typed number
            }
            addedM += aq2.value(1).toDouble();
        }

        gm.insert(QStringLiteral("distanceKm"), qRound((distanceM + addedM) / 1000.0));
        gm.insert(QStringLiteral("baselineKm"), qRound(distanceM / 1000.0));
        gm.insert(QStringLiteral("addedKm"), qRound(addedM / 1000.0));
        gm.insert(QStringLiteral("retired"), q.value(QStringLiteral("retired")).toInt() != 0);
        gm.insert(QStringLiteral("reminders"), reminders);
        m_gears.append(gm);
    }
    emit gearsChanged();
}

// ============================================================================================
// Sommet Sync for gear (#SYNC-4b): mirror the local gear rows to the user's own self-hosted store
// (sync-server/sync.php), keyed by gear id, last-writer-wins by updated_at. Reuses the activity
// sync's config (connections/sommet_sync/{url,token}) and a per-collection cursor. Two-way:
// pull remote gear (upsert newer), then push all local gear + tombstones. Bikes/shoes created on
// one device thus appear on the others and survive dropping intervals.icu.
// NOTE (step): gear *edits* still go through intervals today (write-through), which bumps
// updated_at via storeGear, so pushes carry the latest. Local-only create/edit without intervals
// is a later step; tombstones flow once a gear's `deleted` flag is set.
// ============================================================================================

void GearService::setGearBaseline(const QString &id, double km, double hours)
{
    if (!m_db.isOpen())
        return;
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    QSqlQuery q(m_db);
    q.prepare(QStringLiteral(
        "UPDATE gear SET starting_distance_m = ?, starting_time_s = ?, baseline_at = ?, "
        "updated_at = ? WHERE id = ?"));
    q.addBindValue(km * 1000.0);
    q.addBindValue(hours * 3600.0);
    q.addBindValue(now);
    q.addBindValue(now);
    q.addBindValue(id);
    q.exec();
    loadFromDb();
    sommetGearSyncNow();   // push the new baseline to the shared store right away
}

void GearService::sommetGearSyncNow()
{
    const QSettings s;
    if (s.value(QStringLiteral("connections/sommet_sync/url")).toString().isEmpty()
        || s.value(QStringLiteral("connections/sommet_sync/token")).toString().isEmpty())
        return;                       // not configured - no-op
    if (m_sommetGearBusy)
        return;
    if (!m_db.isOpen())
        openDatabase();
    m_sommetGearBusy = true;
    sommetGearPull();
}

void GearService::sommetGearPull()
{
    const QSettings s;
    QUrl u(s.value(QStringLiteral("connections/sommet_sync/url")).toString());
    const QString token = s.value(QStringLiteral("connections/sommet_sync/token")).toString();
    const qint64 since = s.value(QStringLiteral("connections/sommet_sync/gearLastPull")).toLongLong();
    QUrlQuery q;
    q.addQueryItem(QStringLiteral("c"), QStringLiteral("gear"));
    q.addQueryItem(QStringLiteral("since"), QString::number(since));
    u.setQuery(q);
    QNetworkRequest req(u);
    req.setRawHeader("X-Sommet-Token", token.toUtf8());
    QNetworkReply *reply = m_net.get(req);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            m_sommetGearBusy = false;
            return;                   // unreachable - leave local untouched, retry next trigger
        }
        const QJsonObject root = QJsonDocument::fromJson(reply->readAll()).object();
        int changed = 0;

        // Remote tombstones: mark the local gear deleted (filtered out of the list on load).
        for (const auto &d : root.value(QStringLiteral("deleted")).toArray()) {
            const QString id = d.toString();
            if (id.isEmpty())
                continue;
            QSqlQuery del(m_db);
            del.prepare(QStringLiteral("UPDATE gear SET deleted = 1 WHERE id = ?"));
            del.addBindValue(id);
            del.exec();
            changed++;
        }

        // Remote gear: upsert the row when the remote copy is newer (last-writer-wins).
        for (const auto &rv : root.value(QStringLiteral("records")).toArray()) {
            const QJsonObject r = rv.toObject();
            const QString id = r.value(QStringLiteral("uid")).toString().isEmpty()
                ? r.value(QStringLiteral("id")).toString()
                : r.value(QStringLiteral("uid")).toString();
            if (id.isEmpty())
                continue;
            const qint64 remoteUpdated = r.value(QStringLiteral("updated_at")).toInteger();
            QSqlQuery sel(m_db);
            sel.prepare(QStringLiteral("SELECT updated_at FROM gear WHERE id = ?"));
            sel.addBindValue(id);
            if (sel.exec() && sel.next() && sel.value(0).toLongLong() >= remoteUpdated)
                continue;             // ours is newer or equal
            QSqlQuery up(m_db);
            up.prepare(QStringLiteral(
                "INSERT INTO gear (id, remote_id, parent_id, name, type, component, distance_m, "
                " time_s, retired, component_ids, starting_distance_m, starting_time_s, baseline_at, "
                " updated_at, deleted) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET remote_id=excluded.remote_id, "
                "parent_id=excluded.parent_id, name=excluded.name, type=excluded.type, "
                "component=excluded.component, distance_m=excluded.distance_m, "
                "time_s=excluded.time_s, retired=excluded.retired, "
                "component_ids=excluded.component_ids, starting_distance_m=excluded.starting_distance_m, "
                "starting_time_s=excluded.starting_time_s, baseline_at=excluded.baseline_at, "
                "updated_at=excluded.updated_at, deleted=excluded.deleted"));
            up.addBindValue(id);
            up.addBindValue(r.value(QStringLiteral("remote_id")).toString());
            up.addBindValue(r.value(QStringLiteral("parent_id")).toString());
            up.addBindValue(r.value(QStringLiteral("name")).toString());
            up.addBindValue(r.value(QStringLiteral("type")).toString());
            up.addBindValue(r.value(QStringLiteral("component")).toInt());
            up.addBindValue(r.value(QStringLiteral("distance_m")).toDouble());
            up.addBindValue(r.value(QStringLiteral("time_s")).toDouble());
            up.addBindValue(r.value(QStringLiteral("retired")).toInt());
            up.addBindValue(r.value(QStringLiteral("component_ids")).toString());
            up.addBindValue(r.value(QStringLiteral("starting_distance_m")).toDouble());
            up.addBindValue(r.value(QStringLiteral("starting_time_s")).toDouble());
            up.addBindValue((qint64)r.value(QStringLiteral("baseline_at")).toInteger());
            up.addBindValue(remoteUpdated);
            up.addBindValue(r.value(QStringLiteral("deleted")).toInt());
            up.exec();
            changed++;
        }

        const qint64 now = root.value(QStringLiteral("now")).toInteger();
        if (now > 0)
            QSettings().setValue(QStringLiteral("connections/sommet_sync/gearLastPull"), now);
        if (changed > 0)
            loadFromDb();             // reflect pulled gear in the UI
        sommetGearPushAll();
    });
}

void GearService::sommetGearPushAll()
{
    const QSettings s;
    QUrl u(s.value(QStringLiteral("connections/sommet_sync/url")).toString());
    QUrlQuery cq;
    cq.addQueryItem(QStringLiteral("c"), QStringLiteral("gear"));
    u.setQuery(cq);
    QNetworkRequest req(u);
    req.setRawHeader("X-Sommet-Token",
                     s.value(QStringLiteral("connections/sommet_sync/token")).toString().toUtf8());
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));

    QJsonArray records;
    QJsonArray deleted;
    QSqlQuery q(QStringLiteral(
        "SELECT id, remote_id, parent_id, name, type, component, distance_m, time_s, retired, "
        "component_ids, starting_distance_m, starting_time_s, baseline_at, updated_at, deleted "
        "FROM gear"), m_db);
    while (q.next()) {
        const QString id = q.value(0).toString();
        if (id.isEmpty())
            continue;
        if (q.value(14).toInt() == 1) { deleted.append(id); continue; }   // tombstone
        QJsonObject r;
        r.insert(QStringLiteral("uid"), id);
        r.insert(QStringLiteral("id"), id);
        r.insert(QStringLiteral("remote_id"), q.value(1).toString());
        r.insert(QStringLiteral("parent_id"), q.value(2).toString());
        r.insert(QStringLiteral("name"), q.value(3).toString());
        r.insert(QStringLiteral("type"), q.value(4).toString());
        r.insert(QStringLiteral("component"), q.value(5).toInt());
        r.insert(QStringLiteral("distance_m"), q.value(6).toDouble());
        r.insert(QStringLiteral("time_s"), q.value(7).toDouble());
        r.insert(QStringLiteral("retired"), q.value(8).toInt());
        r.insert(QStringLiteral("component_ids"), q.value(9).toString());
        r.insert(QStringLiteral("starting_distance_m"), q.value(10).toDouble());
        r.insert(QStringLiteral("starting_time_s"), q.value(11).toDouble());
        r.insert(QStringLiteral("baseline_at"), QJsonValue(q.value(12).toLongLong()));
        r.insert(QStringLiteral("updated_at"), QJsonValue(q.value(13).toLongLong()));
        records.append(r);
    }

    QJsonObject body;
    body.insert(QStringLiteral("records"), records);
    body.insert(QStringLiteral("deleted"), deleted);
    QNetworkReply *reply = m_net.post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        sommetReminderPull();   // gear done -> now sync the reminders, then finish
    });
}

void GearService::sommetReminderPull()
{
    const QSettings s;
    QUrl u(s.value(QStringLiteral("connections/sommet_sync/url")).toString());
    const QString token = s.value(QStringLiteral("connections/sommet_sync/token")).toString();
    const qint64 since = s.value(QStringLiteral("connections/sommet_sync/reminderLastPull")).toLongLong();
    QUrlQuery q;
    q.addQueryItem(QStringLiteral("c"), QStringLiteral("gear_reminder"));
    q.addQueryItem(QStringLiteral("since"), QString::number(since));
    u.setQuery(q);
    QNetworkRequest req(u);
    req.setRawHeader("X-Sommet-Token", token.toUtf8());
    QNetworkReply *reply = m_net.get(req);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            m_sommetGearBusy = false;
            return;
        }
        const QJsonObject root = QJsonDocument::fromJson(reply->readAll()).object();
        int changed = 0;
        for (const auto &d : root.value(QStringLiteral("deleted")).toArray()) {
            const QString id = d.toString();
            if (id.isEmpty()) continue;
            QSqlQuery del(m_db);
            del.prepare(QStringLiteral("UPDATE gear_reminder SET deleted = 1 WHERE id = ?"));
            del.addBindValue(id);
            del.exec();
            changed++;
        }
        for (const auto &rv : root.value(QStringLiteral("records")).toArray()) {
            const QJsonObject r = rv.toObject();
            const QString id = r.value(QStringLiteral("uid")).toString().isEmpty()
                ? r.value(QStringLiteral("id")).toString()
                : r.value(QStringLiteral("uid")).toString();
            if (id.isEmpty()) continue;
            const qint64 remoteUpdated = r.value(QStringLiteral("updated_at")).toInteger();
            QSqlQuery sel(m_db);
            sel.prepare(QStringLiteral("SELECT updated_at FROM gear_reminder WHERE id = ?"));
            sel.addBindValue(id);
            if (sel.exec() && sel.next() && sel.value(0).toLongLong() >= remoteUpdated)
                continue;
            QSqlQuery up(m_db);
            up.prepare(QStringLiteral(
                "INSERT INTO gear_reminder (id, gear_id, name, distance_m, time_s, days, activities, "
                " starting_distance_m, starting_time_s, last_reset, remote_id, updated_at, deleted) "
                "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?) "
                "ON CONFLICT(id) DO UPDATE SET gear_id=excluded.gear_id, name=excluded.name, "
                "distance_m=excluded.distance_m, time_s=excluded.time_s, days=excluded.days, "
                "activities=excluded.activities, starting_distance_m=excluded.starting_distance_m, "
                "starting_time_s=excluded.starting_time_s, last_reset=excluded.last_reset, "
                "remote_id=excluded.remote_id, updated_at=excluded.updated_at, deleted=excluded.deleted"));
            up.addBindValue(id);
            up.addBindValue(r.value(QStringLiteral("gear_id")).toString());
            up.addBindValue(r.value(QStringLiteral("name")).toString());
            up.addBindValue(r.value(QStringLiteral("distance_m")).toDouble());
            up.addBindValue(r.value(QStringLiteral("time_s")).toDouble());
            up.addBindValue(r.value(QStringLiteral("days")).toInt());
            up.addBindValue(r.value(QStringLiteral("activities")).toInt());
            up.addBindValue(r.value(QStringLiteral("starting_distance_m")).toDouble());
            up.addBindValue(r.value(QStringLiteral("starting_time_s")).toDouble());
            up.addBindValue((qint64)r.value(QStringLiteral("last_reset")).toInteger());
            up.addBindValue(r.value(QStringLiteral("remote_id")).toString());
            up.addBindValue(remoteUpdated);
            up.addBindValue(r.value(QStringLiteral("deleted")).toInt());
            up.exec();
            changed++;
        }
        const qint64 now = root.value(QStringLiteral("now")).toInteger();
        if (now > 0)
            QSettings().setValue(QStringLiteral("connections/sommet_sync/reminderLastPull"), now);
        if (changed > 0)
            loadFromDb();
        sommetReminderPushAll();
    });
}

void GearService::sommetReminderPushAll()
{
    const QSettings s;
    QUrl u(s.value(QStringLiteral("connections/sommet_sync/url")).toString());
    QUrlQuery cq;
    cq.addQueryItem(QStringLiteral("c"), QStringLiteral("gear_reminder"));
    u.setQuery(cq);
    QNetworkRequest req(u);
    req.setRawHeader("X-Sommet-Token",
                     s.value(QStringLiteral("connections/sommet_sync/token")).toString().toUtf8());
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));

    QJsonArray records, deleted;
    QSqlQuery q(QStringLiteral(
        "SELECT id, gear_id, name, distance_m, time_s, days, activities, starting_distance_m, "
        "starting_time_s, last_reset, remote_id, updated_at, deleted FROM gear_reminder"), m_db);
    while (q.next()) {
        const QString id = q.value(0).toString();
        if (id.isEmpty()) continue;
        if (q.value(12).toInt() == 1) { deleted.append(id); continue; }
        QJsonObject r;
        r.insert(QStringLiteral("uid"), id);
        r.insert(QStringLiteral("id"), id);
        r.insert(QStringLiteral("gear_id"), q.value(1).toString());
        r.insert(QStringLiteral("name"), q.value(2).toString());
        r.insert(QStringLiteral("distance_m"), q.value(3).toDouble());
        r.insert(QStringLiteral("time_s"), q.value(4).toDouble());
        r.insert(QStringLiteral("days"), q.value(5).toInt());
        r.insert(QStringLiteral("activities"), q.value(6).toInt());
        r.insert(QStringLiteral("starting_distance_m"), q.value(7).toDouble());
        r.insert(QStringLiteral("starting_time_s"), q.value(8).toDouble());
        r.insert(QStringLiteral("last_reset"), QJsonValue(q.value(9).toLongLong()));
        r.insert(QStringLiteral("remote_id"), q.value(10).toString());
        r.insert(QStringLiteral("updated_at"), QJsonValue(q.value(11).toLongLong()));
        records.append(r);
    }
    QJsonObject body;
    body.insert(QStringLiteral("records"), records);
    body.insert(QStringLiteral("deleted"), deleted);
    QNetworkReply *reply = m_net.post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        m_sommetGearBusy = false;   // whole gear+reminder sync done
    });
}
