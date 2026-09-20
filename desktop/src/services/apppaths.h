#pragma once

#include <QDir>
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

} // namespace AppPaths
