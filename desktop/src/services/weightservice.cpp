#include "weightservice.h"
#include "backenderror.h"

#include <QDate>
#include <QDateTime>
#include <QHash>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QTimer>
#include <QMap>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QSettings>
#include <QStringList>
#include <QUrlQuery>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

// Body-composition fields a weigh-in may carry, beyond the always-present weight.
static const QStringList kCompFields = {
    QStringLiteral("bmi"), QStringLiteral("bodyFatPct"), QStringLiteral("bodyWaterPct"),
    QStringLiteral("muscleMassKg"), QStringLiteral("boneMassKg")};

static int fieldCount(const QVariantMap &m)
{
    int n = 1;  // weight
    for (const QString &f : kCompFields)
        if (m.contains(f))
            ++n;
    return n;
}

static int sourcePriority(const QString &s)
{
    if (s == QStringLiteral("manual")) return 4;   // you typed it - wins ties
    if (s == QStringLiteral("scale")) return 3;    // your own scale, read locally
    if (s == QStringLiteral("garmin")) return 2;
    return 1;                                       // intervals
}

WeightService::WeightService(QObject *parent) : QObject(parent)
{
    // Restore the chosen provider filter (see the `source` property). Defaults to "all", which
    // is exactly the merge-everything behaviour this service had before the setting existed.
    const QSettings settings;
    m_source = settings.value(QStringLiteral("weight/source"), QStringLiteral("all")).toString();

    // Poll the local backend every few seconds while a scale is set up, so a weigh-in appears on
    // the page by itself (no refresh press). Nothing leaves the machine.
    m_scaleTimer.setInterval(4000);
    connect(&m_scaleTimer, &QTimer::timeout, this, &WeightService::pollScale);
    if (settings.value(QStringLiteral("weight/scale/enabled"), false).toBool())
        m_scaleTimer.start();
    // Learn the backend's scale state once at start (it remembers the scale between runs).
    QTimer::singleShot(1500, this, &WeightService::pollScale);
    // The NAS's readings as last seen, so they show at once (and offline) before a refresh.
    for (const auto &v : QJsonDocument::fromJson(
             settings.value(QStringLiteral("weight/nasCache")).toString().toUtf8()).array())
        m_nasSeries.append(v.toObject().toVariantMap());
}

void WeightService::setSource(const QString &s)
{
    // Guard against a bad value silently emptying the chart: anything unrecognised falls back
    // to "all" rather than filtering every provider out.
    const QString v = (s == QLatin1String("intervals") || s == QLatin1String("garmin"))
                      ? s : QStringLiteral("all");
    if (m_source == v)
        return;
    m_source = v;
    QSettings().setValue(QStringLiteral("weight/source"), v);
    rebuild();          // re-merge from the buffers already in memory - no refetch needed
}

void WeightService::setLoading(bool v)
{
    if (m_loading == v)
        return;
    m_loading = v;
    emit changed();
}

bool WeightService::connected() const
{
    const QSettings s;
    const bool intervals =
        !s.value(QStringLiteral("connections/intervals_icu/athleteId")).toString().isEmpty()
        && !s.value(QStringLiteral("connections/intervals_icu/apiKey")).toString().isEmpty();
    const bool garmin = s.value(QStringLiteral("connections/garmin/connected"), false).toBool();
    const bool scale = s.value(QStringLiteral("weight/scale/enabled"), false).toBool();
    return intervals || garmin || scale || !loadManual().isEmpty()
        || nasConfigured() || !m_nasSeries.isEmpty();
}

double WeightService::latestWeightKg() const
{
    return m_series.isEmpty() ? 0.0
        : m_series.last().toMap().value(QStringLiteral("weightKg")).toDouble();
}

QString WeightService::latestDate() const
{
    return m_series.isEmpty() ? QString()
        : m_series.last().toMap().value(QStringLiteral("date")).toString();
}

double WeightService::changeKg() const
{
    if (m_series.size() < 2)
        return 0.0;
    return latestWeightKg()
        - m_series.first().toMap().value(QStringLiteral("weightKg")).toDouble();
}

