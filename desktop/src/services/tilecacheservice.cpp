#include "tilecacheservice.h"

#include <QDateTime>
#include <QDir>
#include <QDirIterator>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkDiskCache>
#include <QNetworkRequest>
#include <QSet>
#include <QSettings>
#include <QtMath>
#include <QUuid>
#include <algorithm>

#include "tilecachepaths.h"

namespace {

// Same provider URL shapes as desktop's own qml/MapService.qml and Android's
// MapTile.ts - mirrored here in plain C++ because a download has to build these without a
// QML engine or an Image element anywhere in the loop. If a provider's URL shape changes in
// either of those, it needs to change here too.
QString tileUrlFor(const QString &provider, int z, int x, int y)
{
    if (provider == QStringLiteral("ign")) {
        return QStringLiteral("https://data.geopf.fr/wmts?SERVICE=WMTS&REQUEST=GetTile"
                               "&VERSION=1.0.0&LAYER=GEOGRAPHICALGRIDSYSTEMS.PLANIGNV2"
                               "&STYLE=normal&FORMAT=image/png&TILEMATRIXSET=PM"
                               "&TILEMATRIX=%1&TILEROW=%2&TILECOL=%3")
            .arg(z)
            .arg(y)
            .arg(x);
    }
    if (provider == QStringLiteral("osm")) {
        return QStringLiteral("https://tile.openstreetmap.org/%1/%2/%3.png").arg(z).arg(x).arg(y);
    }
    // cyclosm - also the default, matching MapService.qml's own provider default.
    return QStringLiteral("https://a.tile-cyclosm.openstreetmap.fr/cyclosm/%1/%2/%3.png")
        .arg(z)
        .arg(x)
        .arg(y);
}

// Standard Web Mercator tile-index math - the same formula MapView.qml's lonToWorldX/
// latToWorldY use, stopping at the tile index instead of a continuous world pixel.
int lonToTileX(double lon, int z)
{
    return static_cast<int>(std::floor((lon + 180.0) / 360.0 * std::pow(2.0, z)));
}

int latToTileY(double lat, int z)
{
    const double rad = qDegreesToRadians(lat);
    const double merc = std::log(std::tan(rad) + 1.0 / std::cos(rad));
    return static_cast<int>(std::floor((1.0 - merc / M_PI) / 2.0 * std::pow(2.0, z)));
}

// Same real range Android's TileCache.ts/MapScreen.tsx download offline at (2026-08-10:
// "z13-16: wide enough to still see the surrounding area at the low end, close enough to
// read trail detail at the high end") - kept identical so a route downloaded on either
// platform covers the same area at the same detail, matching rule 2's cross-platform parity.
const QList<int> kOfflineZooms = {13, 14, 15, 16};

// Same ~2km margin as Android's downloadRegion() - enough that panning slightly off-track
// still hits the cache.
constexpr double kMarginDegrees = 0.02;

}  // namespace

TileCacheService::TileCacheService(QObject *parent) : QObject(parent)
{
    auto *diskCache = new QNetworkDiskCache(this);
    diskCache->setCacheDirectory(mapTileCacheDirectory());
    diskCache->setMaximumCacheSize(500LL * 1024 * 1024);
    m_network.setCache(diskCache);

    refreshCacheSize();
}

void TileCacheService::setDownloading(bool value)
{
    if (m_downloading == value)
        return;
    m_downloading = value;
    emit downloadingChanged();
}

