#include "garminservice.h"

#include <QDateTime>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QStorageInfo>
#include <QXmlStreamReader>
#include <QtMath>
#include <algorithm>
#include <cmath>

namespace {

// Real, well-known formula (great-circle distance) - used here instead of guessing at one,
// matching this project's own "prefer known formulas" practice. Needed because real eTrex
// GPX carries no distance/duration/ascent summary anywhere (that's this project's own
// exercise_log.py convention for Ambit3 activities, not a real Garmin one) - every one of
// those fields for a Garmin activity/route is derived from the track's own points here.
double haversineMeters(double lat1, double lon1, double lat2, double lon2)
{
    constexpr double kEarthRadiusM = 6371000.0;
    const double dLat = qDegreesToRadians(lat2 - lat1);
    const double dLon = qDegreesToRadians(lon2 - lon1);
    const double a = std::sin(dLat / 2) * std::sin(dLat / 2)
        + std::cos(qDegreesToRadians(lat1)) * std::cos(qDegreesToRadians(lat2))
              * std::sin(dLon / 2) * std::sin(dLon / 2);
    return kEarthRadiusM * 2 * std::atan2(std::sqrt(a), std::sqrt(1 - a));
}

}  // namespace

GarminService::GarminService(QObject *parent) : QObject(parent)
{
    m_detectTimer.setInterval(kDetectIntervalMs);
    connect(&m_detectTimer, &QTimer::timeout, this, &GarminService::detect);
    m_detectTimer.start();
}

QString GarminService::formatFirmwareVersion(const QString &raw)
{
    // Garmin's own convention across their device XML schemas (confirmed against real
    // hardware, GARMIN_USB_IMPORT_SPEC.md): an integer with an implied decimal point two
    // digits from the right - 501 -> 5.01, 1250 -> 12.50. Shown verbatim if it doesn't
    // parse as an integer, rather than guessing.
    bool ok = false;
    const int value = raw.toInt(&ok);
    if (!ok)
        return raw;
    const QString padded = QString::number(value).rightJustified(3, QLatin1Char('0'));
    return padded.left(padded.length() - 2) + QLatin1Char('.') + padded.right(2);
}

bool GarminService::parseGarminDeviceXml(const QString &xmlPath, Volume &volume,
                                          QString &model, QString &firmwareVersion,
                                          QString &partNumber)
{
    QFile file(xmlPath);
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text))
        return false;

    QXmlStreamReader xml(&file);
    QString currentTag;
    bool inModel = false;
    bool inFile = false;
    QString dataTypeName, filePath, fileTransferDirection;

    while (!xml.atEnd()) {
        const auto token = xml.readNext();
        if (token == QXmlStreamReader::StartElement) {
            currentTag = xml.name().toString();
            if (currentTag == QStringLiteral("Model")) {
                inModel = true;
            } else if (currentTag == QStringLiteral("DataType")) {
                dataTypeName.clear();
            } else if (currentTag == QStringLiteral("File")) {
                inFile = true;
                filePath.clear();
                fileTransferDirection.clear();
            }
        } else if (token == QXmlStreamReader::EndElement) {
            const QString tag = xml.name().toString();
            if (tag == QStringLiteral("Model")) {
                inModel = false;
            } else if (tag == QStringLiteral("File")) {
                // <DataType><Name>GPSData</Name>'s two <File> children, distinguished by
                // TransferDirection - OutputFromUnit is where recordings live (real reads),
                // InputToUnit is where we'd write routes/POI - see this class's own header
                // comment. Confirmed against real hardware, not guessed.
                if (dataTypeName == QStringLiteral("GPSData") && !filePath.isEmpty()) {
                    if (fileTransferDirection == QStringLiteral("OutputFromUnit"))
                        volume.activityPath = filePath;
                    else if (fileTransferDirection == QStringLiteral("InputToUnit"))
                        volume.writePath = filePath;
                }
                inFile = false;
            }
            currentTag.clear();
        } else if (token == QXmlStreamReader::Characters && !xml.isWhitespace()) {
            const QString text = xml.text().toString();
            if (inModel) {
                if (currentTag == QStringLiteral("PartNumber"))
                    partNumber = text;
                else if (currentTag == QStringLiteral("SoftwareVersion"))
                    firmwareVersion = formatFirmwareVersion(text);
                else if (currentTag == QStringLiteral("Description"))
                    model = text;
            } else if (currentTag == QStringLiteral("Name") && !inFile) {
                dataTypeName = text;
            } else if (inFile && currentTag == QStringLiteral("Path")) {
                filePath = text;
            } else if (inFile && currentTag == QStringLiteral("TransferDirection")) {
                fileTransferDirection = text;
            }
        }
    }

    if (volume.writePath.isEmpty())
        volume.writePath = QStringLiteral("Garmin/GPX");  // real, confirmed default

    return !model.isEmpty();
}

