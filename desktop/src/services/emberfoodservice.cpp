#include "emberfoodservice.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QRegularExpression>
#include <QSettings>
#include <QUrlQuery>
#include <algorithm>
#include <cmath>
#include <functional>

namespace {
const QByteArray kUserAgent = "Sommet/0.2 (https://github.com/skinnie/sommet)";
const QString kRecentKey = QStringLiteral("ember/recentFoods");
constexpr int kRecentMax = 30;

// A JSON value as a number: OFF sometimes sends numbers as strings.
bool toNum(const QJsonValue &v, double *out)
{
    if (v.isDouble()) { *out = v.toDouble(); return true; }
    if (v.isString()) { bool ok = false; const double d = v.toString().toDouble(&ok); if (ok) { *out = d; return true; } }
    return false;
}
} // namespace

EmberFoodService::EmberFoodService(QObject *parent) : QObject(parent)
{
    const auto arr = QJsonDocument::fromJson(QSettings().value(kRecentKey).toString().toUtf8()).array();
    for (const auto &v : arr) m_recent.append(v.toObject().toVariantMap());
}

QVariantMap EmberFoodService::foodFromOff(const QJsonObject &p)
{
    const auto n = p.value(QStringLiteral("nutriments")).toObject();
    double kcal = 0;
    if (!toNum(n.value(QStringLiteral("energy-kcal_100g")), &kcal)) {
        double kj = 0;   // some products only carry kJ: 1 kcal = 4.184 kJ
        if (!toNum(n.value(QStringLiteral("energy-kj_100g")), &kj)) return {};
        kcal = kj / 4.184;
    }
    const QString name = p.value(QStringLiteral("product_name")).toString().trimmed();
    if (name.isEmpty()) return {};
    QString brand;
    const auto b = p.value(QStringLiteral("brands"));
    const QString brands = b.isArray() ? (b.toArray().isEmpty() ? QString() : b.toArray().at(0).toString())
                                       : b.toString();   // a list in search hits, a string on /product
    brand = brands.split(QLatin1Char(',')).first().trimmed();
    QVariantMap f{{QStringLiteral("source"), QStringLiteral("off")},
                  {QStringLiteral("id"), p.value(QStringLiteral("code")).toString()},
                  {QStringLiteral("name"), name}, {QStringLiteral("brand"), brand},
                  {QStringLiteral("kcal100"), kcal}};
    double v = 0;
    if (toNum(n.value(QStringLiteral("proteins_100g")), &v)) f.insert(QStringLiteral("protein100"), v);
    if (toNum(n.value(QStringLiteral("carbohydrates_100g")), &v)) f.insert(QStringLiteral("carbs100"), v);
    if (toNum(n.value(QStringLiteral("fat_100g")), &v)) f.insert(QStringLiteral("fat100"), v);
    if (toNum(p.value(QStringLiteral("serving_quantity")), &v) && v > 0) f.insert(QStringLiteral("servingG"), v);
    return f;
}

// USDA nutrient ids: 1008 energy kcal (2047/2048 Atwater energy on Foundation foods), 1003
// protein, 1005 carbohydrate, 1004 fat - all per 100 g for Foundation / SR Legacy.
QVariantMap EmberFoodService::foodFromUsda(const QJsonObject &food)
{
    QHash<int, double> byId;
    for (const auto &x : food.value(QStringLiteral("foodNutrients")).toArray()) {
        const auto o = x.toObject();
        double v = 0;
        if (o.value(QStringLiteral("nutrientId")).isDouble() && toNum(o.value(QStringLiteral("value")), &v))
            byId.insert(o.value(QStringLiteral("nutrientId")).toInt(), v);
    }
    const double *kcal = byId.contains(1008) ? &byId[1008] : byId.contains(2047) ? &byId[2047]
                       : byId.contains(2048) ? &byId[2048] : nullptr;
    const QString name = food.value(QStringLiteral("description")).toString().trimmed();
    if (!kcal || name.isEmpty()) return {};
    QVariantMap f{{QStringLiteral("source"), QStringLiteral("usda")},
                  {QStringLiteral("id"), QString::number(food.value(QStringLiteral("fdcId")).toInteger())},
                  {QStringLiteral("name"), name}, {QStringLiteral("brand"), QStringLiteral("USDA")},
                  {QStringLiteral("kcal100"), *kcal}};
    if (byId.contains(1003)) f.insert(QStringLiteral("protein100"), byId[1003]);
    if (byId.contains(1005)) f.insert(QStringLiteral("carbs100"), byId[1005]);
    if (byId.contains(1004)) f.insert(QStringLiteral("fat100"), byId[1004]);
    return f;
}

QVariantMap EmberFoodService::portion(const QVariantMap &food, double grams) const
{
    const double k = std::max(0.0, grams) / 100.0;
    auto r = [&](const char *key) { return qRound(food.value(QString::fromLatin1(key)).toDouble() * k); };
    return {{QStringLiteral("kcal"), r("kcal100")}, {QStringLiteral("protein"), r("protein100")},
            {QStringLiteral("carbs"), r("carbs100")}, {QStringLiteral("fat"), r("fat100")}};
}

void EmberFoodService::remember(const QVariantMap &food)
{
    for (int i = m_recent.size() - 1; i >= 0; --i) {
        const auto o = m_recent[i].toMap();
        if (o.value(QStringLiteral("source")) == food.value(QStringLiteral("source"))
            && o.value(QStringLiteral("id")) == food.value(QStringLiteral("id"))
            && o.value(QStringLiteral("name")) == food.value(QStringLiteral("name")))
            m_recent.removeAt(i);
    }
    m_recent.prepend(food);
    while (m_recent.size() > kRecentMax) m_recent.removeLast();
    QJsonArray arr;
    for (const auto &v : m_recent) arr.append(QJsonObject::fromVariantMap(v.toMap()));
    QSettings().setValue(kRecentKey, QString::fromUtf8(QJsonDocument(arr).toJson(QJsonDocument::Compact)));
    emit recentChanged();
}

