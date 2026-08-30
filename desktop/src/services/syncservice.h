#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QVariantMap>

#include <functional>

// Two-watch "freefly" sync. A thin client over backend/server.py's /api/sync/* - it invents no
// watch-write mechanism of its own: a snapshot is the same per-category read the individual
// pages do (settings_write.py today), and applying a change is the same 0x1101 settings write
// the Watch Settings page already uses. Only one watch connects at a time over the cable, so
// the flow is sequential: snapshot A, swap the cable, snapshot B, preview the diff, swap to the
// target watch, apply. The backend re-checks the connected serial before every write, so a plan
// built for one watch is refused against another (mismatchText is then set).
//
// Kailash "countries visited" is deliberately NOT a category: it is a firmware-computed query
// object with no writable region - see docs/explanation/kailash-history-write-probe.md.
class SyncService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    // The watch plugged in right now: {model, serial, displayName, fw_version}. Empty when none.
    Q_PROPERTY(QVariantMap connected READ connected NOTIFY stateChanged)
    // Each slot's summary (identity + per-category counts), or empty until snapshotted.
    Q_PROPERTY(QVariantMap slotA READ slotA NOTIFY stateChanged)
    Q_PROPERTY(QVariantMap slotB READ slotB NOTIFY stateChanged)
    // The last computed plan: {mode, direction, source, target, categories:[...], changeCount}.
    Q_PROPERTY(QVariantMap plan READ plan NOTIFY planChanged)
    Q_PROPERTY(QString lastActionText READ lastActionText NOTIFY lastActionChanged)
    Q_PROPERTY(bool lastActionOk READ lastActionOk NOTIFY lastActionChanged)
    // Set when an apply is refused because the plugged watch is not the plan's target; cleared
    // on the next successful state refresh. The page shows it as a "plug in the target" prompt.
    Q_PROPERTY(QString mismatchText READ mismatchText NOTIFY mismatchChanged)

public:
    explicit SyncService(QObject *parent = nullptr);

    bool busy() const { return m_busy; }
    QVariantMap connected() const { return m_connected; }
    QVariantMap slotA() const { return m_slotA; }
    QVariantMap slotB() const { return m_slotB; }
    QVariantMap plan() const { return m_plan; }
    QString lastActionText() const { return m_lastActionText; }
    bool lastActionOk() const { return m_lastActionOk; }
    QString mismatchText() const { return m_mismatchText; }

    // Re-read the connected watch and both stored slots.
    Q_INVOKABLE void refreshState();
    // Snapshot the CONNECTED watch into slot "A" or "B".
    Q_INVOKABLE void snapshot(const QString &slot);
    // Compute (dry-run) the diff for the given mode/direction. direction is "AtoB" or "BtoA".
    Q_INVOKABLE void buildPlan(const QString &mode, const QString &direction);
    // Apply the plan to the connected watch. confirm=false re-previews; true writes for real.
    Q_INVOKABLE void apply(const QString &mode, const QString &direction, bool confirm);
    // Forget a stored slot ("A"/"B"), or both when slot is empty.
    Q_INVOKABLE void clearSlot(const QString &slot);

signals:
    void busyChanged();
    void stateChanged();
    void planChanged();
    void lastActionChanged();
    void mismatchChanged();

private:
    QNetworkAccessManager m_network;
    bool m_busy = false;
    QVariantMap m_connected;
    QVariantMap m_slotA;
    QVariantMap m_slotB;
    QVariantMap m_plan;
    QString m_lastActionText;
    bool m_lastActionOk = false;
    QString m_mismatchText;

    void setBusy(bool value);
    void postJson(const QString &path, const QVariantMap &body,
                  std::function<void(const QVariantMap &, bool ok)> onDone);
};
