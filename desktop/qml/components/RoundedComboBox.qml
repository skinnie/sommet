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

    // Optional: the item the popup must stay inside (2026-08-24, André: the Minutes popup ran
    // out of the "Plan a workout" card even though the window had room below - "it has no reason
    // to"). When set, the flip/height decision is made against THIS item's bounds instead of the
    // whole window, so a box near the bottom of a dialog opens upward to stay in the card. Leave
    // null for a plain page combo, which is then bounded by the window as before.
    property Item boundsItem: null

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
            // textAt() is the ComboBox's own display-text lookup: it honours textRole and works
            // for both string arrays and object arrays. The hand-rolled modelData[textRole]
            // version rendered blank items for object models (Gear's default-gear pickers).
            text: control.textAt(index)
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
        // Computed fresh each time it opens (onAboutToShow) rather than via bindings: mapToItem
        // in a binding isn't reactive and evaluates before layout settles, so the flip decision
        // came out wrong (the popup stayed downward even with no room). Read live geometry here.
        property bool openUp: false
        property real popH: 84
        onAboutToShow: {
            var itemH = 34, gap = 4, margin = 8;
            // Size to the whole list (no fixed cap) so short lists show every item instead of
            // scrolling (André); room (below) still caps it so it never leaves the window.
            var wanted = control.count * itemH + 8;
            var ctlTop = control.mapToItem(null, 0, 0).y;
            var winH = control.Window ? control.Window.height : Screen.height;
            // The window is ALWAYS the outer bound (the rule: nothing may pass beyond the
            // window). A boundsItem (e.g. a dialog card) only ever tightens it further, never
            // loosens it - so take the stricter of the two on each side.
            var bTop = Math.max(margin,
                                control.boundsItem ? control.boundsItem.mapToItem(null, 0, 0).y : 0);
            var bBottom = Math.min(winH - margin,
                                   control.boundsItem
                                   ? control.boundsItem.mapToItem(null, 0, 0).y + control.boundsItem.height
                                   : winH);
            var roomBelow = bBottom - (ctlTop + control.height) - gap;
            var roomAbove = (ctlTop - bTop) - gap;
            openUp = (roomBelow < wanted) && (roomAbove > roomBelow);
            var room = Math.max(openUp ? roomAbove : roomBelow, 0);
            popH = Math.max(itemH + 8, Math.min(wanted, room));
        }

        width: control.width
        padding: 4
        height: popH
        y: openUp ? -(popH + 4) : (control.height + 4)

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
