#pragma once

// CiqUpdatesService — checks a connected Garmin Edge's installed Connect IQ apps for available
// updates, with NO Garmin login. It is a thin C++ front to tools/ciq_updates.py, which reads the
// device's own Garmin/GarminDevice.xml manifest and queries Garmin's PUBLIC store metadata API
// (unauthenticated). This only DETECTS updates (installed version < latest); downloading the new
// .prg is behind Garmin auth and is not implemented. Registered into QML as the SommetUi singleton
// CiqUpdatesService (QML_ELEMENT/QML_SINGLETON). The check runs asynchronously (one network call
// per app) so the UI stays responsive.

#include <QObject>
#include <QProcess>
#include <QQmlEngine>
#include <QString>
#include <QVariantList>

class CiqUpdatesService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON
    // Installed CIQ apps: [{name, type, storeid, installed, latest, latestVersion, updateAvailable}].
    Q_PROPERTY(QVariantList apps READ apps NOTIFY stateChanged)
    // How many apps have an update available (0 if none / not yet checked).
    Q_PROPERTY(int updateCount READ updateCount NOTIFY stateChanged)
    // false until the first check() reply lands (tells "never checked" from "checked, nothing").
    Q_PROPERTY(bool checked READ checked NOTIFY stateChanged)
    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY stateChanged)

public:
    explicit CiqUpdatesService(QObject *parent = nullptr);

    QVariantList apps() const { return m_apps; }
    int updateCount() const { return m_updateCount; }
    bool checked() const { return m_checked; }
    bool busy() const { return m_busy; }
    QString lastError() const { return m_lastError; }

    // Find the Edge over USB, read its CIQ manifest, and query the public store for each app's
    // latest version. Asynchronous: sets busy, emits stateChanged when the result lands.
    Q_INVOKABLE void check();

signals:
    void stateChanged();
    void busyChanged();

private:
    void setBusy(bool b);

    QString m_toolPath;           // absolute path to ciq_updates.py
    QString m_python;             // python3 interpreter
    QVariantList m_apps;
    int m_updateCount = 0;
    bool m_checked = false;
    bool m_busy = false;
    QString m_lastError;
};
