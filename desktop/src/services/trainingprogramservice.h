#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QUrl>
#include <QVariantList>
#include <QVariantMap>

// Training Program (2026-08-12, André: "movescount era called them training program. 2 on a
// screen on our app") - the from-scratch revival of Movescount's scheduled-workout feature.
// A plan is workouts placed on real calendar dates; tools/training_plan.py turns it into
// date-gated Suunto Apps (the watch's own clock, SUUNTO_DAYS_AFTER_1_1_2000, selects the
// day's workout - see that file's docstring for the whole mechanism and its live-compiler
// probing) and workout_install.py writes them. This service wraps backend/server.py's four
// /api/trainingprogram endpoints; plan authoring itself is fully offline (local JSON in
// ~/AmbitAppPlans), only compiling needs internet and only installing needs the watch.
class TrainingProgramService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    // Saved plans, newest-edited first. Each: {id, name, entries, updatedAt} - entries is
    // exactly the plan JSON's own list ({date, workout:{name, steps:[...]}}).
    Q_PROPERTY(QVariantList plans READ plans NOTIFY plansChanged)
    Q_PROPERTY(bool installing READ installing NOTIFY installingChanged)
    // Result of the last install() call. dryRun (confirm:false) compiles only and returns
    // the real packing - {ok, dryRun, apps:[{name, dates, binaryLength}]}; a confirmed
    // install returns {ok, installed:[...]} or {ok:false, error, installed:[how far it got]}.
    Q_PROPERTY(QVariantMap lastInstallResult READ lastInstallResult NOTIFY lastInstallResultChanged)

public:
    explicit TrainingProgramService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    QString lastError() const { return m_lastError; }
    QVariantList plans() const { return m_plans; }
    bool installing() const { return m_installing; }
    QVariantMap lastInstallResult() const { return m_lastInstallResult; }

    // GET /api/trainingprogram - local disk only, safe any time.
    Q_INVOKABLE void refreshPlans();

    // POST /api/trainingprogram - saves {name, entries} locally (same name overwrites, so
    // editing doesn't multiply copies). Emits planSaved(id) then refreshes the list.
    Q_INVOKABLE void savePlan(const QVariantMap &plan);

    // POST /api/trainingprogram/delete - removes one saved plan by its id.
    Q_INVOKABLE void deletePlan(const QString &planId);

    // POST /api/trainingprogram/install. confirm:false = compile-only preview (live
    // community compiler, internet, no watch touched) showing exactly which dates pack
    // into which app and each binary's size; confirm:true = the real sequential install
    // of every app onto one mode/display/field, the row then cycling through them the same
    // way SuuntoLink's own multi-shortcut rows do. Same "explicit UI action is the
    // confirmation" rule as every other write in this app.
    Q_INVOKABLE void install(const QVariantMap &plan, int mode, int display, int field,
                             bool confirm);

    // POST /api/intervals/workouts - pull the athlete's PLANNED workouts from intervals.icu in
    // [start, end] and hand them back as dated plan entries {date, mode, workout} via the
    // intervalsImported() signal (HR bands reconstructed from the athlete's zones, resolved to
    // the watch when it's on the cable). Read-only; nothing is written. Credentials come from the
    // caller (ConnectionsService) so this service stays unaware of where they're stored.
    Q_INVOKABLE void importFromIntervals(const QString &start, const QString &end,
                                         const QString &mode, const QString &athleteId,
                                         const QString &apiKey);

    // POST /api/trainingprogram/sync-calendar THEN /api/trainingprogram/planned-moves - one
    // "Sync to watch" action does both halves of what a Movescount sync did: (1) install the
    // plan as native guided workouts in each sport mode's WORKOUT menu, rotating by date
    // (tools/training_calendar.py), and (2) write the plan as NATIVE dated planned moves - the
    // "Today 1/2" card the watch shows in TIME mode -> [Next] (hardware-confirmed 2026-09-03,
    // tools/training_program.py). write:false is a real dry-run for both. Both results merge
    // into lastInstallResult (the rotation fields plus nativeCards/nativeCardError).
    Q_INVOKABLE void syncCalendar(const QVariantList &entries, bool write);

signals:
    void loadingChanged();
    void lastErrorChanged();
    void plansChanged();
    void installingChanged();
    void lastInstallResultChanged();
    void planSaved(const QString &planId);
    void intervalsImported(const QVariantList &entries, const QVariantList &skipped,
                           bool resolvedToWatch);

private:
    QNetworkAccessManager m_network;
    bool m_loading = false;
    QString m_lastError;
    QVariantList m_plans;
    bool m_installing = false;
    QVariantMap m_lastInstallResult;

    void setLoading(bool value);
    void setLastError(const QString &message);
    void setInstalling(bool value);

    // Second half of syncCalendar(): POST /api/trainingprogram/planned-moves and merge the
    // native-card result (count / error) into the rotation result already in m_lastInstallResult.
    void writePlannedMoves(const QVariantList &entries, bool write);

    static QUrl backendUrl(const QString &path);
};