QVariantList WeightService::loadManual() const
{
    const QString raw = QSettings().value(QStringLiteral("weight/manual")).toString();
    if (raw.isEmpty())
        return {};
    QVariantList out;
    for (const auto &v : QJsonDocument::fromJson(raw.toUtf8()).array())
        out.append(v.toObject().toVariantMap());
    return out;
}

void WeightService::refresh(int days)
{
    m_lastError.clear();
    m_intervalsSeries.clear();
    m_garminSeries.clear();

    const QSettings s;
    const bool haveIntervals =
        !s.value(QStringLiteral("connections/intervals_icu/athleteId")).toString().isEmpty()
        && !s.value(QStringLiteral("connections/intervals_icu/apiKey")).toString().isEmpty();
    const bool haveGarmin = s.value(QStringLiteral("connections/garmin/connected"), false).toBool();

    const bool haveScale = s.value(QStringLiteral("weight/scale/enabled"), false).toBool();
    m_scaleSeries.clear();
    const bool haveNas = nasConfigured();

    m_pending = (haveIntervals ? 1 : 0) + (haveGarmin ? 1 : 0) + (haveScale ? 1 : 0)
              + (haveNas ? 1 : 0);
    if (m_pending == 0) {          // manual-only (or nothing) - merge immediately
        rebuild();
        return;
    }
    setLoading(true);
    if (haveIntervals) fetchIntervals(days);
    if (haveGarmin) fetchGarmin(days);
    if (haveNas) fetchNas();
    if (haveScale) {
        // The scale's body-fat estimate needs the height: fetch the profile first when
        // intervals.icu is connected (it refreshes the cached value), else use what is cached.
        if (haveIntervals) fetchProfile(); else fetchScale();
    }
}

void WeightService::oneSourceDone()
{
    if (--m_pending <= 0) {
        setLoading(false);
        rebuild();
    }
}

void WeightService::rebuild()
{
    // Merge all sources by date; keep the richest reading per day (most fields; manual/garmin
    // win ties over intervals).
    QMap<QString, QVariantMap> best;   // date -> chosen reading
    auto consider = [&best](const QVariantList &list) {
        for (const QVariant &v : list) {
            const QVariantMap m = v.toMap();
            const QString date = m.value(QStringLiteral("date")).toString();
            if (date.isEmpty())
                continue;
            if (!best.contains(date)) {
                best.insert(date, m);
                continue;
            }
            const QVariantMap cur = best.value(date);
            const int pm = sourcePriority(m.value(QStringLiteral("source")).toString());
            const int pc = sourcePriority(cur.value(QStringLiteral("source")).toString());
            // Your own scale and your own typing beat a cloud reading of the same day, even one
            // with more fields (André, 2026-10-16: "prefer the scale"); among the cloud sources
            // the richest reading still wins.
            if (pm >= 3 || pc >= 3) {
                if (pm > pc)
                    best.insert(date, m);
                continue;
            }
            const int a = fieldCount(m), b = fieldCount(cur);
            if (a > b || (a == b && pm > pc))
                best.insert(date, m);
        }
    };
    // Provider filter (see the `source` property). Manual entries are deliberately NOT gated:
    // they're the user's own typed-in data, not a provider's, so they stay in every mode.
    if (m_source != QLatin1String("garmin"))
        consider(m_intervalsSeries);
    if (m_source != QLatin1String("intervals"))
        consider(m_garminSeries);
    consider(m_scaleSeries);
    consider(loadManual());
    // Last, so a live source wins a tie; each reading keeps the source it came from.
    consider(m_nasSeries);

    m_series.clear();
    for (auto it = best.constBegin(); it != best.constEnd(); ++it)  // QMap iterates keys sorted
        m_series.append(it.value());
    emit changed();
    pushNas();
}

// ── Sommet Sync: weight readings on the NAS (André, 2026-11-20: "can't we have weight figures
// saved on the NAS, so if intervals.icu is not connected we still have the readings?") ──────
// One record per day, uid = the date, the merged reading of that day with the source it came
// from. Pulled in full each time (a reading a day is a few thousand small records at most).

bool WeightService::nasConfigured() const
{
    const QSettings s;
    return !s.value(QStringLiteral("connections/sommet_sync/url")).toString().isEmpty()
        && !s.value(QStringLiteral("connections/sommet_sync/token")).toString().isEmpty();
}

