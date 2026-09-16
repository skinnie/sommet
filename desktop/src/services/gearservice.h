#pragma once

#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QSqlDatabase>
#include <QVariantList>
#include <functional>

// Gear tracker (v3, desktop) — parity with the Android feature, import-first (André 2026-08-18:
// "get the info from intervals.icu… the aim is to ditch intervals in the future"). This service
// IMPORTS gear + components + maintenance reminders from intervals.icu (GET /gear, HTTP Basic
// API_KEY:<key>) and OWNS them in a local SQLite DB (gear.db), so the data survives without
// intervals. A component/part is child gear linked from its parent's `component_ids`; reminder
// due-ness is computed LOCALLY here (from each reminder's reset-baseline + the gear's distance),
// not read from intervals' percent_used. Schema is the one confirmed against a live GET /gear —
// see docs/reference/intervals-gear-schema.md.
//
// The intervals.icu credentials come from the same QSettings keys ConnectionsService writes
// (connections/intervals_icu/{athleteId,apiKey}); this service reads them directly rather than
// coupling to that singleton in C++.
class GearService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    Q_PROPERTY(bool connected READ connected NOTIFY gearsChanged)
    // Each entry: {id, remoteId, parentId, name, type, component (bool), distanceKm (int),
    // retired (bool), reminders: [{name, label, percent (int), due (bool), soon (bool)}]}
    Q_PROPERTY(QVariantList gears READ gears NOTIFY gearsChanged)
    // Maintenance summary for Home: how many reminders are due (>=100%) / soon (>=90%).
    Q_PROPERTY(int dueCount READ dueCount NOTIFY gearsChanged)
    Q_PROPERTY(int soonCount READ soonCount NOTIFY gearsChanged)
    // Default gear per decoded sport name (sportName -> gear id). Drives the picker's pre-select.
    Q_PROPERTY(QVariantMap assignments READ assignments NOTIFY gearsChanged)
    Q_PROPERTY(QVariantMap gearExceptions READ gearExceptions NOTIFY gearsChanged)

public:
    explicit GearService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    QString lastError() const { return m_lastError; }
    bool connected() const;
    QVariantList gears() const { return m_gears; }
    int dueCount() const { return m_dueCount; }
    int soonCount() const { return m_soonCount; }
    QVariantMap assignments() const { return m_assignments; }
    // Per-sport EXCEPTION to the default gear (André, 2026-08-18): "if country=Portugal and
    // activity=cycling => carrera". Not a rule engine - just one exception over the default:
    // when an activity's start GPS falls within `radiusKm` of the chosen country, use the
    // exception gear instead of the default. Map: sport -> { country, radiusKm, gearId }.
    QVariantMap gearExceptions() const { return m_exceptions; }
    // Countries offered in the exception dropdown, each { name, lat, lon } (centroid).
    Q_INVOKABLE QVariantList countries() const;

    // Local distance tally + manual per-activity gear (D2-a). All local — no network. The
    // activity key is a stable per-activity string (its start time). Gear distance shown =
    // imported intervals baseline + these locally-attributed activities.
    Q_INVOKABLE void setAssignment(const QString &sport, const QString &gearId);
    Q_INVOKABLE QString defaultGearForSport(const QString &sport) const;
    Q_INVOKABLE void setException(const QString &sport, const QString &country,
                                  double radiusKm, const QString &gearId);
    Q_INVOKABLE void clearException(const QString &sport);
    Q_INVOKABLE QVariantMap exceptionFor(const QString &sport) const;
    // Resolve the gear for an activity: the exception gear when (lat,lon) is within the
    // exception's geofence, else the sport default. Pass NaN lat/lon for a location-less
    // (indoor) activity - it always falls through to the default.
    Q_INVOKABLE QString gearForActivity(const QString &sport, double lat, double lon) const;
    Q_INVOKABLE void attributeActivity(const QString &key, const QString &gearId,
                                       double distanceM, double timeS);
    Q_INVOKABLE void clearActivity(const QString &key);
    Q_INVOKABLE QString activityGearId(const QString &key) const;

    // Pull-only import from intervals.icu into the local store, then rebuild gears[].
    Q_INVOKABLE void importFromIntervals();

    // Sommet Sync (#SYNC-4b): mirror gear to the user's OWN self-hosted store (sync-server/
    // sync.php), so bikes/shoes/parts appear on every device and outlive intervals.icu. Two-way,
    // last-writer-wins by updated_at, keyed by gear id (intervals-mirrored gear shares the same id
    // across devices, so this merges onto the same rows instead of duplicating). Reuses the same
    // QSettings config as the activity sync (connections/sommet_sync/{url,token}). No-op when
    // unconfigured; runs on the same triggers as the activity sync.
    Q_INVOKABLE void sommetGearSyncNow();

    // Editing (write-through: push to intervals.icu, then re-import to refresh). Parity with the
    // Android manager. `type` is a free-form gear type ("Bike"/"Shoes"/"Chain"/...).
    Q_INVOKABLE void addGear(const QString &name, const QString &type);
    Q_INVOKABLE void addComponent(const QString &parentId, const QString &name, const QString &type);
    Q_INVOKABLE void renameGear(const QString &id, const QString &name);
    Q_INVOKABLE void setRetired(const QString &id, bool retired);
    Q_INVOKABLE void removeGear(const QString &id);
    Q_INVOKABLE void addReminder(const QString &gearId, const QString &name,
                                 double km, double hours, int days, int activities);
    Q_INVOKABLE void removeReminder(const QString &gearId, const QString &reminderId);

    // Set a gear's manually-entered mileage baseline (km + optional hours), as of NOW. From this
    // moment, rides get added on top of the number you type - so it is never lost, and rides
    // already inside it are not double-counted (see loadFromDb). Local + synced (latest wins);
    // works with or without intervals.icu.
    Q_INVOKABLE void setGearBaseline(const QString &id, double km, double hours);

signals:
    void loadingChanged();
    void lastErrorChanged();
    void gearsChanged();
    void importFinished(int count);

private:
    void openDatabase();
    void loadFromDb();          // rebuild m_gears from the DB
    void storeGear(const QVariantMap &g, const QString &parentId);
    // Authenticated request to intervals.icu; onOk gets the parsed reply on success, errors are
    // routed to lastError. verb is GET/POST/PUT/DELETE; body is sent as JSON when non-empty.
    void send(const QByteArray &verb, const QString &path, const QJsonObject &body,
              std::function<void(const QJsonDocument &)> onOk);
    QString componentIdsJson(const QString &gearId) const; // parent's stored component_ids
    // Local-first (#SYNC-4): update one gear column + bump updated_at, refresh, and push to the
    // NAS - the local write that happens whether or not intervals.icu is connected.
    void localGearField(const QString &id, const QString &column, const QVariant &value);
    void setLoading(bool v);
    void setLastError(const QString &e);
    QString apiKey() const;
    QString athleteId() const;

    // Sommet gear sync internals (#SYNC-4b): pull-then-push against connections/sommet_sync.
    void sommetGearPull();
    void sommetGearPushAll();
    void sommetReminderPull();      // chained after the gear push: same for the gear_reminder table
    void sommetReminderPushAll();
    bool m_sommetGearBusy = false;

    QNetworkAccessManager m_net;
    QSqlDatabase m_db;
    QVariantList m_gears;
    QVariantMap m_assignments;
    QVariantMap m_exceptions;   // sport -> { country, radiusKm, gearId }
    int m_dueCount = 0;
    int m_soonCount = 0;
    bool m_loading = false;
    QString m_lastError;
};
