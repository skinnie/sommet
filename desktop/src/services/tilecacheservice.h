#pragma once

#include <QNetworkAccessManager>
#include <QNetworkReply>
#include <QObject>
#include <QQmlEngine>
#include <QVariantList>
#include <QVector>

// Real, 2026-08-11 (André: "put this offline map cache in the desktop version" - Android's
// TileCache.ts already had an explicit "download this route/POI's area for offline" button,
// desktop only had the passive per-tile disk cache main.cpp's TileNetworkAccessManager adds
// automatically). This is that same explicit region-download feature, ported: given the
// real bounding box of a route/activity/POI, fetch every tile covering it (plus a margin)
// across a fixed practical zoom range, so the area keeps rendering with zero network
// afterward - not a "cache the whole world" background scheme.
//
// Shares its actual cache directory with the QML engine's own tile requests (see
// tilecachepaths.h's own comment on why that has to be exact) - a route downloaded here is
// then just... in the cache, same as if every one of its tiles had been scrolled past
// on-screen. No separate "offline tiles" storage or lookup path anywhere else in the app.
class TileCacheService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool downloading READ downloading NOTIFY downloadingChanged)
    Q_PROPERTY(int downloadDone READ downloadDone NOTIFY progressChanged)
    Q_PROPERTY(int downloadTotal READ downloadTotal NOTIFY progressChanged)
    Q_PROPERTY(int downloadFailed READ downloadFailed NOTIFY progressChanged)
    Q_PROPERTY(qint64 cacheSizeBytes READ cacheSizeBytes NOTIFY cacheSizeChanged)

public:
    explicit TileCacheService(QObject *parent = nullptr);

    bool downloading() const { return m_downloading; }
    int downloadDone() const { return m_done; }
    int downloadTotal() const { return m_total; }
    int downloadFailed() const { return m_failed; }
    qint64 cacheSizeBytes() const { return m_cacheSizeBytes; }

    // points: [{lat, lon}, ...] - the same trackPoints/markers a MapView is already showing
    // (MapWindow.qml passes its own union of both, see MapView.qml's _trackBounds). Ignored
    // while a download is already running - matches Android's own re-entrancy guard.
    // zooms: optional list of zoom levels (defaults to the shared z13-16 set); marginDeg:
    // padding added around the points' bbox (0 for a hand-picked rectangle, ~0.02 for a route).
    // Both defaulted so the existing 2-arg route-download call is unchanged.
    Q_INVOKABLE void downloadRegion(const QVariantList &points, const QString &provider,
                                    const QVariantList &zooms = {}, double marginDeg = 0.02);
    // Tile count for a region — for the offline-maps page's "N tiles / ~X MB" estimate.
    Q_INVOKABLE int countRegionTiles(const QVariantList &points, const QVariantList &zooms = {},
                                     double marginDeg = 0.02) const;
    Q_INVOKABLE void cancelDownload();
    Q_INVOKABLE void refreshCacheSize();
    Q_INVOKABLE void clearCache();

signals:
    void downloadingChanged();
    void progressChanged();
    void cacheSizeChanged();
    void downloadFinished(int done, int total, int failed);

private:
    void setDownloading(bool value);

    QNetworkAccessManager m_network;
    bool m_downloading = false;
    bool m_cancelled = false;
    int m_done = 0;
    int m_total = 0;
    int m_failed = 0;
    qint64 m_cacheSizeBytes = 0;
    QVector<QNetworkReply *> m_activeReplies;
};