static QVariantMap nasReading(const QJsonObject &r)
{
    QVariantMap m = r.toVariantMap();
    m.remove(QStringLiteral("uid"));
    m.remove(QStringLiteral("srv"));
    m.remove(QStringLiteral("updated_at"));
    return m;
}

void WeightService::saveNasCache() const
{
    QJsonArray arr;
    for (const QVariant &v : m_nasSeries)
        arr.append(QJsonObject::fromVariantMap(v.toMap()));
    QSettings().setValue(QStringLiteral("weight/nasCache"),
                         QString::fromUtf8(QJsonDocument(arr).toJson(QJsonDocument::Compact)));
}

void WeightService::fetchNas()
{
    const QSettings s;
    QUrl u(s.value(QStringLiteral("connections/sommet_sync/url")).toString());
    QUrlQuery q;
    q.addQueryItem(QStringLiteral("c"), QStringLiteral("weight"));
    q.addQueryItem(QStringLiteral("since"), QStringLiteral("0"));
    u.setQuery(q);
    QNetworkRequest req(u);
    req.setRawHeader("X-Sommet-Token",
                     s.value(QStringLiteral("connections/sommet_sync/token")).toString().toUtf8());
    QNetworkReply *reply = m_network.get(req);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() == QNetworkReply::NoError) {
            const QJsonObject root = QJsonDocument::fromJson(reply->readAll()).object();
            m_nasSeries.clear();
            for (const auto &v : root.value(QStringLiteral("records")).toArray()) {
                const QVariantMap m = nasReading(v.toObject());
                if (!m.value(QStringLiteral("date")).toString().isEmpty()
                    && m.value(QStringLiteral("weightKg")).toDouble() > 0)
                    m_nasSeries.append(m);
            }
            m_nasPulled = true;
            saveNasCache();
        }
        // Unreachable: keep the cached copy loaded at start, and don't push this session.
        oneSourceDone();
    });
}

void WeightService::pushNas()
{
    if (!m_nasPulled || !nasConfigured())
        return;
    QHash<QString, QVariantMap> have;
    for (const QVariant &v : std::as_const(m_nasSeries)) {
        const QVariantMap m = v.toMap();
        have.insert(m.value(QStringLiteral("date")).toString(), m);
    }
    QJsonArray records;
    QVariantList sent;
    const qint64 now = QDateTime::currentMSecsSinceEpoch();
    for (const QVariant &v : std::as_const(m_series)) {
        const QVariantMap m = v.toMap();
        const QString date = m.value(QStringLiteral("date")).toString();
        if (date.isEmpty() || m.value(QStringLiteral("weightKg")).toDouble() <= 0
            || have.value(date) == m)
            continue;                                  // the server holds exactly this one
        QJsonObject r = QJsonObject::fromVariantMap(m);
        r.insert(QStringLiteral("uid"), date);
        r.insert(QStringLiteral("updated_at"), now);
        records.append(r);
        sent.append(m);
    }
    if (records.isEmpty())
        return;
    const QSettings s;
    QUrl u(s.value(QStringLiteral("connections/sommet_sync/url")).toString());
    QUrlQuery q;
    q.addQueryItem(QStringLiteral("c"), QStringLiteral("weight"));
    u.setQuery(q);
    QNetworkRequest req(u);
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    req.setRawHeader("X-Sommet-Token",
                     s.value(QStringLiteral("connections/sommet_sync/token")).toString().toUtf8());
    const QByteArray body = QJsonDocument(QJsonObject{
        {QStringLiteral("records"), records}, {QStringLiteral("deleted"), QJsonArray()}})
        .toJson(QJsonDocument::Compact);
    QNetworkReply *reply = m_network.post(req, body);
    connect(reply, &QNetworkReply::finished, this, [this, reply, sent]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError)
            return;                                    // sent again on the next refresh
        QHash<QString, int> at;
        for (int i = 0; i < m_nasSeries.size(); ++i)
            at.insert(m_nasSeries.at(i).toMap().value(QStringLiteral("date")).toString(), i);
        for (const QVariant &v : sent) {
            const QString date = v.toMap().value(QStringLiteral("date")).toString();
            if (at.contains(date)) m_nasSeries[at.value(date)] = v;
            else m_nasSeries.append(v);
        }
        saveNasCache();
    });
}

