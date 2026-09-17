#pragma once

#include <QObject>
#include <QQmlEngine>
#include <QSqlDatabase>
#include <QVariantList>
#include <QVariantMap>

// Race planning service (foundation phase) — owns race.db for persisting user-created race events
// and plans. Mirrors GearService's SQLite pattern but with simpler CRUD ops for now.
//
// A RaceEvent: name, event_type (BRM/ultra/other), start datetime, route (GPX or points list),
// optional target finish time, zero-or-more cutoffs (control points with time limits).
// A RacePlan: references a RaceEvent + athlete/bike inputs + baseline output (distance/ETA/moving time).
//
// This is the foundation: create/list/delete operations only. Timeline UI, weather integration,
// what-if scenarios, and the real performance model come later.
class RaceService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    // List of saved race events: [{id, name, event_type, start_dt, ...}]
    Q_PROPERTY(QVariantList events READ events NOTIFY eventsChanged)

public:
    explicit RaceService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    QString lastError() const { return m_lastError; }
    QVariantList events() const { return m_events; }

    // Create a new race event from a baseline plan JSON.
    // Input: {event: {...}, athlete: {...}, bike: {...}, plan: {...}}
    // Persists both the event and the plan, returns the new event ID.
    Q_INVOKABLE QString createEvent(const QVariantMap &eventData);

    // List all saved events.
    Q_INVOKABLE void listEvents();

    // Get a specific event + its plan by ID.
    Q_INVOKABLE QVariantMap getEvent(const QString &id) const;

    // Delete an event and its associated plan.
    Q_INVOKABLE void deleteEvent(const QString &id);

signals:
    void loadingChanged();
    void lastErrorChanged();
    void eventsChanged();
    void eventCreated(const QString &id);
    void eventDeleted(const QString &id);

private:
    void openDatabase();
    void loadFromDb();
    void setLoading(bool v);
    void setLastError(const QString &e);
    QString generateId();

    QSqlDatabase m_db;
    QVariantList m_events;
    bool m_loading = false;
    QString m_lastError;
};
