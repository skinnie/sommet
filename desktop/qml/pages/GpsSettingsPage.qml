import QtQuick
import QtQuick.Controls
import QtCore
import AmbitApp

// GPS settings - the selected bike computer's own settings page, the bike-computer twin of Watch
// settings (André, 2026-09-25: "when it is detected and selected, put that in a gps settings, like
// we have watch settings for suunto ... more linear ... one less button in the home"; then "yes get
// the same treatment" for the Magene). Shown in the nav only while a Bryton or Magene is the active
// device; Home keeps just "Sync rides". Everything that used to be a Home button lives here:
//   both:    Profile - reconcile with intervals.icu (BrytonProfileDialog, per-device apiBase)
//   Bryton:  Data screens (System/Grid.ini)                              - BrytonScreensPanel
//   Magene:  Data screens, Device settings (BLE), Altitude calibration   - Magene*Panel
// The Magene is Bluetooth: each panel reads on its own short connection when the page opens.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    readonly property string kind: DeviceService.activeBikeKind
    readonly property bool isMagene: root.kind === "c406"
    readonly property string mageneAddress: BikeDevices.magene ? BikeDevices.magene.address : ""
    readonly property string deviceName: root.isMagene
        ? (BikeDevices.magene && BikeDevices.magene.name ? BikeDevices.magene.name : qsTr("Magene C406"))
        : qsTr("Bryton Aero 60")

    // Same store Home's one-time ProfileSyncPrompt answers into ("auto" / "manual").
    Settings { id: profileSyncPrefs; category: "bikeProfileSync" }

    property bool altBusy: false
    property string altMsg: ""
    function calibrateAltitude() {
        root.altBusy = true; root.altMsg = ""
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            root.altBusy = false
            let r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) {}
            root.altMsg = r.ok ? qsTr("Altitude calibrated from the C406's GPS ✓")
                               : (r.error || qsTr("Altitude calibration failed."))
        }
        xhr.open("POST", "http://127.0.0.1:8766/api/magene/device")
        xhr.setRequestHeader("Content-Type", "application/json")
        const body = { action: "altitude" }
        if (root.mageneAddress.length > 0) body.address = root.mageneAddress
        xhr.send(JSON.stringify(body))
    }

    Column {
        id: column
        x: Theme.spacingLarge
        y: Theme.spacingLarge
        width: root.width - Theme.spacingLarge * 2
        spacing: Theme.spacingMedium

        Row {
            spacing: Theme.spacingSmall
            BikeComputerIcon { kind: root.kind; size: 22; anchors.verticalCenter: parent.verticalCenter }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("%1 settings").arg(root.deviceName)
                color: Theme.text; font.pixelSize: Theme.fontSizeHeading; font.bold: true
            }
        }

        // ---- Profile (both) ----
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    text: qsTr("Profile")
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: profileSyncPrefs.value(root.kind + "_mode", "") === "auto"
                          ? qsTr("Kept in sync with intervals.icu automatically every time it's connected.")
                          : qsTr("FTP, LTHR, max HR, weight, height, gender and age — compare with intervals.icu and pick the right values.")
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                }
                RoundedButton {
                    text: qsTr("Sync profile")
                    onClicked: {
                        profileDialog.apiBase = root.isMagene ? "magene" : "bryton"
                        profileDialog.deviceName = root.isMagene ? qsTr("Magene") : qsTr("Bryton")
                        profileDialog.address = root.isMagene ? root.mageneAddress : ""
                        profileDialog.open()
                    }
                }
            }
        }

        // ---- Data screens ----
        Card {
            width: parent.width
            Loader {
                width: parent.width
                sourceComponent: root.isMagene ? mageneScreens : brytonScreens
            }
        }
        Component { id: brytonScreens; BrytonScreensPanel { } }
        Component { id: mageneScreens; MageneScreensPanel { address: root.mageneAddress } }

        // ---- Magene only: device settings + altitude ----
        Card {
            width: parent.width
            visible: root.isMagene
            Loader {
                width: parent.width
                active: root.isMagene
                sourceComponent: Column {
                    spacing: Theme.spacingSmall
                    Text {
                        text: qsTr("Device settings")
                        color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                    }
                    MageneSettingsPanel { width: parent.width; address: root.mageneAddress }
                }
            }
        }
        Card {
            width: parent.width
            visible: root.isMagene
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    text: qsTr("Altitude")
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("Re-baseline the C406's barometric altitude to its own GPS fix (take it outside first).")
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: root.altBusy ? qsTr("Calibrating…") : qsTr("Calibrate altitude")
                        enabled: !root.altBusy
                        onClicked: root.calibrateAltitude()
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        visible: root.altMsg.length > 0
                        text: root.altMsg
                        color: root.altMsg.indexOf("✓") >= 0 ? Theme.success : Theme.error
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }
            }
        }
    }

    BrytonProfileDialog { id: profileDialog }
}
