import QtQuick
import SommetUi

// One glyph from Icons.qml, rendered at a given size/color. `Icon { glyph: Icons.home }`
// everywhere an icon is needed - docs/reference/sommet-spec.md's "Material Symbols Rounded, no emoji, no
// platform-specific icons" rule lives here and nowhere else.
Text {
    id: root
    property alias glyph: root.text
    property int size: 24

    font.family: Icons.fontFamily
    font.pixelSize: size
    color: Theme.text
    horizontalAlignment: Text.AlignHCenter
    verticalAlignment: Text.AlignVCenter
    // Real, 2026-08-09 ("general desktop polish pass") - every icon in the app uses this
    // component, so this one Behavior covers every icon's selected/hover/theme color change
    // at once (NavItem's own selected-row icon, POI markers, etc.) rather than needing the
    // same fix repeated per call site.
    Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
}
