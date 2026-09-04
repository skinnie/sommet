#include "healthservice.h"

#include <QDate>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QMap>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QSettings>
#include <QUrlQuery>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

// Merge several [{date,value}] lists into one per-date series. Later lists win ties (call order
// = ascending priority: intervals, garmin, manual).
static QVariantList mergeByDate(const QList<QVariantList> &lists)
{
    QMap<QString, QVariant> byDate;   // date -> value map (QMap keeps dates sorted)
    for (const QVariantList &list : lists)
        for (const QVariant &v : list) {
            const QVariantMap m = v.toMap();
            const QString d = m.value(QStringLiteral("date")).toString();
            if (!d.isEmpty())
                byDate.insert(d, m);
        }
    QVariantList out;
    for (auto it = byDate.constBegin(); it != byDate.constEnd(); ++it)
        out.append(it.value());
    return out;
}

HealthService::HealthService(QObject *parent) : QObject(parent) {}

void HealthService::setLoading(bool v)
{
    if (m_loading == v)
        return;
    m_loading = v;
    emit changed();
}

double HealthService::lastValue(const QVariantList &s)
{
    return s.isEmpty() ? 0.0 : s.last().toMap().value(QStringLiteral("value")).toDouble();
}

QString HealthService::sleepProvider() const
{
    // Default to intervals.icu - it carries sleep for anyone syncing a watch there (Suunto,
    // Garmin…) and needs no extra login; the user can switch to Garmin or "off" in Settings.
    return QSettings().value(QStringLiteral("health/sleepProvider"),
                             QStringLiteral("intervals")).toString();
}

void HealthService::setSleepProvider(const QString &p)
{
    if (p == sleepProvider())
        return;
    QSettings().setValue(QStringLiteral("health/sleepProvider"), p);
    emit changed();
    refresh();
}

QString HealthService::hrvSource() const
{
    // Which cloud source feeds the OVERNIGHT HRV line - one of "intervals" | "garmin" (never
    // both, so the trend line stays one consistent measurement). Default intervals.icu, same
    // reasoning as sleepProvider. The Ambit3 morning line is separate (ambitHrvEnabled).
    return QSettings().value(QStringLiteral("health/hrvSource"),
                             QStringLiteral("intervals")).toString();
}

void HealthService::setHrvSource(const QString &s)
{
    if (s == hrvSource())
        return;
    QSettings().setValue(QStringLiteral("health/hrvSource"), s);
    emit changed();
    rebuild();   // a re-pick only reshuffles already-fetched buffers; no network refetch needed
}

bool HealthService::ambitHrvEnabled() const
{
    // Opt-IN (default off): an intervals.icu-only user shouldn't see the Ambit3 5+5 HRV UI or line
    // at all. Ambit users turn it on in Settings. Data (health/watchHrv) is still written by a
    // sync regardless; the toggle only governs whether the feature/line is shown.
    return QSettings().value(QStringLiteral("health/ambitHrvEnabled"), false).toBool();
}

void HealthService::setAmbitHrvEnabled(bool on)
{
    if (on == ambitHrvEnabled())
        return;
    QSettings().setValue(QStringLiteral("health/ambitHrvEnabled"), on);
    emit changed();
    rebuild();
}

bool HealthService::coospoHrvEnabled() const
{
    // Opt-in (default off): only strap users see the "Measure HRV (COOSPO)" UI. Independent of
    // the Ambit toggle; both feed the same Morning-HRV line (health/watchHrv).
    return QSettings().value(QStringLiteral("health/coospoHrvEnabled"), false).toBool();
}

void HealthService::setCoospoHrvEnabled(bool on)
{
    if (on == coospoHrvEnabled())
        return;
    QSettings().setValue(QStringLiteral("health/coospoHrvEnabled"), on);
    emit changed();
    rebuild();
}

