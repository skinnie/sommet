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
    }
}
