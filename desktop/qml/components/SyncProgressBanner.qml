import QtQuick
import SommetUi

// Sommet Sync progress (sync/12, André 2026-11-19: "maybe we should add a progress bar for sync
// no? and warn about freezes"). Shown on every page while a sync runs, under the SuuntoLink
// banner; pushes the page down, never covers it. A first sync (~4,800 activities and their
// tracks) takes minutes, so it says so. A failed sync stays up until closed.
Rectangle {
    id: root
    readonly property string stage: ActivityService.sommetSyncStage
    readonly property int done: ActivityService.sommetSyncDone
    readonly property int total: ActivityService.sommetSyncTotal
    readonly property bool first: ActivityService.sommetSyncFirst
    property string errorText: ""

    visible: stage !== "" || errorText !== ""
    implicitHeight: col.implicitHeight + Theme.spacingSmall * 2
    radius: Theme.radiusSmall
    color: Theme.card
    border.width: 1
    border.color: errorText !== "" ? Theme.error : Theme.border

    Connections {
        target: ActivityService
        function onSommetSyncError(message) { root.errorText = message || qsTr("The server didn't answer.") }
        function onSommetSyncFinished(pulled, pushed) { root.errorText = "" }
    }

    function stageText() {
        if (stage === "moves") return qsTr("Saving activities")
        if (stage === "tracks") return qsTr("Fetching tracks")
        if (stage === "sending") return qsTr("Sending to your server")
        return ""
    }

    Column {
        id: col
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.verticalCenter: parent.verticalCenter
        anchors.leftMargin: Theme.spacingMedium
        anchors.rightMargin: Theme.spacingMedium
        spacing: 6

        Item {
            width: parent.width
            height: Math.max(title.implicitHeight, closeText.implicitHeight)
            Text {
                id: title
                anchors.left: parent.left
                anchors.right: closeText.left
                anchors.rightMargin: Theme.spacingSmall
                elide: Text.ElideRight
                color: root.errorText !== "" ? Theme.error : Theme.text
                font.pixelSize: Theme.fontSizeBody
                font.weight: Font.DemiBold
                text: root.errorText !== ""
                      ? qsTr("Sommet Sync failed: %1").arg(root.errorText)
                      : qsTr("Sommet Sync · %1").arg(root.stageText())
                        + (root.total > 0 ? "  " + Number(root.done).toLocaleString(Qt.locale(), "f", 0)
                                                 + " / " + Number(root.total).toLocaleString(Qt.locale(), "f", 0) : "")
            }
            Text {
                id: closeText
                visible: root.errorText !== ""
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("Close")
                color: Theme.primary
                font.pixelSize: Theme.fontSizeLabel
                TapHandler { onTapped: root.errorText = "" }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
        }

        // The bar: filled to done/total; with no total yet (waiting for the server) a short
        // block slides across, so it reads as working, not stuck.
        Rectangle {
            visible: root.errorText === ""
            width: parent.width
            height: 4
            radius: 2
            color: Theme.cardNested
            clip: true
            Rectangle {
                visible: root.total > 0
                height: parent.height
                radius: 2
                color: Theme.primary
                width: root.total > 0 ? parent.width * Math.min(1, root.done / root.total) : 0
                Behavior on width { NumberAnimation { duration: 200 } }
            }
            Rectangle {
                id: slider
                visible: root.total <= 0
                height: parent.height
                radius: 2
                color: Theme.primary
                width: parent.width * 0.25
                NumberAnimation on x {
                    running: root.visible && root.total <= 0
                    loops: Animation.Infinite
                    from: -slider.width; to: slider.parent.width; duration: 1200
                }
            }
        }

        Text {
            visible: root.first && root.errorText === ""
            width: parent.width
            wrapMode: Text.WordWrap
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeLabel
            text: qsTr("First sync: all your activities and their tracks are coming in. This can take a few minutes; you can keep using Sommet.")
        }
    }
}
