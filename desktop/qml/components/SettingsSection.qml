import QtQuick
import AmbitApp

// One closed row in Settings (André, 2026-10-21: the Settings split, "progressive disclosure").
// Shows the section's title and its current value; a click, Enter or Space opens the full
// settings in place, under the row. Rows sit in one Card on SettingsPage (padding 4), so the
// header's hover fill and focus ring stay inside the card's rounded corners.
//
// The page owns which rows are open (it remembers them between visits), so `expanded` is a
// plain binding from the page and the row only asks to change it, through toggleRequested().
//
// Keyboard and screen readers: the header takes Tab focus, shows a ring while focused, and
// reports itself as an expandable button with its open/closed state.
Column {
    id: root

    property string title
    property string summary
    property string glyph: ""
    property bool expanded: false
    // Hairline under the row. Off on the last row of the card.
    property bool showDivider: true

    signal toggleRequested()

    // Children declared inside `SettingsSection { ... }` land in the body, under the header.
    default property alias content: body.data

    width: parent ? parent.width : 0

    Rectangle {
        id: header
        width: parent.width
        height: Math.max(56, headerText.implicitHeight + Theme.spacingSmall * 2)
        radius: Theme.radiusSmall
        color: hover.hovered ? Theme.cardNested : "transparent"
        border.width: header.activeFocus ? 2 : 0
        border.color: Theme.primary
        Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }

        activeFocusOnTab: true
        // The page does not scroll by itself when Tab lands on something off screen, so the
        // row brings itself into view (walks up to the nearest Flickable, i.e. the page).
        onActiveFocusChanged: {
            if (!activeFocus)
                return
            let f = root.parent
            while (f && f.contentY === undefined)
                f = f.parent
            if (!f || !f.contentItem)
                return
            const top = header.mapToItem(f.contentItem, 0, 0).y
            const margin = Theme.spacingLarge
            const maxY = Math.max(0, f.contentHeight - f.height)
            if (top - margin < f.contentY)
                f.contentY = Math.max(0, top - margin)
            else if (top + header.height + margin > f.contentY + f.height)
                f.contentY = Math.min(maxY, top + header.height + margin - f.height)
        }
        Keys.onPressed: (event) => {
            if (event.key === Qt.Key_Return || event.key === Qt.Key_Enter
                    || event.key === Qt.Key_Space) {
                root.toggleRequested()
                event.accepted = true
            }
        }

        Accessible.role: Accessible.Button
        Accessible.name: root.title
        Accessible.description: root.summary
        Accessible.expandable: true
        // Note: no `Accessible.expanded` - that attached property does not exist on this Qt
        // (QQuickAccessibleAttached), and assigning it makes the whole SettingsSection component
        // fail to load at runtime, which renders the entire Settings page blank. qmlcachegen does
        // not catch it (attached-property resolution is deferred to runtime, and CI never opens the
        // GUI), so it shipped silently in 0.2.60/0.2.61. `expandable` above is enough to mark the
        // row as an expandable control; the open/closed state is conveyed visually by the chevron.
        Accessible.onPressAction: root.toggleRequested()

        HoverHandler { id: hover; cursorShape: Qt.PointingHandCursor }
        TapHandler { onTapped: root.toggleRequested() }

        Icon {
            id: headerIcon
            visible: root.glyph.length > 0
            glyph: root.glyph
            size: 20
            color: Theme.text
            anchors.left: parent.left
            anchors.leftMargin: 12
            anchors.verticalCenter: parent.verticalCenter
        }
        Column {
            id: headerText
            anchors.left: headerIcon.visible ? headerIcon.right : parent.left
            anchors.leftMargin: headerIcon.visible ? Theme.spacingSmall : 12
            anchors.right: chevron.left
            anchors.rightMargin: Theme.spacingSmall
            anchors.verticalCenter: parent.verticalCenter
            spacing: 2
            Text {
                width: parent.width
                text: root.title
                font.bold: true
                font.pixelSize: Theme.fontSizeBodyLarge
                color: Theme.text
                elide: Text.ElideRight
            }
            Text {
                width: parent.width
                visible: text.length > 0
                text: root.summary
                font.pixelSize: Theme.fontSizeLabel
                color: Theme.mutedText
                wrapMode: Text.WordWrap
            }
        }
        Icon {
            id: chevron
            glyph: Icons.chevronRight
            size: 20
            color: Theme.mutedText
            anchors.right: parent.right
            anchors.rightMargin: 12
            anchors.verticalCenter: parent.verticalCenter
            rotation: root.expanded ? 90 : 0
            Behavior on rotation { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
        }
    }

    // The section's own settings, shown only while open. Same 12 px inset as the header text,
    // and the same row spacing the old cards used inside.
    Item {
        width: parent.width
        visible: root.expanded
        height: visible ? body.height + Theme.spacingMedium : 0
        Column {
            id: body
            x: 12
            y: Theme.spacingSmall / 2
            width: parent.width - 24
            spacing: Theme.spacingSmall
        }
    }

    Rectangle {
        visible: root.showDivider
        x: 12
        width: parent.width - 24
        height: 1
        color: Theme.border
    }
}
