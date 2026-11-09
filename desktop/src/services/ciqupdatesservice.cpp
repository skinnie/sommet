#include "ciqupdatesservice.h"

#include <QDir>
#include <QFileInfo>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSettings>

// Repo root is baked in at configure time (see desktop/CMakeLists.txt); "" is only a dev fallback.
#ifndef SOMMET_REPO_ROOT
#define SOMMET_REPO_ROOT ""
#endif

static QString defaultToolPath()
{
    QSettings s;
    const QString override = s.value(QStringLiteral("edge/ciqToolPath")).toString();
    if (!override.isEmpty())
        return override;
    const QString repoRoot = QDir(QStringLiteral(SOMMET_REPO_ROOT)).absolutePath();
    return repoRoot + QStringLiteral("/tools/ciq_updates.py");
}

CiqUpdatesService::CiqUpdatesService(QObject *parent)
    : QObject(parent)
{
    m_toolPath = defaultToolPath();
    m_python = QStringLiteral("python3");
}

void CiqUpdatesService::setBusy(bool b)
{
    if (m_busy == b)
        return;
    m_busy = b;
    emit busyChanged();
}

void CiqUpdatesService::check()
{
    if (m_busy)
        return;
    if (!QFileInfo::exists(m_toolPath)) {
        m_lastError = QStringLiteral("Connect IQ tool not found: %1").arg(m_toolPath);
        m_checked = true;
        emit stateChanged();
        return;
    }
    setBusy(true);
    m_lastError.clear();

    auto *p = new QProcess(this);
    connect(p, &QProcess::errorOccurred, this, [this, p](QProcess::ProcessError) {
        if (!m_busy)
            return;
        setBusy(false);
        m_checked = true;
        m_apps.clear();
        m_updateCount = 0;
        m_lastError = QStringLiteral("could not run ciq_updates.py");
        emit stateChanged();
        p->deleteLater();
    });
    connect(p, QOverload<int, QProcess::ExitStatus>::of(&QProcess::finished), this,
            [this, p](int, QProcess::ExitStatus) {
                const QByteArray out = p->readAllStandardOutput();
                const QByteArray err = p->readAllStandardError();
                p->deleteLater();
                setBusy(false);
                m_checked = true;

                QJsonParseError perr;
                const QJsonDocument doc = QJsonDocument::fromJson(out, &perr);
                if (perr.error != QJsonParseError::NoError || !doc.isObject()) {
                    m_apps.clear();
                    m_updateCount = 0;
                    m_lastError = QStringLiteral("bad tool output: %1").arg(QString::fromUtf8(err));
                    emit stateChanged();
                    return;
                }
                const QVariantMap obj = doc.object().toVariantMap();
                if (!obj.value(QStringLiteral("ok")).toBool()) {
                    m_apps.clear();
                    m_updateCount = 0;
                    m_lastError = obj.value(QStringLiteral("error")).toString();
                    emit stateChanged();
                    return;
                }
                m_apps = obj.value(QStringLiteral("apps")).toList();
                m_updateCount = obj.value(QStringLiteral("updates")).toInt();
                m_lastError.clear();
                emit stateChanged();
            });
    p->start(m_python, QStringList{m_toolPath});
}
