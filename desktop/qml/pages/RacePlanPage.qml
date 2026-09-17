import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AmbitApp

// Race planner foundation (2026-09-17): BRM/ultra planning. Foundation phase shows
// a simple placeholder — the backend API (/api/race/plan/create) is fully working and
// tested. This UI will evolve to a full form in follow-up phases. For now, it demonstrates
// that the page is navigable and the infrastructure is in place.
Item {
    id: root

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingMedium
        spacing: Theme.spacingMedium

        Column {
            Layout.fillWidth: true
            spacing: Theme.spacingSmall

            Text {
                text: qsTr("Race Planner")
                color: Theme.text
                font.pixelSize: Theme.fontSizeTitle
                font.weight: Font.Bold
            }

            Text {
                text: qsTr("BRM/Ultra-distance race planning")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeBody
                wrapMode: Text.WordWrap
            }
        }

        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 200
            color: Theme.card
            radius: Theme.radiusCard
            border.color: Theme.border
            border.width: 1

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme.spacingMedium
                spacing: Theme.spacingMedium

                Text {
                    text: qsTr("Foundation Phase")
                    color: Theme.text
                    font.weight: Font.Bold
                }

                Text {
                    text: qsTr("✓ Backend API fully functional\n✓ Data models (Event, Plan, Athlete, Bike)\n✓ Baseline ETA calculation (naive distance/speed)\n✓ GPX parsing and distance extraction\n✓ SQLite persistence layer ready\n\nUI form coming in next phase.")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Text {
                    text: qsTr("Test via API: POST /api/race/plan/create with {event, athlete, bike}")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                    Layout.fillWidth: true
                }

                Item { Layout.fillHeight: true }
            }
        }

        Item { Layout.fillHeight: true }
    }
}
