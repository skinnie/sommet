import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import AmbitApp

// Plan a ride/hike with weather. You bring a GPX (drawn in any online planner, Basecamp,
// Komoot, RideWithGPS...), and Sommet paints it by climb steepness and, once you set a start
// time + pace, shows the weather you'll actually meet along it - temperature, rain, head/tail
// wind and sun/moon at each point's ETA - then can send the route straight to the watch.
//
// History: this began as a fully-offline route *planner* on a bundled BRouter engine (tap
// points, auto-route). Removed 2026-08-31 (André: "most of the time we have good online
// planners... weather is something I really need") - route-*drawing* is better done in the
// dedicated tools he already uses, so Plan now takes a finished GPX and adds the one thing
// those tools don't: weather along it. This matches the Android/iOS RouteWeather screen, which
// already worked from an imported GPX. Climb colouring (track_color.py) and weather
// (weather_route.py) both run on any coordinate list, so nothing here needs a router.
//
// Backend: /api/router/color {gpx} (climb, offline) and /api/weather/route {gpx} (Open-Meteo,
// online). The map is the shared MapView; sending uses the same /api/routes as RoutesPage.
Item {
    id: root

    // --- loaded route + climb colouring -----------------------------------------------
    property string plannedGpx: ""      // the uploaded GPX text; "" = nothing loaded
    property string routeName: ""       // the file's name, for the header + send dialog
    property var coloredSegments: []    // [{color, coords:[[lat,lon],...]}] climb-coloured
    property var legendRows: []         // [{key,label,color,distance_m,ascent_m}]
    property var profileRows: []        // [{dist_m,ele_m,grad_pct,color}]
    property var summary: ({})          // {distance_m,ascent_m,descent_m,max_gradient_pct,...}
    property string statusMsg: ""
    property bool busy: false

    // Weather + sun/moon along the route (online: Open-Meteo via the backend). Temp-coloured
    // `weatherSegments` feed the map's coloredSegments; `weatherProfile` feeds a profile canvas;
    // plus wind, a sun/moon summary and a plain-language verdict. `weatherMode` toggles whether
    // the map paints weather or climb.
    property var weatherSegments: []
    property var weatherProfile: []
    property var windArrows: []
    property var weatherAstro: ({})
    property var weatherVerdict: ({})
    property var weatherSummary: ({})
    property bool weatherMode: false
    property bool weatherBusy: false
    property string startTime: "09:00"
    property string paceText: "4.5"
    property string planDate: ""        // "" = today (YYYY-MM-DD)

    readonly property string backend: "http://127.0.0.1:8766"

    // Start/finish dots, derived from the coloured track's first/last coordinate.
    readonly property var startEndMarkers: {
        if (coloredSegments.length === 0) return []
        var first = coloredSegments[0].coords
        var lastSeg = coloredSegments[coloredSegments.length - 1].coords
        if (!first || !lastSeg || first.length === 0 || lastSeg.length === 0) return []
        var s = first[0], e = lastSeg[lastSeg.length - 1]
        return [{ lat: s[0], lon: s[1] }, { lat: e[0], lon: e[1] }]
    }

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

    // Read the picked GPX, colour it by climb, and forecast the weather along it.
    function loadGpx(fileUrl) {
        var gpx = LocalFileService.readText(fileUrl)
        if (!gpx || gpx.length === 0) {
            statusMsg = qsTr("Couldn't read that file")
            return
        }
        var s = fileUrl.toString()
        routeName = decodeURIComponent(s.substring(s.lastIndexOf("/") + 1))
        plannedGpx = gpx
        clearResults()
        colorTrack()
        forecastWeather()
    }

    function colorTrack() {
        if (!plannedGpx) return
        busy = true
        statusMsg = qsTr("Reading route…")
        api("POST", "/api/router/color", { gpx: plannedGpx }, function(status, res) {
            busy = false
            if (!res || res.ok === false) {
                coloredSegments = []; legendRows = []; profileRows = []; summary = ({})
                statusMsg = (res && res.error) ? res.error : qsTr("Couldn't read the route")
                return
            }
            coloredSegments = res.segments || []
            legendRows = res.legend || []
            profileRows = res.profile || []
            summary = res.summary || ({})
            statusMsg = ""
        })
    }

    function forecastWeather() {
        if (!plannedGpx) { statusMsg = qsTr("Upload a GPX first"); return }
        var pace = parseFloat(paceText)
        if (!(pace > 0)) { statusMsg = qsTr("Enter a pace in km/h"); return }
        var body = { gpx: plannedGpx, start: startTime, pace: pace,
                     tz: -(new Date().getTimezoneOffset()) / 60 }
        if (planDate.length) body.date = planDate
        weatherBusy = true
        statusMsg = qsTr("Fetching forecast…")
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

    // Clear just the computed results (kept separate so loadGpx can reset before recomputing).
    function clearResults() {
        coloredSegments = []; legendRows = []; profileRows = []; summary = ({})
        weatherSegments = []; weatherProfile = []; windArrows = []
        weatherAstro = ({}); weatherVerdict = ({}); weatherSummary = ({}); weatherMode = false
    }

    function clearAll() {
        plannedGpx = ""; routeName = ""; statusMsg = ""
        clearResults()
    }

    function doSend() {
        if (!plannedGpx) return
        busy = true
        statusMsg = qsTr("Sending to watch…")
        api("POST", "/api/routes",
            { name: (routeName || "Sommet plan").replace(/\.gpx$/i, ""), gpx: plannedGpx, confirm: true },
            function(status, res) {
                busy = false
                statusMsg = (res && res.ok)
                    ? qsTr("Sent to watch — %1 existing route(s) kept").arg(res.routes_kept || 0)
                    : (res && res.stderr ? res.stderr.trim()
                                         : (res && res.error ? res.error : qsTr("Send failed")))
            })
    }

    function fmtKm(m) { return m === undefined || m === null ? "–" : (m / 1000).toFixed(1) + " km" }
    function fmtM(m)  { return m === undefined || m === null ? "–" : Math.round(m) + " m" }

    // File picker for the GPX (same idiom as RoutesPage's import dialog).
    FileDialog {
        id: gpxDialog
        title: qsTr("Choose a GPX route")
        nameFilters: [qsTr("GPX files (*.gpx)"), qsTr("All files (*)")]
        onAccepted: root.loadGpx(selectedFile)
    }

    // --- layout: map on the left, controls + results on the right ----------------------
    Row {
        anchors.fill: parent

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
                // Centre on the detected location until a route is loaded; MapView auto-fits to
                // the coloured route once one exists. WeatherService is the app's own IP position.
                latitude: WeatherService.latitude
                longitude: WeatherService.longitude
                markers: root.startEndMarkers
                coloredSegments: (root.weatherMode && root.weatherSegments.length > 0)
                                 ? root.weatherSegments : root.coloredSegments
                windArrows: (root.weatherMode && root.windArrows.length > 0) ? root.windArrows : []

                // Drag = pan (no tap-to-place any more; the route comes from the GPX).
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
                    cursorShape: panner.active ? Qt.ClosedHandCursor : Qt.OpenHandCursor
                }
            }

            // "Locate me" - centre the map on the detected location.
            Rectangle {
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.rightMargin: 8
                anchors.topMargin: 84
                width: 34; height: 34; radius: 17
                color: "#CCFFFFFF"
                Canvas {
                    anchors.centerIn: parent
                    width: 20; height: 20
                    onPaint: {
                        var ctx = getContext("2d"); ctx.reset()
                        ctx.strokeStyle = "#333333"; ctx.fillStyle = "#333333"; ctx.lineWidth = 1.6
                        var c = 10
                        ctx.beginPath(); ctx.arc(c, c, 5, 0, 2 * Math.PI); ctx.stroke()
                        ctx.beginPath(); ctx.arc(c, c, 1.6, 0, 2 * Math.PI); ctx.fill()
                        var ticks = [[c, 0, c, 3], [c, 17, c, 20], [0, c, 3, c], [17, c, 20, c]]
                        for (var i = 0; i < ticks.length; i++) {
                            ctx.beginPath(); ctx.moveTo(ticks[i][0], ticks[i][1])
                            ctx.lineTo(ticks[i][2], ticks[i][3]); ctx.stroke()
                        }
                    }
                }
                TapHandler {
                    onTapped: {
                        var la = WeatherService.latitude, lo = WeatherService.longitude
                        if (!isNaN(la) && !isNaN(lo) && (la !== 0 || lo !== 0))
                            map.centerOn(la, lo, 14)
                        else
                            root.statusMsg = qsTr("Location not detected yet")
                    }
                }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
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
                    text: root.busy || root.weatherBusy ? root.statusMsg
                          : root.plannedGpx.length === 0
                            ? qsTr("Upload a GPX to see its climbs and the weather along it. Drag to pan, scroll to zoom.")
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

                    // Title + loaded-route name
                    Column {
                        width: parent.width
                        spacing: 2
                        Text {
                            text: qsTr("Plan route")
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeTitle
                            font.bold: true
                        }
                        Text {
                            width: parent.width
                            text: root.routeName.length > 0 ? root.routeName
                                                            : qsTr("Weather + climbs for a GPX you bring")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            elide: Text.ElideRight
                        }
                    }

                    // Actions
                    Column {
                        width: parent.width
                        spacing: Theme.spacingSmall

                        RoundedButton {
                            width: parent.width
                            text: root.plannedGpx.length > 0 ? qsTr("Upload a different GPX")
                                                             : qsTr("Upload GPX")
                            enabled: !root.busy
                            onClicked: gpxDialog.open()
                        }
                        Row {
                            width: parent.width
                            spacing: Theme.spacingSmall
                            visible: root.plannedGpx.length > 0
                            readonly property real cellW: (width - Theme.spacingSmall) / 2
                            RoundedButton {
                                width: parent.cellW
                                text: qsTr("Clear")
                                enabled: root.plannedGpx.length > 0
                                onClicked: root.clearAll()
                            }
                            RoundedButton {
                                width: parent.cellW
                                text: qsTr("Send to watch")
                                enabled: !root.busy && root.plannedGpx.length > 0
                                onClicked: sendDialog.open()
                            }
                        }
                    }

                    Text {
                        width: parent.width
                        visible: root.statusMsg.length > 0 && !root.busy && !root.weatherBusy
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
        contentItem: Column {
            width: 360
            spacing: Theme.spacingSmall
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
                text: qsTr("Send this route to the connected watch? Your existing routes are kept.")
            }
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: (root.routeName || qsTr("route")) + " · " + root.fmtKm(root.summary.distance_m)
                      + " · ↑ " + root.fmtM(root.summary.ascent_m)
            }
        }
    }
}