void WeightService::fetchIntervals(int days)
{
    const QSettings s;
    const QString athlete =
        s.value(QStringLiteral("connections/intervals_icu/athleteId")).toString();
    const QString key = s.value(QStringLiteral("connections/intervals_icu/apiKey")).toString();
    QUrl url(QStringLiteral("https://intervals.icu/api/v1/athlete/%1/wellness").arg(athlete));
    QUrlQuery q;
    const QDate today = QDate::currentDate();
    q.addQueryItem(QStringLiteral("oldest"),
                   today.addDays(-(days > 0 ? days : 365)).toString(Qt::ISODate));
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
            for (const auto &v : QJsonDocument::fromJson(reply->readAll()).array()) {
                const auto o = v.toObject();
                const auto w = o.value(QStringLiteral("weight"));
                if (w.isNull() || !w.isDouble()
                    || o.value(QStringLiteral("tempWeight")).toBool(false))
                    continue;
                m_intervalsSeries.append(QVariantMap{
                    {QStringLiteral("date"), o.value(QStringLiteral("id")).toString()},
                    {QStringLiteral("weightKg"), w.toDouble()},
                    {QStringLiteral("source"), QStringLiteral("intervals")}});
            }
        } else if (m_lastError.isEmpty()) {
            m_lastError = backendErrorText(reply);
        }
        oneSourceDone();
    });
}

void WeightService::fetchGarmin(int days)
{
    const QUrl url(kBackendBase + QStringLiteral("/api/garmin/weight?days=%1").arg(days));
    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() == QNetworkReply::NoError) {
            const auto o = QJsonDocument::fromJson(reply->readAll()).object();
            m_garminNeedsLogin = o.value(QStringLiteral("needLogin")).toBool(false);
            if (o.value(QStringLiteral("ok")).toBool(false)) {
                for (const auto &v : o.value(QStringLiteral("series")).toArray()) {
                    QVariantMap m = v.toObject().toVariantMap();
                    m.insert(QStringLiteral("source"), QStringLiteral("garmin"));
                    // drop null comp fields so fieldCount reflects real data
                    for (const QString &f : kCompFields)
                        if (m.value(f).isNull())
                            m.remove(f);
                    m_garminSeries.append(m);
                }
            } else if (!m_garminNeedsLogin && m_lastError.isEmpty()) {
                m_lastError = o.value(QStringLiteral("error")).toString();
            }
        } else if (m_lastError.isEmpty()) {
            m_lastError = backendErrorText(reply);
        }
        oneSourceDone();
    });
}

void WeightService::garminLogin(const QString &email, const QString &password,
                                const QString &mfa)
{
    setLoading(true);
    m_lastError.clear();
    QJsonObject payload{{QStringLiteral("email"), email},
                        {QStringLiteral("password"), password}};
    if (!mfa.isEmpty())
        payload.insert(QStringLiteral("mfa"), mfa);
    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/garmin/weight/login")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(req, QJsonDocument(payload).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply, email]() {
        reply->deleteLater();
        const auto o = QJsonDocument::fromJson(reply->readAll()).object();
        if (o.value(QStringLiteral("ok")).toBool(false)) {
            m_garminNeedsLogin = false;
            setLoading(false);
            emit changed();
            emit garminLoggedIn(email);
            refresh();
        } else {
            setLoading(false);
            m_lastError = o.value(QStringLiteral("error")).toString(tr("Garmin login failed."));
            emit changed();
        }
    });
}

void WeightService::addManualWeight(const QString &date, double weightKg, double bodyFatPct)
{
    QVariantList manual = loadManual();
    // Upsert by date.
    QVariantMap entry{{QStringLiteral("date"), date},
                      {QStringLiteral("weightKg"), weightKg},
                      {QStringLiteral("source"), QStringLiteral("manual")}};
    if (bodyFatPct > 0)
        entry.insert(QStringLiteral("bodyFatPct"), bodyFatPct);
    bool replaced = false;
    for (int i = 0; i < manual.size(); ++i)
        if (manual.at(i).toMap().value(QStringLiteral("date")).toString() == date) {
            manual[i] = entry;
            replaced = true;
            break;
        }
    if (!replaced)
        manual.append(entry);
    QJsonArray arr;
    for (const auto &v : manual)
        arr.append(QJsonObject::fromVariantMap(v.toMap()));
    QSettings().setValue(QStringLiteral("weight/manual"),
                         QString::fromUtf8(QJsonDocument(arr).toJson(QJsonDocument::Compact)));
    rebuild();
    sendWeighInToIntervals(date, weightKg, bodyFatPct);
}

