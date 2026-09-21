import QtQuick
import AmbitApp

// One row in NavRail.qml - icon, label, and its own selected/hover state. Split out from
// NavRail itself so Sport Modes (hidden behind FeatureFlags.sportModes) is just one more
// instance of this, not a special case.
Rectangle {
    id: root

    signal clicked

    property string glyph
    property string label
    property bool selected: false
    // Real, 2026-08-08 (Intervals nav entry): set true to swap the Material Symbols glyph
    // for IntervalsIcon - the same "two icons, one visible" pattern HomePage.qml's own Ambit/
    // Garmin hero icon already uses, rather than a generic Component-swap mechanism for what
    // is, so far, exactly one non-glyph nav icon.
    property bool useIntervalsIcon: false
    // Optional custom nav icon: a QML component URL drawn instead of the Material Symbols
    // glyph. Generalises the useIntervalsIcon pattern above so an extension (AppExtensions)
    // can supply an inline-drawn icon without needing a codepoint in the subset font. If the
    // loaded item exposes a `color` property it's bound to the selected/normal colour.
    property url iconSource

    implicitHeight: 44
    radius: Theme.radiusSmall
    // Real bug, found 2026-08-10 (André: "when I pass the mouse on the left buttons there
    // is like a shadow that flickers... too fast the flicker but too slow to disappear so
    // sometimes when I pass between 2 buttons I have 2 shadows").
    //
    // The unhovered state used to be the literal "transparent", which in Qt is #00000000 -
    // BLACK at zero alpha. The Behavior below then interpolated Theme.card -> transparent
    // black, so every fade passed through semi-transparent black and the row visibly
    // darkened on its way in and out. That is the "shadow": quick going in (the pointer is
    // arriving), but the full 120 ms fade-out runs after the pointer has already left, so
    // it lingers - and moving between two rows runs one fade-out and one fade-in at once,
    // which is why there were two.
    //
    // Fading to the SAME colour at zero alpha keeps the whole interpolation on one hue, so
    // it just dissolves. Theme.card rather than Theme.background because that is what it
    // fades from; with matching r/g/b only the alpha actually changes.
    readonly property color _hoverClear: Qt.rgba(Theme.card.r, Theme.card.g, Theme.card.b, 0)
    color: selected ? Theme.primary : (hoverHandler.hovered ? Theme.card : _hoverClear)
    // AMBITAPP_SPEC.md's own Design Language lists "Subtle animations" as a real
    // requirement - real, 2026-08-09 ("general desktop polish pass"): before this, every
    // color in the app (hover, selection, theme changes) snapped instantly with zero
    // animation anywhere in the codebase, confirmed via a real grep for Behavior/
    // *Animation/Transition across every qml/ file (none existed).
    Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }

    HoverHandler { id: hoverHandler }
    TapHandler { onTapped: root.clicked() }

    Row {
        anchors.left: parent.left
        anchors.leftMargin: Theme.spacingMedium
        anchors.verticalCenter: parent.verticalCenter
        spacing: Theme.spacingSmall

        Icon {
            visible: !root.useIntervalsIcon && String(root.iconSource) === ""
            glyph: root.glyph
            size: 20
            color: root.selected ? Theme.card : Theme.text
            anchors.verticalCenter: parent.verticalCenter
        }
        IntervalsIcon {
            visible: root.useIntervalsIcon
            size: 20
            color: root.selected ? Theme.card : Theme.text
            anchors.verticalCenter: parent.verticalCenter
        }
        Loader {
            active: String(root.iconSource) !== ""
            visible: active
            source: root.iconSource
            anchors.verticalCenter: parent.verticalCenter
            onLoaded: if (item && item.color !== undefined)
                          item.color = Qt.binding(function() {
                              return root.selected ? Theme.card : Theme.text })
        }
        Text {
            text: root.label
            color: root.selected ? Theme.card : Theme.text
            font.pixelSize: Theme.fontSizeBodyLarge
            anchors.verticalCenter: parent.verticalCenter
            Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
        }
    }
}