void HealthService::refresh(int days)
{
    m_lastError.clear();
    m_iRhr.clear(); m_iSteps.clear(); m_iHrv.clear(); m_iSleep.clear();
    m_gRhr.clear(); m_gSteps.clear(); m_gHrv.clear(); m_gBattery.clear(); m_gSleep.clear();

    const QSettings s;
    const bool haveIntervals =
        !s.value(QStringLiteral("connections/intervals_icu/athleteId")).toString().isEmpty()
        && !s.value(QStringLiteral("connections/intervals_icu/apiKey")).toString().isEmpty();
    const bool haveGarmin = s.value(QStringLiteral("connections/garmin/connected"), false).toBool();
    // Garmin sleep is a per-day loop (slow), so only fetch it when the user actually picked it.
    const bool wantGarminSleep = haveGarmin && sleepProvider() == QStringLiteral("garmin");
    m_pending = (haveIntervals ? 1 : 0) + (haveGarmin ? 1 : 0) + (wantGarminSleep ? 1 : 0);
    if (m_pending == 0) {
        rebuild();
        return;
    }
    setLoading(true);
    if (haveIntervals) fetchIntervals(days);
    if (haveGarmin) fetchGarmin(days);
    if (wantGarminSleep) fetchGarminSleep(days);
}

void HealthService::oneSourceDone()
{
    if (--m_pending <= 0) {
        setLoading(false);
        rebuild();
    }
}

static QVariantList manualSeries(const QString &field)
{
    // weight/manual-style store at health/manual: [{date, rhr?, hrv?}] -> [{date,value}] for one.
    const QString raw = QSettings().value(QStringLiteral("health/manual")).toString();
    QVariantList out;
    for (const auto &v : QJsonDocument::fromJson(raw.toUtf8()).array()) {
        const auto o = v.toObject();
        if (o.contains(field) && o.value(field).isDouble())
            out.append(QVariantMap{{QStringLiteral("date"), o.value(QStringLiteral("date")).toString()},
                                   {QStringLiteral("value"), o.value(field).toDouble()}});
    }
    return out;
}

// The Ambit3's own resting-HRV readings (rMSSD, ms), written by ActivityService after a sync
// from any 5+5 / lie-still test - stored at health/watchHrv as [{date,value}]. Sparse: it holds
// a value only for days you actually measured on the watch, so it never overwrites intervals/
// Garmin on a day you didn't (mergeByDate only touches dates a source actually carries).
static QVariantList watchHrvSeries()
{
    const QString raw = QSettings().value(QStringLiteral("health/watchHrv")).toString();
    QVariantList out;
    for (const auto &v : QJsonDocument::fromJson(raw.toUtf8()).array()) {
        const auto o = v.toObject();
        if (o.value(QStringLiteral("value")).isDouble())
            out.append(QVariantMap{{QStringLiteral("date"), o.value(QStringLiteral("date")).toString()},
                                   {QStringLiteral("value"), o.value(QStringLiteral("value")).toDouble()}});
    }
    return out;
}

void HealthService::rebuild()
{
    m_rhr = mergeByDate({m_iRhr, m_gRhr, manualSeries(QStringLiteral("rhr"))});
    m_steps = mergeByDate({m_iSteps, m_gSteps});
    // HRV is TWO SEPARATE tracks, never blended (they are different measurements - see the
    // header). `m_hrv` is the OVERNIGHT line from the one chosen cloud source (intervals XOR
    // garmin, like sleep); a hand-typed manual entry still rides this line. `m_hrvAmbit` is the
    // Ambit3's own MORNING/spot rMSSD, an independent coloured line the user toggles on/off.
    const QString hsrc = hrvSource();
    const QVariantList overnightHrv = (hsrc == QStringLiteral("garmin")) ? m_gHrv : m_iHrv;
    m_hrv = mergeByDate({overnightHrv, manualSeries(QStringLiteral("hrv"))});
    // The Morning-HRV line holds readings from either capture path (Ambit 5+5 or COOSPO strap) —
    // both write health/watchHrv — so show it if either feature is enabled.
    m_hrvAmbit = (ambitHrvEnabled() || coospoHrvEnabled()) ? watchHrvSeries() : QVariantList{};
    m_bodyBattery = mergeByDate({m_gBattery});
    // Sleep is a single chosen source (not merged), or off.
    const QString sp = sleepProvider();
    m_sleep = (sp == QStringLiteral("intervals")) ? m_iSleep
            : (sp == QStringLiteral("garmin"))    ? m_gSleep
                                                  : QVariantList{};
    emit changed();
}