void GarminService::detect()
{
    m_detecting = true;
    emit detectingChanged();

    m_volumes.clear();
    QString model, firmware, partNumber;
    QString garminParentDir;

    // Testing mode substitutes its fixture tree for the mounted-volume scan - deliberately
    // instead of, not alongside, real hardware: a simulated device sitting next to a real one
    // would make it impossible to tell which one the app is talking about.
    QStringList roots;
    if (!m_demoRoot.isEmpty()) {
        roots << m_demoRoot + QStringLiteral("/internal")
              << m_demoRoot + QStringLiteral("/sdcard");
    } else {
        const auto storages = QStorageInfo::mountedVolumes();
        for (const auto &storage : storages) {
            if (storage.isValid() && storage.isReady())
                roots << storage.rootPath();
        }
    }

    for (const QString &root : roots) {
        const QString xmlPath = root + QStringLiteral("/Garmin/GarminDevice.xml");
        if (!QFile::exists(xmlPath))
            continue;

        Volume vol;
        vol.rootPath = root;
        QString thisModel, thisFirmware, thisPartNumber;
        if (!parseGarminDeviceXml(xmlPath, vol, thisModel, thisFirmware, thisPartNumber))
            continue;
        vol.hasGarminDeviceXml = true;
        m_volumes.append(vol);
        if (model.isEmpty()) {
            model = thisModel;
            firmware = thisFirmware;
            partNumber = thisPartNumber;
        }
        garminParentDir = QFileInfo(vol.rootPath).absolutePath();
    }

    // SD card heuristic - real-hardware-unverified (this session had no SD card to test
    // against, unlike the rest of this class's discovery logic). Android identifies the SD
    // card as a second mass-storage partition on the *same physical USB device* (libaums
    // sees the real USB topology); a mounted desktop filesystem doesn't expose that
    // directly through QStorageInfo, so this instead treats any other currently-mounted
    // volume under the same parent removable-media directory (e.g. both under
    // /media/$USER/) without its own GarminDeviceXml as the SD card slot.
    bool hasSd = false;
    if (!m_volumes.isEmpty()) {
        for (const QString &root : roots) {
            // The fixture's two volumes are siblings under one folder, so the real parent-dir
            // heuristic below would reject the second one. Testing mode states outright which
            // volume is the card instead of inferring it.
            const bool demoSd = !m_demoRoot.isEmpty() && root.endsWith(QStringLiteral("/sdcard"));
            if (!demoSd && QFileInfo(root).absolutePath() != garminParentDir)
                continue;
            const bool alreadyKnown = std::any_of(
                m_volumes.begin(), m_volumes.end(),
                [&](const Volume &v) { return v.rootPath == root; });
            if (alreadyKnown)
                continue;
            Volume sdVol;
            sdVol.rootPath = root;
            sdVol.hasGarminDeviceXml = false;
            sdVol.writePath = QStringLiteral("Garmin/GPX");
            m_volumes.append(sdVol);
            hasSd = true;
        }
    }

    // Only re-read/re-parse activity and route files on a real connect transition (a fresh
    // plug-in, or a different device swapped in) - detect() itself now runs continuously
    // (see m_detectTimer's own comment), and re-parsing every real GPX file on the device
    // every 2s even while nothing changed would be real, needless disk work.
    const bool wasConnected = m_connected;
    const QString previousModel = m_model;

    m_connected = !m_volumes.isEmpty() && !model.isEmpty();
    m_model = model;
    m_firmwareVersion = firmware;
    m_partNumber = partNumber;
    m_hasSdCard = hasSd;

    m_detecting = false;
    emit detectingChanged();
    emit deviceChanged();

    if (m_connected && (!wasConnected || previousModel != m_model)) {
        refreshActivities();
        refreshDeviceGpx();
    } else if (!m_connected && wasConnected) {
        m_activities.clear();
        m_onDeviceRoutes.clear();
        m_onDevicePois.clear();
        emit activitiesChanged();
        emit deviceGpxChanged();
    }
}

