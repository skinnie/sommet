import QtQuick
import QtQuick.Controls
import AmbitApp

// Offline route planner (André, 2026-08-28: "fully offline router... nice for biking and
// trekking", then "do it all"). Tap points on the map, pick a sport profile, and the local
// BRouter server plans the route with no internet; it comes back painted by climb gradient
// (the OruxMaps feature he called out) with an elevation profile and legend, and can be sent
// straight to the watch through the same GPX route-write /api/routes already does.
//
// All the work is in the Python backend (backend/server.py's /api/router/* and /api/poi/*,
// see docs/offline-routing.md) - this page is the map canvas and the controls over it, the
// same split as every other page. The map itself is the shared MapView (its coloredSegments
// overlay was added for exactly this), reusing the tap-to-pick + drag-to-pan idiom PoisPage
// and the Kailash home picker already use.
Item {
    id: root

    // --- planning state ---------------------------------------------------------------
    property var waypoints: []          // [{lat, lon}] in tap order: first = start, last = end
    property var coloredSegments: []    // [{color, coords:[[lat,lon],...]}] from the backend
    property var legendRows: []         // [{key,label,color,distance_m,ascent_m}]
    property var profileRows: []        // [{dist_m,ele_m,grad_pct,color}]
    property var summary: ({})          // {distance_m,ascent_m,descent_m,max_gradient_pct,...}
    property string plannedGpx: ""
    property string profileName: "trekking"
    property string statusMsg: ""
    property bool busy: false
    property string routerState: "unknown"   // "up" | "down" | "unknown"

    // POI search (optional - needs a prebuilt DB, see docs/offline-routing.md)
    property string poiDb: ""
    property string poiQuery: ""
    property var poiResults: []

    readonly property var profiles: ["trekking", "fastbike", "gravel", "mtb", "hiking-mountain"]
    readonly property string backend: "http://127.0.0.1:8766"

    Component.onCompleted: checkRouter()

    // --- backend calls (same XMLHttpRequest idiom as Main.qml / RoutesPage) ------------
    function api(method, path, body, cb) {
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            var res = null
            try { res = JSON.parse(xhr.responseText) } catch (e) { res = null }
            cb(xhr.status, res)
        }
        xhr.open(method, root.backend + path)
        if (body) xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body ? JSON.stringify(body) : undefined)
    }

    function checkRouter() {
        api("GET", "/api/router/health", null, function(status, res) {
            root.routerState = (res && res.reachable) ? "up" : "down"
        })
    }

    function planRoute() {
        if (waypoints.length < 2) {
            statusMsg = qsTr("Tap at least two points on the map first")
            return
        }
        busy = true
        statusMsg = qsTr("Planning…")
        var via = waypoints.map(function(w) { return [w.lon, w.lat] })
        api("POST", "/api/router/route",
            { via: via, profile: profileName, color: true, gpx: true },
            function(status, res) {
                busy = false
                if (!res || !res.ok) {
                    coloredSegments = []; legendRows = []; profileRows = []; summary = ({})
                    plannedGpx = ""
                    statusMsg = (res && (res.error || res.hint))
                                ? ((res.error || "") + (res.hint ? " — " + res.hint : ""))
                                : qsTr("Routing failed")
                    return
                }
                var col = res.colored || ({})
                coloredSegments = col.segments || []
                legendRows = col.legend || []
                profileRows = col.profile || []
                summary = col.summary || res.summary || ({})
                plannedGpx = res.gpx || ""
                statusMsg = ""
            })
    }

    function clearAll() {
        waypoints = []; coloredSegments = []; legendRows = []; profileRows = []
        summary = ({}); plannedGpx = ""; statusMsg = ""
    }

    function undoWaypoint() {
        if (waypoints.length > 0)
            waypoints = waypoints.slice(0, waypoints.length - 1)
    }

    function doSend() {
        if (!plannedGpx) return
        busy = true
        statusMsg = qsTr("Sending to watch…")
        api("POST", "/api/routes",
            { name: "Sommet plan (" + profileName + ")", gpx: plannedGpx, confirm: true },
            function(status, res) {
                busy = false
                statusMsg = (res && res.ok)
                    ? qsTr("Sent to watch — %1 existing route(s) kept").arg(res.routes_kept || 0)
                    : (res && res.stderr ? res.stderr.trim()
                                         : (res && res.error ? res.error : qsTr("Send failed")))
            })
    }

    function searchPoi() {
        if (!poiDb || !poiQuery) return
        api("POST", "/api/poi/search", { db: poiDb, name: poiQuery, limit: 15 },
            function(status, res) {
                poiResults = (res && res.results) ? res.results : []
                if (res && res.ok === false)
                    statusMsg = res.error || qsTr("POI search failed")
            })
    }

    function fmtKm(m) { return m === undefined || m === null ? "–" : (m / 1000).toFixed(1) + " km" }
    function fmtM(m)  { return m === undefined || m === null ? "–" : Math.round(m) + " m" }

    // --- layout: map on the left, controls + results on the right ----------------------
    Row {
        anchors.fill: parent

        // The interactive planning canvas
        Item {
            id: mapHolder
            width: parent.width - panel.width
            height: parent.height

            MapView {
                id: map
                anchors.fill: parent
                clip: true
                scrollZoom: true
                showZoomControls: true
                zoomLevel: 12
                markers: root.waypoints
                coloredSegments: root.coloredSegments

                // Tap = drop the next waypoint (start, then vias, then end). Same inverse
                // projection the tiles are drawn with.
                TapHandler {
                    onTapped: (event) => {
                        root.waypoints = root.waypoints.concat(
                            [{ lat: map.latAtY(event.position.y),
                               lon: map.lonAtX(event.position.x) }])
                    }
                }
                // Drag = pan (pan-only, unlike PoisPage's drag-to-pick: here a tap already
                // places the point, so a drag must never move it).
                DragHandler {
                    id: panner
                    target: null
                    property real lastX: 0
                    property real lastY: 0
                    onActiveChanged: {
                        if (active) { lastX = centroid.position.x; lastY = centroid.position.y }
                        map.userControlled = true
                    }
                    onCentroidChanged: {
                        if (!active) return
                        map.panX -= centroid.position.x - lastX
                        map.panY -= centroid.position.y - lastY
                        lastX = centroid.position.x
                        lastY = centroid.position.y
                    }
                }
                HoverHandler {
                    cursorShape: panner.active ? Qt.ClosedHandCursor : Qt.CrossCursor
                }
            }

            // On-map hint / status, bottom-left (attribution owns bottom-right)
            Rectangle {
                anchors.left: parent.left
                anchors.bottom: parent.bottom
                anchors.margins: Theme.spacingSmall
                visible: hintText.text.length > 0
                width: hintText.implicitWidth + Theme.spacingMedium
                height: hintText.implicitHeight + Theme.spacingSmall
                radius: Theme.radiusSmall
                color: "#CC000000"
                Text {
                    id: hintText
                    anchors.centerIn: parent
                    color: "white"
                    font.pixelSize: Theme.fontSizeCaption
                    text: root.busy ? root.statusMsg
                          : root.waypoints.length === 0
                            ? qsTr("Tap the map to set a start, then more points. Drag to pan, scroll to zoom.")
                            : root.statusMsg
                }
            }
        }

        // Right-hand control + results panel
        Rectangle {
            id: panel
            width: 340
            height: parent.height
            color: Theme.card
            border.color: Theme.border
            border.width: 1

            ScrollView {
                anchors.fill: parent
                anchors.margins: Theme.spacingMedium
                clip: true
                contentWidth: availableWidth

                Column {
                    width: panel.width - Theme.spacingMedium * 2
                    spacing: Theme.spacingMedium

                    // Title + offline-router status
                    Column {
                        width: parent.width
                        spacing: 2
                        Text {
                            text: qsTr("Plan route")
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeTitle
                            font.bold: true
                        }
                        Row {
                            spacing: Theme.spacingSmall / 2
                            Rectangle {
                                width: 8; height: 8; radius: 4
                                anchors.verticalCenter: parent.verticalCenter
                                color: root.routerState === "up" ? Theme.success
                                     : root.routerState === "down" ? Theme.error : Theme.mutedText
                            }
                            Text {
                                text: root.routerState === "up" ? qsTr("Offline router ready")
                                    : root.routerState === "down" ? qsTr("Offline router not running")
                                    : qsTr("Checking offline router…")
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Text {
                                text: qsTr("· retry")
                                color: Theme.primary
                                font.pixelSize: Theme.fontSizeCaption
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    onClicked: root.checkRouter()
                                }
                            }
                        }
                    }

                    // Sport profile
                    Column {
                        width: parent.width
                        spacing: Theme.spacingSmall / 2
                        Text {
                            text: qsTr("Sport profile")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeLabel
                        }
                        RoundedComboBox {
                            id: profileCombo
                            width: parent.width
                            model: root.profiles
                            currentIndex: root.profiles.indexOf(root.profileName)
                            onActivated: root.profileName = root.profiles[currentIndex]
                        }
                    }

                    // Actions
                    Grid {
                        width: parent.width
                        columns: 2
                        columnSpacing: Theme.spacingSmall
                        rowSpacing: Theme.spacingSmall
                        readonly property real cellW: (width - Theme.spacingSmall) / 2

                        RoundedButton {
                            width: parent.cellW
                            text: qsTr("Route")
                            enabled: !root.busy && root.waypoints.length >= 2
                            onClicked: root.planRoute()
                        }
                        RoundedButton {
                            width: parent.cellW
                            text: qsTr("Undo point")
                            enabled: root.waypoints.length > 0
                            onClicked: root.undoWaypoint()
                        }
                        RoundedButton {
                            width: parent.cellW
                            text: qsTr("Clear")
                            enabled: root.waypoints.length > 0 || root.coloredSegments.length > 0
                            onClicked: root.clearAll()
                        }
                        RoundedButton {
                            width: parent.cellW
                            text: qsTr("Send to watch")
                            enabled: !root.busy && root.plannedGpx.length > 0
                            onClicked: sendDialog.open()
                        }
                    }

                    Text {
                        width: parent.width
                        visible: root.statusMsg.length > 0 && !root.busy
                        text: root.statusMsg
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                        wrapMode: Text.WordWrap
                    }

                    // Summary
                    Rectangle {
                        width: parent.width
                        visible: root.coloredSegments.length > 0
                        height: summaryRow.implicitHeight + Theme.spacingMedium
                        radius: Theme.radiusSmall
                        color: Theme.cardNested
                        border.color: Theme.border
                        border.width: 1
                        Row {
                            id: summaryRow
                            anchors.centerIn: parent
                            width: parent.width - Theme.spacingMedium
                            Repeater {
                                model: [
                                    { k: qsTr("Distance"), v: root.fmtKm(root.summary.distance_m) },
                                    { k: qsTr("Ascent"),   v: root.fmtM(root.summary.ascent_m) },
                                    { k: qsTr("Descent"),  v: root.fmtM(root.summary.descent_m) },
                                    { k: qsTr("Max"),      v: (root.summary.max_gradient_pct === undefined
                                                              || root.summary.max_gradient_pct === null)
                                                              ? "–" : root.summary.max_gradient_pct + " %" }
                                ]
                                delegate: Column {
                                    required property var modelData
                                    width: summaryRow.width / 4
                                    spacing: 1
                                    Text {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        text: modelData.v
                                        color: Theme.text
                                        font.pixelSize: Theme.fontSizeBodyLarge
                                        font.bold: true
                                    }
                                    Text {
                                        anchors.horizontalCenter: parent.horizontalCenter
                                        text: modelData.k
                                        color: Theme.mutedText
                                        font.pixelSize: Theme.fontSizeTiny
                                    }
                                }
                            }
                        }
                    }

                    // Elevation profile, coloured by the same gradient buckets as the map
                    Column {
                        width: parent.width
                        visible: root.profileRows.length > 1
                        spacing: Theme.spacingSmall / 2
                        Text {
                            text: qsTr("Elevation")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeLabel
                        }
                        Canvas {
                            id: profileCanvas
                            width: parent.width
                            height: 90
                            onPaint: {
                                var ctx = getContext("2d")
                                ctx.reset()
                                var rows = root.profileRows
                                if (!rows || rows.length < 2) return
                                var eles = rows.filter(function(r){ return r.ele_m !== null })
                                                .map(function(r){ return r.ele_m })
                                if (eles.length < 2) return
                                var lo = Math.min.apply(null, eles)
                                var hi = Math.max.apply(null, eles)
                                var span = Math.max(1, hi - lo)
                                var maxD = rows[rows.length - 1].dist_m || 1
                                var baseY = height - 1
                                for (var i = 0; i < rows.length; i++) {
                                    if (rows[i].ele_m === null) continue
                                    var x = (rows[i].dist_m / maxD) * width
                                    var h = ((rows[i].ele_m - lo) / span) * (height - 6) + 2
                                    ctx.strokeStyle = rows[i].color
                                    ctx.lineWidth = Math.max(1, width / rows.length + 0.5)
                                    ctx.beginPath()
                                    ctx.moveTo(x, baseY)
                                    ctx.lineTo(x, baseY - h)
                                    ctx.stroke()
                                }
                            }
                            Connections {
                                target: root
                                function onProfileRowsChanged() { profileCanvas.requestPaint() }
                            }
                            onWidthChanged: requestPaint()
                        }
                    }

                    // Legend: which colour is which climb, with how much of the route is in it
                    Column {
                        width: parent.width
                        visible: root.legendRows.length > 0
                        spacing: Theme.spacingSmall / 2
                        Text {
                            text: qsTr("Climb legend")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeLabel
                        }
                        Repeater {
                            model: root.legendRows
                            delegate: Row {
                                required property var modelData
                                width: parent.width
                                spacing: Theme.spacingSmall
                                Rectangle {
                                    width: 14; height: 14; radius: 3
                                    anchors.verticalCenter: parent.verticalCenter
                                    color: modelData.color
                                    border.color: Theme.border
                                    border.width: 1
                                }
                                Text {
                                    width: parent.width - 14 - 70 - Theme.spacingSmall * 2
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: modelData.label
                                    color: Theme.text
                                    font.pixelSize: Theme.fontSizeCaption
                                    elide: Text.ElideRight
                                }
                                Text {
                                    width: 70
                                    anchors.verticalCenter: parent.verticalCenter
                                    horizontalAlignment: Text.AlignRight
                                    text: root.fmtKm(modelData.distance_m)
                                    color: Theme.mutedText
                                    font.pixelSize: Theme.fontSizeCaption
                                }
                            }
                        }
                    }

                    // POI search (optional - needs a prebuilt region DB, see the docs)
                    Column {
                        width: parent.width
                        spacing: Theme.spacingSmall / 2
                        Rectangle { width: parent.width; height: 1; color: Theme.border }
                        Text {
                            text: qsTr("Find a place (offline)")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeLabel
                        }
                        RoundedTextField {
                            id: poiDbField
                            width: parent.width
                            placeholderText: qsTr("POI database path (region.poi.sqlite)")
                            text: root.poiDb
                            onTextChanged: root.poiDb = text
                        }
                        Row {
                            width: parent.width
                            spacing: Theme.spacingSmall
                            RoundedTextField {
                                id: poiQueryField
                                width: parent.width - searchBtn.width - Theme.spacingSmall
                                placeholderText: qsTr("name, e.g. refuge")
                                text: root.poiQuery
                                onTextChanged: root.poiQuery = text
                                onAccepted: root.searchPoi()
                            }
                            RoundedButton {
                                id: searchBtn
                                text: qsTr("Search")
                                enabled: root.poiDb.length > 0 && root.poiQuery.length > 0
                                onClicked: root.searchPoi()
                            }
                        }
                        Text {
                            width: parent.width
                            visible: root.poiDb.length === 0
                            text: qsTr("Build a region DB with tools/poi_search.py — see docs/offline-routing.md.")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeTiny
                            wrapMode: Text.WordWrap
                        }
                        Repeater {
                            model: root.poiResults
                            delegate: Rectangle {
                                required property var modelData
                                width: parent.width
                                height: poiRow.implicitHeight + Theme.spacingSmall
                                radius: Theme.radiusSmall
                                color: Theme.cardNested
                                border.color: Theme.border
                                border.width: 1
                                Row {
                                    id: poiRow
                                    anchors.left: parent.left
                                    anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.margins: Theme.spacingSmall
                                    spacing: Theme.spacingSmall
                                    Column {
                                        width: parent.width - addBtn.width - Theme.spacingSmall
                                        anchors.verticalCenter: parent.verticalCenter
                                        Text {
                                            width: parent.width
                                            text: modelData.name || qsTr("(unnamed)")
                                            color: Theme.text
                                            font.pixelSize: Theme.fontSizeCaption
                                            elide: Text.ElideRight
                                        }
                                        Text {
                                            width: parent.width
                                            text: modelData.category || ""
                                            color: Theme.mutedText
                                            font.pixelSize: Theme.fontSizeTiny
                                            elide: Text.ElideRight
                                        }
                                    }
                                    RoundedButton {
                                        id: addBtn
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: qsTr("Add")
                                        onClicked: root.waypoints = root.waypoints.concat(
                                            [{ lat: modelData.lat, lon: modelData.lon }])
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // Confirm before a real watch write. Existing on-watch routes are preserved by the
    // backend (see _handle_route_write's "Real incident, 2026-08-11" note), so this only
    // adds - but a write to hardware still deserves an explicit yes.
    ThemedDialog {
        id: sendDialog
        anchors.centerIn: parent
        width: 400
        title: qsTr("Add route to watch")
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: root.doSend()
        // Column width is fixed (not derived from the dialog) so the dialog's implicitWidth
        // doesn't feed back into its own content width - that feedback is a binding loop.
        contentItem: Column {
            width: 360
            spacing: Theme.spacingSmall
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                text: qsTr("Send this planned route to the connected watch? Your existing routes are kept.")
            }
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: root.profileName + " · " + root.fmtKm(root.summary.distance_m)
                      + " · ↑ " + root.fmtM(root.summary.ascent_m)
            }
        }
    }
}