void EmberFoodService::clear()
{
    ++m_seq;
    m_pending = 0;
    m_results.clear(); m_offHits.clear(); m_usdaHits.clear();
    emit resultsChanged();
    emit searchingChanged();
}

void EmberFoodService::get(const QUrl &url, int seq, std::function<void(const QJsonObject &)> onJson)
{
    QNetworkRequest req(url);
    req.setRawHeader("User-Agent", kUserAgent);
    req.setRawHeader("Accept", "application/json");
    QNetworkReply *reply = m_net.get(req);
    ++m_pending;
    emit searchingChanged();
    connect(reply, &QNetworkReply::finished, this, [this, reply, seq, onJson]() {
        reply->deleteLater();
        if (seq != m_seq) return;   // a newer search superseded this one
        --m_pending;
        if (reply->error() == QNetworkReply::NoError)
            onJson(QJsonDocument::fromJson(reply->readAll()).object());
        publish();
        emit searchingChanged();
    });
}

// Relevance: query words found at the start of a word in the name (only in the brand: half), then
// shorter names first - so "banana raw" puts USDA's "Bananas, raw" above branded granolas. Same
// rule as EmberFood.ts rankFoods() (unit-tested there).
QVariantList EmberFoodService::rank(const QVariantList &foods, const QString &query)
{
    static const QRegularExpression sep(QStringLiteral("[^\\p{L}\\p{N}]+"));
    const QStringList words = query.toLower().split(sep, Qt::SkipEmptyParts);
    auto found = [&](const QString &text, const QString &w) {
        for (const auto &t : text.toLower().split(sep, Qt::SkipEmptyParts))
            if (t.startsWith(w)) return true;
        return false;
    };
    // A word matched by the name counts 1; one matched only by the brand counts 0.5.
    auto score = [&](const QString &name, const QString &brand) {
        double n = 0;
        for (const auto &w : words) n += found(name, w) ? 1.0 : found(brand, w) ? 0.5 : 0.0;
        return n;
    };
    struct Row { QVariantMap f; double score; int len; int idx; };
    QVector<Row> rows;
    for (int i = 0; i < foods.size(); ++i) {
        const auto f = foods[i].toMap();
        const QString name = f.value(QStringLiteral("name")).toString();
        rows.append({f, score(name, f.value(QStringLiteral("brand")).toString()), int(name.size()), i});
    }
    std::sort(rows.begin(), rows.end(), [](const Row &a, const Row &b) {
        if (a.score != b.score) return a.score > b.score;
        if (a.len != b.len) return a.len < b.len;
        return a.idx < b.idx;
    });
    QVariantList out;
    for (const auto &r : rows) out.append(r.f);
    return out;
}

void EmberFoodService::publish()
{
    m_results = rank(m_offHits + m_usdaHits, m_query);
    emit resultsChanged();
}

void EmberFoodService::search(const QString &query)
{
    const QString q = query.trimmed();
    clear();
    m_query = q;
    if (q.size() < 2) return;
    const int seq = m_seq;
    static const QRegularExpression barcode(QStringLiteral("^\\d{8,14}$"));
    if (barcode.match(q).hasMatch()) {
        get(QUrl(QStringLiteral("https://world.openfoodfacts.org/api/v2/product/%1.json"
                                "?fields=code,product_name,brands,nutriments,serving_quantity").arg(q)),
            seq, [this, q](const QJsonObject &o) {
                if (o.value(QStringLiteral("status")).toInt() != 1) return;
                QJsonObject p = o.value(QStringLiteral("product")).toObject();
                if (!p.contains(QStringLiteral("code"))) p.insert(QStringLiteral("code"), q);
                const auto f = foodFromOff(p);
                if (!f.isEmpty()) m_offHits.append(f);
            });
        return;
    }
    QUrl off(QStringLiteral("https://search.openfoodfacts.org/search"));
    QUrlQuery oq;
    oq.addQueryItem(QStringLiteral("q"), q);
    oq.addQueryItem(QStringLiteral("page_size"), QStringLiteral("20"));
    oq.addQueryItem(QStringLiteral("fields"), QStringLiteral("code,product_name,brands,nutriments,serving_quantity"));
    off.setQuery(oq);
    get(off, seq, [this](const QJsonObject &o) {
        for (const auto &h : o.value(QStringLiteral("hits")).toArray()) {
            const auto f = foodFromOff(h.toObject());
            if (!f.isEmpty()) m_offHits.append(f);
        }
    });
    QUrl usda(QStringLiteral("https://api.nal.usda.gov/fdc/v1/foods/search"));
    QUrlQuery uq;
    uq.addQueryItem(QStringLiteral("query"), q);
    uq.addQueryItem(QStringLiteral("api_key"), QStringLiteral("DEMO_KEY"));
    uq.addQueryItem(QStringLiteral("pageSize"), QStringLiteral("10"));
    uq.addQueryItem(QStringLiteral("dataType"), QStringLiteral("Foundation,SR Legacy"));
    usda.setQuery(uq);
    get(usda, seq, [this](const QJsonObject &o) {
        for (const auto &x : o.value(QStringLiteral("foods")).toArray()) {
            const auto f = foodFromUsda(x.toObject());
            if (!f.isEmpty()) m_usdaHits.append(f);
        }
    });
}
