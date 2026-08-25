import QtQuick
import QtQuick.Controls
import AmbitApp

// Real request 2026-08-09 ("can you just used rounded squares on sport modes buttons and
// settings buttons/sliders?"). Plain QtQuick.Controls Button renders with the platform
// Basic style's own minimal look - the same gap RoundedComboBox.qml already closed for
// dropdowns. A drop-in replacement for `Button { ... }` - extends Button directly, so every
// existing property/signal (text/enabled/checkable/checked/onClicked) keeps working
// unchanged.
Button {
    id: root

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
        color: root.checked ? Theme.primary
            : ((root.pressed || root.hovered) ? Theme.borderStrong : Theme.cardNested)
        border.width: root.checked ? 0 : 1
        border.color: Theme.border
        Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
    }

    contentItem: Text {
        text: root.text
        color: root.checked ? Theme.card : (root.enabled ? Theme.text : Theme.mutedText)
        font.pixelSize: Theme.fontSizeBody
        horizontalAlignment: Text.AlignHCenter
        verticalAlignment: Text.AlignVCenter
        Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
    }

    opacity: root.enabled ? 1.0 : 0.5
    Behavior on opacity { NumberAnimation { duration: 120; easing.type: Easing.OutCubic } }
}
