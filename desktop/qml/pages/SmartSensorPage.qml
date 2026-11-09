import QtQuick
import QtQuick.Controls
import SommetUi

// Suunto Smart Sensor - its own sidebar page (moved out of Settings 2026-08-14, André:
// "Create a card/menu for it, after firmware"). The HR belt is a separate BLE peripheral,
// independent of the watch, so it earns its own page rather than a Settings card. Backed by
// the same SmartSensorService - this only relocates the UI, the logic is unchanged.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 480
        spacing: Theme.spacingLarge

        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.watch; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        text: qsTr("Suunto Smart Sensor")
                        font.bold: true
                        font.pixelSize: Theme.fontSizeBodyLarge
                        color: Theme.text
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("The HR belt (not the watch) - reads its battery, firmware, " +
                                "and a live heart rate sample over Bluetooth.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        // No PIN/passkey here (Just Works pairing, confirmed live
                        // 2026-08-13) - "pairing" is just discovering + connecting, which
                        // refresh() already does on its own whenever the belt isn't
                        // already known to Bluetooth. Label reflects that instead of
                        // always saying "Refresh", since a first press genuinely does
                        // more work than a later one.
                        text: SmartSensorService.loading
                            ? qsTr("Searching…")
                            : (SmartSensorService.checked && SmartSensorService.ok && SmartSensorService.found
                                ? qsTr("Refresh") : qsTr("Pair"))
                        enabled: !SmartSensorService.loading
                        onClicked: SmartSensorService.refresh()
                    }
                    // Real request, 2026-08-13 ("just add a button to forget") - only
                    // shown once there's actually something paired to forget. Not
                    // destructive to the belt itself (see SmartSensorService::forget()'s
                    // own comment): it holds no bond secret worth losing, so this is a
                    // safely repeatable Bluetooth-side reset, freeing Pair to be tried
                    // again from a clean slate.
                    RoundedButton {
                        visible: SmartSensorService.checked && SmartSensorService.ok && SmartSensorService.found
                        text: qsTr("Forget")
                        enabled: !SmartSensorService.loading
                        onClicked: SmartSensorService.forget()
                    }
                }
                Text {
                    visible: SmartSensorService.checked && !SmartSensorService.ok
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: SmartSensorService.errorText
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeBody
                }
                Text {
                    visible: SmartSensorService.checked && SmartSensorService.ok && !SmartSensorService.found
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Not found - make sure it's powered on and nearby, then refresh.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                }
                Column {
                    visible: SmartSensorService.checked && SmartSensorService.ok && SmartSensorService.found
                    width: parent.width
                    spacing: 2
                    Text { text: qsTr("Model: %1 (%2)").arg(SmartSensorService.model).arg(SmartSensorService.manufacturer); color: Theme.text; font.pixelSize: Theme.fontSizeBody }
                    Text { text: qsTr("Serial: %1").arg(SmartSensorService.serial); color: Theme.text; font.pixelSize: Theme.fontSizeBody }
                    Text { text: qsTr("Firmware: %1  (hw %2, sw %3)").arg(SmartSensorService.fwRevision).arg(SmartSensorService.hwRevision).arg(SmartSensorService.swRevision); color: Theme.text; font.pixelSize: Theme.fontSizeBody }
                    Text { text: qsTr("Battery: %1").arg(SmartSensorService.batteryPercent >= 0 ? SmartSensorService.batteryPercent + "%" : qsTr("unknown")); color: Theme.text; font.pixelSize: Theme.fontSizeBody }
                    Text {
                        text: qsTr("Heart rate: %1").arg(
                            SmartSensorService.heartRateBpm > 0
                                ? SmartSensorService.heartRateBpm + " bpm"
                                : qsTr("not worn / no reading"))
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
            }
        }
    }
}
