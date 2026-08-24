import QtQuick
import QtQuick.Controls
import QtQuick.Window
import AmbitApp

// A ComboBox with the app's rounded-corner look (André: "no square boxes! square with rounded
// corners"). The default QQC2 ComboBox draws a square, platform-styled control AND a square
// drop-down popup; this rounds both and themes the text.
//
// 2026-08-24 (André, on the Calendar planner's Minutes/Hours unit): the popup was still square
// ("and squared") and the closed box was a hair shorter than a RoundedTextField next to it
// ("the dropdown menu is bigger"). Fixed here in the one shared component: height now matches
// RoundedTextField (36), and the popup + its item delegate are rounded and themed. An earlier
// note here said a custom delegate "rendered blank items" - the cause was a bare Text delegate
// with no width; the ItemDelegate below sizes to the popup and paints its own text, so it
// renders. Everything else (model, textRole, currentIndex, onActivated…) is standard ComboBox.
ComboBox {
    id: control
    implicitHeight: 36
    font.pixelSize: Theme.fontSizeBody

    background: Rectangle {
        radius: Theme.radiusSmall
        color: Theme.card
        border.width: 1
        border.color: control.activeFocus ? Theme.primary : Theme.mutedText
    }

    contentItem: Text {
        leftPadding: 10
        rightPadding: control.indicator ? control.indicator.width + 6 : 10
        text: control.displayText
        color: Theme.text
        font: control.font
        verticalAlignment: Text.AlignVCenter
        elide: Text.ElideRight
    }

    delegate: ItemDelegate {
        width: ListView.view ? ListView.view.width : control.width
        height: 34
        highlighted: control.highlightedIndex === index
        contentItem: Text {
            text: control.textRole
                  ? (Array.isArray(control.model)
                        ? modelData[control.textRole]
                        : model[control.textRole])
                  : modelData
            color: highlighted ? Theme.card : Theme.text
            font: control.font
            verticalAlignment: Text.AlignVCenter
            leftPadding: 10
            elide: Text.ElideRight
        }
        background: Rectangle {
            radius: Theme.radiusSmall
            color: highlighted ? Theme.primary : "transparent"
        }
    }

    // Adaptive popup, 2026-08-24 (André: "still gets out of screen ... make it adaptable to fit
    // inside"). It sizes to its items but never taller than the room actually available, and
    // flips above the box when there isn't enough room below - so a dropdown near the bottom of
    // a dialog/screen opens upward instead of spilling off. Anything that still doesn't fit
    // scrolls inside.
    popup: Popup {
        id: pop
        readonly property real gap: 4
        readonly property real margin: 8            // keep a little breathing room from the edge
        readonly property real itemH: 34
        readonly property real wanted: Math.min(control.count * itemH + 8, 240)
        // Control's top edge in window coordinates, and the space above/below it in the window.
        readonly property real ctlTop: control.mapToItem(null, 0, 0).y
        readonly property real winH: control.Window ? control.Window.height : Screen.height
        readonly property real roomBelow: winH - (ctlTop + control.height) - gap - margin
        readonly property real roomAbove: ctlTop - gap - margin
        readonly property bool openUp: (roomBelow < wanted) && (roomAbove > roomBelow)
        readonly property real room: openUp ? roomAbove : roomBelow

        width: control.width
        padding: 4
        height: Math.max(itemH + 8, Math.min(wanted, room))
        y: openUp ? -(height + gap) : (control.height + gap)

        contentItem: ListView {
            clip: true
            implicitHeight: contentHeight
            model: control.popup.visible ? control.delegateModel : null
            currentIndex: control.highlightedIndex
            boundsBehavior: Flickable.StopAtBounds
            ScrollIndicator.vertical: ScrollIndicator {}
        }

        background: Rectangle {
            radius: Theme.radiusSmall
            color: Theme.card
            border.width: 1
            border.color: Theme.mutedText
        }
    }
}