QVariantMap GarminService::parseActivityGpx(const QString &gpxText)
{
    QVariantMap result;
    result[QStringLiteral("name")] = QString();
    result[QStringLiteral("startTime")] = QString();
    result[QStringLiteral("gpxText")] = gpxText;
    result[QStringLiteral("fitBase64")] = QString();  // Garmin activities read here are GPX-only

    QVariantList track;
    // Real bug found from real hardware data, 2026-08-08: a Garmin "Current.gpx" file can
    // contain multiple disjoint <trkseg> recording sessions (real device behavior, not
    // malformed data - the file isn't necessarily cleared between separate recordings), and
    // the very first version of this summed distance/duration across the *whole* file as if
    // it were one continuous move - a real track showed "997h duration" for a 35 km move
    // because of a multi-day gap between two sessions in the same file. Points and
    // timestamps are now grouped by segment (segments[] indexed by segment number, never by
    // a pointer into the QList, which QList::append() can invalidate on reallocation), and
    // both distance and duration are computed *within* each segment only - a segment
    // boundary is a real recording gap, not a move, so it's never bridged.
    QList<QList<QDateTime>> segments;
    int currentSegmentIndex = -1;
    QXmlStreamReader xml(gpxText);
    QString currentTag, pendingLat, pendingLon, pendingEle, pendingTime;
    bool haveName = false;

    while (!xml.atEnd()) {
        const auto token = xml.readNext();
        if (token == QXmlStreamReader::StartElement) {
            currentTag = xml.name().toString();
            if (currentTag == QStringLiteral("trkseg")) {
                segments.append(QList<QDateTime>());
                currentSegmentIndex = segments.size() - 1;
            } else if (currentTag == QStringLiteral("trkpt")) {
                pendingLat = xml.attributes().value(QStringLiteral("lat")).toString();
                pendingLon = xml.attributes().value(QStringLiteral("lon")).toString();
                pendingEle.clear();
                pendingTime.clear();
            }
        } else if (token == QXmlStreamReader::EndElement) {
            const QString tag = xml.name().toString();
            if (tag == QStringLiteral("trkpt")) {
                if (currentSegmentIndex < 0) {
                    // Defensive: a bare <trkpt> directly under <trk>, no <trkseg> wrapper -
                    // not real GPX per spec, but treated as its own one-segment file rather
                    // than dropped.
                    segments.append(QList<QDateTime>());
                    currentSegmentIndex = segments.size() - 1;
                }
                QVariantMap point;
                point[QStringLiteral("lat")] = pendingLat.toDouble();
                point[QStringLiteral("lon")] = pendingLon.toDouble();
                point[QStringLiteral("ele")] = pendingEle.toDouble();
                point[QStringLiteral("_seg")] = currentSegmentIndex;
                track.append(point);
                const auto dt = QDateTime::fromString(pendingTime, Qt::ISODate);
                if (dt.isValid())
                    segments[currentSegmentIndex].append(dt);
            }
            currentTag.clear();
        } else if (token == QXmlStreamReader::Characters && !xml.isWhitespace()) {
            const QString text = xml.text().toString();
            if (currentTag == QStringLiteral("name") && !haveName) {
                result[QStringLiteral("name")] = text;
                haveName = true;
            } else if (currentTag == QStringLiteral("ele")) {
                pendingEle = text;
            } else if (currentTag == QStringLiteral("time")) {
                pendingTime = text;
                if (result.value(QStringLiteral("startTime")).toString().isEmpty())
                    result[QStringLiteral("startTime")] = text;
            }
        }
    }

    double distance = 0, ascent = 0;
    for (int i = 1; i < track.size(); i++) {
        const auto prev = track.at(i - 1).toMap();
        const auto cur = track.at(i).toMap();
        if (prev.value(QStringLiteral("_seg")).toInt() != cur.value(QStringLiteral("_seg")).toInt())
            continue;  // a real recording-session boundary, not a real move between them
        distance += haversineMeters(prev.value(QStringLiteral("lat")).toDouble(),
                                     prev.value(QStringLiteral("lon")).toDouble(),
                                     cur.value(QStringLiteral("lat")).toDouble(),
                                     cur.value(QStringLiteral("lon")).toDouble());
        const double eleDelta = cur.value(QStringLiteral("ele")).toDouble()
            - prev.value(QStringLiteral("ele")).toDouble();
        if (eleDelta > 0)
            ascent += eleDelta;
    }

    qint64 durationSeconds = 0;
    for (const auto &seg : segments) {
        if (seg.size() >= 2)
            durationSeconds += seg.first().secsTo(seg.last());
    }

    result[QStringLiteral("distanceMeters")] = distance;
    result[QStringLiteral("durationSeconds")] = durationSeconds;
    result[QStringLiteral("ascentMeters")] = ascent;
    result[QStringLiteral("sportTypeRaw")] = -1;  // no sport-type concept in a real Garmin GPX
    result[QStringLiteral("track")] = track;
    // Same real gap as Kailash's TrackLog (see tools/kailash_tracklog.py's own comment): an
    // eTrex has no sport-mode concept at all. Real captured GPX files DO usually carry a
    // <name> (demo_data/garmin/.../Current.gpx has "Morning walk", "Park loop" - the
    // device's own auto-generated/user track title, read from <metadata><name> here since
    // this parser doesn't scope the "name" tag to <trk>) - a real label, but not one that
    // matches any of the 84 Suunto ActivityType names, so keeping it silently sent every
    // eTrex activity to the generic "Unspecified sport" badge. Always "Walk" instead, same
    // as Kailash - not conditional on whether a name was found - so this lands in the same
    // ActivityTypes bucket as an Ambit "Walk" sport mode (ActivityTypes.qml's forName()
    // resolves both to id 12 "Walking"), which is what lets TotalsPage.qml group these
    // together across devices instead of splitting them into an unclassified pile per
    // device.
    result[QStringLiteral("name")] = QStringLiteral("Walking");
    return result;
}

