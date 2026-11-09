import QtQuick
import QtQuick.Controls
import SommetUi

// Choose which watch Testing mode pretends to be - André, 2026-08-11: "testing mode, opens a
// window and we can choose device, based on all the characteristics we already know...always
// linked".
//
// "Always linked" is the point: every device here, and every number beside it, comes from the
// generated capability table (SuuntoLink's own module for the limits, its Devices.xml for the
// names). Nothing is typed in. A device this project has never physically seen still appears
// with its own real ceilings, and picking it makes the app behave accordingly - a Traverse
// really does show 5 sport modes and 4 displays and no multisport section.
ThemedDialog {
    id: root

    title: qsTr("Choose a device to simulate")
    standardButtons: Dialog.Close
    width: 460
    height: Math.min(560, parent ? parent.height - Theme.spacingLarge * 2 : 560)

    property var devices: []
    property string current: ""

    signal deviceChosen(string variant)

    onOpened: reload()

    function reload() {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            try {
                const reply = JSON.parse(xhr.responseText)
                root.devices = reply.devices || []
            } catch (e) {
                root.devices = []
            }
        }
        xhr.open("GET", "http://127.0.0.1:8766/api/demo/devices")
        xhr.send()
    }

    contentItem: Flickable {
        contentWidth: width
        contentHeight: list.height
        clip: true

        Column {
            id: list
            width: parent.width
            spacing: 2

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: qsTr("The app adapts to whichever you pick - its limits are the real " +
                            "ones for that watch.")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                bottomPadding: Theme.spacingSmall
            }

            Repeater {
                model: root.devices
                delegate: Item {
                    id: deviceRow
                    required property var modelData
                    width: list.width
                    height: 52

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: 1
                        radius: 6
                        color: Theme.primary
                        opacity: deviceRow.modelData.variant === root.current ? 0.16
                                 : (deviceHover.hovered ? 0.08 : 0)
                        Behavior on opacity { NumberAnimation { duration: 120 } }
                    }

                    Column {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.leftMargin: Theme.spacingSmall
                        spacing: 1

                        Text {
                            text: deviceRow.modelData.name
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBody
                            font.bold: deviceRow.modelData.variant === root.current
                        }
                        Text {
                            // The capabilities, spelled out - it is the reason to pick one
                            // device over another here.
                            text: deviceRow.modelData.hasSportModes
                                  ? qsTr("%1 sport modes · %2 displays · %3")
                                      .arg(deviceRow.modelData.maxSportModes)
                                      .arg(deviceRow.modelData.maxDisplays)
                                      .arg(deviceRow.modelData.maxMultisportModes > 0
                                           ? qsTr("%1 multisport").arg(deviceRow.modelData.maxMultisportModes)
                                           : qsTr("no multisport"))
                                  : qsTr("no sport modes on this device")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }

                    HoverHandler { id: deviceHover; cursorShape: Qt.PointingHandCursor }
                    TapHandler {
                        onTapped: {
                            root.deviceChosen(deviceRow.modelData.variant)
                            root.close()
                        }
                    }
                }
            }
        }
    }
}
