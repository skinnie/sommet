import QtQuick
import QtQuick.Controls
import AmbitApp

// One-time "sync this bike computer's profile from intervals.icu?" question (André, 2026-09-25).
// Shown the first time a Bryton / Magene is found with values that differ from intervals.icu.
// "Yes" = sync now and automatically on every later plug (the vendor apps can reset the device's
// profile, see project_bryton_profile_sync), and the manual Sync-profile button goes away.
// "No" = never ask again; the manual Sync-profile button (BrytonProfileDialog) stays.
ThemedDialog {
    id: root

    property string deviceName: ""
    property var diff: []            // [{field, device, intervals}] from /api/<dev>/profile/compare

    signal answered(bool always)

    title: qsTr("Sync %1 profile?").arg(root.deviceName)
    standardButtons: Dialog.NoButton
    closePolicy: Popup.NoAutoClose   // an explicit answer is needed; it's remembered
    width: 440

    readonly property var labels: ({
        "ftp": qsTr("FTP (W)"), "lthr": qsTr("LTHR (bpm)"), "max_hr": qsTr("Max HR (bpm)"),
        "weight": qsTr("Weight (kg)"), "height": qsTr("Height (cm)"), "gender": qsTr("Gender"),
        "age": qsTr("Age")
    })
    function fmt(field, v) {
        if (field === "gender") return v === 1 ? qsTr("Male") : qsTr("Female")
        return v
    }

    contentItem: Column {
        spacing: Theme.spacingMedium

        Text {
            width: parent.width; wrapMode: Text.WordWrap
            text: qsTr("Your %1 has different values than intervals.icu. Update it from "
                       + "intervals.icu?").arg(root.deviceName)
            color: Theme.text; font.pixelSize: Theme.fontSizeBody
        }

        Repeater {
            model: root.diff
            delegate: Row {
                spacing: Theme.spacingMedium
                Text {
                    width: 130
                    text: root.labels[modelData.field] || modelData.field
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
                }
                Text {
                    text: root.fmt(modelData.field, modelData.device) + "  →  "
                          + root.fmt(modelData.field, modelData.intervals)
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                }
            }
        }

        Text {
            width: parent.width; wrapMode: Text.WordWrap
            text: qsTr("“Yes” keeps it in sync every time it's connected, and the Sync profile "
                       + "button goes away. “No” leaves the device as it is and keeps the button.")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
        }

        Row {
            spacing: Theme.spacingSmall
            layoutDirection: Qt.RightToLeft
            width: parent.width
            RoundedButton {
                text: qsTr("Yes, keep it in sync")
                onClicked: { root.close(); root.answered(true) }
            }
            RoundedButton {
                text: qsTr("No, I'll do it myself")
                onClicked: { root.close(); root.answered(false) }
            }
        }
    }
}