void TileCacheService::downloadRegion(const QVariantList &points, const QString &provider,
                                      const QVariantList &zooms, double marginDeg)
{
    if (m_downloading || points.isEmpty())
        return;

    double minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
    for (const QVariant &pointVar : points) {
        const QVariantMap point = pointVar.toMap();
        const double lat = point.value(QStringLiteral("lat")).toDouble();
        const double lon = point.value(QStringLiteral("lon")).toDouble();
        minLat = std::min(minLat, lat);
        maxLat = std::max(maxLat, lat);
        minLon = std::min(minLon, lon);
        maxLon = std::max(maxLon, lon);
    }
    minLat -= marginDeg;
    maxLat += marginDeg;
    minLon -= marginDeg;
    maxLon += marginDeg;

    // Caller-supplied zoom levels (the offline-maps page's detail presets), or the shared default.
    QList<int> levels;
    for (const QVariant &z : zooms) {
        const int zi = z.toInt();
        if (zi >= 0 && zi <= 19)
            levels.append(zi);
    }
    if (levels.isEmpty())
        levels = kOfflineZooms;

    QVector<QUrl> urls;
    for (int z : levels) {
        const int minTx = lonToTileX(minLon, z);
        const int maxTx = lonToTileX(maxLon, z);
        // Latitude inverts in tile-Y (north = smaller Y) - maxLat is the smaller tileY.
        const int minTy = latToTileY(maxLat, z);
        const int maxTy = latToTileY(minLat, z);
        for (int tx = minTx; tx <= maxTx; tx++) {
            for (int ty = minTy; ty <= maxTy; ty++) {
                urls.append(QUrl(tileUrlFor(provider, z, tx, ty)));
            }
        }
    }

    m_done = 0;
    m_failed = 0;
    m_total = urls.size();
    m_cancelled = false;
    emit progressChanged();

    if (urls.isEmpty())
        return;

    setDownloading(true);

    // Fired all at once rather than a manual worker queue - QNetworkAccessManager already
    // caps real concurrent connections per host (Qt default: 6), the same practical
    // concurrency Android's own CONCURRENCY=6 worker pool picks by hand. PreferCache means
    // any tile already on disk (from ordinary browsing, or a previous download) resolves
    // immediately with no real network round trip - this is what makes re-running a
    // download after it already succeeded cheap, matching TileCache.ts's own
    // ensureTileCached() skip-if-exists behavior.
    for (const QUrl &url : std::as_const(urls)) {
        QNetworkRequest request(url);
        request.setHeader(QNetworkRequest::UserAgentHeader, QStringLiteral("Sommet/2.0"));
        request.setAttribute(QNetworkRequest::CacheLoadControlAttribute,
                              QNetworkRequest::PreferCache);
        request.setAttribute(QNetworkRequest::CacheSaveControlAttribute, true);

        QNetworkReply *reply = m_network.get(request);
        m_activeReplies.append(reply);
        connect(reply, &QNetworkReply::finished, this, [this, reply] {
            m_activeReplies.removeOne(reply);
            reply->deleteLater();
            if (m_cancelled)
                return;

            if (reply->error() != QNetworkReply::NoError)
                m_failed++;
            m_done++;
            emit progressChanged();

            if (m_done >= m_total) {
                setDownloading(false);
                emit downloadFinished(m_done, m_total, m_failed);
                refreshCacheSize();
            }
        });
    }
}

int TileCacheService::countRegionTiles(const QVariantList &points, const QVariantList &zooms,
                                       double marginDeg) const
{
    if (points.isEmpty())
        return 0;

    double minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
    for (const QVariant &pointVar : points) {
        const QVariantMap point = pointVar.toMap();
        const double lat = point.value(QStringLiteral("lat")).toDouble();
        const double lon = point.value(QStringLiteral("lon")).toDouble();
        minLat = std::min(minLat, lat);
        maxLat = std::max(maxLat, lat);
        minLon = std::min(minLon, lon);
        maxLon = std::max(maxLon, lon);
    }
    minLat -= marginDeg; maxLat += marginDeg;
    minLon -= marginDeg; maxLon += marginDeg;

    QList<int> levels;
    for (const QVariant &z : zooms) {
        const int zi = z.toInt();
        if (zi >= 0 && zi <= 19)
            levels.append(zi);
    }
    if (levels.isEmpty())
        levels = kOfflineZooms;

    int total = 0;
    for (int z : levels) {
        const int minTx = lonToTileX(minLon, z);
        const int maxTx = lonToTileX(maxLon, z);
        const int minTy = latToTileY(maxLat, z);
        const int maxTy = latToTileY(minLat, z);
        total += (maxTx - minTx + 1) * (maxTy - minTy + 1);
    }
    return total;
}

void TileCacheService::cancelDownload()
{
    if (!m_downloading)
        return;
    m_cancelled = true;
    for (QNetworkReply *reply : std::as_const(m_activeReplies))
        reply->abort();
    m_activeReplies.clear();
    setDownloading(false);
    refreshCacheSize();
}

