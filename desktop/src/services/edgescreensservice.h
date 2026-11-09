#pragma once

// EdgeScreensService — edits the data screens of a Garmin Edge activity profile over USB, with NO
// Garmin Connect and NO cloud. It is a thin C++ front to the Python tools in tools/
// (edge_screens_device.py, which wraps garmin_edge_screens.py) — the same "the Python tool does the
// device work, the desktop calls it" principle used for Bryton/Magene. Registered into QML as the
// SommetUi singleton EdgeScreensService (QML_ELEMENT/QML_SINGLETON below).
//
// The tool reads the SPORT-profile FIT (mesg 14 data screens); this service just runs it, parses
// its JSON, and exposes it to EdgeScreensPage.qml. Read-only until saveScreen(), which writes to the
// device's Garmin/NewFiles (its own sanctioned import dir); the Edge applies it on the next power-up.

#include <QObject>
#include <QProcess>
#include <QQmlEngine>
#include <QString>
#include <QVariantList>

class EdgeScreensService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON
    // Activity profiles on the connected Edge: [{file, name}].
    Q_PROPERTY(QVariantList profiles READ profiles NOTIFY profilesChanged)
    // Full data-field catalog: [{id, name}] — the built-in fields.
    Q_PROPERTY(QVariantList catalog READ catalog NOTIFY catalogChanged)
    // Screens of the currently loaded profile: [{index, layout, fields:[{id,name}]}].
    Q_PROPERTY(QVariantList screens READ screens NOTIFY screensChanged)
    Q_PROPERTY(QString currentProfile READ currentProfile NOTIFY screensChanged)
    // How many built-in typed screens (Map/Elevation/ClimbPro/…) the profile has — shown as a note.
    Q_PROPERTY(int specialScreens READ specialScreens NOTIFY screensChanged)
    // The built-in typed screens themselves: [{index, stype, name, editable, hasFields, fields:[...]}].
    Q_PROPERTY(QVariantList specialList READ specialList NOTIFY screensChanged)
    Q_PROPERTY(bool deviceConnected READ deviceConnected NOTIFY profilesChanged)
    Q_PROPERTY(bool busy READ busy NOTIFY busyChanged)
    Q_PROPERTY(QString lastError READ lastError NOTIFY lastErrorChanged)
    Q_PROPERTY(QString lastNote READ lastNote NOTIFY lastNoteChanged)

public:
    explicit EdgeScreensService(QObject *parent = nullptr);

    QVariantList profiles() const { return m_profiles; }
    QVariantList catalog() const { return m_catalog; }
    QVariantList screens() const { return m_screens; }
    QString currentProfile() const { return m_currentProfile; }
    int specialScreens() const { return m_specialScreens; }
    QVariantList specialList() const { return m_specialList; }
    bool deviceConnected() const { return !m_profiles.isEmpty(); }
    bool busy() const { return m_busy; }
    QString lastError() const { return m_lastError; }
    QString lastNote() const { return m_lastNote; }

    // Load the field catalog + the connected device's profiles. Call on page load.
    Q_INVOKABLE void refresh();
    // Load one profile's screens (file = the .fit name from profiles[]).
    Q_INVOKABLE void loadProfile(const QString &file);
    // Write an edited screen back to the device. fieldsCsv = comma-separated field NAMES or ids,
    // left to right (e.g. "Speed,Power,Heart Rate,Distance"); "none" blanks a slot.
    // variant = the arrangement (mesg14 f8): 0=A, 1=B for counts 3-7; pass -1 to leave it unchanged.
    // togglesCsv = screen toggles to apply in the same write, "field:value,field:value" (empty = none).
    Q_INVOKABLE void saveScreen(const QString &file, int screenIndex, const QString &fieldsCsv,
                                int variant = -1, const QString &togglesCsv = QString());
    // Add a new data screen to the profile (enables the next data-screen slot with these field NAMES/ids).
    Q_INVOKABLE void addScreen(const QString &file, const QString &fieldsCsv, int variant = -1);
    // Set a screen toggle (a mesg-13 settings field, e.g. Stamina's effort display) to a value.
    Q_INVOKABLE void setToggle(const QString &file, int field, int value);

signals:
    void profilesChanged();
    void catalogChanged();
    void screensChanged();
    void busyChanged();
    void lastErrorChanged();
    void lastNoteChanged();
    void saved();

private:
    // Run edge_screens_device.py with args, return parsed top-level JSON object (or set error).
    QVariantMap runTool(const QStringList &args);
    void setBusy(bool b);
    void setError(const QString &e);

    QString m_toolPath;           // absolute path to edge_screens_device.py
    QString m_python;             // python3 interpreter
    QVariantList m_profiles;
    QVariantList m_catalog;
    QVariantList m_screens;
    QString m_currentProfile;
    int m_specialScreens = 0;
    QVariantList m_specialList;
    bool m_busy = false;
    QString m_lastError;
    QString m_lastNote;
};
