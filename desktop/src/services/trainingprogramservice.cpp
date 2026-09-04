#include "trainingprogramservice.h"

#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>

static const QString kBackendBase = QStringLiteral("http://127.0.0.1:8766");

TrainingProgramService::TrainingProgramService(QObject *parent) : QObject(parent)
{
}

QUrl TrainingProgramService::backendUrl(const QString &path)
{
    return QUrl(kBackendBase + path);
}

void TrainingProgramService::setLoading(bool value)
{
    if (m_loading == value)
        return;
    m_loading = value;
    emit loadingChanged();
}

void TrainingProgramService::setLastError(const QString &message)
{
    m_lastError = message;
    emit lastErrorChanged();
}

void TrainingProgramService::setInstalling(bool value)
{
    if (m_installing == value)
        return;
    m_installing = value;
    emit installingChanged();
}

void TrainingProgramService::refreshPlans()
{
    setLoading(true);
    QNetworkReply *reply = m_network.get(
        QNetworkRequest(backendUrl(QStringLiteral("/api/trainingprogram"))));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoading(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!ok) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("GET /api/trainingprogram: %1").arg(reply->errorString())
                : QStringLiteral("GET /api/trainingprogram: %1")
                      .arg(obj.value(QStringLiteral("error")).toString()));
            emit plansChanged();
            return;
        }

        m_plans.clear();
        for (const auto &v : obj.value(QStringLiteral("plans")).toArray())
            m_plans.append(v.toObject().toVariantMap());
        emit plansChanged();
    });
}

void TrainingProgramService::savePlan(const QVariantMap &plan)
{
    QNetworkRequest request(backendUrl(QStringLiteral("/api/trainingprogram")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("plan")] = QJsonObject::fromVariantMap(plan);

    QNetworkReply *reply = m_network.post(
        request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!ok) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/trainingprogram: %1").arg(reply->errorString())
                : QStringLiteral("POST /api/trainingprogram: %1")
                      .arg(obj.value(QStringLiteral("error")).toString()));
            return;
        }
        emit planSaved(obj.value(QStringLiteral("id")).toString());
        refreshPlans();
    });
}

void TrainingProgramService::deletePlan(const QString &planId)
{
    QNetworkRequest request(backendUrl(QStringLiteral("/api/trainingprogram/delete")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("id")] = planId;

    QNetworkReply *reply = m_network.post(
        request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        if (reply->error() != QNetworkReply::NoError
            || !obj.value(QStringLiteral("ok")).toBool()) {
            setLastError(QStringLiteral("POST /api/trainingprogram/delete: %1")
                .arg(reply->error() != QNetworkReply::NoError
                    ? reply->errorString()
                    : obj.value(QStringLiteral("error")).toString()));
            return;
        }
        refreshPlans();
    });
}

