#include "weightservice.h"

#include <QDate>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
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
    if (s == QStringLiteral("manual")) return 3;   // you typed it - wins ties
    if (s == QStringLiteral("garmin")) return 2;
    return 1;                                       // intervals
}

WeightService::WeightService(QObject *parent) : QObject(parent)
{
    // Restore the chosen provider filter (see the `source` property). Defaults to "all", which
    // is exactly the merge-everything behaviour this service had before the setting existed.
    const QSettings settings;
    m_source = settings.value(QStringLiteral("weight/source"), QStringLiteral("all")).toString();
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
    return intervals || garmin || !loadManual().isEmpty();
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

    m_pending = (haveIntervals ? 1 : 0) + (haveGarmin ? 1 : 0);
    if (m_pending == 0) {          // manual-only (or nothing) - merge immediately
        rebuild();
        return;
    }
    setLoading(true);
    if (haveIntervals) fetchIntervals(days);
    if (haveGarmin) fetchGarmin(days);
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
            const int a = fieldCount(m), b = fieldCount(cur);
            if (a > b || (a == b
                    && sourcePriority(m.value(QStringLiteral("source")).toString())
                       > sourcePriority(cur.value(QStringLiteral("source")).toString())))
                best.insert(date, m);
        }
    };
    // Provider filter (see the `source` property). Manual entries are deliberately NOT gated:
    // they're the user's own typed-in data, not a provider's, so they stay in every mode.
    if (m_source != QLatin1String("garmin"))
        consider(m_intervalsSeries);
    if (m_source != QLatin1String("intervals"))
        consider(m_garminSeries);
    consider(loadManual());

    m_series.clear();
    for (auto it = best.constBegin(); it != best.constEnd(); ++it)  // QMap iterates keys sorted
        m_series.append(it.value());
    emit changed();
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
            m_lastError = reply->errorString();
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
            m_lastError = reply->errorString();
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
