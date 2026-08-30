#include "syncservice.h"

#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

SyncService::SyncService(QObject *parent) : QObject(parent) {}

void SyncService::setBusy(bool value)
{
    if (m_busy == value)
        return;
    m_busy = value;
    emit busyChanged();
}

// One JSON POST helper: sets busy, decodes the reply into a QVariantMap, and hands it plus a
// transport-ok flag to onDone. Keeps every action below to just its own before/after logic.
void SyncService::postJson(const QString &path, const QVariantMap &body,
                           std::function<void(const QVariantMap &, bool ok)> onDone)
{
    setBusy(true);
    QNetworkRequest req(QUrl(kBackendBase + path));
    req.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    const QByteArray payload =
        QJsonDocument(QJsonObject::fromVariantMap(body)).toJson(QJsonDocument::Compact);
    QNetworkReply *reply = m_network.post(req, payload);
    connect(reply, &QNetworkReply::finished, this, [this, reply, onDone] {
        reply->deleteLater();
        setBusy(false);
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        onDone(obj.toVariantMap(), reply->error() == QNetworkReply::NoError);
    });
}

void SyncService::refreshState()
{
    setBusy(true);
    QNetworkReply *reply =
        m_network.get(QNetworkRequest(QUrl(kBackendBase + QStringLiteral("/api/sync/state"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setBusy(false);
        if (reply->error() != QNetworkReply::NoError) {
            m_connected = {};
            emit stateChanged();
            return;
        }
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        m_connected = obj.value(QStringLiteral("connected")).toObject().toVariantMap();
        const auto slotsObj = obj.value(QStringLiteral("slots")).toObject();
        m_slotA = slotsObj.value(QStringLiteral("A")).toObject().toVariantMap();
        m_slotB = slotsObj.value(QStringLiteral("B")).toObject().toVariantMap();
        // A fresh state read clears any stale target-mismatch prompt.
        if (!m_mismatchText.isEmpty()) {
            m_mismatchText.clear();
            emit mismatchChanged();
        }
        emit stateChanged();
    });
}

// Every category the sync engine knows how to read. A snapshot always captures all of them;
// the watch that can't do one records it as unsupported, and the page only offers to sync the
// categories both slots actually captured.
static const QVariantList kAllCategories = {QStringLiteral("settings"), QStringLiteral("pois"),
                                            QStringLiteral("routes"),
                                            QStringLiteral("sportModes")};

void SyncService::snapshot(const QString &slot)
{
    QVariantMap body;
    body[QStringLiteral("slot")] = slot;
    body[QStringLiteral("categories")] = kAllCategories;
    postJson(QStringLiteral("/api/sync/snapshot"), body,
             [this, slot](const QVariantMap &obj, bool) {
                 m_lastActionOk = obj.value(QStringLiteral("ok")).toBool();
                 m_lastActionText = m_lastActionOk
                     ? QStringLiteral("Snapshotted watch into slot %1").arg(slot)
                     : obj.value(QStringLiteral("error")).toString();
                 emit lastActionChanged();
                 refreshState();
             });
}

void SyncService::buildPlan(const QString &mode, const QString &direction,
                            const QStringList &categories)
{
    QVariantMap body;
    body[QStringLiteral("mode")] = mode;
    body[QStringLiteral("direction")] = direction;
    body[QStringLiteral("categories")] = QVariant(categories).toList();
    postJson(QStringLiteral("/api/sync/plan"), body, [this](const QVariantMap &obj, bool) {
        if (obj.value(QStringLiteral("ok")).toBool()) {
            m_plan = obj;
            emit planChanged();
        } else {
            m_lastActionOk = false;
            m_lastActionText = obj.value(QStringLiteral("error")).toString();
            emit lastActionChanged();
        }
    });
}

void SyncService::apply(const QString &mode, const QString &direction, bool confirm,
                        const QStringList &categories)
{
    QVariantMap body;
    body[QStringLiteral("mode")] = mode;
    body[QStringLiteral("direction")] = direction;
    body[QStringLiteral("categories")] = QVariant(categories).toList();
    body[QStringLiteral("confirm")] = confirm;
    postJson(QStringLiteral("/api/sync/apply"), body,
             [this, confirm](const QVariantMap &obj, bool) {
                 // The guards: wrong watch plugged (serial), or two different models.
                 const auto errCode = obj.value(QStringLiteral("error")).toString();
                 if (errCode == QStringLiteral("SYNC_TARGET_MISMATCH")
                         || errCode == QStringLiteral("SYNC_MODEL_MISMATCH")) {
                     m_mismatchText = obj.value(QStringLiteral("detail")).toString();
                     emit mismatchChanged();
                     return;
                 }
                 m_lastActionOk = obj.value(QStringLiteral("ok")).toBool();
                 const int applied = obj.value(QStringLiteral("applied")).toInt();
                 if (!m_lastActionOk) {
                     m_lastActionText = obj.value(QStringLiteral("error")).toString();
                 } else if (!confirm) {
                     m_lastActionText = QStringLiteral("Preview ready");
                 } else {
                     m_lastActionText = QStringLiteral("Applied %1 change%2 to the watch")
                         .arg(applied).arg(applied == 1 ? QString() : QStringLiteral("s"));
                 }
                 emit lastActionChanged();
                 if (confirm)
                     refreshState();  // the target changed; its snapshot was dropped backend-side
             });
}

void SyncService::clearSlot(const QString &slot)
{
    QVariantMap body;
    if (!slot.isEmpty())
        body[QStringLiteral("slot")] = slot;
    postJson(QStringLiteral("/api/sync/clear"), body, [this](const QVariantMap &, bool) {
        m_plan = {};
        emit planChanged();
        refreshState();
    });
}
