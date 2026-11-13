#include "poiservice.h"
#include "backenderror.h"

#include <QFile>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QRegularExpression>
#include <QXmlStreamReader>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

PoiService::PoiService(QObject *parent) : QObject(parent) {}

void PoiService::setLoading(bool value)
{
    if (m_loading == value)
        return;
    m_loading = value;
    emit loadingChanged();
}

// Matches write_nav.py's show_entries() print format exactly (confirmed directly against
// real hardware output, 2026-08-08 - see this class's own header comment):
//   Name='Casa Lille, Nor'  RouteName=''  Timestamp='...'  RouteIndex=0  Type=0  SubType=0
//   TypeIndex=0  Flags=0  Location.Latitude=506236700  Location.Longitude=30559214
// Latitude/Longitude are degrees * 1e7, the same convention as WaypointDescriptor's own
// lat/lon fields (show_navigation()'s `w.lat / 1e7`) - sanity-checked against a real POI
// named after a real place (Lille) decoding to Lille's actual coordinates.
QVariantList PoiService::parseOnWatchPois(const QString &rawOutput)
{
    QVariantList pois;
    // Type = the POI's type byte (its icon), in the watch's own numbering: the Ambit3's 18 or
    // the Traverse's 48 (DeviceCapabilities.qml names it). It was not read before 2026-11-13, so
    // every POI in this list drew the plain icon. \b keeps "SubType=" from matching.
    static const QRegularExpression re(
        QStringLiteral(R"(Name='([^']*)'[^\n]*?\bType=(\d+)[^\n]*?Location\.Latitude=(-?\d+)\s+)"
                        R"(Location\.Longitude=(-?\d+))"));

    auto it = re.globalMatch(rawOutput);
    while (it.hasNext()) {
        const auto m = it.next();
        QVariantMap poi;
        poi[QStringLiteral("name")] = m.captured(1);
        poi[QStringLiteral("type")] = m.captured(2).toInt();
        poi[QStringLiteral("lat")] = m.captured(3).toDouble() / 1e7;
        poi[QStringLiteral("lon")] = m.captured(4).toDouble() / 1e7;
        pois.append(poi);
    }
    return pois;
}

QString PoiService::buildWaypointGpx(const QString &name, double lat, double lon) const
{
    return QStringLiteral(
               "<?xml version=\"1.0\" encoding=\"UTF-8\"?>\n"
               "<gpx version=\"1.1\" creator=\"Sommet\" "
               "xmlns=\"http://www.topografix.com/GPX/1/1\">\n"
               "  <wpt lat=\"%1\" lon=\"%2\"><name>%3</name></wpt>\n"
               "</gpx>\n")
        .arg(lat, 0, 'f', 7)
        .arg(lon, 0, 'f', 7)
        .arg(name.toHtmlEscaped());
}

void PoiService::refresh()
{
    setLoading(true);
    QNetworkReply *reply = m_network.get(
        QNetworkRequest(QUrl(kBackendBase + QStringLiteral("/api/pois"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoading(false);

        if (reply->error() != QNetworkReply::NoError) {
            m_ok = false;
            m_lastError = backendErrorText(reply);
            emit lastErrorChanged();
            emit dataChanged();
            return;
        }
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto obj = doc.object();
        m_ok = obj.value(QStringLiteral("ok")).toBool();
        m_rawOutput = obj.value(QStringLiteral("raw_output")).toString();
        m_onWatchPois = m_ok ? parseOnWatchPois(m_rawOutput) : QVariantList();
        if (!m_ok)
            m_lastError = obj.value(QStringLiteral("stderr")).toString();
        emit dataChanged();
    });
}

void PoiService::addPoi(const QString &name, double lat, double lon, int type, double ele)
{
    QJsonObject body;
    body[QStringLiteral("name")] = name;
    body[QStringLiteral("lat")] = lat;
    body[QStringLiteral("lon")] = lon;
    body[QStringLiteral("type")] = type;
    if (!qIsNaN(ele))
        body[QStringLiteral("ele")] = ele;

    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/pois")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(req, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        m_addOk = doc.object().value(QStringLiteral("ok")).toBool();
        if (m_addOk) {
            m_addResultText = QStringLiteral("POI written to the watch.");
            // The real proof: re-read the on-watch list so the new POI shows up in the
            // "On the watch" card below, straight off the hardware rather than assumed.
            refresh();
        } else {
            // Shown exactly as the backend phrases it, not reworded into something that
            // sounds more finished than it is.
            m_addResultText = doc.object().value(QStringLiteral("error")).toString();
            if (m_addResultText.isEmpty())
                m_addResultText = QStringLiteral("POI write failed (backend unreachable?)");
        }
        emit addResultChanged();
    });
}

