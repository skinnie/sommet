#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QVariantList>

// Body-weight & composition for the Weight page (André, 2026-08-24). MERGES every available
// source - intervals.icu, Garmin Connect (Index scale: weight + body composition), and manual
// entries - into one series, de-duplicated per day: when two sources have the same date, the
// reading with the MOST fields wins (a Garmin weigh-in with fat/muscle beats an intervals one
// with weight only; a manual entry you typed wins ties). Read-only against the cloud sources;
// manual entries are stored locally. More sources (BLE scales) can be added the same way.
class WeightService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY changed)
    Q_PROPERTY(bool connected READ connected NOTIFY changed)     // any source available
    Q_PROPERTY(QString lastError READ lastError NOTIFY changed)
    Q_PROPERTY(bool garminNeedsLogin READ garminNeedsLogin NOTIFY changed)
    Q_PROPERTY(QVariantList series READ series NOTIFY changed)    // merged, oldest->newest
    // Which provider(s) feed `series` (André, 2026-08-26: "there is no toggle to choose from
    // intervals.icu or garmin"). "all" (default) is the original behaviour - every source
    // merged, richest reading per day wins; "intervals" / "garmin" restrict it to that one
    // provider. Manual weigh-ins are ALWAYS kept whatever this is set to: they're typed by hand
    // in this app and belong to no provider, so filtering them out would silently discard the
    // user's own data. Persisted in QSettings like the other connection prefs.
    Q_PROPERTY(QString source READ source WRITE setSource NOTIFY changed)
    Q_PROPERTY(QVariantMap latest READ latest NOTIFY changed)
    Q_PROPERTY(bool hasBodyComp READ hasBodyComp NOTIFY changed)
    Q_PROPERTY(double latestWeightKg READ latestWeightKg NOTIFY changed)
    Q_PROPERTY(QString latestDate READ latestDate NOTIFY changed)
    Q_PROPERTY(double changeKg READ changeKg NOTIFY changed)

public:
    explicit WeightService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    bool connected() const;
    QString lastError() const { return m_lastError; }
    bool garminNeedsLogin() const { return m_garminNeedsLogin; }
    QVariantList series() const { return m_series; }
    QString source() const { return m_source; }
    void setSource(const QString &s);
    QVariantMap latest() const {
        return m_series.isEmpty() ? QVariantMap{} : m_series.last().toMap();
    }
    bool hasBodyComp() const { return latest().contains(QStringLiteral("bodyFatPct")); }
    double latestWeightKg() const;
    QString latestDate() const;
    double changeKg() const;

    // Re-pull every connected source (intervals + Garmin) and re-merge with manual entries.
    Q_INVOKABLE void refresh(int days = 365);
    // One-time Garmin Connect login (used from Settings and the Weight page).
    Q_INVOKABLE void garminLogin(const QString &email, const QString &password,
                                 const QString &mfa);
    // Add a manual weigh-in (date "YYYY-MM-DD", kg; bodyFatPct<=0 means "not given"). Stored
    // locally and merged in as the highest-priority source.
    Q_INVOKABLE void addManualWeight(const QString &date, double weightKg, double bodyFatPct);
    Q_INVOKABLE void removeManualWeight(const QString &date);

signals:
    void changed();
    void garminLoggedIn(const QString &email);

private:
    QNetworkAccessManager m_network;
    bool m_loading = false;
    bool m_garminNeedsLogin = false;
    int m_pending = 0;
    QString m_lastError;
    QVariantList m_series;                 // the merged result the UI reads
    QVariantList m_intervalsSeries;        // per-source buffers, refilled on each refresh
    QVariantList m_garminSeries;
    QString m_source;                      // "all" | "intervals" | "garmin" (see the property)

    void setLoading(bool v);
    void fetchIntervals(int days);
    void fetchGarmin(int days);
    void oneSourceDone();
    void rebuild();                        // merge the three buffers -> m_series
    QVariantList loadManual() const;
};
