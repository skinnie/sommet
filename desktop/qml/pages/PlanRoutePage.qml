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

    // Weather + sun/moon along the planned route (online: Open-Meteo via the backend). Same
    // shape family as the climb colouring - temp-coloured `weatherSegments` feed the map's
    // coloredSegments, `weatherProfile` feeds a profile canvas - plus wind, a sun/moon summary
    // and a plain-language verdict. `weatherMode` toggles whether the map paints weather or climb.
    property var weatherSegments: []    // [{color, coords:[[lat,lon],...]}] coloured by temp
    property var weatherProfile: []     // [{dist_m,eta,ele_m,temp_c,feels_c,rain_mm,wind_kmh,wind_rel,color}]
    property var windArrows: []
    property var weatherAstro: ({})
    property var weatherVerdict: ({})
    property var weatherSummary: ({})
    property bool weatherMode: false
    property bool weatherBusy: false
    property string startTime: "09:00"
    property string paceText: "4.5"
    property string planDate: ""        // "" = today (YYYY-MM-DD)

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

    function forecastWeather() {
        if (!plannedGpx) { statusMsg = qsTr("Plan a route first"); return }
        var pace = parseFloat(paceText)
        if (!(pace > 0)) { statusMsg = qsTr("Enter a pace in km/h"); return }
        weatherBusy = true
        statusMsg = qsTr("Fetching forecast…")
        var tz = -(new Date().getTimezoneOffset()) / 60   // JS offset is inverted, in minutes
        var body = { gpx: plannedGpx, start: startTime, pace: pace, tz: tz }
        if (planDate.length) body.date = planDate
        api("POST", "/api/weather/route", body, function(status, res) {
            weatherBusy = false
            if (!res || !res.ok) {
                statusMsg = (res && res.error) ? res.error : qsTr("Weather forecast failed")
                return
            }
            weatherSegments = res.segments || []
            weatherProfile = res.profile || []
            windArrows = res.wind_arrows || []
            weatherAstro = res.astro || ({})
            weatherVerdict = res.verdict || ({})
            weatherSummary = res.summary || ({})
            weatherMode = true            // show the weather-coloured track once we have it
            statusMsg = ""
        })
    }

    function clearAll() {
        waypoints = []; coloredSegments = []; legendRows = []; profileRows = []
        summary = ({}); plannedGpx = ""; statusMsg = ""
        weatherSegments = []; weatherProfile = []; windArrows = []
        weatherAstro = ({}); weatherVerdict = ({}); weatherSummary = ({}); weatherMode = false
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
                coloredSegments: (root.weatherMode && root.weatherSegments.length > 0)
                                 ? root.weatherSegments : root.coloredSegments
                windArrows: (root.weatherMode && root.windArrows.length > 0) ? root.windArrows : []

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

                    // Weather + sun/moon along the route (online forecast at each point's ETA)
                    Column {
                        width: parent.width
                        visible: root.plannedGpx.length > 0
                        spacing: Theme.spacingSmall
                        Rectangle { width: parent.width; height: 1; color: Theme.border }
                        Text {
                            text: qsTr("Weather along route")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeLabel
                        }
                        // start time + pace inputs
                        Row {
                            width: parent.width
                            spacing: Theme.spacingSmall
                            Column {
                                width: (parent.width - Theme.spacingSmall) / 2
                                spacing: 2
                                Text { text: qsTr("Start (HH:MM)"); color: Theme.mutedText
                                       font.pixelSize: Theme.fontSizeTiny }
                                RoundedTextField {
                                    width: parent.width
                                    text: root.startTime
                                    placeholderText: "09:00"
                                    onTextChanged: root.startTime = text
                                }
                            }
                            Column {
                                width: (parent.width - Theme.spacingSmall) / 2
                                spacing: 2
                                Text { text: qsTr("Pace (km/h)"); color: Theme.mutedText
                                       font.pixelSize: Theme.fontSizeTiny }
                                RoundedTextField {
                                    width: parent.width
                                    text: root.paceText
                                    placeholderText: "4.5"
                                    onTextChanged: root.paceText = text
                                    onAccepted: root.forecastWeather()
                                }
                            }
                        }
                        RoundedButton {
                            width: parent.width
                            text: root.weatherBusy ? qsTr("Fetching…") : qsTr("Forecast weather")
                            enabled: !root.weatherBusy && root.plannedGpx.length > 0
                            onClicked: root.forecastWeather()
                        }

                        // verdict strip
                        Rectangle {
                            width: parent.width
                            visible: !!(root.weatherVerdict && root.weatherVerdict.headline)
                            height: vcol.implicitHeight + Theme.spacingMedium
                            radius: Theme.radiusSmall
                            property string vstate: (root.weatherVerdict && root.weatherVerdict.state) || "ok"
                            color: vstate === "ok" ? Qt.rgba(0.18, 0.62, 0.42, 0.14)
                                 : vstate === "critical" ? Qt.rgba(0.84, 0.27, 0.25, 0.16)
                                 : Qt.rgba(0.88, 0.57, 0.18, 0.16)
                            border.width: 1
                            border.color: vstate === "ok" ? Theme.success
                                        : vstate === "critical" ? Theme.error : "#E0912F"
                            Column {
                                id: vcol
                                anchors.left: parent.left
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                anchors.margins: Theme.spacingSmall
                                spacing: 2
                                Text {
                                    width: parent.width
                                    text: (root.weatherVerdict && root.weatherVerdict.headline) || ""
                                    color: Theme.text
                                    font.pixelSize: Theme.fontSizeCaption
                                    font.bold: true
                                    wrapMode: Text.WordWrap
                                }
                                Text {
                                    width: parent.width
                                    visible: !!(root.weatherVerdict && root.weatherVerdict.detail)
                                    text: (root.weatherVerdict && root.weatherVerdict.detail) || ""
                                    color: Theme.mutedText
                                    font.pixelSize: Theme.fontSizeTiny
                                    wrapMode: Text.WordWrap
                                }
                            }
                        }

                        // sun/moon quick summary + map colour toggle
                        Row {
                            width: parent.width
                            visible: root.weatherProfile.length > 1
                            spacing: Theme.spacingMedium
                            Text {
                                text: "☀ " + qsTr("set %1").arg((root.weatherAstro.sun && root.weatherAstro.sun.sunset) || "–")
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Text {
                                text: "🌙 " + (root.weatherAstro.moon_illumination !== undefined
                                      ? Math.round(root.weatherAstro.moon_illumination * 100) + "%" : "–")
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            Item { width: 1; height: 1 }
                        }
                        RoundedButton {
                            width: parent.width
                            visible: root.weatherSegments.length > 0 && root.coloredSegments.length > 0
                            text: root.weatherMode ? qsTr("Map: showing weather — switch to climb")
                                                   : qsTr("Map: showing climb — switch to weather")
                            onClicked: root.weatherMode = !root.weatherMode
                        }

                        // weather profile: temp-coloured elevation line + rain bars + wind line
                        Canvas {
                            id: wxCanvas
                            width: parent.width
                            height: 120
                            visible: root.weatherProfile.length > 1
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var rows = root.weatherProfile
                                if (!rows || rows.length < 2) return
                                var W = width, H = height
                                var maxD = rows[rows.length - 1].dist_m || 1
                                var xOf = function(d) { return (d / maxD) * (W - 2) + 1 }
                                var eles = rows.filter(function(r){ return r.ele_m !== null })
                                                .map(function(r){ return r.ele_m })
                                var lo = eles.length ? Math.min.apply(null, eles) : 0
                                var hi = eles.length ? Math.max.apply(null, eles) : 1
                                var span = Math.max(1, hi - lo)
                                var yEle = function(e) { return (H - 3) - ((e - lo) / span) * (H - 22) }
                                var rmax = Math.max(0.6, Math.max.apply(null,
                                            rows.map(function(r){ return r.rain_mm || 0 })))
                                var wmax = Math.max(10, Math.max.apply(null,
                                            rows.map(function(r){ return r.wind_kmh || 0 })))
                                var yWind = function(w) { return (H - 3) - (Math.min(w, wmax) / wmax) * (H - 22) }
                                var bw = Math.max(2, (W / rows.length) * 0.55)
                                // rain bars from the baseline
                                ctx.globalAlpha = 0.5
                                ctx.fillStyle = "#4e7cc4"
                                for (var i = 0; i < rows.length; i++) {
                                    var rmm = rows[i].rain_mm || 0
                                    if (rmm < 0.05) continue
                                    var rh = (rmm / rmax) * (H * 0.5)
                                    ctx.fillRect(xOf(rows[i].dist_m) - bw / 2, (H - 3) - rh, bw, rh)
                                }
                                ctx.globalAlpha = 1
                                // elevation line, coloured by temperature bucket
                                ctx.lineWidth = 1.6; ctx.lineCap = "round"
                                for (i = 1; i < rows.length; i++) {
                                    if (rows[i].ele_m === null || rows[i - 1].ele_m === null) continue
                                    ctx.strokeStyle = rows[i].color
                                    ctx.beginPath()
                                    ctx.moveTo(xOf(rows[i - 1].dist_m), yEle(rows[i - 1].ele_m))
                                    ctx.lineTo(xOf(rows[i].dist_m), yEle(rows[i].ele_m))
                                    ctx.stroke()
                                }
                                // wind line, coloured by head/cross/tail
                                var relCol = { headwind: "#d6453f", crosswind: "#e0912f", tailwind: "#2e9e6b" }
                                for (i = 1; i < rows.length; i++) {
                                    ctx.strokeStyle = relCol[rows[i].wind_rel] || Theme.mutedText
                                    ctx.beginPath()
                                    ctx.moveTo(xOf(rows[i - 1].dist_m), yWind(rows[i - 1].wind_kmh))
                                    ctx.lineTo(xOf(rows[i].dist_m), yWind(rows[i].wind_kmh))
                                    ctx.stroke()
                                }
                            }
                            Connections {
                                target: root
                                function onWeatherProfileChanged() { wxCanvas.requestPaint() }
                            }
                            onWidthChanged: requestPaint()
                        }
                        Text {
                            width: parent.width
                            visible: root.weatherProfile.length > 1
                            text: qsTr("line = elevation coloured by temp · bars = rain mm · wind: ")
                                  + "<font color='#2e9e6b'>tail</font> <font color='#e0912f'>cross</font> <font color='#d6453f'>head</font>"
                            textFormat: Text.RichText
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeTiny
                            wrapMode: Text.WordWrap
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
