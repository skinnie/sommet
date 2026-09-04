#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QVariantList>

// Daily health metrics for the Health page (André, 2026-08-24). MERGES intervals.icu and Garmin
// Connect (plus the Ambit3's own resting-HRV tests and manual entries) per metric, de-duplicated
// by date. On a same-day tie the priority is intervals < garmin < watch < manual (later wins):
// the watch's own 5+5 rMSSD outranks the cloud sources on a day it was measured, and a manual
// entry still wins over all. Metrics: resting HR, steps, HRV (rMSSD ms - same axis across all
// sources: intervals `hrv`, Garmin last-night avg, and our Ambit3 lying-phase RMSSD), body
// battery. Read-only against the cloud sources; watch and manual entries are local.
class HealthService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY changed)
    Q_PROPERTY(QString lastError READ lastError NOTIFY changed)
    Q_PROPERTY(bool needsLogin READ needsLogin NOTIFY changed)
    Q_PROPERTY(QVariantList rhr READ rhr NOTIFY changed)
    Q_PROPERTY(QVariantList steps READ steps NOTIFY changed)
    Q_PROPERTY(QVariantList hrv READ hrv NOTIFY changed)
    Q_PROPERTY(QVariantList bodyBattery READ bodyBattery NOTIFY changed)
    // Sleep is a single-SELECTED source (not merged): "intervals", "garmin", or "off" (for an
    // Ambit-only user with no sleep data). Persisted in QSettings health/sleepProvider.
    Q_PROPERTY(QVariantList sleep READ sleep NOTIFY changed)
    Q_PROPERTY(QString sleepProvider READ sleepProvider WRITE setSleepProvider NOTIFY changed)
    // HRV is split into two DELIBERATELY SEPARATE tracks, because they are different
    // measurements that must never share a line (André, 2026-08-25): `hrv` is the OVERNIGHT
    // value from a single chosen cloud source (hrvSource = "intervals" | "garmin", like
    // sleepProvider), and `hrvAmbit` is the Ambit3's own MORNING/spot rMSSD (from a 5+5 or
    // lie-still test), shown as its own coloured line and tracked against its own baseline.
    // ambitHrvEnabled toggles that second line independently of the overnight source.
    Q_PROPERTY(QVariantList hrvAmbit READ hrvAmbit NOTIFY changed)
    Q_PROPERTY(double latestHrvAmbit READ latestHrvAmbit NOTIFY changed)
    Q_PROPERTY(QString hrvSource READ hrvSource WRITE setHrvSource NOTIFY changed)
    Q_PROPERTY(bool ambitHrvEnabled READ ambitHrvEnabled WRITE setAmbitHrvEnabled NOTIFY changed)
    // COOSPO (BLE HR strap) morning-HRV feature toggle - independent of the Ambit3 one, so an
    // intervals.icu-only user sees neither, an Ambit user enables Ambit, a strap user enables this.
    Q_PROPERTY(bool coospoHrvEnabled READ coospoHrvEnabled WRITE setCoospoHrvEnabled NOTIFY changed)
    Q_PROPERTY(double latestRhr READ latestRhr NOTIFY changed)
    Q_PROPERTY(double latestSteps READ latestSteps NOTIFY changed)
    Q_PROPERTY(double latestHrv READ latestHrv NOTIFY changed)
    Q_PROPERTY(double latestBodyBattery READ latestBodyBattery NOTIFY changed)
    Q_PROPERTY(double latestSleep READ latestSleep NOTIFY changed)
    // One-tap "Install HRV app": installs the 5+5 HRV Suunto App onto an HRV sport mode via the
    // backend (/api/hrv/install). hrvInstalling drives the button's busy state; hrvInstallMessage
    // is the last result/error to show the user.
    Q_PROPERTY(bool hrvInstalling READ hrvInstalling NOTIFY changed)
    Q_PROPERTY(QString hrvInstallMessage READ hrvInstallMessage NOTIFY changed)
    // "Measure HRV now" from a BLE HR strap (COOSPO HW9): busy flag + result/error message.
    // A successful reading lands on the same Morning-HRV line as the watch (health/watchHrv).
    Q_PROPERTY(bool strapMeasuring READ strapMeasuring NOTIFY changed)
    Q_PROPERTY(QString strapMessage READ strapMessage NOTIFY changed)

public:
    explicit HealthService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    QString lastError() const { return m_lastError; }
    bool needsLogin() const { return m_needsLogin; }
    QVariantList rhr() const { return m_rhr; }
    QVariantList steps() const { return m_steps; }
    QVariantList hrv() const { return m_hrv; }
    QVariantList bodyBattery() const { return m_bodyBattery; }
    QVariantList sleep() const { return m_sleep; }
    QString sleepProvider() const;
    void setSleepProvider(const QString &p);
    QVariantList hrvAmbit() const { return m_hrvAmbit; }
    double latestHrvAmbit() const { return lastValue(m_hrvAmbit); }
    QString hrvSource() const;
    void setHrvSource(const QString &s);
    bool ambitHrvEnabled() const;
    void setAmbitHrvEnabled(bool on);
    bool coospoHrvEnabled() const;
    void setCoospoHrvEnabled(bool on);
    double latestRhr() const { return lastValue(m_rhr); }
    double latestSteps() const { return lastValue(m_steps); }
    double latestHrv() const { return lastValue(m_hrv); }
    double latestBodyBattery() const { return lastValue(m_bodyBattery); }
    double latestSleep() const { return lastValue(m_sleep); }

    bool hrvInstalling() const { return m_hrvInstalling; }
    QString hrvInstallMessage() const { return m_hrvInstallMessage; }
    bool strapMeasuring() const { return m_strapMeasuring; }
    QString strapMessage() const { return m_strapMessage; }

    Q_INVOKABLE void refresh(int days = 30);
    // Install the 5+5 HRV app onto an HRV sport mode (creating the mode if needed) via the backend.
    Q_INVOKABLE void installHrvApp();
    // Read a morning-HRV spot reading from a BLE HR strap (default the COOSPO HW9) for `seconds`,
    // then store its RMSSD on the Morning-HRV line. Backend does the BLE read (tools/hrv_strap.py).
    Q_INVOKABLE void readStrapHrv(int seconds = 120);
    // Manual daily reading; pass <=0 for a metric you're not entering.
    Q_INVOKABLE void addManualHealth(const QString &date, double restingHr, double hrvMs);

signals:
    void changed();

private:
    QNetworkAccessManager m_network;
    bool m_loading = false;
    bool m_needsLogin = false;
    int m_pending = 0;
    QString m_lastError;
    QVariantList m_rhr, m_steps, m_hrv, m_bodyBattery, m_sleep;   // merged / selected results
    QVariantList m_hrvAmbit;                                      // Ambit3 morning-HRV (own line)
    bool m_hrvInstalling = false;
    QString m_hrvInstallMessage;
    bool m_strapMeasuring = false;
    QString m_strapMessage;
    // Per-source buffers.
    QVariantList m_iRhr, m_iSteps, m_iHrv, m_iSleep;         // intervals (sleep from sleepSecs)
    QVariantList m_gRhr, m_gSteps, m_gHrv, m_gBattery, m_gSleep;  // garmin

    static double lastValue(const QVariantList &s);
    void setLoading(bool v);
    void fetchIntervals(int days);
    void fetchGarmin(int days);
    void fetchGarminSleep(int days);
    void oneSourceDone();
    void rebuild();
};
