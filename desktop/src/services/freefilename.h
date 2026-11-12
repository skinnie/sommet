#pragma once

#include <QDir>
#include <QFileInfo>
#include <QRegularExpression>
#include <QString>

// A free path for fileName inside dir: "a.gpx", else "a (2).gpx", "a (3).gpx", ... Characters
// that aren't allowed in file names on Windows/macOS/Linux become "_". Never overwrites.
inline QString freeFileName(const QString &dir, const QString &fileName)
{
    QString safe = fileName;
    safe.replace(QRegularExpression(QStringLiteral("[\\\\/:*?\"<>|]")), QStringLiteral("_"));
    QDir d(dir);
    if (!d.exists(safe))
        return d.filePath(safe);
    const QFileInfo fi(safe);
    const QString base = fi.completeBaseName(), ext = fi.suffix();
    for (int n = 2; n < 10000; ++n) {
        const QString cand = ext.isEmpty() ? QStringLiteral("%1 (%2)").arg(base).arg(n)
                                           : QStringLiteral("%1 (%2).%3").arg(base).arg(n).arg(ext);
        if (!d.exists(cand))
            return d.filePath(cand);
    }
    return d.filePath(safe);
}
