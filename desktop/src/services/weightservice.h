#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QTimer>
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
    // Local Bluetooth scale (Etekcity FIT 8S broadcast, no cloud, no pairing): the backend's
    // tools/scale_service.py listens and stores weigh-ins; this is its state for the UI.
    // {available, listening, address, lastSeen, error, lastWeighInT}
    Q_PROPERTY(QVariantMap scale READ scale NOTIFY scaleChanged)
    // Height (cm) and sex ("M"/"F") for the body-fat estimate: from the intervals.icu profile,
    // overridable by hand. {heightCm, sex, source: "intervals"|"manual"|""}
    Q_PROPERTY(QVariantMap bodyProfile READ bodyProfile NOTIFY changed)
    // Percentage points added to the estimated body fat, for one person to line the estimate up
    // with a reference they trust (a DXA scan, calipers). 0 by default.
    Q_PROPERTY(double bodyFatOffset READ bodyFatOffset WRITE setBodyFatOffset NOTIFY changed)
    // Send Sommet's own weigh-ins (Bluetooth scale, typed by hand) to intervals.icu wellness when
    // it is connected (🟣76, 2026-10-27). Weight on by default; body fat is a second opt-in
    // because the scale's number is an estimate and intervals.icu doesn't mark it as one.
    Q_PROPERTY(bool sendToIntervals READ sendToIntervals WRITE setSendToIntervals NOTIFY changed)
    Q_PROPERTY(bool sendBodyFatToIntervals READ sendBodyFatToIntervals WRITE setSendBodyFatToIntervals NOTIFY changed)

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
    QVariantMap scale() const { return m_scaleStatus; }
    QVariantMap bodyProfile() const;
    double bodyFatOffset() const;
    void setBodyFatOffset(double v);
    bool sendToIntervals() const;
    void setSendToIntervals(bool on);
    bool sendBodyFatToIntervals() const;
    void setSendBodyFatToIntervals(bool on);

    // Re-pull every connected source (intervals + Garmin) and re-merge with manual entries.
    Q_INVOKABLE void refresh(int days = 365);
    // One-time Garmin Connect login (used from Settings and the Weight page).
    Q_INVOKABLE void garminLogin(const QString &email, const QString &password,
                                 const QString &mfa);
    // Add a manual weigh-in (date "YYYY-MM-DD", kg; bodyFatPct<=0 means "not given"). Stored
    // locally and merged in as the highest-priority source.
    Q_INVOKABLE void addManualWeight(const QString &date, double weightKg, double bodyFatPct);
    Q_INVOKABLE void removeManualWeight(const QString &date);

    // --- local Bluetooth scale ---
    // Listen for scales for `seconds` (set-up wizard); the answer comes as scaleFound.
    Q_INVOKABLE void scaleFind(int seconds = 15);
    // Start listening (remember `address` as THE scale when given). Stop / forget it again.
    Q_INVOKABLE void scaleStart(const QString &address = QString());
    Q_INVOKABLE void scaleStop();
    Q_INVOKABLE void scaleForget();
    // Height / sex typed by hand (used when intervals.icu is not connected or has none).
    Q_INVOKABLE void setBodyProfile(double heightCm, const QString &sex);

signals:
    void changed();
    void garminLoggedIn(const QString &email);
    void scaleChanged();
    // The wizard's answer: [{address, weightKg, rssi}], or an error text.
    void scaleFound(const QVariantList &found, const QString &error);
    // A weigh-in that was not there a moment ago (the page highlights it).
    void newScaleWeighIn(const QString &date, double weightKg);

private:
    // PUT one day's weigh-in to intervals.icu wellness (a merge: only weight / bodyFat change).
    void sendWeighInToIntervals(const QString &date, double weightKg, double bodyFatPct);

    QNetworkAccessManager m_network;
    bool m_loading = false;
    bool m_garminNeedsLogin = false;
    int m_pending = 0;
    QString m_lastError;
    QVariantList m_series;                 // the merged result the UI reads
    QVariantList m_intervalsSeries;        // per-source buffers, refilled on each refresh
    QVariantList m_garminSeries;
    QVariantList m_scaleSeries;
    QVariantMap m_scaleStatus;
    QTimer m_scaleTimer;                   // polls the backend while a scale is set up
    qint64 m_scaleLastT = 0;               // newest scale weigh-in seen, to spot a new one
    bool m_scaleSeen = false;              // first poll after start is not "new"
    QString m_source;                      // "all" | "intervals" | "garmin" (see the property)

    void setLoading(bool v);
    void fetchIntervals(int days);
    void fetchGarmin(int days);
    void fetchProfile();                   // height + sex from intervals.icu, cached
    void fetchScale(bool poll = false);    // local scale weigh-ins from the backend
    // Sommet Sync (the NAS): every reading this device sees goes there, and what is there comes
    // back, so a device without intervals.icu (or offline) still has the history.
    bool nasConfigured() const;
    void fetchNas();
    void pushNas();
    void saveNasCache() const;
    QVariantList m_nasSeries;              // the server's readings (also cached locally)
    bool m_nasPulled = false;              // pulled this session: only then push (never stale)
    void pollScale();                      // status + weigh-ins; cheap, local
    void scalePost(const QString &path, const QJsonObject &body);
    void oneSourceDone();
    void rebuild();                        // merge the three buffers -> m_series
    QVariantList loadManual() const;
};
