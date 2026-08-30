#include "tilecacheservice.h"

#include <QDir>
#include <QDirIterator>
#include <QNetworkDiskCache>
#include <QNetworkRequest>
#include <QtMath>
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
