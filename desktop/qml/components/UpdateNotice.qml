import QtQuick
import QtQuick.Layouts
import SommetUi

// "Sommet 0.2.58 is ready" (André, 2026-11-03, 🟣124): the newer version, the first lines of
// what's new, and a Download button. On Home it closes with ×, until the next version comes
// out; Settings → About shows it without the ×. Same look as StatusNote: a card with a thin
// coloured bar on the left. The Android twin is components/UpdateNotice.tsx.
Rectangle {
    id: root

    property bool closable: true
    // How many "what's new" lines to show before "See all on the release page".
    property int maxHighlights: 3

    readonly property bool linuxOnly: !UpdateService.hasDirectDownload

    visible: UpdateService.available && (!closable || UpdateService.showNotice)
    implicitHeight: body.implicitHeight + 2 * Theme.spacingMedium
    radius: Theme.radiusSmall
    color: Theme.card
    border.width: 1
    border.color: Theme.border

    Rectangle {
        x: 5
        y: 8
        width: 3
        height: parent.height - 16
        radius: 1.5
        color: Theme.primary
    }

    RowLayout {
        id: body
        x: 18
        y: Theme.spacingMedium
        width: root.width - x - (root.closable ? 40 : Theme.spacingMedium)
        spacing: Theme.spacingSmall

        Icon {
            Layout.alignment: Qt.AlignTop
            glyph: Icons.download
            size: 20
            color: Theme.primary
        }

        ColumnLayout {
            Layout.fillWidth: true
            spacing: 4

            Text {
                Layout.fillWidth: true
                text: qsTr("Sommet %1 is ready").arg(UpdateService.latestVersion)
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                font.bold: true
                wrapMode: Text.WordWrap
            }
            Text {
                Layout.fillWidth: true
                text: qsTr("You have %1.").arg(UpdateService.currentVersion)
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeLabel
            }
            Text {
                Layout.fillWidth: true
                Layout.topMargin: 2
                visible: UpdateService.highlights.length > 0
                text: qsTr("What's new:")
                color: Theme.text
                font.pixelSize: Theme.fontSizeLabel
            }
            Repeater {
                model: UpdateService.highlights.slice(0, root.maxHighlights)
                delegate: Text {
                    required property string modelData
                    Layout.fillWidth: true
                    text: "• " + modelData
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeLabel
                    wrapMode: Text.WordWrap
                }
            }
            Row {
                Layout.topMargin: Theme.spacingSmall
                spacing: Theme.spacingSmall
                RoundedButton {
                    accent: true
                    // Linux runs from source: there is no file to download, only the page.
                    text: root.linuxOnly ? qsTr("Open the release page") : qsTr("Download")
                    onClicked: UpdateService.download()
                }
                RoundedButton {
                    visible: !root.linuxOnly
                    text: UpdateService.highlights.length > root.maxHighlights
                          ? qsTr("See all that's new") : qsTr("Release notes")
                    onClicked: UpdateService.openReleasePage()
                }
            }
        }
    }

    Rectangle {
        visible: root.closable
        x: root.width - width - 8
        y: 8
        width: 24
        height: 24
        radius: Theme.radiusSmall
        color: closeHover.hovered ? Theme.cardNested : "transparent"
        Icon {
            anchors.centerIn: parent
            glyph: Icons.close
            size: 16
            color: closeHover.hovered ? Theme.text : Theme.mutedText
        }
        HoverHandler { id: closeHover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: UpdateService.dismiss() }
        Accessible.role: Accessible.Button
        Accessible.name: qsTr("Close until the next version")
        Accessible.onPressAction: UpdateService.dismiss()
    }

    Accessible.role: Accessible.AlertMessage
    Accessible.name: qsTr("Sommet %1 is ready").arg(UpdateService.latestVersion)
}