void TileCacheService::refreshCacheSize()
{
    qint64 sum = 0;
    QDirIterator it(mapTileCacheDirectory(), QDir::Files, QDirIterator::Subdirectories);
    while (it.hasNext()) {
        it.next();
        sum += it.fileInfo().size();
    }
    if (sum != m_cacheSizeBytes) {
        m_cacheSizeBytes = sum;
        emit cacheSizeChanged();
    }
}

void TileCacheService::clearCache()
{
    // QNetworkDiskCache::clear() rather than deleting the directory by hand - it owns real
    // bookkeeping alongside the cached tile files, so this keeps m_network's (and, since
    // both point at the same directory, the QML engine's own) view of the cache consistent
    // with what's actually left on disk.
    if (auto *cache = m_network.cache())
        static_cast<QNetworkDiskCache *>(cache)->clear();
    refreshCacheSize();
}

// ── Saved offline areas ─────────────────────────────────────────────────────

namespace {

const QString kRegionsKey = QStringLiteral("map/offlineRegions");
constexpr qint64 kAvgTileBytes = 15360;  // ~15 KB, same estimate the mobile manager uses

QJsonArray loadRegionsArray()
{
    const QString raw = QSettings().value(kRegionsKey).toString();
    if (raw.isEmpty())
        return {};
    return QJsonDocument::fromJson(raw.toUtf8()).array();
}

void storeRegionsArray(const QJsonArray &arr)
{
    QSettings().setValue(kRegionsKey, QString::fromUtf8(QJsonDocument(arr).toJson(QJsonDocument::Compact)));
}

QList<int> zoomsFromJson(const QJsonArray &z)
{
    QList<int> out;
    for (const QJsonValue &v : z) {
        const int zi = v.toInt();
        if (zi >= 0 && zi <= 19)
            out.append(zi);
    }
    return out;
}

// "z/x/y" keys covering a bbox across the given zoom levels.
QSet<QString> regionTileKeys(double minLat, double minLon, double maxLat, double maxLon, const QList<int> &levels)
{
    QSet<QString> keys;
    for (int z : levels) {
        const int minTx = lonToTileX(minLon, z);
        const int maxTx = lonToTileX(maxLon, z);
        const int minTy = latToTileY(maxLat, z);
        const int maxTy = latToTileY(minLat, z);
        for (int tx = minTx; tx <= maxTx; tx++)
            for (int ty = minTy; ty <= maxTy; ty++)
                keys.insert(QStringLiteral("%1/%2/%3").arg(z).arg(tx).arg(ty));
    }
    return keys;
}

}  // namespace

QVariantList TileCacheService::savedRegions() const
{
    QVariantList out;
    const QJsonArray arr = loadRegionsArray();
    for (const QJsonValue &v : arr) {
        const QJsonObject o = v.toObject();
        QVariantList zooms;
        for (const QJsonValue &z : o.value(QStringLiteral("zooms")).toArray())
            zooms.append(z.toInt());
        out.append(QVariantMap{
            { QStringLiteral("id"), o.value(QStringLiteral("id")).toString() },
            { QStringLiteral("name"), o.value(QStringLiteral("name")).toString() },
            { QStringLiteral("provider"), o.value(QStringLiteral("provider")).toString() },
            { QStringLiteral("minLat"), o.value(QStringLiteral("minLat")).toDouble() },
            { QStringLiteral("minLon"), o.value(QStringLiteral("minLon")).toDouble() },
            { QStringLiteral("maxLat"), o.value(QStringLiteral("maxLat")).toDouble() },
            { QStringLiteral("maxLon"), o.value(QStringLiteral("maxLon")).toDouble() },
            { QStringLiteral("zooms"), zooms },
            { QStringLiteral("tileCount"), o.value(QStringLiteral("tileCount")).toInt() },
            { QStringLiteral("bytes"), o.value(QStringLiteral("bytes")).toDouble() },
            { QStringLiteral("savedAt"), o.value(QStringLiteral("savedAt")).toDouble() },
        });
    }
    return out;
}

