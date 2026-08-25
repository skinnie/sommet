import QtQuick
import QtQuick.Controls
import AmbitApp

// Suunto Apps builder launcher. Merged 2026-08-23 (André): one card, two options, instead of
// two separate nav entries (App Zone + Intervals) that each opened a different tool. Neither
// tool is duplicated in-app - both are real, self-contained programs with their own local
// server + browser UI (tools/apps_gui.py and tools/workout_gui.py); this only launches them,
// the same "launch the other real app" scope both launchers always had.
//
// The two are genuinely different builders and must stay wired to their own tool - the bug
// this fixes was the Workout option opening the generic app builder:
//   - Workout Builder  (IntervalsService -> workout_gui.py): a structured interval workout,
//     installed as a native guided workout in the watch's WORKOUT menu (target band + step
//     text).
//   - App Builder      (AppZoneService  -> apps_gui.py):     a free-form App Zone script,
//     compiled and installed onto a sport mode's display field.
// Distinct from the Suunto Apps CATALOG (installing pre-made apps), which lives in the Sport
// Modes data-field picker.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    property string lastResultText: ""
    property bool lastResultOk: true

    // Read the watch's current per-app logging state so the toggle card below reflects it.
    // Read-only; safe whenever a watch is connected (the card explains itself when it isn't).
    Component.onCompleted: AppsService.refreshLogging()

    function launch(service) {
        const error = service.launch();
        root.lastResultOk = error.length === 0;
        root.lastResultText = error.length === 0
            ? qsTr("Launched - check your browser.")
            : error;
    }

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 480
        spacing: Theme.spacingMedium

        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingMedium

                Row {
                    spacing: Theme.spacingSmall
                    Text {
                        text: Icons.apps
                        font.family: Icons.fontFamily
                        font.pixelSize: 28
                        color: Theme.primary
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        text: qsTr("Apps")
                        font.bold: true
                        font.pixelSize: Theme.fontSizeHeading
                        color: Theme.text
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Build something for the watch and install it. Each option opens "
                                + "in your default browser as its own local app, separate from "
                                + "this window. Compiling needs internet; everything else works "
                                + "offline.")
                }

                // --- Interval workout -------------------------------------------------------
                Rectangle {
                    width: parent.width
                    height: workoutCol.height + Theme.spacingMedium * 2
                    radius: Theme.radiusSmall
                    color: "transparent"
                    border.width: 1
                    border.color: Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.35)

                    Column {
                        id: workoutCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Theme.spacingMedium
                        spacing: Theme.spacingSmall

                        Text {
                            text: qsTr("Interval workout")
                            font.bold: true
                            color: Theme.text
                        }
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            text: qsTr("A structured interval workout, installed as a native "
                                        + "guided workout in the watch's WORKOUT menu - target "
                                        + "band and step text.")
                        }
                        RoundedButton {
                            text: qsTr("Open Workout Builder")
                            onClicked: root.launch(IntervalsService)
                        }
                    }
                }

                // --- Free-form App Zone app -------------------------------------------------
                Rectangle {
                    width: parent.width
                    height: appCol.height + Theme.spacingMedium * 2
                    radius: Theme.radiusSmall
                    color: "transparent"
                    border.width: 1
                    border.color: Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.35)

                    Column {
                        id: appCol
                        anchors.left: parent.left
                        anchors.right: parent.right
                        anchors.top: parent.top
                        anchors.margins: Theme.spacingMedium
                        spacing: Theme.spacingSmall

                        Text {
                            text: qsTr("Custom app")
                            font.bold: true
                            color: Theme.text
                        }
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            text: qsTr("A free-form App Zone script, compiled on the community "
                                        + "compiler and installed onto a sport mode's display "
                                        + "field.")
                        }
                        RoundedButton {
                            text: qsTr("Open App Builder")
                            onClicked: root.launch(AppZoneService)
                        }
                    }
                }

                Text {
                    visible: root.lastResultText.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: root.lastResultOk ? Theme.success : Theme.error
                    text: root.lastResultText
                }
            }
        }

        // --- App logging ---------------------------------------------------------------
        // André, 2026-08-25 ("can't we have the toggle?"): the Movescount-era per-app logging
        // (EXERCISE_MODES_RULE.LogRule) is on by default when an app is installed, but this
        // card is where you turn a specific app's logging off (or back on) without the CLI.
        // A logged app's per-sample output lands in the recorded Move (Charts tab / FIT dev
        // fields / intervals custom streams). Needs a connected watch to read and write.
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingMedium

                Row {
                    spacing: Theme.spacingSmall
                    Text {
                        text: Icons.activities
                        font.family: Icons.fontFamily
                        font.pixelSize: 28
                        color: Theme.primary
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        text: qsTr("App logging")
                        font.bold: true
                        font.pixelSize: Theme.fontSizeHeading
                        color: Theme.text
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Each Suunto App on a sport mode can log its output into every "
                                + "recorded Move - it then shows up on the activity's Charts, in "
                                + "the exported FIT, and as an intervals.icu custom stream. On by "
                                + "default; turn it off here for any app you don't want recorded.")
                }

                // One row per activated app, grouped visually by mode via the small mode label.
                Repeater {
                    model: AppsService.loggedApps
                    delegate: Row {
                        width: parent ? parent.width : 0
                        spacing: Theme.spacingMedium

                        Column {
                            width: parent.width - logSwitch.width - Theme.spacingMedium
                            spacing: 1
                            anchors.verticalCenter: parent.verticalCenter
                            Text {
                                width: parent.width
                                elide: Text.ElideRight
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeBody
                                text: modelData.app && modelData.app.length > 0
                                      ? modelData.app
                                      : qsTr("App #%1").arg(modelData.ruleIdx)
                            }
                            Text {
                                width: parent.width
                                elide: Text.ElideRight
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                                text: modelData.modeName && modelData.modeName.length > 0
                                      ? modelData.modeName
                                      : qsTr("Mode %1").arg(modelData.mode)
                            }
                        }

                        RoundedSwitch {
                            id: logSwitch
                            anchors.verticalCenter: parent.verticalCenter
                            checked: modelData.logRule
                            enabled: !AppsService.loggingBusy
                            onToggled: AppsService.setLogging(modelData.mode, modelData.slot, checked)
                        }
                    }
                }

                // Empty state: either no watch, or no app installed on any mode yet.
                Text {
                    visible: AppsService.loggedApps.length === 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("No Suunto App is installed on a sport mode yet - install one from "
                                + "a sport mode's data-field picker and it'll appear here to log. "
                                + "(Connect your watch if this looks empty unexpectedly.)")
                }
            }
        }
    }
}