void HealthService::fetchIntervals(int days)
{
    const QSettings s;
    const QString athlete =
        s.value(QStringLiteral("connections/intervals_icu/athleteId")).toString();
    const QString key = s.value(QStringLiteral("connections/intervals_icu/apiKey")).toString();
    QUrl url(QStringLiteral("https://intervals.icu/api/v1/athlete/%1/wellness").arg(athlete));
    QUrlQuery q;
    const QDate today = QDate::currentDate();
    q.addQueryItem(QStringLiteral("oldest"),
                   today.addDays(-(days > 0 ? days : 30)).toString(Qt::ISODate));
    q.addQueryItem(QStringLiteral("newest"), today.toString(Qt::ISODate));
    url.setQuery(q);
    QNetworkRequest req(url);
    req.setRawHeader("Authorization",
                     "Basic " + (QByteArrayLiteral("API_KEY:") + key.toUtf8()).toBase64());
    req.setRawHeader("User-Agent",
                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Sommet/1.0");
    QNetworkReply *reply = m_network.get(req);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() == QNetworkReply::NoError) {
            const auto arr = QJsonDocument::fromJson(reply->readAll()).array();
            auto pull = [&arr](const QString &field) {
                QVariantList out;
                for (const auto &v : arr) {
                    const auto o = v.toObject();
                    const auto val = o.value(field);
                    if (!val.isNull() && val.isDouble())
                        out.append(QVariantMap{{QStringLiteral("date"),
                            o.value(QStringLiteral("id")).toString()},
                            {QStringLiteral("value"), val.toDouble()}});
                }
                return out;
            };
            m_iRhr = pull(QStringLiteral("restingHR"));
            m_iHrv = pull(QStringLiteral("hrv"));
            m_iSteps = pull(QStringLiteral("steps"));
            // Sleep: intervals stores seconds; convert to hours.
            for (const auto &v : arr) {
                const auto o = v.toObject();
                const auto secs = o.value(QStringLiteral("sleepSecs"));
                if (!secs.isNull() && secs.isDouble())
                    m_iSleep.append(QVariantMap{
                        {QStringLiteral("date"), o.value(QStringLiteral("id")).toString()},
                        {QStringLiteral("value"), secs.toDouble() / 3600.0}});
            }
        } else if (m_lastError.isEmpty()) {
            m_lastError = reply->errorString();
        }
        oneSourceDone();
    });
}

void HealthService::fetchGarminSleep(int days)
{
    const QUrl url(kBackendBase + QStringLiteral("/api/garmin/sleep?days=%1").arg(days));
    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() == QNetworkReply::NoError) {
            const auto o = QJsonDocument::fromJson(reply->readAll()).object();
            if (o.value(QStringLiteral("ok")).toBool(false))
                for (const auto &v : o.value(QStringLiteral("sleep")).toArray())
                    m_gSleep.append(v.toObject().toVariantMap());
        }
        oneSourceDone();
    });
}

void HealthService::fetchGarmin(int days)
{
    const QUrl url(kBackendBase + QStringLiteral("/api/garmin/health?days=%1").arg(days));
    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() == QNetworkReply::NoError) {
            const auto o = QJsonDocument::fromJson(reply->readAll()).object();
            m_needsLogin = o.value(QStringLiteral("needLogin")).toBool(false);
            if (o.value(QStringLiteral("ok")).toBool(false)) {
                auto toSeries = [](const QJsonArray &a) {
                    QVariantList out;
                    for (const auto &v : a) out.append(v.toObject().toVariantMap());
                    return out;
                };
                m_gRhr = toSeries(o.value(QStringLiteral("rhr")).toArray());
                m_gSteps = toSeries(o.value(QStringLiteral("steps")).toArray());
                m_gHrv = toSeries(o.value(QStringLiteral("hrv")).toArray());
                m_gBattery = toSeries(o.value(QStringLiteral("bodyBattery")).toArray());
            } else if (!m_needsLogin && m_lastError.isEmpty()) {
                m_lastError = o.value(QStringLiteral("error")).toString();
            }
        } else if (m_lastError.isEmpty()) {
            m_lastError = reply->errorString();
        }
        oneSourceDone();
    });
}