void WeightService::removeManualWeight(const QString &date)
{
    QVariantList manual = loadManual();
    QJsonArray arr;
    for (const auto &v : manual)
        if (v.toMap().value(QStringLiteral("date")).toString() != date)
            arr.append(QJsonObject::fromVariantMap(v.toMap()));
    QSettings().setValue(QStringLiteral("weight/manual"),
                         QString::fromUtf8(QJsonDocument(arr).toJson(QJsonDocument::Compact)));
    rebuild();
}

// ---------------------------------------------------------------------------------------------
// Body profile (height, sex) for the body-fat estimate

QVariantMap WeightService::bodyProfile() const
{
    const QSettings s;
    const double mh = s.value(QStringLiteral("weight/profile/manualHeightCm"), 0.0).toDouble();
    const QString ms = s.value(QStringLiteral("weight/profile/manualSex")).toString();
    if (mh > 0 && !ms.isEmpty())
        return {{QStringLiteral("heightCm"), mh}, {QStringLiteral("sex"), ms},
                {QStringLiteral("source"), QStringLiteral("manual")}};
    const double ih = s.value(QStringLiteral("weight/profile/heightCm"), 0.0).toDouble();
    const QString is = s.value(QStringLiteral("weight/profile/sex")).toString();
    if (ih > 0 && !is.isEmpty())
        return {{QStringLiteral("heightCm"), ih}, {QStringLiteral("sex"), is},
                {QStringLiteral("source"), QStringLiteral("intervals")}};
    // Last read off the watch (SettingsWriteService caches it on every settings read).
    const double wh = s.value(QStringLiteral("weight/profile/watchHeightCm"), 0.0).toDouble();
    const QString ws = s.value(QStringLiteral("weight/profile/watchSex")).toString();
    if (wh > 0 && !ws.isEmpty())
        return {{QStringLiteral("heightCm"), wh}, {QStringLiteral("sex"), ws},
                {QStringLiteral("source"), QStringLiteral("watch")}};
    return {{QStringLiteral("heightCm"), 0.0}, {QStringLiteral("sex"), QString()},
            {QStringLiteral("source"), QString()}};
}

void WeightService::setBodyProfile(double heightCm, const QString &sex)
{
    QSettings s;
    const QString sx = sex.left(1).toUpper();
    const bool ok = heightCm >= 100 && heightCm <= 230 && (sx == QLatin1String("M") || sx == QLatin1String("F"));
    s.setValue(QStringLiteral("weight/profile/manualHeightCm"), ok ? heightCm : 0.0);
    s.setValue(QStringLiteral("weight/profile/manualSex"), ok ? sx : QString());
    emit changed();
    refresh();
}

bool WeightService::sendToIntervals() const
{
    return QSettings().value(QStringLiteral("weight/sendToIntervals"), true).toBool();
}

void WeightService::setSendToIntervals(bool on)
{
    QSettings().setValue(QStringLiteral("weight/sendToIntervals"), on);
    emit changed();
}

bool WeightService::sendBodyFatToIntervals() const
{
    return QSettings().value(QStringLiteral("weight/sendBodyFatToIntervals"), false).toBool();
}

void WeightService::setSendBodyFatToIntervals(bool on)
{
    QSettings().setValue(QStringLiteral("weight/sendBodyFatToIntervals"), on);
    emit changed();
}

