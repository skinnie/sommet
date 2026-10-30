#pragma once

#include <QNetworkAccessManager>
#include <QObject>
#include <QQmlEngine>
#include <QUrl>
#include <QVariantList>

// Step 10. AMBITAPP_SPEC.md: "Backup & Restore. Initially support Routes, POIs." The real
// mechanism is write_nav.py's own `nav --save PREFIX` / `restore PREFIX --write` - documented
// there as "the backup that milestone 4 asked for and never had," already hardware-tested.
// This Service is a thin client over backend/server.py's /api/backup(s)/restore, which just
// picks a real place on disk for PREFIX to live - no new backup format invented here.
//
// "Sport Modes, Settings, Profiles" (the spec's own "Future" list for this page) aren't
// covered by this mechanism at all - `nav --save` only ever touched the routes/waypoints
// regions - so they're not simulated here either.
class BackupService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

    Q_PROPERTY(bool loading READ loading NOTIFY loadingChanged)
    // Each: {prefix, label, createdAt (unix seconds)}
    Q_PROPERTY(QVariantList backups READ backups NOTIFY backupsChanged)
    Q_PROPERTY(QString lastActionText READ lastActionText NOTIFY lastActionChanged)
    Q_PROPERTY(bool lastActionOk READ lastActionOk NOTIFY lastActionChanged)

    // 2026-08-07 (V3_CHANGELOG.md): firmware backup, not flashing - there is no known way to
    // write firmware to the watch over this protocol, and separately the downloaded file
    // isn't even a real zip (see tools/firmware_check.py's own note) - this only ever saves
    // a local copy in case Suunto's server stops serving old versions later.
    Q_PROPERTY(bool firmwareLoading READ firmwareLoading NOTIFY firmwareLoadingChanged)
    Q_PROPERTY(bool firmwareCheckOk READ firmwareCheckOk NOTIFY firmwareCheckChanged)
    Q_PROPERTY(QString firmwareLatestVersion READ firmwareLatestVersion NOTIFY firmwareCheckChanged)
    Q_PROPERTY(QString firmwareUploadDate READ firmwareUploadDate NOTIFY firmwareCheckChanged)
    Q_PROPERTY(QString firmwareDownloadUrl READ firmwareDownloadUrl NOTIFY firmwareCheckChanged)
    Q_PROPERTY(QString firmwareActionText READ firmwareActionText NOTIFY firmwareActionChanged)
    Q_PROPERTY(bool firmwareActionOk READ firmwareActionOk NOTIFY firmwareActionChanged)

public:
    explicit BackupService(QObject *parent = nullptr);

    bool loading() const { return m_loading; }
    QVariantList backups() const { return m_backups; }
    QString lastActionText() const { return m_lastActionText; }
    bool lastActionOk() const { return m_lastActionOk; }

    bool firmwareLoading() const { return m_firmwareLoading; }
    bool firmwareCheckOk() const { return m_firmwareCheckOk; }
    QString firmwareLatestVersion() const { return m_firmwareLatestVersion; }
    QString firmwareUploadDate() const { return m_firmwareUploadDate; }
    QString firmwareDownloadUrl() const { return m_firmwareDownloadUrl; }
    QString firmwareActionText() const { return m_firmwareActionText; }
    bool firmwareActionOk() const { return m_firmwareActionOk; }

    Q_INVOKABLE void refresh();
    // destFolder empty -> default ~/AmbitAppBackups; otherwise the backup is written straight
    // into that folder (e.g. a cloud-sync folder), André 2026-08-16.
    Q_INVOKABLE void createBackup(const QUrl &destFolder = QUrl());
    // confirm=false rehearses (real dry-run through write_nav.py restore, nothing written).
    Q_INVOKABLE void restoreBackup(const QString &prefix, bool confirm);
    // Put a backup's learned swim styles on the plugged-in watch (/api/swim-styles/restore):
    // only those settings are written, the watch's current ones are saved first.
    Q_INVOKABLE void restoreSwimStyles(const QString &prefix);

    Q_INVOKABLE void checkFirmware();
    Q_INVOKABLE void downloadFirmware();

signals:
    void loadingChanged();
    void backupsChanged();
    void lastActionChanged();
    void firmwareLoadingChanged();
    void firmwareCheckChanged();
    void firmwareActionChanged();

private:
    QNetworkAccessManager m_network;
    bool m_loading = false;
    QVariantList m_backups;
    QString m_lastActionText;
    bool m_lastActionOk = false;

    bool m_firmwareLoading = false;
    bool m_firmwareCheckOk = false;
    QString m_firmwareLatestVersion;
    QString m_firmwareUploadDate;
    QString m_firmwareDownloadUrl;
    QString m_firmwareActionText;
    bool m_firmwareActionOk = false;

    void setLoading(bool value);
    void setFirmwareLoading(bool value);
};
