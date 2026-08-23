#pragma once

#include <QObject>
#include <QQmlEngine>
#include <QString>

// Real, 2026-08-08 ("Create a new menu, for suunto, called Intervals, that would link to our
// interval workout builder"). tools/workout_gui.py (packaged via PyInstaller, see
// tools/packaging/README.md) is a self-contained local web server + browser UI that already
// opens the user's own default browser once its server is up - the same thing double-
// clicking the packaged app does. Embedding it as an in-app browser view would need
// Qt6::WebEngine (a large dependency this project doesn't otherwise use anywhere) just to
// duplicate what the user's own browser already does for free - not worth it for what's
// fundamentally a "launch this other real app" action, not a page this app needs to render
// itself. So this Service does exactly that: prefers the packaged PyInstaller build for the
// current OS if one's actually been built (checked directly - only Linux has been as of this
// writing, see dist/linux/), falling back to `python3 tools/workout_gui.py` otherwise.
// Suunto-only (it's the Ambit3 App-Zone workout compiler, no Garmin equivalent) - NavRail
// hides this entry the same way it hides Ambit-only Backup content for a connected Garmin.
class IntervalsService : public QObject
{
    Q_OBJECT
    QML_ELEMENT
    QML_SINGLETON

public:
    explicit IntervalsService(QObject *parent = nullptr);

    // Starts the workout builder detached (it outlives this app either way, same as a
    // double-clicked app would) - "" on success, a friendly error otherwise. Nothing more
    // for this app to do afterward: the tool opens its own browser tab once its local
    // server is up.
    // `workoutName`, 2026-08-23: the Calendar opens this builder for a chosen day+sport with
    // the title pre-filled (e.g. "Running_24_08"), the scheduled-workout "workaround". Empty
    // (the default) just opens the builder as before.
    Q_INVOKABLE QString launch(const QString &workoutName = QString());
};