void WeightService::sendWeighInToIntervals(const QString &date, double weightKg, double bodyFatPct)
{
    if (!sendToIntervals() || weightKg <= 0 || date.isEmpty())
        return;
    const QSettings s;
    const QString athlete =
        s.value(QStringLiteral("connections/intervals_icu/athleteId")).toString();
    const QString key = s.value(QStringLiteral("connections/intervals_icu/apiKey")).toString();
    if (athlete.isEmpty() || key.isEmpty())
        return;                                   // not connected: nothing to send
    QJsonObject body{{QStringLiteral("weight"), qRound(weightKg * 100.0) / 100.0}};
    if (bodyFatPct > 0 && sendBodyFatToIntervals())
        body.insert(QStringLiteral("bodyFat"), qRound(bodyFatPct * 10.0) / 10.0);
    QNetworkRequest req(QUrl(QStringLiteral("https://intervals.icu/api/v1/athlete/%1/wellness/%2")
                                 .arg(athlete, date)));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    req.setRawHeader("Authorization",
                     "Basic " + (QByteArrayLiteral("API_KEY:") + key.toUtf8()).toBase64());
    req.setRawHeader("User-Agent",
                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Sommet/1.0");
    QNetworkReply *reply = m_network.put(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, date]() {
        reply->deleteLater();
        if (reply->error() != QNetworkReply::NoError) {
            m_lastError = tr("Couldn't send the %1 weigh-in to intervals.icu: %2")
                              .arg(date, reply->errorString());
            emit changed();
        }
    });
}

double WeightService::bodyFatOffset() const
{
    return QSettings().value(QStringLiteral("weight/bodyFatOffset"), 0.0).toDouble();
}

void WeightService::setBodyFatOffset(double v)
{
    if (qFuzzyCompare(1.0 + bodyFatOffset(), 1.0 + v))
        return;
    QSettings().setValue(QStringLiteral("weight/bodyFatOffset"), qBound(-20.0, v, 20.0));
    emit changed();
    fetchScale(true);              // the offset applies to the whole history, on read
}

void WeightService::fetchProfile()
{
    const QSettings s;
    const QString athlete =
        s.value(QStringLiteral("connections/intervals_icu/athleteId")).toString();
    const QString key = s.value(QStringLiteral("connections/intervals_icu/apiKey")).toString();
    QNetworkRequest req(QUrl(QStringLiteral("https://intervals.icu/api/v1/athlete/%1").arg(athlete)));
    req.setRawHeader("Authorization",
                     "Basic " + (QByteArrayLiteral("API_KEY:") + key.toUtf8()).toBase64());
    req.setRawHeader("User-Agent",
                     "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Sommet/1.0");
    QNetworkReply *reply = m_network.get(req);
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        if (reply->error() == QNetworkReply::NoError) {
            const auto doc = QJsonDocument::fromJson(reply->readAll());
            const QJsonObject o = doc.isArray() ? doc.array().first().toObject() : doc.object();
            const double hm = o.value(QStringLiteral("height")).toDouble();          // metres
            const QString sex = o.value(QStringLiteral("sex")).toString();
            QSettings s;
            if (hm > 0.5 && (sex == QLatin1String("M") || sex == QLatin1String("F"))) {
                s.setValue(QStringLiteral("weight/profile/heightCm"), qRound(hm * 100.0));
                s.setValue(QStringLiteral("weight/profile/sex"), sex);
            }
        }                                      // on failure the cached profile is used
        fetchScale();
    });
}

// ---------------------------------------------------------------------------------------------
// Local Bluetooth scale (backend: tools/scale_service.py)

