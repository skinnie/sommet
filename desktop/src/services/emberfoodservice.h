#pragma once
#include <QJsonObject>
#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QVariantList>
#include <QVariantMap>
#include <functional>

// Ember food search - fill a meal's kcal/macros from a food database (André, 2026-09-27,
// "go for ember"; issue #20). Twin of android/src/services/EmberFood.ts (unit-tested there) -
// same sources, same per-100 g normalising, same barcode rule.
//
// Idea from SparkyFitness, NOT its code (non-commercial licence, incompatible with GPL-3.0):
// the same free databases, queried directly -
//   - Open Food Facts (ODbL, attribution): packaged products by name (search.openfoodfacts.org)
//     or barcode (world.openfoodfacts.org/api/v2/product/<code>); a USB barcode scanner types the
//     digits into the search box, so it works with no extra code.
//   - USDA FoodData Central (public domain): generic foods, Foundation + SR Legacy; the user's own
//     free key when set in Settings, else the rate-limited DEMO_KEY.
// Recent picks (last 30) are kept in QSettings so repeat meals work offline.
class EmberFoodService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON
    Q_PROPERTY(bool searching READ searching NOTIFY searchingChanged)
    // [{source, id, name, brand, kcal100, protein100, carbs100, fat100, servingG}]
    Q_PROPERTY(QVariantList results READ results NOTIFY resultsChanged)
    Q_PROPERTY(QVariantList recent READ recent NOTIFY recentChanged)
    // Built with Qt Multimedia + ZXing (CMake SOMMET_HAS_WEBCAM_SCAN): the dialog offers "Scan".
    Q_PROPERTY(bool webcamScanAvailable READ webcamScanAvailable CONSTANT)
    // Personal USDA FoodData Central key (free, api.data.gov). Empty = the shared DEMO_KEY, which
    // allows ~30 searches an hour per network (André, 2026-09-28). Kept in QSettings on this machine.
    Q_PROPERTY(QString usdaApiKey READ usdaApiKey WRITE setUsdaApiKey NOTIFY usdaApiKeyChanged)
public:
    explicit EmberFoodService(QObject *parent = nullptr);
    bool searching() const { return m_pending > 0; }
    QVariantList results() const { return m_results; }
    QVariantList recent() const { return m_recent; }
    QString usdaApiKey() const;
    void setUsdaApiKey(const QString &key);
    bool webcamScanAvailable() const
    {
#ifdef SOMMET_HAS_WEBCAM_SCAN
        return true;
#else
        return false;
#endif
    }

    Q_INVOKABLE void search(const QString &query);   // name, or 8-14 digits = barcode
    Q_INVOKABLE void clear();
    // {kcal, protein, carbs, fat} for `grams` of `food`, rounded.
    Q_INVOKABLE QVariantMap portion(const QVariantMap &food, double grams) const;
    Q_INVOKABLE void remember(const QVariantMap &food);

    static QVariantMap foodFromOff(const QJsonObject &p);
    static QVariantMap foodFromUsda(const QJsonObject &f);
    static QVariantList rank(const QVariantList &foods, const QString &query);

signals:
    void searchingChanged();
    void resultsChanged();
    void recentChanged();
    void usdaApiKeyChanged();

private:
    QNetworkAccessManager m_net;
    QVariantList m_results, m_offHits, m_usdaHits;
    QVariantList m_recent;
    int m_pending = 0;
    int m_seq = 0;
    QString m_query;
    void get(const QUrl &url, int seq, std::function<void(const QJsonObject &)> onJson,
             std::function<void()> onError = {});
    void publish();
};
