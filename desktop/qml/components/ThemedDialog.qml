import QtQuick
import QtQuick.Controls
import AmbitApp

// Every dialog in this app, themed - real bug, 2026-08-11 (André, on the row picker): "the
// screen that appears has white background and grey letters not being visible, and not
// matching the theme (I am on dark theme, the background should be dark)."
//
// The cause is that a plain QQC2 Dialog draws its background, header and footer from the
// platform Basic style, which is light regardless of what the app is doing - the same class
// of problem the Rounded* components were made for. Fixed in one place rather than per
// dialog so a new dialog cannot reintroduce it.
//
// The `palette` assignments matter as much as the explicit background: the standard buttons
// (Ok/Cancel/Close) are drawn by the style, not by us, and they read their colours from the
// palette. Without these they stayed light-on-light and effectively invisible - exactly the
// symptom André described, one level down from the background itself.
Dialog {
    id: root

    modal: true
    // RULE (André, 2026-08-25): every dialog opens centred in the window over a dimmed
    // backdrop. Centring lives here in the base so no dialog can forget it - `Overlay.overlay`
    // is the app-wide overlay layer, so this is the middle of the whole window regardless of
    // which page opened the dialog. The dim scrim is the `Overlay.modal` at the bottom of this
    // file. Any new dialog MUST build on ThemedDialog (never a bare Dialog/Popup) to inherit both.
    anchors.centerIn: Overlay.overlay

    palette.window: Theme.card
    palette.windowText: Theme.text
    palette.base: Theme.card
    palette.text: Theme.text
    palette.button: Theme.card
    palette.buttonText: Theme.primary
    palette.highlight: Theme.primary
    palette.highlightedText: Theme.card
    palette.mid: Theme.mutedText
    palette.dark: Theme.mutedText

    background: Rectangle {
        color: Theme.card
        radius: Theme.radiusCard
        border.width: 1
        border.color: Theme.mutedText
    }

    // Padding is set here rather than left to the style so the gaps above and below the
    // content match - André, 2026-08-11: the title sat a long way from the text under it
    // while the buttons were almost touching the bottom edge. One value, used on both.
    padding: Theme.spacingMedium

    header: Text {
        text: root.title
        visible: root.title.length > 0
        color: Theme.text
        font.bold: true
        font.pixelSize: Theme.fontSizeBodyLarge
        elide: Text.ElideRight
        leftPadding: Theme.spacingMedium
        rightPadding: Theme.spacingMedium
        topPadding: Theme.spacingMedium
        bottomPadding: Theme.spacingSmall
    }

    // The button row needs its background removed, not just recoloured - real bug, 2026-08-11
    // (André: "on item 5 the bottom borders are not rendering ok"). A DialogButtonBox paints
    // its own opaque, SQUARE-cornered background, and it sits at the very bottom of the
    // dialog - so it covered the rounded background's two bottom corners and the border ran
    // out into a straight edge. Exactly the same shape of fault as the combo-box popup
    // highlight earlier today: a child painting past a rounded parent.
    //
    // Making it transparent lets the dialog's own rounded background show through, so the
    // border closes properly on all four corners. `standardButtons` is forwarded because
    // replacing the footer replaces the box Dialog would have built from it - Dialog still
    // wires accepted/rejected from whatever DialogButtonBox is here.
    // The buttons are ours, not the style's - André, 2026-08-11: "cancel button is square
    // shaped. audit all the desktop app and change all square shaped buttons to rounded
    // corners one as per our theme. make it a rule." A DialogButtonBox draws platform
    // buttons, which are square whatever the app looks like, so forwarding standardButtons
    // to it could never produce a rounded one. Building the row from RoundedButton instead
    // is the only way the dialogs match everything else, and it also drops the opaque
    // square-cornered background that was cutting the dialog's own bottom corners.
    footer: Item {
        visible: root.standardButtons !== 0
        implicitHeight: footerRow.implicitHeight + Theme.spacingMedium

        Row {
            id: footerRow
            anchors.right: parent.right
            anchors.top: parent.top
            anchors.rightMargin: Theme.spacingMedium
            spacing: Theme.spacingSmall

            RoundedButton {
                visible: (root.standardButtons & Dialog.Cancel) !== 0
                text: qsTr("Cancel")
                onClicked: root.reject()
            }
            RoundedButton {
                visible: (root.standardButtons & Dialog.Close) !== 0
                text: qsTr("Close")
                onClicked: root.reject()
            }
            RoundedButton {
                visible: (root.standardButtons & Dialog.Ok) !== 0
                text: qsTr("OK")
                onClicked: root.accept()
            }
        }
    }

    // Dim the app behind a dialog so the dialog reads as the thing in front - André asked for
    // a blur. A real blur means snapshotting the whole window into a texture and running it
    // through an effect every time a dialog opens; on this project's target hardware (a 2012
    // X230, see PROJECT_RULES rule 5 and 12) that is a real cost for a decoration. A dim
    // scrim gets the same separation for the price of one rectangle, which is what desktop
    // apps generally do. Say the word if you want the blur anyway and I will do it as a
    // one-shot snapshot rather than a live effect.
    Overlay.modal: Rectangle {
        color: Qt.rgba(0, 0, 0, 0.55)
    }
}
