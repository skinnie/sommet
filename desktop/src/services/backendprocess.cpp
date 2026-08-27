#include "backendprocess.h"

#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QHostAddress>
#include <QProcess>
#include <QProcessEnvironment>
#include <QTcpSocket>
#include <QTimer>

namespace {

// The port the helper serves on - must match server.py and the QML front-end (127.0.0.1:8766).
constexpr quint16 kBackendPort = 8766;
// Cap on automatic restarts, so a helper that crashes on every launch can't spin forever.
constexpr int kMaxRestarts = 5;

// Set true once the app is genuinely shutting down, so the supervisor below tells our own
// deliberate terminate() apart from a real crash and doesn't respawn a helper into a dying app.
bool g_appQuitting = false;

// Where the cloud release build drops the frozen helper, relative to the app executable.
// Each platform's packaging step (see .github/workflows/desktop-release.yml) puts it here:
//   Windows:  <appdir>\backend\ambit-backend.exe
//   Linux:    <appdir>/backend/ambit-backend
//   macOS:    Sommet.app/Contents/Resources/backend/ambit-backend
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

// Is something already listening on the backend port? Used twice: to reuse an existing backend
// (an orphan left by a previous run, or a second app instance) instead of spawning a duplicate
// that would just fail to bind; and, after our helper exits, to tell "it died and nothing is
// there" (respawn) from "a sibling is still serving" (leave it). A refused connection to
// localhost returns immediately, so this does not stall startup in the common (free) case.
bool backendPortInUse()
{
    QTcpSocket probe;
    probe.connectToHost(QHostAddress(QHostAddress::LocalHost), kBackendPort);
    return probe.waitForConnected(300);
}

void launchHelper(QObject *parent, const QString &path, int attempt)
{
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

    // Supervise it: if the helper dies while the app is still running, bring it back. This is
    // what stops a one-off crash from leaving the app permanently showing "backend not running"
    // (the original symptom reported 2026-08-27). If, when it exits, something else is already
    // serving the port, a sibling backend is up - reuse that rather than fight over :8766.
    QObject::connect(proc, &QProcess::finished, proc,
                     [parent, path, attempt](int /*code*/, QProcess::ExitStatus /*status*/) {
        if (g_appQuitting) {
            return;  // deliberate shutdown, not a crash
        }
        if (backendPortInUse()) {
            return;  // a sibling/orphan backend is serving; use it instead of respawning
        }
        if (attempt + 1 >= kMaxRestarts) {
            return;  // repeated crashes - stop trying rather than loop forever
        }
        // Small linear backoff so a crash-loop can't hammer the CPU.
        QTimer::singleShot(1000 * (attempt + 1), parent, [parent, path, attempt]() {
            launchHelper(parent, path, attempt + 1);
        });
    });

    proc->start();
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

    // Install the shutdown flag once, so the supervisor can tell a real crash from the deliberate
    // terminate() we issue on quit. Connected before any helper's terminate() slot, so it always
    // runs first on aboutToQuit.
    static bool quitHookInstalled = false;
    if (!quitHookInstalled) {
        QObject::connect(qApp, &QCoreApplication::aboutToQuit, qApp, []() { g_appQuitting = true; });
        quitHookInstalled = true;
    }

    // If a backend is already serving :8766 - an orphan left by a previous run, or a second app
    // instance - reuse it instead of spawning a duplicate that would just fail to bind (Errno 48)
    // and leave the app looking backend-less. This is the exact wedge behind the original
    // "backend not running" report: a stale helper held the port and every relaunch died on it.
    if (backendPortInUse()) {
        return;
    }

    launchHelper(parent, path, 0);
}
