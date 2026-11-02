import QtQuick
import QtQuick.Controls
import AmbitApp

// "Search on a map" - real request, 2026-08-11 (André, for POIs and the Kailash home
// picker): "1st box 'search on a map', like we do for google maps, and once we select it
// inputs coordinates".
//
// Searches on Enter, not per keystroke. That is a deliberate constraint, not laziness:
// Nominatim is a free service whose usage policy allows one request per second, and
// type-ahead would blow through that on every word typed. The backend enforces the limit too
// (see server.py) - this just avoids provoking it.
Item {
    id: root

    property real latitude: 0
    property real longitude: 0

    // Emitted when a result is chosen, so the owning dialog can move its map and fill in its
    // own coordinate fields.
    signal placeChosen(real lat, real lon)

    implicitHeight: searchField.height + (resultsBox.visible ? resultsBox.height + 4 : 0)

    property var results: []
    property bool searching: false
    property string error: ""

    function search() {
        const text = searchField.text.trim()
        if (text.length === 0)
            return
        searching = true
        error = ""
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            root.searching = false
            try {
                const reply = JSON.parse(xhr.responseText)
                if (reply.ok) {
                    root.results = reply.results || []
                    // An empty list is a real answer ("no such place"), and saying so beats
                    // showing nothing and looking broken.
                    if (root.results.length === 0)
                        root.error = qsTr("Nothing found for that.")
                } else {
                    root.results = []
                    root.error = qsTr("Search is unavailable right now - you can still " +
                                       "pick a point on the map.")
                }
            } catch (e) {
                root.results = []
                root.error = qsTr("Search is unavailable right now - you can still pick a " +
                                   "point on the map.")
            }
        }
        xhr.open("GET", "http://127.0.0.1:8766/api/geocode?q=" + encodeURIComponent(text))
        xhr.send()
    }

    SearchField {
        id: searchField
        width: parent.width
        placeholderText: qsTr("Search for a place")
        onAccepted: root.search()
    }

    Rectangle {
        id: resultsBox
        anchors.top: searchField.bottom
        anchors.topMargin: 4
        width: parent.width
        height: Math.min(resultsColumn.height + 8, 190)
        visible: root.results.length > 0 || root.error.length > 0 || root.searching
        radius: 8
        color: Theme.card
        border.width: 1
        border.color: Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.3)

        Text {
            anchors.centerIn: parent
            visible: root.searching || (root.results.length === 0 && root.error.length > 0)
            width: parent.width - 16
            horizontalAlignment: Text.AlignHCenter
            wrapMode: Text.WordWrap
            text: root.searching ? qsTr("Searching...") : root.error
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
        }

        Flickable {
            anchors.fill: parent
            anchors.margins: 4
            visible: root.results.length > 0
            contentWidth: width
            contentHeight: resultsColumn.height
            clip: true

            Column {
                id: resultsColumn
                width: parent.width
                Repeater {
                    model: root.results
                    delegate: Item {
                        id: resultRow
                        required property var modelData
                        width: resultsColumn.width
                        height: 30

                        // Sibling background, not an opacity on the row itself: fading the
                        // container would fade the label with it. Same lesson as the
                        // activity rows' grey flash.
                        Rectangle {
                            anchors.fill: parent
                            radius: 5
                            color: Theme.primary
                            opacity: resultHover.hovered ? 0.12 : 0
                            Behavior on opacity { NumberAnimation { duration: 100 } }
                        }

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.leftMargin: 6
                            elide: Text.ElideRight
                            text: resultRow.modelData.name
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeCaption
                        }

                        HoverHandler { id: resultHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: {
                                root.placeChosen(resultRow.modelData.lat,
                                                 resultRow.modelData.lon)
                                root.results = []
                                root.error = ""
                            }
                        }
                    }
                }
            }
        }
    }
}
