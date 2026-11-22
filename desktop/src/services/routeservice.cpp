#include "routeservice.h"
#include "backenderror.h"

#include <QFile>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QRegularExpression>
#include <QSettings>
#include <QXmlStreamReader>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

RouteService::RouteService(QObject *parent) : QObject(parent)
{
    m_uploadPoll.setInterval(1000);
    m_librarySyncSoon.setSingleShot(true);
    m_librarySyncSoon.setInterval(5000);
    connect(&m_librarySyncSoon, &QTimer::timeout, this, &RouteService::syncLibraries);
    connect(&m_uploadPoll, &QTimer::timeout, this, &RouteService::pollUploadProgress);
}

void RouteService::setUploadProgress(const QString &stage, int done, int total)
{
    if (stage == m_uploadStage && done == m_uploadDone && total == m_uploadTotal)
        return;
    m_uploadStage = stage;
    m_uploadDone = done;
    m_uploadTotal = total;
    emit uploadProgressChanged();
}

// Where a Bluetooth route write is (the backend's "writing_route"); nothing over the cable.
void RouteService::pollUploadProgress()
{
    QNetworkReply *reply = m_network.get(
        QNetworkRequest(QUrl(kBackendBase + QStringLiteral("/api/ble/status"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        if (!m_uploading || reply->error() != QNetworkReply::NoError)
            return;
        const QJsonObject w = QJsonDocument::fromJson(reply->readAll()).object()
                                  .value(QStringLiteral("writing_route")).toObject();
        setUploadProgress(w.value(QStringLiteral("stage")).toString(),
                          w.value(QStringLiteral("done")).toInt(),
                          w.value(QStringLiteral("total")).toInt());
    });
}

void RouteService::setLoading(bool value)
{
    if (m_loading == value)
        return;
    m_loading = value;
    emit loadingChanged();
}

void RouteService::setLastError(const QString &message)
{
    m_lastError = message;
    emit lastErrorChanged();
}

// Matches write_nav.py's show_navigation() print format exactly (checked directly against
// that function's own f-string, not guessed):
//   route[0] 'Morning run'  842 points  12345 m  ascent 210 descent 195  waypoints 3
// Python's `!r` wraps the name in quotes (single, unless the name itself contains one) -
// stripped here for the common case rather than fully reimplementing Python's repr parsing.
QVariantList RouteService::parseOnWatchRoutes(const QString &rawOutput)
{
    QVariantList routes;
    static const QRegularExpression re(
        QStringLiteral(R"(route\[\d+\] (.+?)  (\d+) points  (\d+) m  )"
                        R"(ascent (\d+) descent (\d+)  waypoints (\d+))"));

    auto it = re.globalMatch(rawOutput);
    while (it.hasNext()) {
        const auto m = it.next();
        QString name = m.captured(1);
        if (name.size() >= 2 && (name.front() == QLatin1Char('\'') || name.front() == QLatin1Char('"'))
            && name.back() == name.front()) {
            name = name.mid(1, name.size() - 2);
        }
        QVariantMap route;
        route[QStringLiteral("name")] = name;
        route[QStringLiteral("pointCount")] = m.captured(2).toInt();
        route[QStringLiteral("distanceMeters")] = m.captured(3).toDouble();
        route[QStringLiteral("ascentMeters")] = m.captured(4).toDouble();
        route[QStringLiteral("descentMeters")] = m.captured(5).toDouble();
        route[QStringLiteral("waypointCount")] = m.captured(6).toInt();
        routes.append(route);
    }
    return routes;
}

// Real request 2026-08-08 ("add a map for each gpx"): every field parseOnWatchRoutes()
// above scraped from print()'d text, plus a real per-point track - both come from the same
// /api/nav read now (write_nav.py's nav --json), so a route thumbnail costs nothing extra
// over what was already being fetched for the summary list.
QVariantList RouteService::parseOnWatchRoutesJson(const QJsonArray &routesArray)
{
    QVariantList routes;
    for (const auto &routeValue : routesArray) {
        const auto obj = routeValue.toObject();
        QVariantMap route;
        route[QStringLiteral("name")] = obj.value(QStringLiteral("name")).toString();
        route[QStringLiteral("pointCount")] = obj.value(QStringLiteral("pointCount")).toInt();
        route[QStringLiteral("distanceMeters")] = obj.value(QStringLiteral("distanceMeters")).toDouble();
        route[QStringLiteral("ascentMeters")] = obj.value(QStringLiteral("ascentMeters")).toDouble();
        route[QStringLiteral("descentMeters")] = obj.value(QStringLiteral("descentMeters")).toDouble();
        route[QStringLiteral("waypointCount")] = obj.value(QStringLiteral("waypointCount")).toInt();

        QVariantList track;
        for (const auto &pointValue : obj.value(QStringLiteral("track")).toArray()) {
            const auto point = pointValue.toObject();
            QVariantMap p;
            p[QStringLiteral("lat")] = point.value(QStringLiteral("lat")).toDouble();
            p[QStringLiteral("lon")] = point.value(QStringLiteral("lon")).toDouble();
            const auto eleValue = point.value(QStringLiteral("ele"));
            p[QStringLiteral("ele")] = eleValue.isNull() ? QVariant() : eleValue.toDouble();
            track.append(p);
        }
        route[QStringLiteral("track")] = track;
        routes.append(route);
    }
    return routes;
}

void RouteService::refresh()
{
    setLoading(true);
    setLastError(QString());

    QNetworkReply *reply = m_network.get(
        QNetworkRequest(QUrl(kBackendBase + QStringLiteral("/api/nav"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoading(false);

        if (reply->error() != QNetworkReply::NoError) {
            setLastError(backendErrorText(reply));
            return;
        }
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto obj = doc.object();
        if (!obj.value(QStringLiteral("ok")).toBool()) {
            setLastError(obj.value(QStringLiteral("stderr")).toString());
            m_onWatchRoutes.clear();
            emit onWatchRoutesChanged();
            return;
        }

        const auto routesArray = obj.value(QStringLiteral("routes")).toArray();
        m_onWatchRoutes = !routesArray.isEmpty()
            ? parseOnWatchRoutesJson(routesArray)
            // Fallback for correctness, not the expected path: an older backend without
            // --json support. A genuinely empty watch also has an empty routesArray, and
            // the regex fallback correctly finds nothing there either.
            : parseOnWatchRoutes(obj.value(QStringLiteral("raw_output")).toString());
        emit onWatchRoutesChanged();
    });
}

void RouteService::loadGpxFile(const QUrl &fileUrl)
{
    QFile file(fileUrl.toLocalFile());
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        setLastError(QStringLiteral("Couldn't open %1").arg(fileUrl.toLocalFile()));
        return;
    }
    const QString gpxText = QString::fromUtf8(file.readAll());

    // Generic GPX parse - unlike ActivityService's parser, this has to accept GPX from
    // anywhere (any GPS device/app a person might import a route from), not just this
    // project's own generator, so it only looks for the parts every real-world GPX file
    // actually has: <name> and <trkpt lat lon><ele>. No <extensions> assumptions.
    QVariantList track;
    QString name;
    QXmlStreamReader xml(gpxText);
    QString currentTag, pendingLat, pendingLon, pendingEle;
    while (!xml.atEnd()) {
        const auto token = xml.readNext();
        if (token == QXmlStreamReader::StartElement) {
            currentTag = xml.name().toString();
            if (currentTag == QStringLiteral("trkpt") || currentTag == QStringLiteral("rtept")) {
                pendingLat = xml.attributes().value(QStringLiteral("lat")).toString();
                pendingLon = xml.attributes().value(QStringLiteral("lon")).toString();
                pendingEle.clear();
            }
        } else if (token == QXmlStreamReader::EndElement) {
            const QString tag = xml.name().toString();
            if (tag == QStringLiteral("trkpt") || tag == QStringLiteral("rtept")) {
                QVariantMap point;
                point[QStringLiteral("lat")] = pendingLat.toDouble();
                point[QStringLiteral("lon")] = pendingLon.toDouble();
                point[QStringLiteral("ele")] = pendingEle.toDouble();
                track.append(point);
            }
            currentTag.clear();
        } else if (token == QXmlStreamReader::Characters && !xml.isWhitespace()) {
            if (currentTag == QStringLiteral("name") && name.isEmpty())
                name = xml.text().toString();
            else if (currentTag == QStringLiteral("ele"))
                pendingEle = xml.text().toString();
        }
    }

    m_pendingRoute.clear();
    m_pendingRoute[QStringLiteral("name")] = name.isEmpty() ? fileUrl.fileName() : name;
    m_pendingRoute[QStringLiteral("track")] = track;
    m_pendingRouteGpxText = gpxText;
    m_uploadResultText.clear();
    emit pendingRouteChanged();
}

void RouteService::uploadPendingRoute(bool confirm)
{
    uploadPending(confirm, -1, QString());
}

void RouteService::uploadPendingRouteTo(int productId, const QString &serial)
{
    uploadPending(true, productId, serial);
}

void RouteService::uploadPending(bool confirm, int productId, const QString &serial)
{
    if (m_pendingRouteGpxText.isEmpty()) {
        setLastError(QStringLiteral("No GPX file loaded - use Import GPX first"));
        return;
    }

    QJsonObject body;
    body[QStringLiteral("name")] = m_pendingRoute.value(QStringLiteral("name")).toString();
    body[QStringLiteral("gpx")] = m_pendingRouteGpxText;
    body[QStringLiteral("confirm")] = confirm;
    if (productId >= 0)
        body[QStringLiteral("productId")] = productId;
    if (!serial.isEmpty())
        body[QStringLiteral("serial")] = serial;

    if (m_uploading)
        return;
    m_uploading = true;
    m_uploadResultText.clear();
    setUploadProgress(QString(), 0, 0);
    emit uploadingChanged();
    emit uploadResultChanged();
    m_uploadPoll.start();

    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/routes")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(req, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply, confirm] {
        reply->deleteLater();
        m_uploadPoll.stop();
        m_uploading = false;
        setUploadProgress(QString(), 0, 0);
        emit uploadingChanged();
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto obj = doc.object();
        m_uploadOk = obj.value(QStringLiteral("ok")).toBool();
        const QString raw = obj.value(QStringLiteral("raw_output")).toString();
        if (m_uploadOk) {
            // The Bluetooth answer has no text of its own: until 2026-11-22 a route sent that
            // way ended with nothing on the page at all.
            m_uploadResultText = !raw.isEmpty() ? raw
                : confirm ? QStringLiteral("Route sent to the watch.") : QString();
        } else {
            // "error" is what the Bluetooth path answers with, "stderr" the cable path.
            m_uploadResultText = obj.value(QStringLiteral("error")).toString();
            if (m_uploadResultText.isEmpty())
                m_uploadResultText = obj.value(QStringLiteral("stderr")).toString();
            if (m_uploadResultText.isEmpty())
                m_uploadResultText = raw;
            if (m_uploadResultText.isEmpty())
                m_uploadResultText = reply->error() != QNetworkReply::NoError
                    ? backendErrorText(reply) : QStringLiteral("Sending the route failed.");
        }
        emit uploadResultChanged();
        // The proof: read the watch's routes again, so the new one shows under "On the watch".
        if (m_uploadOk && confirm)
            refresh();
    });
}

void RouteService::exportRoute(int index)
{
    m_exporting = true;
    m_exportedGpx.clear();
    m_exportError.clear();
    emit exportingChanged();

    QJsonObject body;
    body[QStringLiteral("index")] = index;
    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/routes/export")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(req, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        m_exporting = false;
        emit exportingChanged();

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (obj.value(QStringLiteral("ok")).toBool()) {
            m_exportedGpx = obj.value(QStringLiteral("gpx")).toString();
        } else {
            const QString err = obj.value(QStringLiteral("stderr")).toString();
            m_exportError = !err.isEmpty() ? err
                : (reply->error() != QNetworkReply::NoError ? backendErrorText(reply)
                                                              : QStringLiteral("Export failed"));
        }
        emit exportedGpxChanged();
    });
}