void HealthService::addManualHealth(const QString &date, double restingHr, double hrvMs)
{
    const QString raw = QSettings().value(QStringLiteral("health/manual")).toString();
    QJsonArray arr = QJsonDocument::fromJson(raw.toUtf8()).array();
    // Upsert by date.
    QJsonObject entry{{QStringLiteral("date"), date}};
    if (restingHr > 0) entry.insert(QStringLiteral("rhr"), restingHr);
    if (hrvMs > 0) entry.insert(QStringLiteral("hrv"), hrvMs);
    bool replaced = false;
    for (int i = 0; i < arr.size(); ++i)
        if (arr.at(i).toObject().value(QStringLiteral("date")).toString() == date) {
            arr[i] = entry; replaced = true; break;
        }
    if (!replaced)
        arr.append(entry);
    QSettings().setValue(QStringLiteral("health/manual"),
                         QString::fromUtf8(QJsonDocument(arr).toJson(QJsonDocument::Compact)));
    rebuild();
}

void HealthService::installHrvApp()
{
    if (m_hrvInstalling)
        return;
    m_hrvInstalling = true;
    m_hrvInstallMessage = tr("Installing the HRV app on your watch…");
    emit changed();

    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/hrv/install")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    const QByteArray payload = QJsonDocument(QJsonObject{{QStringLiteral("confirm"), true}})
                                   .toJson(QJsonDocument::Compact);
    QNetworkReply *reply = m_network.post(req, payload);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        m_hrvInstalling = false;
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError && obj.isEmpty()) {
            m_hrvInstallMessage = tr("Couldn't reach the watch. Connect it by USB and try again.");
        } else if (obj.value(QStringLiteral("ok")).toBool()) {
            m_hrvInstallMessage = obj.value(QStringLiteral("alreadyInstalled")).toBool()
                ? tr("The HRV app is already on your watch's HRV mode.")
                : tr("Installed. Start the HRV mode on your watch to use it.");
        } else {
            const QString e = obj.value(QStringLiteral("error")).toString();
            m_hrvInstallMessage = e.isEmpty() ? tr("Install failed. Please try again.")
                                              : tr("Install failed: %1").arg(e);
        }
        emit changed();
    });
}

void HealthService::readStrapHrv(int seconds)
{
    if (m_strapMeasuring)
        return;
    m_strapMeasuring = true;
    m_strapMessage = tr("Measuring… wear the strap and stay still for %1s.").arg(seconds);
    emit changed();

    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/hrv/strap")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    const QByteArray payload = QJsonDocument(QJsonObject{
        {QStringLiteral("seconds"), seconds}}).toJson(QJsonDocument::Compact);
    QNetworkReply *reply = m_network.post(req, payload);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        m_strapMeasuring = false;
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError && obj.isEmpty()) {
            m_strapMessage = tr("Couldn't reach the strap. Make sure it's on and worn.");
        } else if (obj.value(QStringLiteral("ok")).toBool()
                   && obj.value(QStringLiteral("rmssd_ms")).isDouble()) {
            const double rmssd = obj.value(QStringLiteral("rmssd_ms")).toDouble();
            const double hr = obj.value(QStringLiteral("mean_hr_bpm")).toDouble();
            // Store on the Morning-HRV line (same store the watch's 5+5 uses).
            const QString date = QDate::currentDate().toString(Qt::ISODate);
            const QString raw = QSettings().value(QStringLiteral("health/watchHrv")).toString();
            QJsonArray arr = QJsonDocument::fromJson(raw.toUtf8()).array();
            bool replaced = false;
            for (int i = 0; i < arr.size(); ++i)
                if (arr.at(i).toObject().value(QStringLiteral("date")).toString() == date) {
                    arr[i] = QJsonObject{{QStringLiteral("date"), date},
                                         {QStringLiteral("value"), rmssd}};
                    replaced = true; break;
                }
            if (!replaced)
                arr.append(QJsonObject{{QStringLiteral("date"), date},
                                       {QStringLiteral("value"), rmssd}});
            QSettings().setValue(QStringLiteral("health/watchHrv"),
                QString::fromUtf8(QJsonDocument(arr).toJson(QJsonDocument::Compact)));
            m_strapMessage = tr("Saved: RMSSD %1 ms (HR %2 bpm).")
                                 .arg(qRound(rmssd)).arg(qRound(hr));
            rebuild();   // refresh the Morning-HRV series/tile from health/watchHrv
        } else {
            const QString e = obj.value(QStringLiteral("error")).toString();
            m_strapMessage = e.isEmpty() ? tr("Measurement failed. Try again.")
                                         : tr("Failed: %1").arg(e);
        }
        emit changed();
    });
}
