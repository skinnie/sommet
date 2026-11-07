#include "updateservice.h"

#include <QCoreApplication>
#include <QDebug>
#include <QDesktopServices>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>
#include <QNetworkReply>
#include <QNetworkRequest>
#include <QRegularExpression>
#include <QSettings>
#include <QUrl>

namespace {

const char kLatestApi[] = "https://api.github.com/repos/skinnie/sommet/releases/latest";
// Only links into this project's own releases are ever opened or saved.
const char kReleasesPrefix[] = "https://github.com/skinnie/sommet/releases/";
// "Once a day", a little early so a PC started every morning still gets its daily look.
constexpr qint64 kCheckEverySecs = 20 * 3600;
constexpr int kMaxHighlights = 8;

// The file each desktop download job attaches to the release (.github/workflows/desktop-release.yml).
#if defined(Q_OS_MACOS)
const char kAssetName[] = "Sommet-macos.dmg";
#elif defined(Q_OS_WIN)
const char kAssetName[] = "Sommet-windows.zip";
#elif defined(Q_OS_LINUX)
// The AppImage the desktop-release.yml `linux` job attaches. With this, Linux now offers a
// direct download (the single-file AppImage) instead of only opening the release page.
const char kAssetName[] = "Sommet-linux-x86_64.AppImage";
#else
const char kAssetName[] = "";
#endif

QList<int> versionParts(const QString &v)
{
    static const QRegularExpression re(QStringLiteral("(\\d+)\\.(\\d+)\\.(\\d+)"));
    const QRegularExpressionMatch m = re.match(v);
    if (!m.hasMatch())
        return {};
    return {m.captured(1).toInt(), m.captured(2).toInt(), m.captured(3).toInt()};
}

QString plainVersion(const QString &tag)
{
    const QList<int> p = versionParts(tag);
    return p.isEmpty() ? QString()
                       : QStringLiteral("%1.%2.%3").arg(p[0]).arg(p[1]).arg(p[2]);
}

bool isOurs(const QString &url)
{
    return url.startsWith(QLatin1String(kReleasesPrefix));
}

} // namespace

UpdateService::UpdateService(QObject *parent)
    : QObject(parent)
{
    loadSaved();
    // The first look waits a few seconds so it never slows the window opening; then every hour
    // checkIfDue() asks GitHub only when the last answer is about a day old.
    QTimer::singleShot(5000, this, &UpdateService::checkIfDue);
    m_daily.setInterval(3600 * 1000);
    connect(&m_daily, &QTimer::timeout, this, &UpdateService::checkIfDue);
    m_daily.start();
}

QString UpdateService::currentVersion() const
{
    return QCoreApplication::applicationVersion();
}

bool UpdateService::available() const
{
    return isNewer(m_latestVersion, currentVersion());
}

bool UpdateService::showNotice() const
{
    return available() && m_dismissed != m_latestVersion;
}

QString UpdateService::lastChecked() const
{
    return m_lastChecked.isValid()
        ? m_lastChecked.toLocalTime().toString(QStringLiteral("dd/MM/yyyy HH:mm"))
        : QString();
}

bool UpdateService::autoCheck() const
{
    return QSettings().value(QStringLiteral("updates/autoCheck"), true).toBool();
}

void UpdateService::setAutoCheck(bool on)
{
    if (on == autoCheck())
        return;
    QSettings().setValue(QStringLiteral("updates/autoCheck"), on);
    emit autoCheckChanged();
    if (on)
        checkIfDue();
}

void UpdateService::checkIfDue()
{
    if (!autoCheck() || m_checking)
        return;
    if (m_lastChecked.isValid()
        && m_lastChecked.secsTo(QDateTime::currentDateTimeUtc()) < kCheckEverySecs)
        return;
    check();
}

void UpdateService::check()
{
    if (m_checking)
        return;
    m_checking = true;
    emit changed();

    QNetworkRequest req{QUrl(QString::fromLatin1(kLatestApi))};
    req.setRawHeader("Accept", "application/vnd.github+json");
    // GitHub refuses API requests without a User-Agent.
    req.setRawHeader("User-Agent", "Sommet/" + currentVersion().toUtf8());
    req.setTransferTimeout(15000);
    QNetworkReply *reply = m_nam.get(req);
    connect(reply, &QNetworkReply::finished, this, [this, reply] { handleReply(reply); });
}

