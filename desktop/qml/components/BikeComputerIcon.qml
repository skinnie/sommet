import QtQuick
import AmbitApp

// Picks the right head-unit silhouette for the connected device (André, 2026-09-04: "when
// hammerhead is connected we see the hammerhead icon, when any garmin edge is connected we see
// the edge 1040 icon"). `kind` is the backend device tag: "karoo", "c406" (Magene C406 Pro, over
// Bluetooth), "bryton" (Bryton Aero 60, over USB mass storage), or "edge" (default). Sized and
// coloured like Icon.qml (`size`/`color`), so call sites stay `BikeComputerIcon { size; kind }`.
Item {
    id: root
    property int size: 24
    property color color: Theme.text
    property string kind: "edge"

    implicitWidth: size
    implicitHeight: size

    KarooIcon {
        anchors.centerIn: parent
        size: root.size
        color: root.color
        visible: root.kind === "karoo"
    }
    MageneIcon {
        anchors.centerIn: parent
        size: root.size
        color: root.color
        visible: root.kind === "c406"
    }
    BrytonIcon {
        anchors.centerIn: parent
        size: root.size
        color: root.color
        visible: root.kind === "bryton" || root.kind === "brytonble"
    }
    EdgeIcon {
        anchors.centerIn: parent
        size: root.size
        color: root.color
        visible: root.kind !== "karoo" && root.kind !== "c406" && root.kind !== "bryton" && root.kind !== "brytonble"
    }
}
