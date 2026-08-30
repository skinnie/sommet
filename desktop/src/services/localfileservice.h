#pragma once

#include <QObject>
#include <QQmlEngine>
#include <QUrl>

// Real, 2026-08-07: shared save-to-disk helper for every "export a real file, let the user
// pick where" feature (Routes' on-watch route export, Activities' GPX/FIT export) - one
// implementation instead of duplicating QFile-writing logic in each Service, the same
// reasoning as Card.qml for UI. Pure local disk I/O, no network, no watch - the actual GPX/
// FIT bytes always come from elsewhere (RouteService, ActivityService); this only ever
// writes what it's given to wherever a QML FileDialog (SaveFile mode) says.
class LocalFileService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    // FileDialog's own currentFolder default - real request 2026-08-07 ("open window to
    // select where to save, where default location is downloads folder"), matching the real
    // Android app's own save-to-Downloads behavior (opensportsync-main's saveToDownloads())
    // as closely as a desktop file-picker dialog can.
    Q_PROPERTY(QUrl downloadsLocation READ downloadsLocation CONSTANT)
    // Mirrors backend/server.py's own BACKUP_DIR constant (~/AmbitAppBackups) - not asked
    // over HTTP since it's a fixed, well-known path either side can compute the same way,
    // the same "fixed convention, not configurable yet" pattern DeviceService's own comment
    // already documents for the backend's address.
    Q_PROPERTY(QUrl backupsLocation READ backupsLocation CONSTANT)

public:
    explicit LocalFileService(QObject *parent = nullptr);

    QUrl downloadsLocation() const;
    QUrl backupsLocation() const;

    // Both return "" on success, a friendly error message otherwise - callers show it
    // directly. A local file write failure (permissions, full disk, bad path) is already
    // about as user-facing as it gets, unlike a watch/network error - no separate
    // technical-detail log needed the way DeviceService's setLastError() has.
    Q_INVOKABLE QString saveText(const QUrl &fileUrl, const QString &text);
    Q_INVOKABLE QString saveBase64(const QUrl &fileUrl, const QString &base64);

    // Real request 2026-08-07 ("replace the rehearse restore button with open backup
    // folder") - opens folderUrl in the system's own file manager (xdg-open on Linux, via
    // QDesktopServices). Returns false if the OS couldn't find a handler for it; there's
    // nothing more specific to report beyond that.
    Q_INVOKABLE bool openFolder(const QUrl &folderUrl);

    // Back up the app's OWN databases - activities.db (which is also where every activity's
    // GPX/FIT text lives, so this IS the activity backup) and gear.db - into a timestamped
    // "sommet-data-YYYYMMDD-HHmmss" subfolder of destFolder (André, 2026-08-30: "can't choose
    // where to save the database"). An empty destFolder falls back to ~/AmbitAppBackups, the
    // same default the watch backup uses; a chosen folder can be a Dropbox/OneDrive/Drive sync
    // folder for keyless cloud backup, matching the watch-backup card's own model.
    //
    // Nothing to do with a watch or the Python backend - a pure, always-available local copy,
    // so it works with no watch plugged in and no server running. Uses SQLite's own
    // `VACUUM INTO` from a short-lived second connection rather than a raw file copy, so the
    // snapshot is transactionally consistent even if a sync is writing the live DB at the time
    // (a plain copy could tear mid-write). Returns "" on success, a friendly message otherwise,
    // the same convention as saveText().
    Q_INVOKABLE QString backupDatabase(const QUrl &destFolder);
};