void TileCacheService::saveRegion(const QString &name, const QString &provider,
                                  const QVariantList &corners, const QVariantList &zooms, int tileCount)
{
    if (corners.isEmpty())
        return;
    double minLat = 90, maxLat = -90, minLon = 180, maxLon = -180;
    for (const QVariant &c : corners) {
        const QVariantMap m = c.toMap();
        const double lat = m.value(QStringLiteral("lat")).toDouble();
        const double lon = m.value(QStringLiteral("lon")).toDouble();
        minLat = std::min(minLat, lat);
        maxLat = std::max(maxLat, lat);
        minLon = std::min(minLon, lon);
        maxLon = std::max(maxLon, lon);
    }
    QJsonArray zoomsJson;
    for (const QVariant &z : zooms)
        zoomsJson.append(z.toInt());

    QJsonObject o;
    o[QStringLiteral("id")] = QUuid::createUuid().toString(QUuid::Id128);
    o[QStringLiteral("name")] = name.trimmed().isEmpty()
        ? QStringLiteral("Area %1, %2").arg((minLat + maxLat) / 2, 0, 'f', 2).arg((minLon + maxLon) / 2, 0, 'f', 2)
        : name.trimmed();
    o[QStringLiteral("provider")] = provider;
    o[QStringLiteral("minLat")] = minLat;
    o[QStringLiteral("minLon")] = minLon;
    o[QStringLiteral("maxLat")] = maxLat;
    o[QStringLiteral("maxLon")] = maxLon;
    o[QStringLiteral("zooms")] = zoomsJson;
    o[QStringLiteral("tileCount")] = tileCount;
    o[QStringLiteral("bytes")] = double(qint64(tileCount) * kAvgTileBytes);
    o[QStringLiteral("savedAt")] = double(QDateTime::currentMSecsSinceEpoch());

    QJsonArray arr = loadRegionsArray();
    arr.prepend(o);
    storeRegionsArray(arr);
    emit savedRegionsChanged();
}

void TileCacheService::deleteSavedRegion(const QString &id)
{
    QJsonArray arr = loadRegionsArray();
    QJsonObject target;
    QJsonArray rest;
    for (const QJsonValue &v : arr) {
        const QJsonObject o = v.toObject();
        if (o.value(QStringLiteral("id")).toString() == id)
            target = o;
        else
            rest.append(o);
    }
    if (target.isEmpty())
        return;

    const QString provider = target.value(QStringLiteral("provider")).toString();
    const QList<int> levels = zoomsFromJson(target.value(QStringLiteral("zooms")).toArray());

    // Tiles still needed by another saved area of the SAME provider — keep those.
    QSet<QString> keep;
    for (const QJsonValue &v : rest) {
        const QJsonObject o = v.toObject();
        if (o.value(QStringLiteral("provider")).toString() != provider)
            continue;
        keep.unite(regionTileKeys(o.value(QStringLiteral("minLat")).toDouble(),
                                  o.value(QStringLiteral("minLon")).toDouble(),
                                  o.value(QStringLiteral("maxLat")).toDouble(),
                                  o.value(QStringLiteral("maxLon")).toDouble(),
                                  zoomsFromJson(o.value(QStringLiteral("zooms")).toArray())));
    }

    // Remove this area's tiles (except the shared ones) from the disk cache.
    if (auto *cache = m_network.cache()) {
        const double minLat = target.value(QStringLiteral("minLat")).toDouble();
        const double minLon = target.value(QStringLiteral("minLon")).toDouble();
        const double maxLat = target.value(QStringLiteral("maxLat")).toDouble();
        const double maxLon = target.value(QStringLiteral("maxLon")).toDouble();
        for (int z : levels) {
            const int minTx = lonToTileX(minLon, z);
            const int maxTx = lonToTileX(maxLon, z);
            const int minTy = latToTileY(maxLat, z);
            const int maxTy = latToTileY(minLat, z);
            for (int tx = minTx; tx <= maxTx; tx++) {
                for (int ty = minTy; ty <= maxTy; ty++) {
                    if (keep.contains(QStringLiteral("%1/%2/%3").arg(z).arg(tx).arg(ty)))
                        continue;
                    cache->remove(QUrl(tileUrlFor(provider, z, tx, ty)));
                }
            }
        }
    }

    storeRegionsArray(rest);
    refreshCacheSize();
    emit savedRegionsChanged();
}