void GarminService::setDemoRoot(const QString &root)
{
    if (m_demoRoot == root)
        return;
    m_demoRoot = root;
    detect();
}

void GarminService::refreshActivities()
{
    m_activitiesLoading = true;
    emit activitiesChanged();

    QVariantList result;
    for (const auto &vol : m_volumes) {
        // Recordings only ever live on the internal-memory volume (see
        // GARMIN_USB_IMPORT_SPEC.md's own discovery strategy) - real, not a guess.
        if (!vol.hasGarminDeviceXml || vol.activityPath.isEmpty())
            continue;
        QDir dir(vol.rootPath + QLatin1Char('/') + vol.activityPath);
        if (!dir.exists())
            continue;
        const auto files =
            dir.entryList({QStringLiteral("*.gpx"), QStringLiteral("*.GPX")}, QDir::Files);
        for (const QString &fileName : files) {
            QFile f(dir.filePath(fileName));
            if (!f.open(QIODevice::ReadOnly | QIODevice::Text))
                continue;
            QVariantMap parsed = parseActivityGpx(QString::fromUtf8(f.readAll()));
            parsed[QStringLiteral("index")] = result.size();
            result.append(parsed);
        }
    }

    m_activities = result;
    m_activitiesLoading = false;
    emit activitiesChanged();
}

