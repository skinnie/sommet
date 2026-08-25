#include "appsservice.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QUrlQuery>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

AppsService::AppsService(QObject *parent) : QObject(parent)
{
}

QUrl AppsService::backendUrl(const QString &path)
{
    return QUrl(kBackendBase + path);
}

void AppsService::setLoading(bool value)
{
    if (m_loading == value)
        return;
    m_loading = value;
    emit loadingChanged();
}

void AppsService::setLastError(const QString &message)
{
    m_lastError = message;
    emit lastErrorChanged();
}

void AppsService::setSearching(bool value)
{
    if (m_searching == value)
        return;
    m_searching = value;
    emit searchingChanged();
}

void AppsService::setInstalling(bool value)
{
    if (m_installing == value)
        return;
    m_installing = value;
    emit installingChanged();
}

void AppsService::setImporting(bool value)
{
    if (m_importing == value)
        return;
    m_importing = value;
    emit importingChanged();
}

void AppsService::setLoggingBusy(bool value)
{
    if (m_loggingBusy == value)
        return;
    m_loggingBusy = value;
    emit loggingBusyChanged();
}

void AppsService::refreshLogging()
{
    QNetworkReply *reply = m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/apps/logging"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!ok) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("GET /api/apps/logging: %1").arg(reply->errorString())
                : QStringLiteral("GET /api/apps/logging: %1").arg(
                    obj.value(QStringLiteral("error")).toString()));
            m_loggedApps.clear();
            emit loggedAppsChanged();
            return;
        }

        m_loggedApps.clear();
        for (const auto &v : obj.value(QStringLiteral("rules")).toArray()) {
            const auto e = v.toObject();
            // Only activated apps are worth a logging toggle - an app the mode doesn't use
            // records nothing regardless of LogRule. use_rule/log_rule arrive as JSON booleans
            // (custom_modes decodes them to Python bools), so read via toVariant().toBool() -
            // QJsonValue::toInt() returns 0 for a JSON bool and would drop every row.
            if (!e.value(QStringLiteral("use_rule")).toVariant().toBool())
                continue;
            QVariantMap row;
            row[QStringLiteral("mode")] = e.value(QStringLiteral("mode")).toInt();
            row[QStringLiteral("modeName")] = e.value(QStringLiteral("mode_name")).toString();
            row[QStringLiteral("slot")] = e.value(QStringLiteral("slot")).toInt();
            row[QStringLiteral("ruleIdx")] = e.value(QStringLiteral("rule_idx")).toInt();
            const auto app = e.value(QStringLiteral("app"));
            row[QStringLiteral("app")] = app.isNull() ? QString() : app.toString();
            row[QStringLiteral("logRule")] = e.value(QStringLiteral("log_rule")).toVariant().toBool();
            m_loggedApps.append(row);
        }
        setLastError(QString());
        emit loggedAppsChanged();
    });
}

void AppsService::setLogging(int mode, int slot, bool on)
{
    setLoggingBusy(true);
    QNetworkRequest request(backendUrl(QStringLiteral("/api/apps/logging")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("mode")] = mode;
    body[QStringLiteral("slot")] = slot;
    body[QStringLiteral("on")] = on;

    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoggingBusy(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!ok) {
            const QString err = reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/apps/logging: %1").arg(reply->errorString())
                : QStringLiteral("POST /api/apps/logging: %1").arg(
                    obj.value(QStringLiteral("error")).toString());
            setLastError(err);
            emit loggingToggled(false, err);
            // Re-read so the switch snaps back to the watch's real state after a failed write.
            refreshLogging();
            return;
        }
        setLastError(QString());
        emit loggingToggled(true, QString());
        // The write changed the watch; re-read the authoritative state.
        refreshLogging();
    });
}

void AppsService::refreshCatalogStatus()
{
    QNetworkReply *reply = m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/apps/catalog_status"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        m_hasCatalog = obj.value(QStringLiteral("hasCatalog")).toBool();
        m_catalogCount = obj.value(QStringLiteral("count")).toInt();
        emit catalogStatusChanged();
    });
}

void AppsService::importCatalog(const QString &path)
{
    setImporting(true);
    QNetworkRequest request(backendUrl(QStringLiteral("/api/apps/import")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    // A QML FileDialog hands back a file:// URL; the backend wants a plain local path.
    const QString local = path.startsWith(QStringLiteral("file:")) ? QUrl(path).toLocalFile() : path;
    QJsonObject body;
    body[QStringLiteral("path")] = local;

    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setImporting(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!ok) {
            const QString err = reply->error() != QNetworkReply::NoError
                ? reply->errorString()
                : obj.value(QStringLiteral("error")).toString();
            setLastError(QStringLiteral("POST /api/apps/import: %1").arg(err));
            emit catalogImported(false, 0, err);
            return;
        }
        setLastError(QString());
        const int count = obj.value(QStringLiteral("count")).toInt();
        m_hasCatalog = count > 0;
        m_catalogCount = count;
        emit catalogStatusChanged();
        emit catalogImported(true, count, QString());
    });
}

