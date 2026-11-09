#pragma once

#include <QDir>
#include <QFile>
#include <QFileInfo>
#include <QList>
#include <QPair>
#include <QSettings>
#include <QStandardPaths>
#include <QString>

// Where the app keeps its OWN databases (activities.db, gear.db). Defaults to the OS app-data
// location; the user can point it anywhere via Settings -> Database ("choose where your data is
// kept") - a NAS mount, or a Dropbox/Mega folder. Whatever they choose is their call: a cloud
// folder only safely shares between desktops one-at-a-time (it replaces the file, it can't merge),
// and the phone can't use a folder at all - the NAS/server sync is the only real cross-device
// path, and it always keeps a local copy. This single helper is the one source of truth so every
// service (activities, gear, coach, backup) reads/writes the same place.
namespace AppPaths {

inline QString defaultDatabaseDir()
{
    const QString def = QStandardPaths::writableLocation(QStandardPaths::AppDataLocation);
    QDir().mkpath(def);
    return def;
}

inline QString databaseDir()
{
    const QString custom = QSettings().value(QStringLiteral("database/dir")).toString();
    if (!custom.isEmpty() && QDir(custom).exists())
        return custom;
    return defaultDatabaseDir();
}

// Backups, Garmin key, scale weigh-ins, ... (the backend's BACKUP_DIR).
inline QString backupsDir()
{
    return QDir::homePath() + QStringLiteral("/SommetBackups");
}

// Moves an old folder's contents into the new one item by item; an item already on both sides
// stays in the old folder (never overwritten). Removes the old folder if it ends up empty.
inline void mergeFolder(const QString &oldPath, const QString &newPath)
{
    QDir oldDir(oldPath);
    const auto items = oldDir.entryInfoList(QDir::AllEntries | QDir::Hidden | QDir::System
                                            | QDir::NoDotAndDotDot);
    for (const QFileInfo &item : items) {
        const QString target = newPath + QLatin1Char('/') + item.fileName();
        const QFileInfo t(target);
        if (!t.exists() && !t.isSymLink())
            QDir().rename(item.absoluteFilePath(), target);
        else if (item.isDir() && !item.isSymLink() && t.isDir())
            mergeFolder(item.absoluteFilePath(), target);
    }
    QDir().rmdir(oldPath);   // fails (harmlessly) unless empty
}

// Up to 0.2.66 the app's folders carried its old name (AmbitApp). Moves them to the new names
// once; called at start-up before the backend starts (tools/sommet_paths.py does the same for a
// backend or tool run on its own). Never fails: if a move doesn't work, the old folder stays.
inline void moveOldFolders()
{
    const QString home = QDir::homePath();
    const QList<QPair<QString, QString>> moves = {
        {home + QStringLiteral("/AmbitAppBackups"), home + QStringLiteral("/SommetBackups")},
        {home + QStringLiteral("/AmbitAppPlans"), home + QStringLiteral("/SommetPlans")},
        {home + QStringLiteral("/.config/ambit-app"), home + QStringLiteral("/.config/Sommet")},
        // Bluetooth log, Karoo captures, old map tiles: moved too, nothing is deleted.
        {home + QStringLiteral("/.cache/AmbitApp"), home + QStringLiteral("/.cache/Sommet")},
    };
    for (const auto &m : moves) {
        const QFileInfo oldInfo(m.first);
        if (!oldInfo.isDir() || oldInfo.isSymLink())
            continue;
        if (!QFileInfo::exists(m.second)) {
            QDir().mkpath(QFileInfo(m.second).absolutePath());
            QDir().rename(m.first, m.second);
        } else if (QFileInfo(m.second).isDir()) {
            mergeFolder(m.first, m.second);
        }
    }
    // The old log file next to the databases.
    QFile::remove(QStandardPaths::writableLocation(QStandardPaths::AppDataLocation)
                  + QStringLiteral("/ambitapp.log"));
}

} // namespace AppPaths
