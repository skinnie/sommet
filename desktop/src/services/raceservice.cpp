#include "raceservice.h"

#include <QDateTime>
#include <QDir>
#include <QJsonDocument>
#include <QJsonObject>
#include <QStandardPaths>
#include <QSqlError>
#include <QSqlQuery>
#include <QUuid>

RaceService::RaceService(QObject *parent) : QObject(parent)
{
    openDatabase();
    loadFromDb();
}

void RaceService::openDatabase()
{
    const auto dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(dir);
    m_db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), QStringLiteral("race"));
    m_db.setDatabaseName(dir + QStringLiteral("/race.db"));

    if (!m_db.open()) {
        setLastError(QStringLiteral("Cannot open race.db: %1").arg(m_db.lastError().text()));
        return;
    }

    // Create tables if they don't exist.
    QSqlQuery q(m_db);
    if (!q.exec(QStringLiteral(
            "CREATE TABLE IF NOT EXISTS race_event ("
            "  id TEXT PRIMARY KEY,"
            "  name TEXT NOT NULL,"
            "  event_type TEXT,"
            "  start_dt TEXT,"
            "  gpx TEXT,"
            "  points TEXT,"
            "  target_finish_dt TEXT,"
            "  cutoffs TEXT,"
            "  created_at TEXT,"
            "  updated_at TEXT"
            ")"))) {
        setLastError(QStringLiteral("Cannot create race_event table: %1").arg(q.lastError().text()));
        return;
    }

    if (!q.exec(QStringLiteral(
            "CREATE TABLE IF NOT EXISTS race_plan ("
            "  id TEXT PRIMARY KEY,"
            "  event_id TEXT NOT NULL,"
            "  athlete TEXT,"
            "  bike TEXT,"
            "  provisional BOOLEAN,"
            "  distance_m REAL,"
            "  finish_eta_dt TEXT,"
            "  moving_time_s REAL,"
            "  required_avg_speed_kmh REAL,"
            "  summary TEXT,"
            "  created_at TEXT,"
            "  FOREIGN KEY(event_id) REFERENCES race_event(id)"
            ")"))) {
        setLastError(QStringLiteral("Cannot create race_plan table: %1").arg(q.lastError().text()));
    }
}

void RaceService::loadFromDb()
{
    m_events.clear();

    QSqlQuery q(QStringLiteral("SELECT id, name, event_type, start_dt, created_at FROM race_event ORDER BY created_at DESC"), m_db);
    if (!q.exec()) {
        setLastError(QStringLiteral("Cannot query race_event: %1").arg(q.lastError().text()));
        return;
    }

    while (q.next()) {
        QVariantMap event;
        event["id"] = q.value(0).toString();
        event["name"] = q.value(1).toString();
        event["event_type"] = q.value(2).toString();
        event["start_dt"] = q.value(3).toString();
        event["created_at"] = q.value(4).toString();
        m_events.append(event);
    }

    emit eventsChanged();
}

QString RaceService::generateId()
{
    return QUuid::createUuid().toString(QUuid::WithoutBraces);
}

