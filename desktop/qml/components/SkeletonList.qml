pragma ComponentBehavior: Bound
import QtQuick
import AmbitApp

// Grey rows shaped like the list to come, while a long read runs (André, 2026-11-02, desktop
// picks #13). One animation for the whole block, kept light for the X230: the rows pulse
// between full and half strength every 1.2 s. Show it only while the list is empty and loading.
Column {
    id: root

    property int count: 6
    property int rowHeight: 56

    width: parent ? parent.width : 0
    spacing: Theme.spacingSmall

    SequentialAnimation on opacity {
        running: root.visible
        loops: Animation.Infinite
        NumberAnimation { to: 0.5; duration: 600; easing.type: Easing.InOutQuad }
        NumberAnimation { to: 1.0; duration: 600; easing.type: Easing.InOutQuad }
    }

    Repeater {
        model: root.count
        delegate: Rectangle {
            width: root.width
            height: root.rowHeight
            radius: Theme.radiusSmall
            color: Theme.card
            border.width: 1
            border.color: Theme.border

            Rectangle {
                x: 12
                anchors.verticalCenter: parent.verticalCenter
                width: 34
                height: 34
                radius: Theme.radiusSmall
                color: Theme.cardNested
            }
            Column {
                x: 58
                anchors.verticalCenter: parent.verticalCenter
                spacing: 6
                Rectangle { width: Math.max(40, (root.width - 80) * 0.55); height: 9; radius: 4.5; color: Theme.cardNested }
                Rectangle { width: Math.max(30, (root.width - 80) * 0.3); height: 9; radius: 4.5; color: Theme.cardNested }
            }
        }
    }

    Accessible.role: Accessible.StaticText
    Accessible.name: qsTr("Loading")
}
