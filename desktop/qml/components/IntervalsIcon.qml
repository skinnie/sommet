import QtQuick
import SommetUi

// Four bars alternating low/high heights - a plain interval-training profile (rest/effort/
// rest/effort), the same motif as tools/packaging/icon.png (this project's own workout-
// builder app icon, "four simple bars in an alternating low/high pattern" per that icon's
// own make_icon.py comment). Redrawn here as a single monochrome QML shape rather than
// reusing that raster PNG directly - real request 2026-08-08 ("use icon.png but make it
// match the colors of our app, if not do something similar"): every other icon in this app
// (Icon.qml's font glyphs, EtrexIcon.qml) is a single `color`-driven shape so NavItem's
// selected-state color inversion (dark text -> Theme.card on a solid Theme.primary
// background) works the same way for every nav entry - icon.png's own two-tone teal/white
// design would look broken sitting on that solid selected background. Theme.primary/accent
// were already carried over from this same icon's colors when the app's palette was chosen
// (see Theme.qml's own header comment) - so the visual identity still matches, just via the
// shared color token rather than the raw PNG. Sized/colored the same way Icon.qml is
// (`size`/`color`), so it drops into the exact same call sites (`IntervalsIcon { size: 20 }`).
Item {
    id: root
    property int size: 24
    property color color: Theme.text

    implicitWidth: size
    implicitHeight: size

    readonly property var _heightFactors: [0.38, 0.85, 0.38, 0.85]
    readonly property real _barWidth: size * 0.16
    readonly property real _barSpacing: size * 0.1
    readonly property real _totalWidth: _barWidth * 4 + _barSpacing * 3

    Repeater {
        model: root._heightFactors
        delegate: Rectangle {
            width: root._barWidth
            height: root.size * modelData
            radius: width * 0.4
            x: (root.width - root._totalWidth) / 2 + index * (root._barWidth + root._barSpacing)
            y: root.height - height
            color: root.color
        }
    }
}
