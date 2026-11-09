import QtQuick
import QtQuick.Controls
import SommetUi

// Setting the watch's clock - André, 2026-08-11 (item 15): "regarding when you click, I would
// prefer we change to a window appearing like we did for the data fields, with the option
// from device, from different timezone."
//
// This replaces an inline menu that expanded inside the Home card. That menu existed because
// of a real problem: Overlay.overlay is null in this app, so three earlier attempts at a
// floating Popup failed, and expanding in place was the shape that worked. ThemedDialog is
// parented like every other dialog here and has since proven fine, so the card no longer has
// to grow and shrink to offer two choices.
ThemedDialog {
    id: root

    title: qsTr("Sync the watch clock")
    standardButtons: Dialog.Cancel
    width: 380

    onOpened: {
        useTimezone = false
        if (DeviceService.timezones.length === 0)
            DeviceService.fetchTimezones()
    }

    // Which of the two the dialog is showing. Starts on the simple one.
    property bool useTimezone: false

    contentItem: Column {
        spacing: Theme.spacingSmall

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: qsTr("Set the watch to this computer's time, or to another timezone.")
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeBody
        }

        RoundedButton {
            width: parent.width
            text: qsTr("From this device")
            enabled: !DeviceService.timeSyncBusy
            onClicked: {
                DeviceService.syncTime()
                root.close()
            }
        }

        RoundedButton {
            width: parent.width
            visible: !root.useTimezone
            text: qsTr("From a different timezone...")
            enabled: !DeviceService.timeSyncBusy
            onClicked: root.useTimezone = true
        }

        Column {
            width: parent.width
            visible: root.useTimezone
            spacing: Theme.spacingSmall

            RoundedComboBox {
                id: tzCombo
                width: parent.width
                model: DeviceService.timezones
            }
            Text {
                visible: tzCombo.currentText.length > 0
                width: parent.width
                wrapMode: Text.WordWrap
                text: qsTr("It is %1 there now.")
                        .arg(DeviceService.currentTimeInZone(tzCombo.currentText))
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
            }
            RoundedButton {
                width: parent.width
                text: qsTr("Sync to this timezone")
                enabled: DeviceService.timezones.length > 0 && !DeviceService.timeSyncBusy
                onClicked: {
                    DeviceService.syncTime(tzCombo.currentText)
                    root.close()
                }
            }
        }
    }
}