void WeightService::fetchScale(bool poll)
{
    const QVariantMap prof = bodyProfile();
    QUrl url(kBackendBase + QStringLiteral("/api/scale/weighins"));
    QUrlQuery q;
    if (prof.value(QStringLiteral("heightCm")).toDouble() > 0) {
        q.addQueryItem(QStringLiteral("height"), QString::number(prof.value(QStringLiteral("heightCm")).toDouble()));
        q.addQueryItem(QStringLiteral("sex"), prof.value(QStringLiteral("sex")).toString());
    }
    q.addQueryItem(QStringLiteral("offset"), QString::number(bodyFatOffset()));
    url.setQuery(q);
    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply, poll]() {
        reply->deleteLater();
        QVariantList list;
        bool ok = false;
        if (reply->error() == QNetworkReply::NoError) {
            const auto o = QJsonDocument::fromJson(reply->readAll()).object();
            ok = o.value(QStringLiteral("ok")).toBool(false);
            for (const auto &v : o.value(QStringLiteral("series")).toArray())
                list.append(v.toObject().toVariantMap());
        } else if (!poll && m_lastError.isEmpty()) {
            m_lastError = reply->errorString();
        }
        if (ok) {
            qint64 newest = 0;
            for (const QVariant &v : list)
                newest = qMax(newest, v.toMap().value(QStringLiteral("t")).toLongLong());
            if (m_scaleSeen && newest > m_scaleLastT && !list.isEmpty()) {
                const QVariantMap last = list.last().toMap();
                emit newScaleWeighIn(last.value(QStringLiteral("date")).toString(),
                                     last.value(QStringLiteral("weightKg")).toDouble());
                sendWeighInToIntervals(last.value(QStringLiteral("date")).toString(),
                                       last.value(QStringLiteral("weightKg")).toDouble(),
                                       last.value(QStringLiteral("bodyFatPct")).toDouble());
            }
            m_scaleLastT = qMax(m_scaleLastT, newest);
            m_scaleSeen = true;
            if (poll && list == m_scaleSeries)
                return;                          // nothing changed: no repaint
            m_scaleSeries = list;
        }
        if (poll)
            rebuild();
        else
            oneSourceDone();
    });
}

void WeightService::pollScale()
{
    QNetworkReply *reply = m_network.get(QNetworkRequest(QUrl(kBackendBase + QStringLiteral("/api/scale/status"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply]() {
        reply->deleteLater();
        QVariantMap st;
        if (reply->error() == QNetworkReply::NoError) {
            st = QJsonDocument::fromJson(reply->readAll()).object().toVariantMap();
        } else {
            st.insert(QStringLiteral("error"), tr("The Sommet backend is not reachable."));
        }
        if (st != m_scaleStatus) {
            m_scaleStatus = st;
            emit scaleChanged();
        }
        // A scale the backend already knows and is listening to (set up earlier, or by another
        // run) is a source here too: remember that, and keep the page live.
        if (!st.value(QStringLiteral("address")).toString().isEmpty()
                && st.value(QStringLiteral("listening")).toBool()
                && !QSettings().value(QStringLiteral("weight/scale/enabled"), false).toBool()) {
            QSettings().setValue(QStringLiteral("weight/scale/enabled"), true);
            m_scaleTimer.start();
            emit changed();
            refresh();
            return;
        }
        fetchScale(true);
    });
}

void WeightService::scalePost(const QString &path, const QJsonObject &body)
{
    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/scale/") + path));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    req.setTransferTimeout(90000);
    QNetworkReply *reply = m_network.post(req, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply, path]() {
        reply->deleteLater();
        const auto o = QJsonDocument::fromJson(reply->readAll()).object();
        if (path == QLatin1String("find")) {
            const bool ok = reply->error() == QNetworkReply::NoError && o.value(QStringLiteral("ok")).toBool();
            emit scaleFound(ok ? o.value(QStringLiteral("found")).toArray().toVariantList() : QVariantList{},
                            ok ? QString() : o.value(QStringLiteral("error")).toString(reply->errorString()));
            return;
        }
        m_scaleStatus = o.toVariantMap();
        emit scaleChanged();
        pollScale();
    });
}

void WeightService::scaleFind(int seconds)
{
    scalePost(QStringLiteral("find"), {{QStringLiteral("seconds"), seconds}});
}

void WeightService::scaleStart(const QString &address)
{
    QSettings().setValue(QStringLiteral("weight/scale/enabled"), true);
    m_scaleSeen = false;
    m_scaleTimer.start();
    QJsonObject body;
    if (!address.isEmpty())
        body.insert(QStringLiteral("address"), address);
    scalePost(QStringLiteral("start"), body);
    emit changed();
}

void WeightService::scaleStop()
{
    QSettings().setValue(QStringLiteral("weight/scale/enabled"), false);
    m_scaleTimer.stop();
    scalePost(QStringLiteral("stop"), {});
    emit changed();
}

void WeightService::scaleForget()
{
    QSettings().setValue(QStringLiteral("weight/scale/enabled"), false);
    m_scaleTimer.stop();
    m_scaleSeries.clear();
    m_scaleLastT = 0;
    scalePost(QStringLiteral("forget"), {});
    rebuild();
}