void PoiService::removePoi(const QString &name, double lat, double lon)
{
    if (m_removing)
        return;
    QJsonObject body;
    body[QStringLiteral("name")] = name;
    body[QStringLiteral("lat")] = lat;
    body[QStringLiteral("lon")] = lon;

    m_removing = true;
    m_removeResultText.clear();
    emit removeResultChanged();

    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/pois/remove")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(req, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply, name] {
        reply->deleteLater();
        const QJsonObject obj = QJsonDocument::fromJson(reply->readAll()).object();
        m_removing = false;
        m_removeOk = obj.value(QStringLiteral("ok")).toBool();
        if (m_removeOk) {
            const int left = obj.value(QStringLiteral("left")).toInt();
            const int empty = obj.value(QStringLiteral("emptyCleared")).toInt();
            // Same wording as the Android app's poiRemoved().
            m_removeResultText = left == 1
                ? QStringLiteral("“%1” removed. 1 POI left on the watch.").arg(name)
                : QStringLiteral("“%1” removed. %2 POIs left on the watch.").arg(name, QString::number(left));
            if (empty == 1)
                m_removeResultText += QStringLiteral(" An empty entry (a name too long for the "
                                                     "watch) was removed too.");
            else if (empty > 1)
                m_removeResultText += QStringLiteral(" %1 empty entries (a name too long for "
                                                     "the watch) were removed too.").arg(empty);
        } else {
            m_removeResultText = obj.value(QStringLiteral("error")).toString();
            if (m_removeResultText.isEmpty())
                m_removeResultText = reply->error() != QNetworkReply::NoError
                    ? backendErrorText(reply) : QStringLiteral("Removing the POI failed.");
        }
        emit removeResultChanged();
        // Re-read either way: after a failure the list may have been put back.
        refresh();
    });
}

void PoiService::importGpxFile(const QUrl &fileUrl)
{
    m_importedPois.clear();
    m_importError.clear();

    QFile file(fileUrl.toLocalFile());
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text)) {
        m_importError = QStringLiteral("Couldn't open %1").arg(fileUrl.toLocalFile());
        emit importedPoisChanged();
        return;
    }

    // <wpt lat lon><name>...</name></wpt> - GPX's own waypoint element, distinct from
    // <trkpt>/<rtept> (RouteService's own parser, which reads those instead). Same
    // "accept GPX from anywhere" stance as RouteService::loadGpxFile: only looks for what
    // every real-world GPX file actually has, no assumptions beyond that.
    QXmlStreamReader xml(&file);
    bool inWpt = false;
    QString currentTag, pendingLat, pendingLon, pendingName, pendingEle;
    while (!xml.atEnd()) {
        const auto token = xml.readNext();
        if (token == QXmlStreamReader::StartElement) {
            currentTag = xml.name().toString();
            if (currentTag == QStringLiteral("wpt")) {
                inWpt = true;
                pendingLat = xml.attributes().value(QStringLiteral("lat")).toString();
                pendingLon = xml.attributes().value(QStringLiteral("lon")).toString();
                pendingName.clear();
                pendingEle.clear();
            }
        } else if (token == QXmlStreamReader::EndElement) {
            if (xml.name() == QStringLiteral("wpt") && inWpt) {
                QVariantMap poi;
                poi[QStringLiteral("name")] =
                    pendingName.isEmpty() ? QStringLiteral("Unnamed") : pendingName;
                poi[QStringLiteral("lat")] = pendingLat.toDouble();
                poi[QStringLiteral("lon")] = pendingLon.toDouble();
                // <ele> in metres, when the file has one: a Traverse keeps a POI's altitude.
                bool eleOk = false;
                const double ele = pendingEle.toDouble(&eleOk);
                if (eleOk)
                    poi[QStringLiteral("ele")] = ele;
                m_importedPois.append(poi);
                inWpt = false;
            }
            currentTag.clear();
        } else if (token == QXmlStreamReader::Characters && !xml.isWhitespace()) {
            if (inWpt && currentTag == QStringLiteral("name"))
                pendingName = xml.text().toString();
            else if (inWpt && currentTag == QStringLiteral("ele"))
                pendingEle = xml.text().toString();
        }
    }

    if (xml.hasError()) {
        m_importError = QStringLiteral("GPX parse error: %1").arg(xml.errorString());
        m_importedPois.clear();
    } else if (m_importedPois.isEmpty()) {
        m_importError = QStringLiteral("No <wpt> waypoints found in this file.");
    }
    emit importedPoisChanged();
}
