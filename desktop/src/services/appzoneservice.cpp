#include "appzoneservice.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QProcess>

// Baked in at configure time (CMakeLists.txt), same reasoning as IntervalsService.
#ifndef SOMMET_REPO_ROOT
#define SOMMET_REPO_ROOT ""
#endif

namespace {

// The App Zone builder (tools/apps_gui.py) is bundled INSIDE the frozen watch helper (it's just
// another tool), so in a packaged download the "Open App Builder" button needs nothing installed
// - it just asks that helper to run it (frozen_entry.py's --apps-builder sentinel). Same path
// convention as BackendProcess / IntervalsService (the helper sits next to the app in backend/,
// or in Contents/Resources on mac).
QString bundledBackendPath()
{
    const QDir appDir(QCoreApplication::applicationDirPath());
#if defined(Q_OS_MACOS)
    return appDir.absoluteFilePath(QStringLiteral("../Resources/backend/sommet-backend"));
#elif defined(Q_OS_WIN)
    return appDir.absoluteFilePath(QStringLiteral("backend/sommet-backend.exe"));
#else
    return appDir.absoluteFilePath(QStringLiteral("backend/sommet-backend"));
#endif
}

}  // namespace

AppZoneService::AppZoneService(QObject *parent) : QObject(parent)
{
}

QString AppZoneService::launch()
{
    // Packaged download: the bundled helper carries the App Zone builder - just launch it.
    const QString bundled = QFileInfo(bundledBackendPath()).absoluteFilePath();
    if (QFileInfo::exists(bundled)) {
        if (QProcess::startDetached(bundled, {QStringLiteral("--apps-builder")}))
            return QString();
    }

    // Source checkout: run tools/apps_gui.py with the system Python.
    const QString repoRoot = QDir(QStringLiteral(SOMMET_REPO_ROOT)).absolutePath();
    const QString fallbackScript = repoRoot + QStringLiteral("/tools/apps_gui.py");

#if defined(Q_OS_WIN)
    const QString pythonCommand = QStringLiteral("python");
#else
    const QString pythonCommand = QStringLiteral("python3");
#endif

    if (QFileInfo::exists(fallbackScript)) {
        if (QProcess::startDetached(pythonCommand, {fallbackScript}))
            return QString();
        return QStringLiteral("Found %1 but couldn't start %2 - is Python installed and on PATH?")
            .arg(fallbackScript, pythonCommand);
    }

    return QStringLiteral("Couldn't find the App Zone builder - expected it at %1")
        .arg(fallbackScript);
}
