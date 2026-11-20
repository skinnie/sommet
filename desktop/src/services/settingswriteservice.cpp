#include "settingswriteservice.h"
#include "backenderror.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QSettings>

#include <utility>

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
    // An error belongs to the watch it happened on.
    m_lastErrorIsRead = false;
    m_rereadAfterFailedWrite = false;
    setLastError(QString());
    emit deviceChanged();
}

void SettingsWriteService::refresh()
{
    setLoading(true);
    QString path = QStringLiteral("/api/settings");
    if (!m_device.isEmpty())
        path += QStringLiteral("?device=") + QUrl::toPercentEncoding(m_device);
    const bool afterFailedWrite = std::exchange(m_rereadAfterFailedWrite, false);
    QNetworkRequest request(backendUrl(path));
    // The page is waiting for it: ahead of the background reads in the backend's watch queue.
    request.setRawHeader("X-Sommet-Foreground", "1");
    QNetworkReply *reply = m_network.get(request);
    connect(reply, &QNetworkReply::finished, this, [this, reply, afterFailedWrite] {
        reply->deleteLater();
        setLoading(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool readOk = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();

        // The re-read after a failed write failed too - the watch was unplugged, or the helper
        // stopped (hardware test 2026-10-23). Keep the write's own "Couldn't change ..." and the
        // values last read, instead of swapping both for the generic read banner.
        if (!readOk && afterFailedWrite && !m_settings.isEmpty()) {
            emit settingsChanged();   // the controls go back to the values last read
            return;
        }

        m_ok = readOk;
        if (!m_ok) {
            const QString own = obj.value(QStringLiteral("error")).toString();
            setLastError(!own.isEmpty() ? own
                : reply->error() != QNetworkReply::NoError ? backendErrorText(reply)
                : obj.value(QStringLiteral("stderr")).toString());
            m_lastErrorIsRead = true;
            emit settingsChanged();
            return;
        }

        // A read that works clears an earlier READ error. A write error survives only the re-read
        // right after that write, so it is seen; any later good read (the page opened again,
        // another watch) clears it too.
        if (m_lastErrorIsRead || !afterFailedWrite) {
            m_lastErrorIsRead = false;
            setLastError(QString());
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
            // Height and sex for the scale's body-fat estimate (2026-10-27: "kinda dumb" to ask
            // for what the watch already has). Used when intervals.icu doesn't give them.
            if (entry.value(QStringLiteral("value")).isDouble()) {
                const double v = entry.value(QStringLiteral("value")).toDouble();
                if (it.key() == QStringLiteral("body_height") && v >= 100 && v <= 230)
                    QSettings().setValue(QStringLiteral("weight/profile/watchHeightCm"), v);
                else if (it.key() == QStringLiteral("gender") || it.key() == QStringLiteral("is_male"))
                    QSettings().setValue(QStringLiteral("weight/profile/watchSex"),
                                         v == 1 ? QStringLiteral("M") : QStringLiteral("F"));
            }

            rows.append(row);
        }
        m_settings = rows;
        // No blanket setLastError(QString()) here (2026-10-22): it wiped a failed write's error
        // the moment the re-read after it came back. Read errors are cleared above.
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
            // The backend's own message first (e.g. "the watch isn't connected", or Testing mode
            // refusing a real write) - Qt's "server replied: Conflict" says nothing (2026-10-22).
            const QString bodyError = obj.value(QStringLiteral("error")).toString();
            QString label = key;   // the row's own label ("Storm alarm"), not the schema key
            for (const auto &row : std::as_const(m_settings)) {
                const auto m = row.toMap();
                if (m.value(QStringLiteral("key")).toString() == key) {
                    label = m.value(QStringLiteral("label")).toString();
                    if (label.isEmpty())
                        label = key;
                    break;
                }
            }
            setLastError(QStringLiteral("Couldn't change %1: %2").arg(label,
                !bodyError.isEmpty() ? bodyError
                : reply->error() != QNetworkReply::NoError ? backendErrorText(reply)
                : QStringLiteral("the watch didn't confirm the change")));
            m_lastErrorIsRead = false;
            // Real value may or may not have changed depending on where the write failed -
            // re-fetching is the only honest way to show what the watch actually has now,
            // rather than leaving the UI showing the value the user tried to set.
            m_rereadAfterFailedWrite = true;
            refresh();
            return;
        }

        setLastError(QString());
        refresh();
    });
}
