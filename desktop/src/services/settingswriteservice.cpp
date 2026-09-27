#include "settingswriteservice.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QSettings>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

SettingsWriteService::SettingsWriteService(QObject *parent) : QObject(parent)
{
}

QUrl SettingsWriteService::backendUrl(const QString &path)
{
    return QUrl(kBackendBase + path);
}

void SettingsWriteService::setLoading(bool value)
{
    if (m_loading == value)
        return;
    m_loading = value;
    emit loadingChanged();
}

void SettingsWriteService::setLastError(const QString &message)
{
    m_lastError = message;
    emit lastErrorChanged();
}

void SettingsWriteService::setWritingKey(const QString &key)
{
    if (m_writingKey == key)
        return;
    m_writingKey = key;
    emit writingKeyChanged();
}

void SettingsWriteService::setDevice(const QString &value)
{
    if (m_device == value)
        return;
    m_device = value;
    emit deviceChanged();
}

void SettingsWriteService::refresh()
{
    setLoading(true);
    QString path = QStringLiteral("/api/settings");
    if (!m_device.isEmpty())
        path += QStringLiteral("?device=") + QUrl::toPercentEncoding(m_device);
    QNetworkReply *reply = m_network.get(QNetworkRequest(backendUrl(path)));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoading(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        m_ok = (reply->error() == QNetworkReply::NoError) && obj.value(QStringLiteral("ok")).toBool();

        if (!m_ok) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("GET /api/settings: %1").arg(reply->errorString())
                : QStringLiteral("GET /api/settings: %1")
                    .arg(obj.value(QStringLiteral("error")).toString(
                        obj.value(QStringLiteral("stderr")).toString())));
            emit settingsChanged();
            return;
        }

        QVariantList rows;
        const auto settingsObj = obj.value(QStringLiteral("settings")).toObject();
        for (auto it = settingsObj.constBegin(); it != settingsObj.constEnd(); ++it) {
            const auto entry = it.value().toObject();
            if (!entry.value(QStringLiteral("ok")).toBool())
                continue;  // not present in this watch's own schema - not an error to show

            // Real bug, hit twice on 2026-08-10: this used to copy a hardcoded allowlist of
            // six field names, so every new piece of metadata settings_write.py started
            // reporting was silently dropped on the floor here - first `writable`/`screen`
            // (which collapsed SettingsPage's whole grouping into "Other"), then `label`/
            // `control`/`unit`/`decimals` (which left the page rendering auto-capitalised
            // key names and the wrong control for every field). Copy the entry wholesale
            // instead: the tool is the single source of what a row carries, and a UI that
            // doesn't recognise a key simply ignores it. `choices` is the one field that
            // still needs converting - see below.
            QVariantMap row = entry.toVariantMap();
            row[QStringLiteral("key")] = it.key();

            QVariantList choices;
            for (const auto &c : entry.value(QStringLiteral("choices")).toArray()) {
                const auto pair = c.toArray();
                QVariantMap choice;
                choice[QStringLiteral("value")] = pair.at(0).toVariant();
                choice[QStringLiteral("label")] = pair.at(1).toString();
                choices.append(choice);
            }
            row[QStringLiteral("choices")] = choices;

            // Remember the HR profile for the Coach's local load estimate (Banister TRIMP needs
            // max/rest HR and sex) - it runs without a watch attached, so it can't read them live.
            // The Ambit3 schema calls sex "gender" (1 = Male), the Ambit1/2 one "is_male".
            static const QStringList kHrProfileKeys = {QStringLiteral("max_hr"),
                QStringLiteral("rest_hr"), QStringLiteral("is_male"), QStringLiteral("gender")};
            if (kHrProfileKeys.contains(it.key()) && entry.value(QStringLiteral("value")).isDouble()) {
                const QString key = it.key() == QStringLiteral("gender") ? QStringLiteral("is_male") : it.key();
                QSettings().setValue(QStringLiteral("hrProfile/") + key,
                                     entry.value(QStringLiteral("value")).toDouble());
            }

            rows.append(row);
        }
        m_settings = rows;
        setLastError(QString());
        emit settingsChanged();
    });
}

void SettingsWriteService::writeSetting(const QString &key, const QVariant &value)
{
    setWritingKey(key);

    QNetworkRequest request(backendUrl(QStringLiteral("/api/settings")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("key")] = key;
    // QJsonValue::fromVariant handles both int (enum/number) and double cleanly; the
    // backend/tool only ever needs a plain numeric value, never a display label.
    body[QStringLiteral("value")] = QJsonValue::fromVariant(value);
    body[QStringLiteral("confirm")] = true;
    if (!m_device.isEmpty())
        body[QStringLiteral("device")] = m_device;

    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, key] {
        reply->deleteLater();
        if (m_writingKey == key)
            setWritingKey(QString());

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool writeOk = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();

        if (!writeOk) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/settings (%1): %2").arg(key, reply->errorString())
                : QStringLiteral("POST /api/settings (%1): %2").arg(key,
                    obj.value(QStringLiteral("error")).toString(
                        QStringLiteral("write not confirmed by re-read"))));
            // Real value may or may not have changed depending on where the write failed -
            // re-fetching is the only honest way to show what the watch actually has now,
            // rather than leaving the UI showing the value the user tried to set.
            refresh();
            return;
        }

        setLastError(QString());
        refresh();
    });
}
