import QtQuick
import QtQuick.Controls
import AmbitApp

// How to get water / food / cemetery stops onto a route: use PitStopper (pitstopper.net) and load ITS
// export instead of the original GPX. One card, shown on both the Route and Race Plan pages so they
// always give the same instructions (André, 2026-09-21: "just provide clear instructions on both").
// Hidden by the caller once the route already carries POIs.
//
// Why loading the export works: PitStopper's exported GPX holds the identical route (same points,
// distance, elevation) plus the places as waypoints, and PlanStore reads those waypoints itself.
Rectangle {
    id: root
    width: parent ? parent.width : 400
    implicitHeight: col.implicitHeight + Theme.spacingMedium * 2
    radius: Theme.radiusSmall
    color: Theme.cardNested
    border.color: Theme.border
    border.width: 1

    Column {
        id: col
        anchors.fill: parent
        anchors.margins: Theme.spacingMedium
        spacing: Theme.spacingSmall

        Text {
            text: qsTr("Add water, food & cemetery stops (optional)")
            color: Theme.text
            font.pixelSize: Theme.fontSizeLabel
            font.weight: Font.Medium
            width: parent.width
            wrapMode: Text.WordWrap
        }
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
            text: qsTr("1.  Open pitstopper.net and upload your route (GPX).\n" +
                       "2.  Tick what you want to find — e.g. Water, Food & Drink, and a custom tag for " +
                       "cemeteries — then Search.\n" +
                       "3.  Export → Export a file → GPX (not FIT: FIT cuts the names).\n" +
                       "4.  Load that file here instead of your original — it is the same route plus the places.")
        }
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
            font.italic: true
            text: qsTr("If you change the route afterwards (cut it, extend it), export again from PitStopper.")
        }
        RoundedButton {
            text: qsTr("Open pitstopper.net")
            onClicked: Qt.openUrlExternally("https://pitstopper.net")
        }
    }
}
