#pragma once

#include <QObject>
#include <QQmlEngine>
#include <QTimer>
#include <QVariantList>

// Real, 2026-08-08 ("let's forward to implement the garmin support as we did in the
// android"). Garmin devices (eTrex series confirmed - opensportsync-main's
// GARMIN_USB_IMPORT_SPEC.md, verified against real hardware, André's own eTrex 30) work
// completely differently from the Ambit3: plain files on a FAT filesystem (USB Mass
// Storage), not the NSP flash protocol - a genuinely separate feature set, not an extension
// of DeviceService/RouteService/PoiService, matching that spec's own explicit stance ("the
// two are not merged or cross-compatible").
//
// The one real difference from the Android implementation: Android needed `libaums` (a
// userspace USB/SCSI/FAT driver) because BlissOS didn't reliably auto-mount MSC devices.
// Desktop Linux does - the same spec doc already confirmed a real eTrex 30 auto-mounts
// cleanly via udisks2, zero special handling. So this class needs no native USB code at
// all: QStorageInfo finds the already-mounted volume, QDir/QFile do everything else.
//
// Discovery: every Garmin device carries `Garmin/GarminDevice.xml` at its mount root - real,
// parseable XML giving model/firmware/part number, and (via <DataType><Name>GPSData</Name>,
// distinguished by TransferDirection) the real read path (OutputFromUnit - recorded
// activities) and write path (InputToUnit - always "Garmin/GPX"), not hardcoded guesses.
// Internal memory and an inserted SD card enumerate as separate mounted volumes.
//
// SAFETY RULE, explicit and non-negotiable (confirmed with the real Android app's own
// author): writes NEVER go to the internal-memory volume, only to an SD card volume if one
// is present - no silent fallback. writeGpxToDevice() refuses outright otherwise.
class GarminService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool connected READ connected NOTIFY deviceChanged)
    Q_PROPERTY(bool detecting READ detecting NOTIFY detectingChanged)
    Q_PROPERTY(QString model READ model NOTIFY deviceChanged)
    Q_PROPERTY(QString firmwareVersion READ firmwareVersion NOTIFY deviceChanged)
    Q_PROPERTY(QString partNumber READ partNumber NOTIFY deviceChanged)
    Q_PROPERTY(bool hasSdCard READ hasSdCard NOTIFY deviceChanged)
    // Testing mode's simulated eTrex (André, 2026-08-11: "we add the garmin etrex, that we
    // also know the characteristics"). Set to the fixture folder and detect() treats its two
    // subfolders as mounted volumes - a real folder tree, walked by the real discovery code,
    // so nothing below this line is stubbed. Empty means "look at real hardware only".
    Q_PROPERTY(QString demoRoot READ demoRoot WRITE setDemoRoot NOTIFY deviceChanged)

    Q_PROPERTY(bool activitiesLoading READ activitiesLoading NOTIFY activitiesChanged)
    // Same shape as ActivityService.activities (name, startTime, distanceMeters,
    // durationSeconds, ascentMeters, track, gpxText) so ActivitiesPage.qml/ActivityCard.qml/
    // ActivityDetail.qml can bind to either source without changes - distance/duration/
    // ascent are computed here from the track's own points+timestamps rather than read from
    // an <extensions> block (real eTrex GPX has none - that block is this project's own
    // exercise_log.py convention, not a real Garmin one).
    Q_PROPERTY(QVariantList activities READ activities NOTIFY activitiesChanged)

    Q_PROPERTY(bool deviceGpxLoading READ deviceGpxLoading NOTIFY deviceGpxChanged)
    // Files in Garmin/GPX (every mounted volume, not just internal memory) that aren't
    // BaseCamp-style "Waypoints*.gpx" - real routes/tracks, matching RouteService.
    // onWatchRoutes' shape (name, pointCount, distanceMeters, ascentMeters, descentMeters,
    // track) as closely as a real GPX file (no descent/waypointCount fields to draw from)
    // allows.
    Q_PROPERTY(QVariantList onDeviceRoutes READ onDeviceRoutes NOTIFY deviceGpxChanged)
    // Files in Garmin/GPX matching "Waypoints*.gpx" (BaseCamp's real naming, confirmed
    // against real hardware) - each {name, lat, lon}, one per <wpt>, matching PoiService.
    // onWatchPois' shape.
    Q_PROPERTY(QVariantList onDevicePois READ onDevicePois NOTIFY deviceGpxChanged)

    Q_PROPERTY(QString writeError READ writeError NOTIFY writeResultChanged)
    Q_PROPERTY(bool writeOk READ writeOk NOTIFY writeResultChanged)

    Q_PROPERTY(bool backingUp READ backingUp NOTIFY backupChanged)
    Q_PROPERTY(QString backupResultText READ backupResultText NOTIFY backupChanged)
    Q_PROPERTY(bool backupOk READ backupOk NOTIFY backupChanged)

