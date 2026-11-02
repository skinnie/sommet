import QtQuick
import AmbitApp

// Connect IQ updates — lists the installed Connect IQ apps on a connected Garmin Edge and flags
// which have a newer version in Garmin's store. No Garmin login: it reads the device's own CIQ
// manifest over USB and queries Garmin's PUBLIC store metadata API. Detection only — downloading
// and installing an update is behind Garmin auth and not implemented. Backed by CiqUpdatesService
// -> tools/ciq_updates.py.
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
                    Icon { glyph: Icons.apps; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        text: qsTr("Connect IQ apps")
                        font.bold: true
                        font.pixelSize: Theme.fontSizeBodyLarge
                        color: Theme.text
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Checks the Connect IQ apps installed on a connected Edge for updates, " +
                                "using Garmin's public store — no login. Detection only: installing an " +
                                "update isn't available here.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("To download and install an update, use openExpress — it handles the " +
                                "Garmin sign-in that installing requires.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                }

                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: qsTr("Check for updates")
                        busy: CiqUpdatesService.busy
                        enabled: !CiqUpdatesService.busy
                        onClicked: CiqUpdatesService.check()
                    }
                }

                StatusNote {
                    width: parent.width
                    text: CiqUpdatesService.lastError
                    tone: "error"
                }

                StatusNote {
                    width: parent.width
                    text: !(CiqUpdatesService.checked && CiqUpdatesService.lastError.length === 0) ? ""
                        : CiqUpdatesService.updateCount > 0
                        ? qsTr("%n update(s) available.", "", CiqUpdatesService.updateCount)
                        : qsTr("All apps are up to date.")
                    tone: "success"
                }
            }
        }

        // One card per installed app (only after a successful check).
        Repeater {
            model: CiqUpdatesService.apps
            Card {
                width: column.width
                Row {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Column {
                        width: parent.width - updateBadge.width - Theme.spacingSmall
                        spacing: 2
                        Text {
                            width: parent.width
                            elide: Text.ElideRight
                            text: modelData.name
                            font.bold: true
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBody
                        }
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            text: modelData.updateAvailable
                                ? qsTr("Installed v%1 · store v%2").arg(modelData.installed).arg(modelData.latest)
                                : qsTr("Up to date (v%1)").arg(modelData.installed)
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    Rectangle {
                        id: updateBadge
                        visible: modelData.updateAvailable === true
                        anchors.verticalCenter: parent.verticalCenter
                        width: badgeText.width + Theme.spacingSmall * 2
                        height: badgeText.height + 6
                        radius: height / 2
                        color: Theme.success
                        Text {
                            id: badgeText
                            anchors.centerIn: parent
                            text: qsTr("Update")
                            color: "white"
                            font.pixelSize: Theme.fontSizeCaption
                            font.bold: true
                        }
                    }
                }
            }
        }
    }
}
