#pragma once

#include <QJsonArray>
#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QVariantList>

// Step 8. Two real, distinct capabilities this project's Python tooling already has -
// AMBITAPP_SPEC.md's "Existing Features: Route management - Import GPX, Export GPX, Upload,
// Download" - and one honest gap between them:
//
// - "on-watch routes" (what `write_nav.py nav` already reads back and shows) - real, parsed
//   here from `/api/nav`'s raw text output against that command's own known print format
//   (`write_nav.py`'s `show_navigation()`, checked directly - not guessed). No GPS points
//   though: that printout never includes per-point coordinates, only route-level summary
//   fields, so no thumbnail map is possible for a route already on the watch this way.
// - "a local GPX file about to be uploaded" - real GPS points (loadGpxFile() parses them),
//   so *this* direction genuinely does get a thumbnail map preview before upload.
//
// "Download" (pull a specific on-watch route back out as a real GPX file with its actual
// points) - real now, 2026-08-07 (matching the real Android app's own "Route export",
// opensportsync-main's NavigationService.ts): write_nav.py's route_to_gpx() decodes each
// point's watch-relative (x, y) back to absolute lat/lon via ambit_format.inverse_xy(), the
// exact inverse of what building a route already does. Was `downloadAvailable = false`
// (with a comment explaining `nav`'s text summary alone wasn't enough to reconstruct one) -
// still true in spirit, just no longer true in fact.
class RouteService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    Q_PROPERTY(bool downloadAvailable READ downloadAvailable CONSTANT)
    Q_PROPERTY(QString exportedGpx READ exportedGpx NOTIFY exportedGpxChanged)
    Q_PROPERTY(QString exportError READ exportError NOTIFY exportedGpxChanged)
    Q_PROPERTY(bool exporting READ exporting NOTIFY exportingChanged)
    // Each: {name, pointCount, distanceMeters, ascentMeters, descentMeters, waypointCount}
    Q_PROPERTY(QVariantList onWatchRoutes READ onWatchRoutes NOTIFY onWatchRoutesChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)

    // The most recently loaded local GPX file, pending upload - {name, track: [{lat,lon,ele}]}
    Q_PROPERTY(QVariantMap pendingRoute READ pendingRoute NOTIFY pendingRouteChanged)
    Q_PROPERTY(QString pendingRouteGpxText READ pendingRouteGpxText NOTIFY pendingRouteChanged)

    Q_PROPERTY(QString uploadResultText READ uploadResultText NOTIFY uploadResultChanged)
    Q_PROPERTY(bool uploadOk READ uploadOk NOTIFY uploadResultChanged)

public:
    explicit RouteService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    bool downloadAvailable() const { return true; }
    QString exportedGpx() const { return m_exportedGpx; }
    QString exportError() const { return m_exportError; }
    bool exporting() const { return m_exporting; }
    // Each: {name, pointCount, distanceMeters, ascentMeters, descentMeters, waypointCount,
    // track: [{lat, lon, ele}, ...]} - track added 2026-08-08 ("add a map for each gpx"),
    // real points from write_nav.py's nav --json, not a placeholder.
    QVariantList onWatchRoutes() const { return m_onWatchRoutes; }
    QString lastError() const { return m_lastError; }
    QVariantMap pendingRoute() const { return m_pendingRoute; }
    QString pendingRouteGpxText() const { return m_pendingRouteGpxText; }
    QString uploadResultText() const { return m_uploadResultText; }
    bool uploadOk() const { return m_uploadOk; }

    Q_INVOKABLE void refresh();
    // fileUrl: a QUrl from a QML FileDialog's selectedFile - "Import GPX."
    Q_INVOKABLE void loadGpxFile(const QUrl &fileUrl);
    // "Upload" - confirm=false rehearses (real dry-run through write_nav.py, nothing sent),
    // confirm=true actually writes. Matches backend/server.py's own safety default.
    Q_INVOKABLE void uploadPendingRoute(bool confirm);
    // Same, to one specific plugged watch (Routes' "Send to device" lists each one) - pinned by
    // USB product id + serial for this write only, so two Ambit3 Peaks can be told apart.
    Q_INVOKABLE void uploadPendingRouteTo(int productId, const QString &serial);

    // Fetches on-watch route `index`'s full-point GPX (read-only, 0x0b17 same as refresh())
    // into exportedGpx - the caller (RoutesPage.qml) then hands that text straight to
    // LocalFileService.saveText() once the user picks a location in a real save dialog.
    Q_INVOKABLE void exportRoute(int index);

signals:
    void loadingChanged();
    void onWatchRoutesChanged();
    void lastErrorChanged();
    void pendingRouteChanged();
    void uploadResultChanged();
    void exportedGpxChanged();
    void exportingChanged();

private:
    void uploadPending(bool confirm, int productId, const QString &serial);
    QNetworkAccessManager m_network;
    bool m_loading = false;
    QVariantList m_onWatchRoutes;
    QString m_lastError;
    QVariantMap m_pendingRoute;
    QString m_pendingRouteGpxText;
    QString m_uploadResultText;
    bool m_uploadOk = false;
    QString m_exportedGpx;
    QString m_exportError;
    bool m_exporting = false;

    void setLoading(bool value);
    void setLastError(const QString &message);
    static QVariantList parseOnWatchRoutes(const QString &rawOutput);
    // Primary path since 2026-08-08: real per-route tracks from /api/nav's own "routes"
    // JSON array (nav --json), not text-scraped. parseOnWatchRoutes() above stays as a
    // fallback for correctness (an older backend without --json support), not the expected
    // path.
    static QVariantList parseOnWatchRoutesJson(const QJsonArray &routesArray);
};