void AppsService::refreshInstalledApps()
{
    setLoading(true);
    QNetworkReply *reply = m_network.get(QNetworkRequest(backendUrl(QStringLiteral("/api/apps"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoading(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!ok) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("GET /api/apps: %1").arg(reply->errorString())
                : QStringLiteral("GET /api/apps: %1").arg(obj.value(QStringLiteral("error"))
                    .toString(obj.value(QStringLiteral("stderr")).toString())));
            emit installedAppsChanged();
            return;
        }

        m_installedApps.clear();
        for (const auto &v : obj.value(QStringLiteral("entries")).toArray()) {
            const auto e = v.toObject();
            QVariantMap row;
            row[QStringLiteral("ruleIdx")] = e.value(QStringLiteral("ruleIdx")).toInt();
            row[QStringLiteral("name")] = e.value(QStringLiteral("name")).toString();
            row[QStringLiteral("activityId")] = e.value(QStringLiteral("activityId")).toInt();
            row[QStringLiteral("binaryLength")] = e.value(QStringLiteral("binaryLength")).toInt();
            const auto match = e.value(QStringLiteral("catalogMatch"));
            if (match.isObject()) {
                QVariantMap m;
                const auto mo = match.toObject();
                m[QStringLiteral("ruleId")] = mo.value(QStringLiteral("ruleId")).toInt();
                m[QStringLiteral("name")] = mo.value(QStringLiteral("name")).toString();
                m[QStringLiteral("categoryId")] = mo.value(QStringLiteral("categoryId")).toInt();
                m[QStringLiteral("description")] = mo.value(QStringLiteral("description")).toString();
                row[QStringLiteral("catalogMatch")] = m;
            }
            m_installedApps.append(row);
        }
        setLastError(QString());
        emit installedAppsChanged();
    });
}

void AppsService::searchCatalog(const QString &query, const QString &variant, int categoryId)
{
    setSearching(true);
    QUrl url = backendUrl(QStringLiteral("/api/apps/catalog"));
    QUrlQuery q;
    if (!query.isEmpty())
        q.addQueryItem(QStringLiteral("q"), query);
    if (!variant.isEmpty())
        q.addQueryItem(QStringLiteral("variant"), variant);
    if (categoryId >= 0)
        q.addQueryItem(QStringLiteral("category"), QString::number(categoryId));
    url.setQuery(q);

    QNetworkReply *reply = m_network.get(QNetworkRequest(url));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setSearching(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!ok) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("GET /api/apps/catalog: %1").arg(reply->errorString())
                : QStringLiteral("GET /api/apps/catalog: %1").arg(
                    obj.value(QStringLiteral("error")).toString()));
            m_searchResults.clear();
            emit searchResultsChanged();
            return;
        }

        m_searchResults.clear();
        for (const auto &v : obj.value(QStringLiteral("results")).toArray()) {
            const auto e = v.toObject();
            QVariantMap row;
            row[QStringLiteral("ruleId")] = e.value(QStringLiteral("ruleId")).toInt();
            row[QStringLiteral("name")] = e.value(QStringLiteral("name")).toString();
            row[QStringLiteral("categoryId")] = e.value(QStringLiteral("categoryId")).toInt();
            row[QStringLiteral("activityId")] = e.value(QStringLiteral("activityId")).toInt();
            row[QStringLiteral("description")] = e.value(QStringLiteral("description")).toString();
            row[QStringLiteral("userCount")] = e.value(QStringLiteral("userCount")).toInt();
            m_searchResults.append(row);
        }
        emit searchResultsChanged();
    });
}

void AppsService::install(int mode, int display, int field, int ruleId, bool confirm)
{
    setInstalling(true);

    QNetworkRequest request(backendUrl(QStringLiteral("/api/apps/install")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("mode")] = mode;
    body[QStringLiteral("display")] = display;
    body[QStringLiteral("field")] = field;
    body[QStringLiteral("ruleId")] = ruleId;
    body[QStringLiteral("confirm")] = confirm;

    QNetworkReply *reply = m_network.post(request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, confirm] {
        reply->deleteLater();
        setInstalling(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();

        QVariantMap result;
        result[QStringLiteral("ok")] = ok;
        result[QStringLiteral("dryRun")] = !confirm;
        if (ok) {
            result[QStringLiteral("name")] = obj.value(QStringLiteral("name")).toString();
            if (obj.contains(QStringLiteral("wouldBeRuleIdx")))
                result[QStringLiteral("wouldBeRuleIdx")] = obj.value(QStringLiteral("wouldBeRuleIdx")).toInt();
            if (obj.contains(QStringLiteral("ruleIdx")))
                result[QStringLiteral("ruleIdx")] = obj.value(QStringLiteral("ruleIdx")).toInt();
            if (obj.contains(QStringLiteral("ruleId")))
                result[QStringLiteral("ruleId")] = obj.value(QStringLiteral("ruleId")).toInt();
        } else {
            result[QStringLiteral("error")] = reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/apps/install: %1").arg(reply->errorString())
                : QStringLiteral("POST /api/apps/install: %1").arg(
                    obj.value(QStringLiteral("error")).toString());
            setLastError(result[QStringLiteral("error")].toString());
        }
        m_lastInstallResult = result;
        emit lastInstallResultChanged();

        // A real write changed the watch's own Apps region - refresh the installed list
        // so a UI showing it doesn't go stale.
        if (ok && confirm)
            refreshInstalledApps();
    });
}
