#include "backendprocess.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QProcess>
#include <QProcessEnvironment>

namespace {

// Where the cloud release build drops the frozen helper, relative to the app executable.
// Each platform's packaging step (see .github/workflows/desktop-release.yml) puts it here:
//   Windows:  <appdir>\backend\ambit-backend.exe
//   Linux:    <appdir>/backend/ambit-backend
//   macOS:    AmbitApp.app/Contents/Resources/backend/ambit-backend
//             (applicationDirPath() is Contents/MacOS, so the helper is one dir up in Resources)
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

void BackendProcess::startIfBundled(QObject *parent)
{
    const QString path = QFileInfo(bundledBackendPath()).absoluteFilePath();
    if (!QFileInfo::exists(path)) {
        // Dev build (plain `cmake --build`): no helper was bundled. run-desktop.sh starts the
        // Python backend instead, so there is nothing to do here. This is the normal path when
        // building from source and must stay a no-op.
        return;
    }

    auto *proc = new QProcess(parent);
    proc->setProgram(path);
    // Let the helper's own stdout/stderr show up in the app's console/log, same as running
    // server.py by hand - useful when a user reports "it won't talk to the watch".
    proc->setProcessChannelMode(QProcess::ForwardedChannels);

#if defined(Q_OS_MACOS)
    // The frozen helper's `hid` binding dlopen()s libhidapi.dylib by bare name. A .app launched
    // from Finder/LaunchServices inherits no shell environment, and Homebrew's /opt/homebrew/lib
    // is not a default dyld search path, so the watch is invisible unless we point dyld at the
    // copy we ship next to the helper (Contents/Resources/backend/libhidapi.dylib - see
    // desktop-release.yml). Setting DYLD_LIBRARY_PATH on the child is the exact mechanism a live
    // Apple-Silicon test confirmed (2026-08-27): with it, /api/devices enumerates the Ambit.
    QProcessEnvironment env = QProcessEnvironment::systemEnvironment();
    const QString helperDir = QFileInfo(path).absolutePath();
    const QString existing = env.value(QStringLiteral("DYLD_LIBRARY_PATH"));
    env.insert(QStringLiteral("DYLD_LIBRARY_PATH"),
               existing.isEmpty() ? helperDir : helperDir + QLatin1Char(':') + existing);
    proc->setProcessEnvironment(env);
#endif

    // Tie the helper to the app: when the app quits, stop the helper so it never keeps the
    // :8766 port (or a USB handle) after the window is gone.
    QObject::connect(qApp, &QCoreApplication::aboutToQuit, proc, [proc]() {
        proc->terminate();
        if (!proc->waitForFinished(3000)) {
            proc->kill();
            proc->waitForFinished(1000);
        }
    });

    proc->start();
}
