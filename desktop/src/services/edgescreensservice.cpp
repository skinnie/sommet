#include "edgescreensservice.h"

#include <QDir>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QSettings>
#include <QStandardPaths>

// Repo root is baked in at configure time (see desktop/CMakeLists.txt); "" is only a dev fallback.
#ifndef SOMMET_REPO_ROOT
#define SOMMET_REPO_ROOT ""
#endif

// Path to the Python tool that does the device work. Defaults to the bundled tools/ next to the
// repo root; overridable via the QSettings key "edge/toolPath" so a packaged build can point
// elsewhere without a rebuild.
static QString defaultToolPath()
{
    QSettings s;
    const QString override = s.value(QStringLiteral("edge/toolPath")).toString();
    if (!override.isEmpty())
        return override;
    const QString repoRoot = QDir(QStringLiteral(SOMMET_REPO_ROOT)).absolutePath();
    return repoRoot + QStringLiteral("/tools/edge_screens_device.py");
}

EdgeScreensService::EdgeScreensService(QObject *parent)
    : QObject(parent)
{
    m_toolPath = defaultToolPath();
    m_python = QStringLiteral("python3");
}

void EdgeScreensService::setBusy(bool b)
{
    if (m_busy == b)
        return;
    m_busy = b;
    emit busyChanged();
}

void EdgeScreensService::setError(const QString &e)
{
    m_lastError = e;
    emit lastErrorChanged();
}

// Runs the tool synchronously. Operations are short (one MTP listing / file copy), so a blocking
// call with a generous timeout keeps v1 simple; move to async QProcess if the UI ever stutters.
QVariantMap EdgeScreensService::runTool(const QStringList &args)
{
    if (!QFileInfo::exists(m_toolPath)) {
        setError(QStringLiteral("Edge tool not found: %1").arg(m_toolPath));
        return {};
    }
    QProcess p;
    p.start(m_python, QStringList{m_toolPath} << args);
    if (!p.waitForStarted(5000)) {
        setError(QStringLiteral("could not start python3"));
        return {};
    }
    if (!p.waitForFinished(60000)) {
        p.kill();
        setError(QStringLiteral("Edge tool timed out"));
        return {};
    }
    const QByteArray out = p.readAllStandardOutput();
    QJsonParseError perr;
    const QJsonDocument doc = QJsonDocument::fromJson(out, &perr);
    if (perr.error != QJsonParseError::NoError || !doc.isObject()) {
        setError(QStringLiteral("bad tool output: %1").arg(QString::fromUtf8(p.readAllStandardError())));
        return {};
    }
    const QVariantMap obj = doc.object().toVariantMap();
    if (!obj.value(QStringLiteral("ok")).toBool()) {
        setError(obj.value(QStringLiteral("error")).toString());
        return {};
    }
    setError(QString());  // clear on success
    return obj;
}

void EdgeScreensService::refresh()
{
    setBusy(true);

    const QVariantMap cat = runTool({QStringLiteral("--catalog")});
    m_catalog = cat.value(QStringLiteral("fields")).toList();
    emit catalogChanged();

    const QVariantMap prof = runTool({QStringLiteral("--list-profiles")});
    m_profiles = prof.value(QStringLiteral("profiles")).toList();
    emit profilesChanged();

    // Auto-load a profile's screens right away, so the page shows content the moment the Edge is
    // connected — no need to switch profiles to make screens appear. Keep the current one if it's still
    // present (e.g. after a save→refresh), otherwise load the first profile.
    QString toLoad;
    if (!m_currentProfile.isEmpty()) {
        for (const QVariant &p : m_profiles)
            if (p.toMap().value(QStringLiteral("file")).toString() == m_currentProfile) { toLoad = m_currentProfile; break; }
    }
    if (toLoad.isEmpty() && !m_profiles.isEmpty())
        toLoad = m_profiles.first().toMap().value(QStringLiteral("file")).toString();

    setBusy(false);
    if (!toLoad.isEmpty())
        loadProfile(toLoad);
}

void EdgeScreensService::loadProfile(const QString &file)
{
    setBusy(true);
    const QVariantMap r = runTool({QStringLiteral("--load"), file});
    if (!r.isEmpty()) {
        m_screens = r.value(QStringLiteral("screens")).toList();
        m_specialScreens = r.value(QStringLiteral("specials")).toInt();
        m_specialList = r.value(QStringLiteral("specialList")).toList();
        m_currentProfile = file;
        emit screensChanged();
    }
    setBusy(false);
}

void EdgeScreensService::saveScreen(const QString &file, int screenIndex, const QString &fieldsCsv,
                                    int variant, const QString &togglesCsv)
{
    setBusy(true);
    QStringList args{QStringLiteral("--save"), file,
                     QStringLiteral("--screen"), QString::number(screenIndex),
                     QStringLiteral("--fields"), fieldsCsv};
    if (variant >= 0)
        args << QStringLiteral("--variant") << QString::number(variant);
    if (!togglesCsv.isEmpty())
        args << QStringLiteral("--toggles") << togglesCsv;
    const QVariantMap r = runTool(args);
    if (!r.isEmpty()) {
        m_lastNote = r.value(QStringLiteral("note")).toString();
        emit lastNoteChanged();
        emit saved();
    }
    setBusy(false);
}

void EdgeScreensService::addScreen(const QString &file, const QString &fieldsCsv, int variant)
{
    setBusy(true);
    QStringList args{QStringLiteral("--add"), file, QStringLiteral("--fields"), fieldsCsv};
    if (variant >= 0)
        args << QStringLiteral("--variant") << QString::number(variant);
    const QVariantMap r = runTool(args);
    if (!r.isEmpty()) {
        m_lastNote = r.value(QStringLiteral("note")).toString();
        emit lastNoteChanged();
        emit saved();
    }
    setBusy(false);
}

void EdgeScreensService::setToggle(const QString &file, int field, int value)
{
    setBusy(true);
    const QVariantMap r = runTool({QStringLiteral("--toggle"), file,
                                   QStringLiteral("--field"), QString::number(field),
                                   QStringLiteral("--value"), QString::number(value)});
    if (!r.isEmpty()) {
        m_lastNote = r.value(QStringLiteral("note")).toString();
        emit lastNoteChanged();
        emit saved();
    }
    setBusy(false);
}