void UpdateService::handleReply(QNetworkReply *reply)
{
    reply->deleteLater();
    m_checking = false;
    const int status = reply->attribute(QNetworkRequest::HttpStatusCodeAttribute).toInt();

    if (reply->error() == QNetworkReply::NoError && status == 200
        && applyRelease(reply->readAll())) {
        m_lastChecked = QDateTime::currentDateTimeUtc();
        m_state = available() ? QStringLiteral("available") : QStringLiteral("upToDate");
        QSettings s;
        s.setValue(QStringLiteral("updates/lastChecked"), m_lastChecked);
        s.setValue(QStringLiteral("updates/latestVersion"), m_latestVersion);
        s.setValue(QStringLiteral("updates/pageUrl"), m_pageUrl);
        s.setValue(QStringLiteral("updates/downloadUrl"), m_downloadUrl);
        s.setValue(QStringLiteral("updates/highlights"), m_highlights);
    } else if (status == 404) {
        // No published release at all: nothing is newer than this build.
        m_lastChecked = QDateTime::currentDateTimeUtc();
        m_state = QStringLiteral("upToDate");
    } else {
        // Offline, GitHub busy, or an answer that isn't a release. The last good answer stays
        // (a note already shown stays shown); the hourly timer tries again.
        m_state = QStringLiteral("error");
        qWarning() << "UpdateService: release check failed:" << status << reply->errorString();
    }
    emit changed();
}

bool UpdateService::applyRelease(const QByteArray &json)
{
    const QJsonObject o = QJsonDocument::fromJson(json).object();
    const QString tag = o.value(QStringLiteral("tag_name")).toString();
    const QString page = o.value(QStringLiteral("html_url")).toString();
    if (versionParts(tag).isEmpty() || !isOurs(page)
        || o.value(QStringLiteral("draft")).toBool()
        || o.value(QStringLiteral("prerelease")).toBool())
        return false;

    QString download;
    if (kAssetName[0] != '\0') {
        const QJsonArray assets = o.value(QStringLiteral("assets")).toArray();
        for (const QJsonValue &a : assets) {
            const QJsonObject asset = a.toObject();
            const QString url = asset.value(QStringLiteral("browser_download_url")).toString();
            if (asset.value(QStringLiteral("name")).toString() == QLatin1String(kAssetName)
                && isOurs(url))
                download = url;
        }
    }

    m_latestVersion = plainVersion(tag);
    m_pageUrl = page;
    m_downloadUrl = download;
    m_highlights = highlightsFrom(o.value(QStringLiteral("body")).toString());
    return true;
}

void UpdateService::loadSaved()
{
    const QSettings s;
    m_dismissed = s.value(QStringLiteral("updates/dismissedVersion")).toString();
    m_lastChecked = s.value(QStringLiteral("updates/lastChecked")).toDateTime();
    const QString page = s.value(QStringLiteral("updates/pageUrl")).toString();
    const QString download = s.value(QStringLiteral("updates/downloadUrl")).toString();
    if (!isOurs(page))
        return;
    m_latestVersion = plainVersion(s.value(QStringLiteral("updates/latestVersion")).toString());
    m_pageUrl = page;
    m_downloadUrl = isOurs(download) ? download : QString();
    m_highlights = s.value(QStringLiteral("updates/highlights")).toStringList();
    if (m_lastChecked.isValid())
        m_state = available() ? QStringLiteral("available") : QStringLiteral("upToDate");
}

void UpdateService::download()
{
    const QString url = m_downloadUrl.isEmpty() ? m_pageUrl : m_downloadUrl;
    if (isOurs(url))
        QDesktopServices::openUrl(QUrl(url));
}

void UpdateService::openReleasePage()
{
    if (isOurs(m_pageUrl))
        QDesktopServices::openUrl(QUrl(m_pageUrl));
}

void UpdateService::dismiss()
{
    if (m_latestVersion.isEmpty())
        return;
    m_dismissed = m_latestVersion;
    QSettings().setValue(QStringLiteral("updates/dismissedVersion"), m_dismissed);
    emit changed();
}

bool UpdateService::isNewer(const QString &latest, const QString &current)
{
    const QList<int> a = versionParts(latest);
    const QList<int> b = versionParts(current);
    if (a.isEmpty() || b.isEmpty())
        return false;
    return a > b;
}

QStringList UpdateService::highlightsFrom(const QString &body)
{
    // tools/release_notes.py writes one "## 2026-11-03: Title (0.2.57)" heading per change.
    static const QRegularExpression re(
        QStringLiteral("^##\\s+\\d{4}-\\d{2}-\\d{2}:\\s*(.+?)\\s*$"),
        QRegularExpression::MultilineOption);
    QStringList out;
    auto it = re.globalMatch(body);
    while (it.hasNext() && out.size() < kMaxHighlights)
        out << it.next().captured(1);
    return out;
}
