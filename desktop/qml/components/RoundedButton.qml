import QtQuick
import QtQuick.Controls
import SommetUi

// Real request 2026-08-09 ("can you just used rounded squares on sport modes buttons and
// settings buttons/sliders?"). Plain QtQuick.Controls Button renders with the platform
// Basic style's own minimal look - the same gap RoundedComboBox.qml already closed for
// dropdowns. A drop-in replacement for `Button { ... }` - extends Button directly, so every
// existing property/signal (text/enabled/checkable/checked/onClicked) keeps working
// unchanged.
Button {
    id: root

    // Primary/call-to-action style: a filled Theme.primary button with light text, so the main
    // action on a screen (e.g. a wizard's Next / See my plan) clearly reads as clickable and isn't
    // mistaken for disabled - the default flat grey tile was too close to the dimmed disabled look
    // (André, 2026-09-20). Reuses the same fill the `checked` toggle state already uses.
    property bool accent: false
    // Working (André, 2026-11-02, desktop picks #5): the label stays, so the button keeps its
    // name, three dots pulse after it, and clicks and keys are ignored until it's done. Use it
    // instead of swapping the text to "Working…".
    property bool busy: false
    // Delete / Remove / Forget: the calm red of the Activities delete dialog (10% red fill, red
    // hairline and bold red text, 20% on hover).
    property bool danger: false
    readonly property bool _filled: (root.checked || root.accent) && !root.danger
    // A button that is disabled only because it's working keeps full strength.
    readonly property bool _live: root.enabled || root.busy
    readonly property color _textColor: root.danger ? Theme.error
        : root._filled ? Theme.card
        : (root._live ? Theme.text : Theme.mutedText)

    Accessible.description: root.busy ? qsTr("Working") : ""

    hoverEnabled: true
    implicitHeight: 36
    leftPadding: Theme.spacingMedium
    rightPadding: Theme.spacingMedium

    // Real, 2026-08-25 (André: "let's make that type of button default for all app. same
    // grey as fitness") - every RoundedButton now uses the flat, borderless tile look first
    // tried on Coach's suggestion chips (themselves matching the Fitness/Fatigue/Freshness
    // readiness tiles): Theme.cardNested fill, darkening to Theme.borderStrong on hover/press
    // for feedback. One primitive, so this ripples to every button in the app at once -
    // Cancel/Save/Retry/Export, the lot - instead of a per-page restyle.
    //
    // A thin Theme.border hairline stays (much quieter than the old Theme.mutedText outline
    // this replaces) - the Fitness tiles themselves never need one because they always sit on
    // a solid white/dark Card, but a button can land directly on Theme.surface too (e.g.
    // HomePage's "Retry"), and cardNested measures only ~12/765 units from surface in light
    // mode - close enough to nearly vanish with no edge at all. The hairline is the safety net
    // that keeps every button legible regardless of what it's sitting on, without bringing
    // back a visible "outlined button" look.
    background: Rectangle {
        implicitHeight: 36
        radius: Theme.radiusSmall
        color: root.danger
            ? Qt.alpha(Theme.error, (root.pressed || root.hovered) && root.enabled && !root.busy ? 0.20 : 0.10)
            : root._filled
            ? ((root.pressed || root.hovered) && root.enabled && !root.busy ? Qt.darker(Theme.primary, 1.15) : Theme.primary)
            : ((root.pressed || root.hovered) && !root.busy ? Theme.borderStrong : Theme.cardNested)
        border.width: root._filled ? 0 : 1
        border.color: root.danger ? Theme.error : Theme.border
        Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
    }

    contentItem: Item {
        implicitWidth: label.implicitWidth + (root.busy ? dots.width + Theme.spacingSmall : 0)
        implicitHeight: label.implicitHeight

        Row {
            anchors.centerIn: parent
            spacing: Theme.spacingSmall

            Text {
                id: label
                anchors.verticalCenter: parent.verticalCenter
                text: root.text
                color: root._textColor
                font.pixelSize: Theme.fontSizeBody
                font.bold: root.danger
                Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
            }
            PulseDots {
                id: dots
                anchors.verticalCenter: parent.verticalCenter
                visible: root.busy
                color: root._textColor
                dotSize: 5
            }
        }
    }

    // While busy: swallow clicks (and show the busy cursor) and the keys that would press it.
    MouseArea {
        anchors.fill: parent
        visible: root.busy
        acceptedButtons: Qt.AllButtons
        hoverEnabled: true
        cursorShape: Qt.BusyCursor
    }
    Keys.onPressed: (event) => {
        if (root.busy && (event.key === Qt.Key_Space || event.key === Qt.Key_Return
                          || event.key === Qt.Key_Enter))
            event.accepted = true
    }
    Keys.onReleased: (event) => {
        if (root.busy && (event.key === Qt.Key_Space || event.key === Qt.Key_Return
                          || event.key === Qt.Key_Enter))
            event.accepted = true
    }

    opacity: root._live ? 1.0 : 0.5
    Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
}
