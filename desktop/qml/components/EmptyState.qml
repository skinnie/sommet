import QtQuick
import SommetUi

// What an empty page says (André, 2026-11-02, desktop picks #11): an icon, a title, one
// sentence on what to do, and optionally one button that does it. Modelled on Home's "No watch
// connected". Errors still go through ErrorBanner.
//
//   EmptyState {
//       glyph: Icons.watch
//       title: qsTr("No activities yet")
//       text: qsTr("Plug in your watch, or connect intervals.icu.")
//       actionText: qsTr("Open Settings → Connections")
//       onAction: { NavBus.pendingSettingsSection = "connections"; NavBus.navigate("settings") }
//   }
Column {
    id: root

    property string glyph: ""
    property string title
    property string text
    property string actionText: ""
    signal action()

    width: parent ? parent.width : implicitWidth
    spacing: Theme.spacingSmall
    topPadding: Theme.spacingLarge
    bottomPadding: Theme.spacingLarge

    Rectangle {
        visible: root.glyph.length > 0
        anchors.horizontalCenter: parent.horizontalCenter
        width: 48
        height: 48
        radius: 24
        color: Theme.cardNested
        Icon {
            anchors.centerIn: parent
            glyph: root.glyph
            size: 24
            color: Theme.primary
        }
    }

    Text {
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        text: root.title
        color: Theme.text
        font.pixelSize: Theme.fontSizeSubtitle
        font.bold: true
    }

    Text {
        visible: root.text.length > 0
        width: parent.width
        horizontalAlignment: Text.AlignHCenter
        wrapMode: Text.WordWrap
        text: root.text
        color: Theme.mutedText
        font.pixelSize: Theme.fontSizeBody
    }

    Item { width: 1; height: 2; visible: root.actionText.length > 0 }

    RoundedButton {
        visible: root.actionText.length > 0
        anchors.horizontalCenter: parent.horizontalCenter
        accent: true
        text: root.actionText
        onClicked: root.action()
    }
}
