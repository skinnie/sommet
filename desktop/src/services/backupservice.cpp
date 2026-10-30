#include "backupservice.h"
#include "backenderror.h"

#include <QDateTime>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

BackupService::BackupService(QObject *parent) : QObject(parent) {}

void BackupService::setLoading(bool value)
{
    if (m_loading == value)
        return;
    m_loading = value;
    emit loadingChanged();
}

void BackupService::setFirmwareLoading(bool value)
{
    if (m_firmwareLoading == value)
        return;
    m_firmwareLoading = value;
    emit firmwareLoadingChanged();
}

void BackupService::refresh()
{
    setLoading(true);
    QNetworkReply *reply = m_network.get(
        QNetworkRequest(QUrl(kBackendBase + QStringLiteral("/api/backups"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoading(false);
        if (reply->error() != QNetworkReply::NoError) {
            m_lastActionText = backendErrorText(reply);
            m_lastActionOk = false;
            emit lastActionChanged();
            return;
        }
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        m_backups.clear();
        for (const auto &v : doc.object().value(QStringLiteral("backups")).toArray())
            m_backups.append(v.toObject().toVariantMap());
        emit backupsChanged();
    });
}

void BackupService::createBackup(const QUrl &destFolder)
{
    setLoading(true);
    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/backup")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    // A chosen folder (e.g. a cloud-sync folder) writes the backup straight there; empty means
    // the default ~/AmbitAppBackups. The folder-save doesn't refresh the local list afterwards
    // (the files live elsewhere), so remember which case this was.
    const bool toFolder = destFolder.isValid() && !destFolder.isEmpty();
    QJsonObject body;
    if (toFolder)
        body[QStringLiteral("dir")] = destFolder.toLocalFile();
    QNetworkReply *reply = m_network.post(req, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, toFolder] {
        reply->deleteLater();
        setLoading(false);
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto obj = doc.object();
        m_lastActionOk = obj.value(QStringLiteral("ok")).toBool();
        if (!m_lastActionOk) {
            m_lastActionText = obj.value(QStringLiteral("stderr")).toString();
        } else if (toFolder) {
            m_lastActionText = QStringLiteral("Saved a backup to the folder");
        } else {
            m_lastActionText = QStringLiteral("Backed up to %1")
                .arg(obj.value(QStringLiteral("label")).toString());
        }
        emit lastActionChanged();
        // Only the default location feeds the "Existing backups" list; a folder-save lives
        // wherever the user pointed it (their cloud folder), so there's nothing to refresh.
        if (m_lastActionOk && !toFolder)
            refresh();
    });
}

void BackupService::restoreBackup(const QString &prefix, bool confirm)
{
    QJsonObject body;
    body[QStringLiteral("prefix")] = prefix;
    body[QStringLiteral("confirm")] = confirm;

    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/restore")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(req, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto obj = doc.object();
        m_lastActionOk = obj.value(QStringLiteral("ok")).toBool();
        const QString raw = obj.value(QStringLiteral("raw_output")).toString();
        const QString err = obj.value(QStringLiteral("stderr")).toString();
        m_lastActionText = m_lastActionOk ? raw : (err.isEmpty() ? raw : err);
        emit lastActionChanged();
    });
}

void BackupService::restoreSwimStyles(const QString &prefix)
{
    QJsonObject body;
    body[QStringLiteral("prefix")] = prefix;
    body[QStringLiteral("confirm")] = true;

    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/swim-styles/restore")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(req, QJsonDocument(body).toJson());
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        m_lastActionOk = obj.value(QStringLiteral("ok")).toBool();
        m_lastActionText = obj.value(QStringLiteral("text")).toString();
        if (m_lastActionText.isEmpty())
            m_lastActionText = obj.value(QStringLiteral("error")).toString();
        if (m_lastActionText.isEmpty())
            m_lastActionText = tr("No answer from the watch.");
        emit lastActionChanged();
    });
}

void BackupService::checkFirmware()
{
    setFirmwareLoading(true);
    QNetworkReply *reply = m_network.get(
        QNetworkRequest(QUrl(kBackendBase + QStringLiteral("/api/firmware"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setFirmwareLoading(false);
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto obj = doc.object();
        m_firmwareCheckOk = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (m_firmwareCheckOk) {
            m_firmwareLatestVersion = obj.value(QStringLiteral("latest_firmware_version")).toString();
            // André, 2026-08-11 (item 19): the raw field reads
            // "2022-03-09T20:23:42ZZZ" - an ISO timestamp with a doubled zone suffix, shown
            // verbatim. He wants the date and the time and nothing else. Parsed rather than
            // string-sliced so a differently-shaped value degrades to itself instead of
            // being cut in the wrong place; the trailing ZZZ is trimmed first because
            // QDateTime will not parse it.
            {
                QString raw = obj.value(QStringLiteral("upload_date")).toString();
                QString trimmed = raw;
                while (trimmed.endsWith(QLatin1Char('Z')))
                    trimmed.chop(1);
                QDateTime parsed = QDateTime::fromString(trimmed, Qt::ISODate);
                m_firmwareUploadDate = parsed.isValid()
                    ? parsed.toString(QStringLiteral("yyyy-MM-dd HH:mm"))
                    : raw;
            }
            m_firmwareDownloadUrl = obj.value(QStringLiteral("download_url")).toString();
        } else {
            m_firmwareActionText = obj.value(QStringLiteral("stderr")).toString();
            emit firmwareActionChanged();
        }
        emit firmwareCheckChanged();
    });
}

void BackupService::downloadFirmware()
{
    setFirmwareLoading(true);
    QNetworkRequest req(QUrl(kBackendBase + QStringLiteral("/api/firmware/download")));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QNetworkReply *reply = m_network.post(req, QByteArray("{}"));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setFirmwareLoading(false);
        const auto doc = QJsonDocument::fromJson(reply->readAll());
        const auto obj = doc.object();
        m_firmwareActionOk = obj.value(QStringLiteral("ok")).toBool();
        m_firmwareActionText = m_firmwareActionOk
            ? QStringLiteral("Saved to %1 (%2 bytes) - backup only, this cannot be used to "
                              "flash the watch.")
                  .arg(obj.value(QStringLiteral("path")).toString())
                  .arg(obj.value(QStringLiteral("size_bytes")).toInt())
            : obj.value(QStringLiteral("stderr")).toString();
        emit firmwareActionChanged();
    });
}