QVariantMap GarminService::parseRouteOrPoiGpx(const QString &gpxText, bool isWaypointFile)
{
    Q_UNUSED(isWaypointFile);
    QVariantMap result;
    QVariantList track;
    QVariantList waypoints;
    QString trackName;

    QXmlStreamReader xml(gpxText);
    QString currentTag, pendingLat, pendingLon, pendingEle, pendingWptName;
    bool inWpt = false, inTrkOrRte = false;

    while (!xml.atEnd()) {
        const auto token = xml.readNext();
        if (token == QXmlStreamReader::StartElement) {
            currentTag = xml.name().toString();
            if (currentTag == QStringLiteral("wpt")) {
                inWpt = true;
                pendingLat = xml.attributes().value(QStringLiteral("lat")).toString();
                pendingLon = xml.attributes().value(QStringLiteral("lon")).toString();
                pendingWptName.clear();
            } else if (currentTag == QStringLiteral("trk") || currentTag == QStringLiteral("rte")) {
                inTrkOrRte = true;
            } else if (currentTag == QStringLiteral("trkpt") || currentTag == QStringLiteral("rtept")) {
                pendingLat = xml.attributes().value(QStringLiteral("lat")).toString();
                pendingLon = xml.attributes().value(QStringLiteral("lon")).toString();
                pendingEle.clear();
            }
        } else if (token == QXmlStreamReader::EndElement) {
            const QString tag = xml.name().toString();
            if (tag == QStringLiteral("wpt")) {
                QVariantMap w;
                w[QStringLiteral("name")] =
                    pendingWptName.isEmpty() ? QStringLiteral("Waypoint") : pendingWptName;
                w[QStringLiteral("lat")] = pendingLat.toDouble();
                w[QStringLiteral("lon")] = pendingLon.toDouble();
                waypoints.append(w);
                inWpt = false;
            } else if (tag == QStringLiteral("trk") || tag == QStringLiteral("rte")) {
                inTrkOrRte = false;
            } else if (tag == QStringLiteral("trkpt") || tag == QStringLiteral("rtept")) {
                QVariantMap p;
                p[QStringLiteral("lat")] = pendingLat.toDouble();
                p[QStringLiteral("lon")] = pendingLon.toDouble();
                p[QStringLiteral("ele")] = pendingEle.toDouble();
                track.append(p);
            }
            currentTag.clear();
        } else if (token == QXmlStreamReader::Characters && !xml.isWhitespace()) {
            const QString text = xml.text().toString();
            if (currentTag == QStringLiteral("name")) {
                if (inWpt)
                    pendingWptName = text;
                else if (inTrkOrRte && trackName.isEmpty())
                    trackName = text;
            } else if (currentTag == QStringLiteral("ele") && !inWpt) {
                pendingEle = text;
            }
        }
    }

    result[QStringLiteral("name")] = trackName;
    result[QStringLiteral("track")] = track;
    result[QStringLiteral("waypoints")] = waypoints;
    return result;
}