void RouteService::syncLibrariesSoon()
{
    m_librarySyncSoon.start();
}

void RouteService::syncLibraries()
{
    QSettings settings;
    const QString url = settings.value(QStringLiteral("connections/sommet_sync/url")).toString();
    const QString token = settings.value(QStringLiteral("connections/sommet_sync/token")).toString();
    if (url.isEmpty() || token.isEmpty())
        return;                         // Sommet Sync is not set up: each device keeps its own
    if (m_librarySyncRunning) {
        m_librarySyncAgain = true;
        return;
    }
    m_librarySyncRunning = true;
    QNetworkRequest request(QUrl(kBackendBase + QStringLiteral("/api/library/sync")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body.insert(QStringLiteral("url"), url);
    body.insert(QStringLiteral("token"), token);     // to our own backend on this computer only
    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        m_librarySyncRunning = false;
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const int changed = obj.value(QStringLiteral("routes")).toObject().value(QStringLiteral("changed")).toInt()
                          + obj.value(QStringLiteral("pois")).toObject().value(QStringLiteral("changed")).toInt();
        if (!obj.value(QStringLiteral("ok")).toBool())
            qInfo().noquote() << "[library-sync]" << obj.value(QStringLiteral("error")).toString(
                QStringLiteral("no answer from the backend"));
        emit librariesSynced(changed > 0);
        if (m_librarySyncAgain) {
            m_librarySyncAgain = false;
            syncLibraries();
        }
    });
}