QString RaceService::createEvent(const QVariantMap &eventData)
{
    setLoading(true);
    setLastError(QString());

    if (!eventData.contains("event")) {
        setLastError("Missing 'event' in eventData");
        setLoading(false);
        return QString();
    }

    const auto eventJson = eventData["event"].toMap();
    const auto athleteJson = eventData["athlete"].toMap();
    const auto bikeJson = eventData["bike"].toMap();
    const auto planJson = eventData["plan"].toMap();

    const auto eventId = generateId();
    const auto planId = generateId();
    const auto now = QDateTime::currentDateTime().toString(Qt::ISODate);

    QSqlQuery q(m_db);

    // Insert the event.
    q.prepare(QStringLiteral(
        "INSERT INTO race_event (id, name, event_type, start_dt, gpx, points, target_finish_dt, cutoffs, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"));
    q.addBindValue(eventId);
    q.addBindValue(eventJson["name"].toString());
    q.addBindValue(eventJson["event_type"].toString());
    q.addBindValue(eventJson["start_dt"].toString());
    q.addBindValue(eventJson["gpx"].toString());
    q.addBindValue(QJsonDocument::fromVariant(eventJson["points"]).toJson(QJsonDocument::Compact));
    q.addBindValue(eventJson["target_finish_dt"].toString());
    q.addBindValue(QJsonDocument::fromVariant(eventJson["cutoffs"]).toJson(QJsonDocument::Compact));
    q.addBindValue(now);
    q.addBindValue(now);

    if (!q.exec()) {
        setLastError(QStringLiteral("Cannot insert event: %1").arg(q.lastError().text()));
        setLoading(false);
        return QString();
    }

    // Insert the plan.
    q.prepare(QStringLiteral(
        "INSERT INTO race_plan (id, event_id, athlete, bike, provisional, distance_m, finish_eta_dt, moving_time_s, required_avg_speed_kmh, summary, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"));
    q.addBindValue(planId);
    q.addBindValue(eventId);
    q.addBindValue(QJsonDocument::fromVariant(athleteJson).toJson(QJsonDocument::Compact));
    q.addBindValue(QJsonDocument::fromVariant(bikeJson).toJson(QJsonDocument::Compact));
    q.addBindValue(planJson["provisional"].toBool());
    q.addBindValue(planJson["distance_m"].toDouble());
    q.addBindValue(planJson["finish_eta_dt"].toString());
    q.addBindValue(planJson["moving_time_s"].toDouble());
    q.addBindValue(planJson["required_avg_speed_kmh"].toDouble());
    q.addBindValue(QJsonDocument::fromVariant(planJson["summary"]).toJson(QJsonDocument::Compact));
    q.addBindValue(now);

    if (!q.exec()) {
        setLastError(QStringLiteral("Cannot insert plan: %1").arg(q.lastError().text()));
        // Rollback: delete the event we just inserted.
        QSqlQuery del(m_db);
        del.prepare("DELETE FROM race_event WHERE id = ?");
        del.addBindValue(eventId);
        del.exec();
        setLoading(false);
        return QString();
    }

    loadFromDb();
    setLoading(false);
    emit eventCreated(eventId);
    return eventId;
}

void RaceService::listEvents()
{
    loadFromDb();
}

QVariantMap RaceService::getEvent(const QString &id) const
{
    QVariantMap result;

    QSqlQuery q(m_db);
    q.prepare(QStringLiteral("SELECT * FROM race_event WHERE id = ?"));
    q.addBindValue(id);

    if (!q.exec() || !q.next()) {
        return result;
    }

    result["id"] = q.value(0).toString();
    result["name"] = q.value(1).toString();
    result["event_type"] = q.value(2).toString();
    result["start_dt"] = q.value(3).toString();
    result["gpx"] = q.value(4).toString();
    // points, target_finish_dt, cutoffs are JSON strings; parse them for the QML side.
    result["points"] = QJsonDocument::fromJson(q.value(5).toByteArray()).toVariant();
    result["target_finish_dt"] = q.value(6).toString();
    result["cutoffs"] = QJsonDocument::fromJson(q.value(7).toByteArray()).toVariant();

    return result;
}

void RaceService::deleteEvent(const QString &id)
{
    setLoading(true);
    setLastError(QString());

    QSqlQuery q(m_db);

    // Delete associated plans first.
    q.prepare(QStringLiteral("DELETE FROM race_plan WHERE event_id = ?"));
    q.addBindValue(id);
    if (!q.exec()) {
        setLastError(QStringLiteral("Cannot delete plans: %1").arg(q.lastError().text()));
        setLoading(false);
        return;
    }

    // Delete the event.
    q.prepare(QStringLiteral("DELETE FROM race_event WHERE id = ?"));
    q.addBindValue(id);
    if (!q.exec()) {
        setLastError(QStringLiteral("Cannot delete event: %1").arg(q.lastError().text()));
        setLoading(false);
        return;
    }

    loadFromDb();
    setLoading(false);
    emit eventDeleted(id);
}

void RaceService::setLoading(bool v)
{
    if (m_loading == v)
        return;
    m_loading = v;
    emit loadingChanged();
}

void RaceService::setLastError(const QString &e)
{
    if (m_lastError == e)
        return;
    m_lastError = e;
    emit lastErrorChanged();
}