void GarminService::refreshDeviceGpx()
{
    m_deviceGpxLoading = true;
    emit deviceGpxChanged();

    QVariantList routes, pois;
    for (const auto &vol : m_volumes) {
        const QString writePath =
            vol.writePath.isEmpty() ? QStringLiteral("Garmin/GPX") : vol.writePath;
        QDir dir(vol.rootPath + QLatin1Char('/') + writePath);
        if (!dir.exists())
            continue;

        const auto files =
            dir.entryList({QStringLiteral("*.gpx"), QStringLiteral("*.GPX")}, QDir::Files);
        for (const QString &fileName : files) {
            QFile f(dir.filePath(fileName));
            if (!f.open(QIODevice::ReadOnly | QIODevice::Text))
                continue;
            const QString gpxText = QString::fromUtf8(f.readAll());
            // BaseCamp's real POI naming, confirmed against real hardware
            // (GARMIN_USB_IMPORT_SPEC.md) - everything else in Garmin/GPX is a route/track.
            const bool isWaypointFile =
                fileName.startsWith(QStringLiteral("waypoints"), Qt::CaseInsensitive);
            const auto parsed = parseRouteOrPoiGpx(gpxText, isWaypointFile);

            if (isWaypointFile) {
                for (const auto &wVal : parsed.value(QStringLiteral("waypoints")).toList())
                    pois.append(wVal);
                continue;
            }

            const auto track = parsed.value(QStringLiteral("track")).toList();
            if (track.isEmpty())
                continue;

            QString name = parsed.value(QStringLiteral("name")).toString();
            if (name.isEmpty())
                name = QFileInfo(fileName).completeBaseName();

            double distance = 0, ascent = 0, descent = 0;
            for (int i = 1; i < track.size(); i++) {
                const auto prev = track.at(i - 1).toMap();
                const auto cur = track.at(i).toMap();
                distance += haversineMeters(prev.value(QStringLiteral("lat")).toDouble(),
                                             prev.value(QStringLiteral("lon")).toDouble(),
                                             cur.value(QStringLiteral("lat")).toDouble(),
                                             cur.value(QStringLiteral("lon")).toDouble());
                const double eleDelta = cur.value(QStringLiteral("ele")).toDouble()
                    - prev.value(QStringLiteral("ele")).toDouble();
                if (eleDelta > 0)
                    ascent += eleDelta;
                else
                    descent += -eleDelta;
            }

            QVariantMap route;
            route[QStringLiteral("name")] = name;
            route[QStringLiteral("pointCount")] = track.size();
            route[QStringLiteral("distanceMeters")] = distance;
            route[QStringLiteral("ascentMeters")] = ascent;
            route[QStringLiteral("descentMeters")] = descent;
            route[QStringLiteral("waypointCount")] = 0;
            route[QStringLiteral("track")] = track;
            route[QStringLiteral("gpxText")] = gpxText;
            route[QStringLiteral("fileName")] = fileName;
            route[QStringLiteral("onSdCard")] = !vol.hasGarminDeviceXml;
            routes.append(route);
        }
    }

    m_onDeviceRoutes = routes;
    m_onDevicePois = pois;
    m_deviceGpxLoading = false;
    emit deviceGpxChanged();
}

void GarminService::writeGpxToDevice(const QString &fileName, const QString &gpxText)
{
    m_writeError.clear();
    m_writeOk = false;

    // SAFETY RULE, real and non-negotiable (see this class's own header comment) - never
    // internal memory, no silent fallback. Enforced here, not just left to the UI to check.
    const Volume *sdVolume = nullptr;
    for (const auto &vol : m_volumes) {
        if (!vol.hasGarminDeviceXml) {
            sdVolume = &vol;
            break;
        }
    }
    if (!sdVolume) {
        m_writeError = QStringLiteral(
            "No SD card detected in the device - writing to internal memory is never "
            "allowed. Insert an SD card and try again.");
        emit writeResultChanged();
        return;
    }

    const QString writePath =
        sdVolume->writePath.isEmpty() ? QStringLiteral("Garmin/GPX") : sdVolume->writePath;
    QDir dir(sdVolume->rootPath + QLatin1Char('/') + writePath);
    if (!dir.exists() && !dir.mkpath(QStringLiteral("."))) {
        m_writeError = QStringLiteral("Couldn't create %1 on the SD card").arg(writePath);
        emit writeResultChanged();
        return;
    }

    QFile file(dir.filePath(fileName));
    if (!file.open(QIODevice::WriteOnly | QIODevice::Text)) {
        m_writeError = QStringLiteral("Couldn't open %1 for writing: %2")
                            .arg(file.fileName(), file.errorString());
        emit writeResultChanged();
        return;
    }
    file.write(gpxText.toUtf8());
    m_writeOk = true;
    emit writeResultChanged();
    refreshDeviceGpx();
}

