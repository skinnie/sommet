#include "localfileservice.h"
#include "apppaths.h"

#include <QDateTime>
#include <QDesktopServices>
#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QSettings>
#include <QSqlDatabase>
#include <QSqlError>
#include <QSqlQuery>
#include <QStandardPaths>

// Defined further down; forward-declared so moveDatabaseTo() (above it) can use it.
static QString snapshotSqlite(const QString &srcPath, const QString &dstPath);

LocalFileService::LocalFileService(QObject *parent) : QObject(parent) {}

QUrl LocalFileService::downloadsLocation() const
{
    const QString dir = QStandardPaths::writableLocation(QStandardPaths::DownloadLocation);
    return QUrl::fromLocalFile(dir);
}

QUrl LocalFileService::backupsLocation() const
{
    return QUrl::fromLocalFile(QDir::homePath() + QStringLiteral("/AmbitAppBackups"));
}

bool LocalFileService::openFolder(const QUrl &folderUrl)
{
    return QDesktopServices::openUrl(folderUrl);
}

QString LocalFileService::databaseLocation() const
{
    return AppPaths::databaseDir();
}

QString LocalFileService::moveDatabaseTo(const QUrl &folderUrl)
{
    const QString target = folderUrl.toLocalFile();
    if (target.isEmpty())
        return tr("Pick a folder first.");
    const QString src = AppPaths::databaseDir();
    if (QDir(target).absolutePath() == QDir(src).absolutePath())
        return tr("The database is already kept there.");
    if (!QDir().mkpath(target))
        return tr("Couldn't use that folder (%1).").arg(target);

    // Copy the databases into the chosen folder, transactionally (VACUUM INTO), same as a backup.
    const QStringList names{QStringLiteral("activities.db"), QStringLiteral("gear.db")};
    QStringList failed;
    for (const QString &name : names) {
        const QString from = src + QLatin1Char('/') + name;
        if (!QFileInfo::exists(from))
            continue;   // nothing of this DB yet - fine
        const QString err = snapshotSqlite(from, QDir(target).filePath(name));
        if (!err.isEmpty())
            failed.append(QStringLiteral("%1 (%2)").arg(name, err));
    }
    if (!failed.isEmpty())
        return tr("Couldn't move the database: %1").arg(failed.join(QStringLiteral("; ")));

    // Remember the new location; it takes effect on the next launch (the old copy stays as a
    // safety net until the user removes it).
    QSettings().setValue(QStringLiteral("database/dir"), target);
    emit databaseLocationChanged();
    return QString();
}

// A transactionally-consistent snapshot of one SQLite file at srcPath into dstPath, taken
// from a private second connection so it can't be torn by whatever connection the app already
// holds open on the same file. `VACUUM INTO` refuses to write over an existing file, so a
// stale destination is removed first. Returns "" on success, the SQLite error text otherwise.
static QString snapshotSqlite(const QString &srcPath, const QString &dstPath)
{
    QFile::remove(dstPath);
    // Unique per call - a connection name is process-global, and reusing one that's still
    // referenced is the classic Qt "connection already exists" warning + silent reuse.
    const QString connName =
        QStringLiteral("db-backup-%1").arg(QDateTime::currentMSecsSinceEpoch());
    QString error;
    {
        QSqlDatabase db = QSqlDatabase::addDatabase(QStringLiteral("QSQLITE"), connName);
        db.setDatabaseName(srcPath);
        if (!db.open()) {
            error = db.lastError().text();
        } else {
            // VACUUM INTO takes no bound placeholder, so the path is inlined - single quotes
            // doubled per SQL string-literal rules (a home folder can contain an apostrophe).
            QString quoted = dstPath;
            quoted.replace(QLatin1Char('\''), QStringLiteral("''"));
            QSqlQuery q(db);
            if (!q.exec(QStringLiteral("VACUUM INTO '%1'").arg(quoted)))
                error = q.lastError().text();
        }
        // db must go out of scope before removeDatabase(), or Qt warns the connection is
        // still in use and skips the cleanup.
    }
    QSqlDatabase::removeDatabase(connName);
    return error;
}

QString LocalFileService::backupDatabase(const QUrl &destFolder)
{
    const QString appData = AppPaths::databaseDir();   // back up from wherever the user keeps the data

    QString destRoot = destFolder.toLocalFile();
    if (destRoot.isEmpty())
        destRoot = QDir::homePath() + QStringLiteral("/AmbitAppBackups");

    const QString stamp = QDateTime::currentDateTime().toString(QStringLiteral("yyyyMMdd-HHmmss"));
    const QString outDir = destRoot + QStringLiteral("/sommet-data-") + stamp;
    if (!QDir().mkpath(outDir))
        return tr("Couldn't create the backup folder %1").arg(outDir);

    // activities.db is the whole point (activities + their GPX/FIT text); gear.db rides along
    // and is simply skipped if it doesn't exist yet (no gear added). coach reads activities.db,
    // so it needs no file of its own.
    const QStringList names{QStringLiteral("activities.db"), QStringLiteral("gear.db")};
    int copied = 0;
    QStringList failed;
    for (const QString &name : names) {
        const QString src = appData + QLatin1Char('/') + name;
        if (!QFileInfo::exists(src))
            continue;
        const QString err = snapshotSqlite(src, QDir(outDir).filePath(name));
        if (err.isEmpty())
            ++copied;
        else
            failed.append(QStringLiteral("%1 (%2)").arg(name, err));
    }

    if (copied == 0 && failed.isEmpty()) {
        QDir(outDir).removeRecursively();  // don't leave an empty stamped folder behind
        return tr("There's no activity database to back up yet.");
    }
    if (!failed.isEmpty())
        return tr("Couldn't back up %1").arg(failed.join(QStringLiteral("; ")));
    return QString();
}

static QString writeFile(const QUrl &fileUrl, const QByteArray &data)
{
    const QString path = fileUrl.toLocalFile();
    if (path.isEmpty())
        return QStringLiteral("Invalid file location");
    QFile file(path);
    if (!file.open(QIODevice::WriteOnly))
        return QStringLiteral("Couldn't open %1 for writing: %2").arg(path, file.errorString());
    if (file.write(data) != data.size())
        return QStringLiteral("Couldn't write all data to %1").arg(path);
    return QString();
}

QString LocalFileService::saveText(const QUrl &fileUrl, const QString &text)
{
    return writeFile(fileUrl, text.toUtf8());
}

QString LocalFileService::saveBase64(const QUrl &fileUrl, const QString &base64)
{
    return writeFile(fileUrl, QByteArray::fromBase64(base64.toUtf8()));
}

QString LocalFileService::readText(const QUrl &fileUrl)
{
    const QString path = fileUrl.toLocalFile();
    if (path.isEmpty())
        return QString();
    QFile file(path);
    if (!file.open(QIODevice::ReadOnly))
        return QString();
    return QString::fromUtf8(file.readAll());
}
