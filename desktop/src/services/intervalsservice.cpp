#include "intervalsservice.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QProcess>
#include <QByteArray>

// Baked in at configure time (CMakeLists.txt) - this is a personal dev-checkout tool tied to
// one repo layout, not something installed separately from it (same "fixed convention, not
// configurable yet" reasoning as DeviceService's own hardcoded backend address), so a real
// absolute path computed once at build time is more reliable than guessing one at runtime
// from wherever the built binary happens to be invoked.
#ifndef AMBITAPP_REPO_ROOT
#define AMBITAPP_REPO_ROOT ""
#endif

namespace {

// The Workout Builder (tools/workout_gui.py) is bundled INSIDE the frozen watch helper (it's
// just another tool), so in a packaged download the "Open Workout Builder" button needs
// nothing installed - it just asks that helper to run it. Same path convention as
// BackendProcess (the helper sits next to the app in backend/, or in Contents/Resources on mac).
QString bundledBackendPath()
{
    const QDir appDir(QCoreApplication::applicationDirPath());
#if defined(Q_OS_MACOS)
    return appDir.absoluteFilePath(QStringLiteral("../Resources/backend/ambit-backend"));
#elif defined(Q_OS_WIN)
    return appDir.absoluteFilePath(QStringLiteral("backend/ambit-backend.exe"));
#else
    return appDir.absoluteFilePath(QStringLiteral("backend/ambit-backend"));
#endif
}

}  // namespace

IntervalsService::IntervalsService(QObject *parent) : QObject(parent)
{
}

static QString launchBuilder(const QStringList &nameArgs)
{

    // Packaged download: the bundled helper carries the Workout Builder - just launch it.
    const QString bundled = QFileInfo(bundledBackendPath()).absoluteFilePath();
    if (QFileInfo::exists(bundled)) {
        if (QProcess::startDetached(bundled,
                QStringList{QStringLiteral("--workout-builder")} + nameArgs))
            return QString();
    }

    // Source checkout: run tools/workout_gui.py with the system Python.
    //
    // The tools/*.py is now the ONLY source-checkout path - the dist/ build was dropped here
    // 2026-08-23. A stale dist/linux/Ambit3 Workout Builder (frozen 2026-08-19) was actually
    // serving the App Zone builder, so "Open Workout Builder" opened the app builder - exactly
    // the bug André hit ("workout link opens apps link"). The .py in the tree is the source of
    // truth and always current; a hand-built dist/ binary can silently drift or be mislabeled,
    // so it must not shadow it. dist/ matters only for the real packaged download, where
    // bundledBackendPath() above is what runs.
    const QString repoRoot = QDir(QStringLiteral(AMBITAPP_REPO_ROOT)).absolutePath();
    const QString fallbackScript = repoRoot + QStringLiteral("/tools/workout_gui.py");

#if defined(Q_OS_WIN)
    const QString pythonCommand = QStringLiteral("python");
#else
    const QString pythonCommand = QStringLiteral("python3");
#endif

    if (QFileInfo::exists(fallbackScript)) {
        if (QProcess::startDetached(pythonCommand, QStringList{fallbackScript} + nameArgs))
            return QString();
        return QStringLiteral("Found %1 but couldn't start %2 - is Python installed and on PATH?")
            .arg(fallbackScript, pythonCommand);
    }

    return QStringLiteral("Couldn't find the Workout Builder - expected it at %1")
        .arg(fallbackScript);
}

QString IntervalsService::launch(const QString &workoutName)
{
    // The builder pre-fills its name field from --name (see workout_gui.py). Empty = a plain
    // launch, unchanged.
    QStringList args;
    if (!workoutName.isEmpty())
        args << QStringLiteral("--name") << workoutName;
    return launchBuilder(args);
}

QString IntervalsService::launchWithWorkout(const QString &workoutJson)
{
    // The planner's "Create workout" hands over a whole workout as JSON. It goes to the
    // builder base64'd (--workout-b64), the same value the page also accepts as ?workout=,
    // so a shell/URL never has to carry raw JSON. Empty JSON = a plain launch.
    if (workoutJson.isEmpty())
        return launchBuilder(QStringList{});
    const QString b64 = QString::fromLatin1(
        workoutJson.toUtf8().toBase64(QByteArray::Base64UrlEncoding));
    return launchBuilder(QStringList{QStringLiteral("--workout-b64"), b64});
}