void TrainingProgramService::install(const QVariantMap &plan, int mode, int display,
                                     int field, bool confirm)
{
    setInstalling(true);

    QNetworkRequest request(backendUrl(QStringLiteral("/api/trainingprogram/install")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("plan")] = QJsonObject::fromVariantMap(plan);
    body[QStringLiteral("mode")] = mode;
    body[QStringLiteral("display")] = display;
    body[QStringLiteral("field")] = field;
    body[QStringLiteral("confirm")] = confirm;

    QNetworkReply *reply = m_network.post(
        request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, confirm] {
        reply->deleteLater();
        setInstalling(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();

        QVariantMap result;
        result[QStringLiteral("ok")] = ok;
        result[QStringLiteral("dryRun")] = !confirm;
        if (obj.contains(QStringLiteral("apps")))
            result[QStringLiteral("apps")] =
                obj.value(QStringLiteral("apps")).toArray().toVariantList();
        if (obj.contains(QStringLiteral("installed")))
            result[QStringLiteral("installed")] =
                obj.value(QStringLiteral("installed")).toArray().toVariantList();
        if (!ok) {
            result[QStringLiteral("error")] = reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/trainingprogram/install: %1")
                      .arg(reply->errorString())
                : obj.value(QStringLiteral("error")).toString();
            setLastError(result[QStringLiteral("error")].toString());
        }
        m_lastInstallResult = result;
        emit lastInstallResultChanged();
    });
}

void TrainingProgramService::importFromIntervals(const QString &start, const QString &end,
                                                 const QString &mode, const QString &athleteId,
                                                 const QString &apiKey)
{
    setLoading(true);

    QNetworkRequest request(backendUrl(QStringLiteral("/api/intervals/workouts")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("athlete_id")] = athleteId;
    body[QStringLiteral("api_key")] = apiKey;
    body[QStringLiteral("start")] = start;
    body[QStringLiteral("end")] = end;
    body[QStringLiteral("mode")] = mode;

    QNetworkReply *reply = m_network.post(
        request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setLoading(false);

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();
        if (!ok) {
            setLastError(reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/intervals/workouts: %1").arg(reply->errorString())
                : obj.value(QStringLiteral("error")).toString());
            return;
        }
        emit intervalsImported(obj.value(QStringLiteral("entries")).toArray().toVariantList(),
                               obj.value(QStringLiteral("skipped")).toArray().toVariantList(),
                               obj.value(QStringLiteral("resolvedToWatch")).toBool());
    });
}

void TrainingProgramService::syncCalendar(const QVariantList &entries, bool write)
{
    setInstalling(true);

    QNetworkRequest request(backendUrl(QStringLiteral("/api/trainingprogram/sync-calendar")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("entries")] = QJsonArray::fromVariantList(entries);
    body[QStringLiteral("write")] = write;

    QNetworkReply *reply = m_network.post(
        request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply, write, entries] {
        reply->deleteLater();
        // installing stays true until the native-card write (below) also finishes.

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();

        QVariantMap result;
        result[QStringLiteral("ok")] = ok;
        result[QStringLiteral("dryRun")] = !write;
        result[QStringLiteral("rotation")] = true;
        for (const auto key : {"today", "removed", "added", "displaysAdded", "failed"}) {
            const QString k = QString::fromLatin1(key);
            if (obj.contains(k)) {
                if (obj.value(k).isArray())
                    result[k] = obj.value(k).toArray().toVariantList();
                else
                    result[k] = obj.value(k).toVariant();
            }
        }
        if (!ok) {
            result[QStringLiteral("error")] = reply->error() != QNetworkReply::NoError
                ? QStringLiteral("POST /api/trainingprogram/sync-calendar: %1")
                      .arg(reply->errorString())
                : obj.value(QStringLiteral("error")).toString();
            setLastError(result[QStringLiteral("error")].toString());
        }
        m_lastInstallResult = result;
        emit lastInstallResultChanged();

        // Second half: the native "Today 1/2" planned-move cards. Fire it regardless of the
        // rotation result - the two are independent watch writes (WORKOUT-menu guidance vs the
        // TIME-mode card), and a user whose guided-workout compile failed still wants the cards.
        writePlannedMoves(entries, write);
    });
}

void TrainingProgramService::writePlannedMoves(const QVariantList &entries, bool write)
{
    QNetworkRequest request(backendUrl(QStringLiteral("/api/trainingprogram/planned-moves")));
    request.setHeader(QNetworkRequest::ContentTypeHeader, QStringLiteral("application/json"));
    QJsonObject body;
    body[QStringLiteral("entries")] = QJsonArray::fromVariantList(entries);
    body[QStringLiteral("write")] = write;

    QNetworkReply *reply = m_network.post(
        request, QJsonDocument(body).toJson(QJsonDocument::Compact));
    connect(reply, &QNetworkReply::finished, this, [this, reply] {
        reply->deleteLater();
        setInstalling(false);  // the whole "Sync to watch" action ends here

        const auto obj = QJsonDocument::fromJson(reply->readAll()).object();
        const bool ok = (reply->error() == QNetworkReply::NoError)
            && obj.value(QStringLiteral("ok")).toBool();

        // Merge into the rotation result already shown; never overwrite its ok/error - the
        // guided-workout half and the native-card half report independently.
        QVariantMap result = m_lastInstallResult;
        if (ok) {
            result[QStringLiteral("nativeCards")] =
                obj.value(QStringLiteral("count")).toInt();
            const auto dates = obj.value(QStringLiteral("dates")).toArray().toVariantList();
            if (!dates.isEmpty()) {
                result[QStringLiteral("nativeCardFirst")] = dates.first();
                result[QStringLiteral("nativeCardLast")] = dates.last();
            }
        } else {
            result[QStringLiteral("nativeCardError")] =
                reply->error() != QNetworkReply::NoError
                    ? QStringLiteral("POST /api/trainingprogram/planned-moves: %1")
                          .arg(reply->errorString())
                    : obj.value(QStringLiteral("error")).toString();
        }
        m_lastInstallResult = result;
        emit lastInstallResultChanged();
    });
}
