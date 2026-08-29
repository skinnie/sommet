#include <QFontDatabase>
#include <QGuiApplication>
#include <QIcon>
#include <QNetworkAccessManager>
#include <QNetworkDiskCache>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QQmlApplicationEngine>
#include <QQmlNetworkAccessManagerFactory>

#include "services/backendprocess.h"
#include "services/tilecachepaths.h"

namespace {

// Real bug found 2026-08-07: MapView.qml's plain Image elements (the XYZ tile renderer, see
// its own header comment) go through QQmlEngine's default QNetworkAccessManager, which sends
// no User-Agent at all. OpenStreetMap's tile usage policy
// (operations.osmfoundation.org/policies/tiles/) requires a real, identifying one on every
// request to tile.openstreetmap.org - without it, the app's own tile requests were getting
// classified as bulk/anonymous traffic and started coming back as OSM's own "Access blocked"
// 418 image instead of map tiles. This is the one place in the app that talks to a raw tile
// server, so the fix lives here (applies to every QML network request) rather than in
// MapView.qml or MapService.qml.
//
// Real perf bug found 2026-08-11 (André: "maps load slow, can't we cache/go offline?"):
// this QNetworkAccessManager had no QNetworkDiskCache attached at all, so *every* tile of
// *every* map view - including one already shown five seconds ago, or yesterday - went
// back out over the network. MapService.qml's `offlineAvailable: false` comment says real
// MBTiles-based offline is future work (a bigger, separate feature: bundling/downloading
// whole regions ahead of time). This is the small, real piece of that rule 11 goal
// ("prefer full offline capability") available now for free: a standard HTTP disk cache,
// same mechanism a browser uses, so any tile already fetched once loads instantly and
// still renders with no network at all, no code changes needed anywhere else since
// QNetworkDiskCache is transparent to the QNetworkAccessManager that owns it.
class TileNetworkAccessManager : public QNetworkAccessManager
{
public:
    explicit TileNetworkAccessManager(QObject *parent = nullptr)
        : QNetworkAccessManager(parent)
    {
        auto *diskCache = new QNetworkDiskCache(this);
        diskCache->setCacheDirectory(mapTileCacheDirectory());
        // Map tiles for a few countries at typical zoom levels comfortably fit in this;
        // Qt evicts oldest-first once it's full, so this is a ceiling, not a reservation.
        diskCache->setMaximumCacheSize(500LL * 1024 * 1024);
        setCache(diskCache);
    }

protected:
    QNetworkReply *createRequest(Operation op, const QNetworkRequest &originalRequest,
                                  QIODevice *outgoingData = nullptr) override
    {
        QNetworkRequest request(originalRequest);
        request.setHeader(QNetworkRequest::UserAgentHeader, QStringLiteral("Sommet/2.0"));
        // PreferCache (not the Qt default AlwaysNetwork/PreferNetwork): map tiles for a
        // given z/x/y almost never change, so once cached there is no real reason to ever
        // revalidate over the network - this is what makes revisits and app restarts load
        // from disk instead of re-downloading.
        request.setAttribute(QNetworkRequest::CacheLoadControlAttribute,
                              QNetworkRequest::PreferCache);
        request.setAttribute(QNetworkRequest::CacheSaveControlAttribute, true);
        return QNetworkAccessManager::createRequest(op, request, outgoingData);
    }
};

class TileNetworkAccessManagerFactory : public QQmlNetworkAccessManagerFactory
{
public:
    QNetworkAccessManager *create(QObject *parent) override
    {
        return new TileNetworkAccessManager(parent);
    }
};

}  // namespace

// Bootstraps the QML application - deliberately thin. Per AMBITAPP_SPEC.md's architecture
// diagram (QML -> ViewModels -> Services -> Current Backend -> libambit), no watch-protocol
// logic belongs here or anywhere in C++ yet: the "Current Backend" for now is the existing,
// hardware-proven Python tooling (tools/*.py), reached over HTTP by C++ Services classes as
// those get built (see ambitapp-v2/backend/ once that step lands). This file only ever
// starts the UI.
int main(int argc, char *argv[])
{
    // Qt Quick's distance-field text renderer does SUBPIXEL antialiasing by default, which paints
    // an orange/blue colour fringe on glyph edges - very visible on bold marks like the POI pin
    // (André, 2026-08-13: the Waypoint box "has the red corners"). Neither Text.renderType nor the
    // font engine's subpixel setting (fontconfig rgba) affect it - only this scenegraph knob does.
    // Must be set before the QGuiApplication/scenegraph reads it. "gray" = grayscale AA, no fringe.
    qputenv("QSG_DISTANCEFIELD_ANTIALIASING", "gray");

    QGuiApplication app(argc, argv);
    app.setOrganizationName(QStringLiteral("Sommet"));
    app.setApplicationName(QStringLiteral("Sommet"));
    // Surfaces to QML as Qt.application.version (used by the About card) so the
    // shown version tracks CMake's PROJECT_VERSION instead of a hardcoded string.
    app.setApplicationVersion(QStringLiteral(APP_VERSION));

    // Start the bundled watch helper if this is a packaged download (no-op in a dev build,
    // where run-desktop.sh starts the Python backend instead). See BackendProcess for why.
    BackendProcess::startIfBundled(&app);

    // Real, 2026-08-09 ("use the android app icon for our desktop app") - same mark as
    // android/src/components/ui/Icon.tsx's "mountain" case, regenerated as a filled
    // silhouette for desktop sizes by tools/packaging/make_desktop_app_icon.py. Set on the
    // QGuiApplication (not a per-window property) so it applies to the taskbar/dock entry
    // too, not just the QML window's own icon.
    app.setWindowIcon(QIcon(QStringLiteral(":/qt/qml/AmbitApp/packaging/icon.png")));

    // Registered once, here, rather than via a QML FontLoader per Icon instance - qml/Icons.qml
    // just references the family name. See assets/fonts/NOTICE.md for what this font actually
    // is (a subsetted Material Symbols Rounded) and why it's this small.
    QFontDatabase::addApplicationFont(
        QStringLiteral(":/qt/qml/AmbitApp/assets/fonts/MaterialSymbolsRounded.ttf"));

    // Must be set before the engine is used for anything (it lazily creates its default
    // QNetworkAccessManager on first network request) - and must outlive `engine`, which this
    // declaration order guarantees (locals are destroyed in reverse declaration order).
    TileNetworkAccessManagerFactory tileNetworkFactory;

    QQmlApplicationEngine engine;
    engine.setNetworkAccessManagerFactory(&tileNetworkFactory);
    QObject::connect(
        &engine, &QQmlApplicationEngine::objectCreationFailed,
        &app, [] { QCoreApplication::exit(-1); },
        Qt::QueuedConnection);
    engine.loadFromModule("AmbitApp", "Main");

    return app.exec();
}
