pragma ComponentBehavior: Bound
import QtQuick
import SommetUi

// One switch for two or three choices (André, 2026-11-02, desktop picks #14): a track, and the
// chosen part as a card with bold text. Click, or Tab to it and use the arrow keys. It doesn't
// change currentIndex itself: bind currentIndex to your state and set the state in onActivated,
// like the radio buttons it replaces.
//
//   SegmentedControl {
//       model: [qsTr("Light"), qsTr("Dark"), qsTr("System")]
//       currentIndex: ["light", "dark", "system"].indexOf(Theme.override)
//       onActivated: (i) => Theme.override = ["light", "dark", "system"][i]
//   }
Rectangle {
    id: root

    property var model: []
    property int currentIndex: 0
    signal activated(int index)

    function _go(step) {
        const n = root.model.length
        if (n === 0)
            return
        const next = ((root.currentIndex < 0 ? 0 : root.currentIndex) + step + n) % n
        root.activated(next)
    }

    implicitWidth: row.implicitWidth + 6
    implicitHeight: 36
    radius: Theme.radiusSmall + 2
    color: Theme.cardNested
    border.width: root.activeFocus ? 2 : 1
    border.color: root.activeFocus ? Theme.primary : Theme.border

    activeFocusOnTab: true
    Keys.onLeftPressed: _go(-1)
    Keys.onUpPressed: _go(-1)
    Keys.onRightPressed: _go(1)
    Keys.onDownPressed: _go(1)

    Row {
        id: row
        x: 3
        y: 3
        height: root.height - 6
        spacing: 2

        Repeater {
            model: root.model
            delegate: Rectangle {
                id: seg
                required property int index
                required property var modelData
                readonly property bool chosen: index === root.currentIndex

                height: row.height
                width: boldMetric.implicitWidth + 28
                radius: Theme.radiusSmall
                color: seg.chosen ? Theme.card : (segHover.hovered ? Qt.alpha(Theme.card, 0.5) : "transparent")
                border.width: seg.chosen ? 1 : 0
                border.color: Theme.border
                Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }

                // Sized for the bold label, so the switch doesn't change width with the choice.
                Text {
                    id: boldMetric
                    visible: false
                    text: seg.modelData
                    font.pixelSize: Theme.fontSizeBody
                    font.bold: true
                }
                Text {
                    anchors.centerIn: parent
                    text: seg.modelData
                    color: seg.chosen ? Theme.text : Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                    font.bold: seg.chosen
                }

                HoverHandler { id: segHover; cursorShape: Qt.PointingHandCursor }
                TapHandler {
                    onTapped: {
                        if (!seg.chosen)
                            root.activated(seg.index)
                    }
                }

                Accessible.role: Accessible.RadioButton
                Accessible.name: seg.modelData
                Accessible.checkable: true
                Accessible.checked: seg.chosen
                Accessible.onPressAction: root.activated(seg.index)
            }
        }
    }
}