public:
    explicit GarminService(QObject *parent = nullptr);

    bool connected() const { return m_connected; }
    bool detecting() const { return m_detecting; }
    QString model() const { return m_model; }
    QString firmwareVersion() const { return m_firmwareVersion; }
    QString partNumber() const { return m_partNumber; }
    bool hasSdCard() const { return m_hasSdCard; }
    QString demoRoot() const { return m_demoRoot; }
    void setDemoRoot(const QString &root);

    bool activitiesLoading() const { return m_activitiesLoading; }
    QVariantList activities() const { return m_activities; }

    bool deviceGpxLoading() const { return m_deviceGpxLoading; }
    QVariantList onDeviceRoutes() const { return m_onDeviceRoutes; }
    QVariantList onDevicePois() const { return m_onDevicePois; }

    QString writeError() const { return m_writeError; }
    bool writeOk() const { return m_writeOk; }

    bool backingUp() const { return m_backingUp; }
    QString backupResultText() const { return m_backupResultText; }
    bool backupOk() const { return m_backupOk; }

    // Scans every currently-mounted volume (QStorageInfo) for Garmin/GarminDevice.xml.
    // Cheap (filesystem checks, no network/subprocess) - safe to call often, unlike
    // DeviceService.refresh()'s real USB round trip.
    Q_INVOKABLE void detect();

    // Reads and parses every real activity file in the internal-memory volume's resolved
    // OutputFromUnit path (e.g. Garmin/GPX/Current).
    Q_INVOKABLE void refreshActivities();

    // Reads and parses Garmin/GPX (routes + POI files) across every mounted volume.
    Q_INVOKABLE void refreshDeviceGpx();

    // SD-card-only, explicit and enforced here (not just in the UI) - fails with a clear
    // writeError if no SD card volume is present. fileName should already end in .gpx.
    Q_INVOKABLE void writeGpxToDevice(const QString &fileName, const QString &gpxText);

    // Deletes one route file (onDeviceRoutes' own fileName) from the SD card's Garmin/GPX -
    // same safety rule as writeGpxToDevice(): a file on the internal-memory volume is refused
    // here, not just hidden in the UI. Returns "" on success, else the error.
    Q_INVOKABLE QString deleteRouteFromSdCard(const QString &fileName);

    // Copies every real file (not just the parsed subset) from every mounted volume's
    // Garmin/GPX folder into destFolder, one subfolder per volume ("internal"/"sdcard") to
    // avoid name collisions between them - a real file copy, not a database export, matching
    // this app's own "no database" stance elsewhere (ActivityService's own cache).
    Q_INVOKABLE void backupToFolder(const QUrl &destFolder);

signals:
    void deviceChanged();
    void detectingChanged();
    void activitiesChanged();
    void deviceGpxChanged();
    void writeResultChanged();
    void backupChanged();

private:
    struct Volume {
        QString rootPath;
        bool hasGarminDeviceXml = false;
        QString activityPath;  // resolved OutputFromUnit path, empty if not found
        QString writePath;     // resolved InputToUnit path, defaults to "Garmin/GPX"
    };

    bool m_connected = false;
    bool m_detecting = false;
    QString m_model;
    QString m_firmwareVersion;
    QString m_partNumber;
    bool m_hasSdCard = false;
    QString m_demoRoot;

    QList<Volume> m_volumes;

    // Real bug, 2026-08-08 ("I had two devices connected at the same time. It only shows
    // one, and it takes a bit of time to re-detect the etrex after I unplug the suunto"):
    // detect() used to only ever run once, from HomePage.qml's own Component.onCompleted -
    // if you're already on Home when you unplug the Ambit3, nothing re-triggers it, so the
    // UI stays showing whichever device was found first until you navigate away and back.
    // Unlike DeviceService's own Ambit3 polling (a real USB round trip through a Python
    // subprocess, deliberately *not* polled continuously - see its own header comment),
    // detect() here is a plain filesystem check (QStorageInfo + one small XML file) - cheap
    // enough to just poll continuously, decoupling "is a Garmin here" from page navigation
    // entirely, the same way WeatherService's own background timer runs unconditionally.
    QTimer m_detectTimer;
    static constexpr int kDetectIntervalMs = 2000;

    bool m_activitiesLoading = false;
    QVariantList m_activities;

    bool m_deviceGpxLoading = false;
    QVariantList m_onDeviceRoutes;
    QVariantList m_onDevicePois;

    QString m_writeError;
    bool m_writeOk = false;

    bool m_backingUp = false;
    QString m_backupResultText;
    bool m_backupOk = false;

    static bool parseGarminDeviceXml(const QString &xmlPath, Volume &volume,
                                      QString &model, QString &firmwareVersion,
                                      QString &partNumber);
    static QVariantMap parseActivityGpx(const QString &gpxText);
    static QVariantMap parseRouteOrPoiGpx(const QString &gpxText, bool isWaypointFile);
    static QString formatFirmwareVersion(const QString &raw);
};