QString GarminService::deleteRouteFromSdCard(const QString &fileName)
{
    if (fileName.isEmpty() || fileName.contains(QLatin1Char('/')) || fileName.contains(QLatin1Char('\\'))
        || fileName.startsWith(QLatin1Char('.')) || !fileName.endsWith(QStringLiteral(".gpx"), Qt::CaseInsensitive)
        || fileName.startsWith(QStringLiteral("waypoints"), Qt::CaseInsensitive))
        return QStringLiteral("Not a route file: %1").arg(fileName);

    // SD card volumes only (no GarminDevice.xml) - never internal memory.
    for (const auto &vol : m_volumes) {
        if (vol.hasGarminDeviceXml)
            continue;
        const QString writePath =
            vol.writePath.isEmpty() ? QStringLiteral("Garmin/GPX") : vol.writePath;
        QDir dir(vol.rootPath + QLatin1Char('/') + writePath);
        if (!dir.exists(fileName))
            continue;
        if (!dir.remove(fileName))
            return QStringLiteral("Couldn't delete %1 from the SD card").arg(fileName);
        refreshDeviceGpx();
        return QString();
    }
    return QStringLiteral("%1 isn't on the SD card - routes in the eTrex's internal memory are never deleted from here.")
        .arg(fileName);
}

void GarminService::backupToFolder(const QUrl &destFolder)
{
    m_backingUp = true;
    emit backupChanged();

    const QString destPath = destFolder.toLocalFile();
    if (destPath.isEmpty()) {
        m_backupOk = false;
        m_backupResultText = QStringLiteral("Invalid destination folder");
        m_backingUp = false;
        emit backupChanged();
        return;
    }

    // Real request 2026-08-08: "backups gpx from Garmin\GPX ... both from internal memory
    // and sdcard" - copies every real file (not a parsed subset) from each mounted volume's
    // Garmin/GPX folder, which is where both routes and POI files actually live (confirmed
    // against real hardware, GARMIN_USB_IMPORT_SPEC.md - there's no separate Garmin\POI
    // folder on real devices; POI files are just "Waypoints*.gpx" inside the same folder).
    // One subfolder per volume to avoid name collisions between them.
    int copied = 0;
    QStringList errors;
    for (const auto &vol : m_volumes) {
        const QString label =
            vol.hasGarminDeviceXml ? QStringLiteral("internal") : QStringLiteral("sdcard");
        const QString writePath =
            vol.writePath.isEmpty() ? QStringLiteral("Garmin/GPX") : vol.writePath;
        QDir srcDir(vol.rootPath + QLatin1Char('/') + writePath);
        if (!srcDir.exists())
            continue;

        const QString destSubdir = destPath + QLatin1Char('/') + label;
        QDir().mkpath(destSubdir);

        const auto files =
            srcDir.entryList({QStringLiteral("*.gpx"), QStringLiteral("*.GPX")}, QDir::Files);
        for (const QString &fileName : files) {
            const QString destFile = destSubdir + QLatin1Char('/') + fileName;
            QFile::remove(destFile);  // QFile::copy refuses to overwrite an existing file
            if (QFile::copy(srcDir.filePath(fileName), destFile))
                copied++;
            else
                errors.append(fileName);
        }
    }

    m_backupOk = errors.isEmpty();
    m_backupResultText = errors.isEmpty()
        ? QStringLiteral("Backed up %1 file(s) to %2").arg(copied).arg(destPath)
        : QStringLiteral("Backed up %1 file(s), %2 failed: %3")
              .arg(copied)
              .arg(errors.size())
              .arg(errors.join(QStringLiteral(", ")));
    m_backingUp = false;
    emit backupChanged();
}
