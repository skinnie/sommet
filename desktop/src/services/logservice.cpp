#include "logservice.h"

#include <QDateTime>
#include <QDesktopServices>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QStandardPaths>
#include <QSysInfo>
#include <QTextStream>
#include <QUrl>
#include <QUrlQuery>

// André's own GitHub address, from this repository's commit history
// (`git config user.email`) rather than typed from memory.
static const QString kReportAddress =
    QStringLiteral("5618623+skinnie@users.noreply.github.com");

// Mail clients and the platforms that hand them a URL both truncate long ones. 1500
// characters of log is enough to carry the actual failure and its immediate context; the
// full file is one click away in the folder this also opens.
static constexpr int kInlineLogChars = 1500;

LogService::LogService(QObject *parent) : QObject(parent)
{
}

QString LogService::logPath() const
{
    const QString dir = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    return dir + QStringLiteral("/sommet.log");
}

QString LogService::userMessage() const
{
    return tr("An error has occurred. Please send the logs.");
}

void LogService::append(const QString &line)
{
    const QString path = logPath();
    QDir().mkpath(QFileInfo(path).absolutePath());
    QFile file(path);
    if (!file.open(QIODevice::Append | QIODevice::Text))
        return;  // never block the UI on being unable to log
    QTextStream out(&file);
    out << QDateTime::currentDateTime().toString(Qt::ISODate) << ' ' << line << '\n';
}

QString LogService::tailOfLog(int maxChars)
{
    LogService probe;
    QFile file(probe.logPath());
    if (!file.open(QIODevice::ReadOnly | QIODevice::Text))
        return QStringLiteral("(no log file yet)");
    const QByteArray all = file.readAll();
    const QByteArray tail = all.size() > maxChars ? all.right(maxChars) : all;
    return QString::fromUtf8(tail);
}

void LogService::reportProblem(const QString &context)
{
    if (!context.isEmpty())
        append(QStringLiteral("REPORTED: ") + context);

    const QString body =
        tr("Describe what you were doing when this happened:\n\n\n"
           "----- system -----\n"
           "Sommet on %1 (%2)\n"
           "Log file: %3\n\n"
           "----- last log lines -----\n%4\n\n"
           "(The full log is in the folder this opened - attach it if you can.)")
            .arg(QSysInfo::prettyProductName(), QSysInfo::currentCpuArchitecture(),
                 logPath(), tailOfLog(kInlineLogChars));

    QUrl mail(QStringLiteral("mailto:") + kReportAddress);
    QUrlQuery query;
    query.addQueryItem(QStringLiteral("subject"),
                       QStringLiteral("Sommet problem report"));
    query.addQueryItem(QStringLiteral("body"), body);
    mail.setQuery(query);

    QDesktopServices::openUrl(mail);
    revealLog();
}

void LogService::revealLog()
{
    const QFileInfo info(logPath());
    QDir().mkpath(info.absolutePath());
    QDesktopServices::openUrl(QUrl::fromLocalFile(info.absolutePath()));
}
