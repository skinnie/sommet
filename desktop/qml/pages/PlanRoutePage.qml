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
    // `weatherProfile` feeds the croqui; plus wind, rain marks, a sun/moon summary and a verdict.
    // The map ALWAYS paints climb (André, 2026-08-31); `overlayMode` cycles what's laid over it:
    // 0 climb only, 1 + wind & rain, 2 + temperature (°C / feels-like labels along the route).
    property var weatherSegments: []
    property var weatherProfile: []
    property var windArrows: []
    property var rainMarks: []          // [{lat,lon,rain_mm}] raindrops on the map
    property var tempMarks: []          // [{lat,lon,temp_c,feels_c}] temperature labels on the map
    property var weatherAstro: ({})
    property var weatherVerdict: ({})
    property var weatherSummary: ({})
    property var weatherLegend: []      // [{key,label,color}] temperature buckets
    // Map overlay over the always-climb route: 0 = climb only, 1 = + wind & rain, 2 = + temperature
    property int overlayMode: 1
    // Ride the route the other way (many planners export a loop in the "wrong" direction, so the
    // graph's time/climb order runs backwards). Flips the map AND the graphs together.
    property bool reversed: false
    property bool weatherBusy: false
    property string startTime: "09:00"
    property string paceText: "20"       // km/h - a cycling default (André rides; editable)
    property string planDate: ""        // "" = today (YYYY-MM-DD)

    // Graph<->map cursor + zoom (André, 2026-08-31). cursorDist = metres along the route under the
    // cursor, shared by both graphs and the map dot; viewStart/viewEnd = the fraction of the route
    // the graphs are zoomed to (0..1 = whole route). Not persisted - transient view state.
    property real cursorDist: -1
    property real viewStart: 0
    property real viewEnd: 1
    readonly property real routeLenM: (summary && summary.distance_m) ? summary.distance_m : 0

    // Multi-day planner (André, 2026-09-02, à la Komoot): split the route into N equal-distance
    // daily stages. Each stage carries its distance, ascent (from the climb profile) and an ETA
    // guess from pace; tapping one focuses the graphs on it and forecasts that day's weather.
    property int numDays: 1
    property int splitMode: 0           // 0 = even distance, 1 = even effort (ascent + km)
    property int activeStage: -1        // -1 = whole route
    property var dayBounds: []          // internal day-boundary distances (m), length numDays-1;
                                        // recomputed from the mode, then draggable to override.

    // Recompute the default boundaries for the current mode + day count. "Even effort" balances
    // each day's distance PLUS climb (100 m of ascent counted like 1 km flat), so a steep day is
    // shorter - André, 2026-09-02 ("divide by similar ratio ascent/km").
    function recomputeBounds() {
        var n = numDays
        if (n < 2 || routeLenM <= 0 || profileRows.length < 2) { dayBounds = []; return }
        var b = [], i
        if (splitMode === 0) {
            for (i = 1; i < n; i++) b.push(routeLenM * i / n)
        } else {
            var cumEff = [], cd = [], eff = 0, prevD = 0, prevE = null
            for (var j = 0; j < profileRows.length; j++) {
                var r = profileRows[j]
                if (r.ele_m === null) continue
                var dd = Math.max(0, r.dist_m - prevD)
                var asc = (prevE !== null && r.ele_m > prevE) ? (r.ele_m - prevE) : 0
                eff += dd + 100 * asc; prevD = r.dist_m; prevE = r.ele_m
                cumEff.push(eff); cd.push(r.dist_m)
            }
            var total = eff || 1
            for (i = 1; i < n; i++) {
                var target = total * i / n, dist = routeLenM * i / n
                for (var k = 1; k < cumEff.length; k++) {
                    if (cumEff[k] >= target) {
                        var t = (target - cumEff[k - 1]) / Math.max(1e-6, cumEff[k] - cumEff[k - 1])
                        dist = cd[k - 1] + (cd[k] - cd[k - 1]) * t; break
                    }
                }
                b.push(dist)
            }
        }
        dayBounds = b
    }
    onNumDaysChanged: recomputeBounds()
    onSplitModeChanged: recomputeBounds()

    // Move one boundary (from a drag), clamped between its neighbours.
    function setBound(idx, distM) {
        if (idx < 0 || idx >= dayBounds.length) return
        var lo = idx === 0 ? 500 : dayBounds[idx - 1] + 500
        var hi = idx === dayBounds.length - 1 ? routeLenM - 500 : dayBounds[idx + 1] - 500
        var b = dayBounds.slice()
        b[idx] = Math.max(lo, Math.min(hi, distM))
        dayBounds = b
        persist()
    }

    readonly property var stages: {
        if (numDays < 2 || routeLenM <= 0 || profileRows.length < 2 || dayBounds.length !== numDays - 1) return []
        var edges = [0].concat(dayBounds).concat([routeLenM])
        var out = [], pace = parseFloat(paceText) || 20
        for (var i = 0; i < numDays; i++) {
            var sm = edges[i], em = edges[i + 1]
            var asc = 0, prev = null
            for (var j = 0; j < profileRows.length; j++) {
                var r = profileRows[j]
                if (r.ele_m === null || r.dist_m < sm || r.dist_m > em) continue
                if (prev !== null && r.ele_m > prev) asc += r.ele_m - prev
                prev = r.ele_m
            }
            out.push({ index: i, startFrac: sm / routeLenM, endFrac: em / routeLenM,
                       startKm: sm / 1000, endKm: em / 1000, distKm: (em - sm) / 1000,
                       ascentM: Math.round(asc), hours: ((em - sm) / 1000) / pace })
        }
        return out
    }
    readonly property var dayMarkDists: root.dayBounds
    function selectStage(i) {
        if (activeStage === i) {   // tap again = back to whole route
            activeStage = -1; viewStart = 0; viewEnd = 1; planDate = ""
        } else {
            activeStage = i
            viewStart = stages[i].startFrac; viewEnd = stages[i].endFrac
            var base = new Date(); base.setDate(base.getDate() + i)
            planDate = (i === 0) ? "" : (base.getFullYear() + "-" + ("0" + (base.getMonth() + 1)).slice(-2) + "-" + ("0" + base.getDate()).slice(-2))
        }
        forecastWeather()
        persist()
    }

    readonly property string backend: "http://127.0.0.1:8766"

    // Restore whatever was loaded before the page was navigated away from (the Loader destroys
    // it), and write back after every change - see PlanStore's header.
    Component.onCompleted: restoreFromStore()

    function restoreFromStore() {
        startTime = PlanStore.startTime; paceText = PlanStore.paceText; planDate = PlanStore.planDate
        if (!PlanStore.hasRoute) return
        plannedGpx = PlanStore.plannedGpx; routeName = PlanStore.routeName
        coloredSegments = PlanStore.coloredSegments; legendRows = PlanStore.legendRows
        profileRows = PlanStore.profileRows; summary = PlanStore.summary
        weatherSegments = PlanStore.weatherSegments; weatherProfile = PlanStore.weatherProfile
        windArrows = PlanStore.windArrows; rainMarks = PlanStore.rainMarks; tempMarks = PlanStore.tempMarks
        weatherAstro = PlanStore.weatherAstro; weatherVerdict = PlanStore.weatherVerdict
        weatherSummary = PlanStore.weatherSummary; weatherLegend = PlanStore.weatherLegend
        overlayMode = PlanStore.overlayMode; reversed = PlanStore.reversed; numDays = PlanStore.numDays; splitMode = PlanStore.splitMode; dayBounds = PlanStore.dayBounds
    }

    function persist() {
        PlanStore.plannedGpx = plannedGpx; PlanStore.routeName = routeName
        PlanStore.coloredSegments = coloredSegments; PlanStore.legendRows = legendRows
        PlanStore.profileRows = profileRows; PlanStore.summary = summary
        PlanStore.weatherSegments = weatherSegments; PlanStore.weatherProfile = weatherProfile
        PlanStore.windArrows = windArrows; PlanStore.rainMarks = rainMarks; PlanStore.tempMarks = tempMarks
        PlanStore.weatherAstro = weatherAstro; PlanStore.weatherVerdict = weatherVerdict
        PlanStore.weatherSummary = weatherSummary; PlanStore.weatherLegend = weatherLegend
        PlanStore.overlayMode = overlayMode; PlanStore.reversed = reversed; PlanStore.numDays = numDays; PlanStore.splitMode = splitMode; PlanStore.dayBounds = dayBounds
        PlanStore.startTime = startTime; PlanStore.paceText = paceText; PlanStore.planDate = planDate
    }

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
        api("POST", "/api/router/color", { gpx: plannedGpx, reverse: reversed }, function(status, res) {
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
            recomputeBounds()
            persist()
        })
    }

    function forecastWeather() {
        if (!plannedGpx) { statusMsg = qsTr("Upload a GPX first"); return }
        var pace = parseFloat(paceText)
        if (!(pace > 0)) { statusMsg = qsTr("Enter a pace in km/h"); return }
        var body = { gpx: plannedGpx, reverse: reversed, start: startTime, pace: pace,
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
            weatherLegend = res.legend || []
            rainMarks = res.rain_marks || []; tempMarks = res.temp_marks || []
            // Map stays on CLIMB colouring; wind + rain overlay it. (Toggle switches the fill.)
            statusMsg = ""
            persist()
        })
    }

    // Clear just the computed results (kept separate so loadGpx can reset before recomputing).
    function clearResults() {
        coloredSegments = []; legendRows = []; profileRows = []; summary = ({})
        weatherSegments = []; weatherProfile = []; windArrows = []; rainMarks = []; tempMarks = []
        weatherAstro = ({}); weatherVerdict = ({}); weatherSummary = ({})
        weatherLegend = []; overlayMode = 1
    }

    function clearAll() {
        plannedGpx = ""; routeName = ""; statusMsg = ""
        clearResults()
        persist()
    }

    // Ride it the other way: flip the direction and recompute climb + weather (both take a
    // `reverse` flag), so the map and both graphs turn around together.
    function toggleReverse() {
        if (!plannedGpx) return
        reversed = !reversed
        cursorDist = -1; resetView()
        colorTrack()
        forecastWeather()
        persist()
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

    // Interpolate the weather profile at a distance (metres) - for the crosshair readout that
    // follows the cursor across the graphs and the map.
    function sampleAt(dist) {
        var rows = weatherProfile
        if (!rows || rows.length < 2) return null
        if (dist <= rows[0].dist_m) return rows[0]
        if (dist >= rows[rows.length - 1].dist_m) return rows[rows.length - 1]
        for (var i = 1; i < rows.length; i++) {
            if (rows[i].dist_m >= dist) {
                var a = rows[i - 1], b = rows[i]
                var t = (dist - a.dist_m) / Math.max(1e-6, b.dist_m - a.dist_m)
                var lerp = function(x, y) { return x + (y - x) * t }
                return { dist_m: dist, eta: (t < 0.5 ? a.eta : b.eta),
                         temp_c: lerp(a.temp_c, b.temp_c), feels_c: lerp(a.feels_c, b.feels_c),
                         rain_mm: lerp(a.rain_mm, b.rain_mm), wind_kmh: lerp(a.wind_kmh, b.wind_kmh),
                         ele_m: (a.ele_m != null && b.ele_m != null) ? lerp(a.ele_m, b.ele_m)
                                : (b.ele_m != null ? b.ele_m : a.ele_m) }
            }
        }
        return rows[rows.length - 1]
    }

    // Zoom the graphs' distance window around a fraction (0..1 of the route) - shared by wheel-zoom
    // on either graph, so the temperature croqui and the elevation profile stay aligned.
    function zoomView(centreFrac, factor) {
        var span = viewEnd - viewStart
        var ns = Math.max(0.03, Math.min(1, span * factor))
        var nStart = centreFrac - (centreFrac - viewStart) * (ns / span)
        var nEnd = nStart + ns
        if (nStart < 0) { nStart = 0; nEnd = ns }
        if (nEnd > 1) { nEnd = 1; nStart = 1 - ns }
        viewStart = nStart; viewEnd = nEnd
    }
    function panView(fracDelta) {
        var span = viewEnd - viewStart
        var nStart = viewStart - fracDelta, nEnd = viewEnd - fracDelta
        if (nStart < 0) { nStart = 0; nEnd = span }
        if (nEnd > 1) { nEnd = 1; nStart = 1 - span }
        viewStart = nStart; viewEnd = nEnd
    }
    function resetView() { viewStart = 0; viewEnd = 1 }

    // --- forecast day (Open-Meteo gives ~16 days ahead) ------------------------------------
    function _isoToday() {
        var t = new Date()
        return t.getFullYear() + "-" + ("0" + (t.getMonth() + 1)).slice(-2) + "-" + ("0" + t.getDate()).slice(-2)
    }
    function planDateDisplay() {
        var d = planDate.length ? new Date(planDate + "T00:00:00") : new Date()
        return d.toLocaleDateString(Qt.locale(), "ddd d MMM")
    }
    function shiftDay(delta) {
        var base = planDate.length ? new Date(planDate + "T00:00:00") : new Date()
        base.setDate(base.getDate() + delta)
        var today = new Date(); today.setHours(0, 0, 0, 0)
        var max = new Date(today); max.setDate(max.getDate() + 15)   // Open-Meteo forecast horizon
        if (base < today) base = today
        if (base > max) base = max
        var iso = base.getFullYear() + "-" + ("0" + (base.getMonth() + 1)).slice(-2) + "-" + ("0" + base.getDate()).slice(-2)
        planDate = (iso === _isoToday()) ? "" : iso    // "" means today
        if (plannedGpx) forecastWeather()
        persist()
    }

    // File picker for the GPX (same idiom as RoutesPage's import dialog).
    FileDialog {
        id: gpxDialog
        title: qsTr("Choose a GPX route")
        nameFilters: [qsTr("GPX files (*.gpx)"), qsTr("All files (*)")]
        onAccepted: root.loadGpx(selectedFile)
    }

    // Save dialog for exporting one day's GPX portion.
    property string exportGpxText: ""
    FileDialog {
        id: saveDayDialog
        title: qsTr("Export day as GPX")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("GPX files (*.gpx)")]
        onAccepted: {
            var err = LocalFileService.saveText(selectedFile, root.exportGpxText)
            root.statusMsg = err.length ? err : qsTr("Saved.")
        }
    }
    // Right-click menu for a day stage (export / send) - the app's rounded-card menu language.
    ThemedMenu {
        id: dayMenu
        property var stage: null
        ThemedMenuItem { text: qsTr("Export day as GPX…"); onTriggered: if (dayMenu.stage) root.exportDay(dayMenu.stage) }
        ThemedMenuItem { text: qsTr("Send day to watch"); onTriggered: if (dayMenu.stage) root.sendDay(dayMenu.stage) }
    }
    function sendDay(st) {
        if (!plannedGpx) return
        var name = "Day " + (st.index + 1)
        statusMsg = qsTr("Preparing day %1…").arg(st.index + 1)
        api("POST", "/api/route/slice",
            { gpx: plannedGpx, reverse: reversed, start_km: st.startKm, end_km: st.endKm, name: name },
            function(status, res) {
                if (!res || !res.ok || !res.gpx) { statusMsg = (res && res.error) ? res.error : qsTr("Slice failed"); return }
                busy = true; statusMsg = qsTr("Sending day %1 to watch…").arg(st.index + 1)
                api("POST", "/api/routes", { name: name, gpx: res.gpx, confirm: true }, function(s2, r2) {
                    busy = false
                    statusMsg = (r2 && r2.ok) ? qsTr("Day %1 sent to watch.").arg(st.index + 1)
                             : (r2 && r2.error ? r2.error : qsTr("Send failed"))
                })
            })
    }
    // Slice the loaded GPX to a day's distance range and open the save dialog with a sensible name.
    function exportDay(st) {
        if (!plannedGpx) return
        var name = (routeName || "route").replace(/\.gpx$/i, "") + " - Day " + (st.index + 1)
        statusMsg = qsTr("Preparing day %1…").arg(st.index + 1)
        api("POST", "/api/route/slice",
            { gpx: plannedGpx, reverse: reversed, start_km: st.startKm, end_km: st.endKm, name: name },
            function(status, res) {
                if (!res || !res.ok || !res.gpx) { statusMsg = (res && res.error) ? res.error : qsTr("Export failed"); return }
                exportGpxText = res.gpx
                saveDayDialog.currentFile = LocalFileService.downloadsLocation + "/" + name.replace(/[^\w -]/g, "_") + ".gpx"
                saveDayDialog.open()
            })
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
                // The map fill is ALWAYS climb (André, 2026-08-31: "show only climb" / "show climb
                // with weather"). The toggle just shows/hides the weather overlay - the wind arrows
                // + rain icons - over that climb-coloured route.
                coloredSegments: root.coloredSegments
                windArrows: root.overlayMode === 1 ? root.windArrows : []
                rainMarks: root.overlayMode !== 0 ? root.rainMarks : []   // rain shown with wind AND temp
                tempMarks: root.overlayMode === 2 ? root.tempMarks : []
                dayMarkDists: root.dayMarkDists
                onDayBoundDragged: (i, d) => root.setBound(i, d)
                // graph <-> map cursor: show a dot where the graph is hovered, and push the
                // reverse (hovering the route moves the graph crosshair).
                highlightDist: root.cursorDist
                onRouteHovered: (dist) => { root.cursorDist = dist }
                onRouteHoverEnded: root.cursorDist = -1
                // The graphs follow the map: whatever portion of the route is on screen sets the
                // graphs' distance window (André, 2026-08-31).
                onRouteVisibleRange: (s, e) => { root.viewStart = s; root.viewEnd = Math.max(e, s + 0.01) }

                // Drag = pan (no tap-to-place any more; the route comes from the GPX). Suspended
                // while a day flag is under the cursor, so dragging a flag adjusts the day split.
                DragHandler {
                    id: panner
                    enabled: !map.dayFlagHovered
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
                        RoundedButton {
                            width: parent.width
                            visible: root.plannedGpx.length > 0
                            text: root.reversed ? qsTr("⇄ Direction: reversed — tap to flip back")
                                                : qsTr("⇄ Reverse direction")
                            enabled: !root.busy && !root.weatherBusy
                            onClicked: root.toggleReverse()
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

                    // Multi-day planner: split the route into equal daily stages (Komoot-style).
                    Column {
                        width: parent.width
                        visible: root.coloredSegments.length > 0
                        spacing: Theme.spacingSmall
                        Rectangle { width: parent.width; height: 1; color: Theme.border }
                        Row {
                            width: parent.width
                            spacing: Theme.spacingSmall
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                width: parent.width - dayMinus.width - dayPlus.width - dayCount.width - Theme.spacingSmall * 3
                                text: qsTr("Split into days")
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeLabel
                            }
                            RoundedButton {
                                id: dayMinus
                                text: "−"
                                enabled: root.numDays > 1
                                onClicked: { root.numDays = Math.max(1, root.numDays - 1); root.activeStage = -1; root.viewStart = 0; root.viewEnd = 1; root.persist() }
                            }
                            Text {
                                id: dayCount
                                anchors.verticalCenter: parent.verticalCenter
                                width: 22
                                horizontalAlignment: Text.AlignHCenter
                                text: root.numDays
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeBodyLarge
                                font.bold: true
                            }
                            RoundedButton {
                                id: dayPlus
                                text: "+"
                                enabled: root.numDays < 14
                                onClicked: { root.numDays = Math.min(14, root.numDays + 1); root.activeStage = -1; root.viewStart = 0; root.viewEnd = 1; root.persist() }
                            }
                        }
                        // Split mode - even distance vs even effort (distance + climb).
                        Row {
                            width: parent.width
                            visible: root.numDays > 1
                            spacing: Theme.spacingSmall
                            RoundedButton {
                                width: (parent.width - Theme.spacingSmall) / 2
                                text: qsTr("Even distance")
                                highlighted: root.splitMode === 0
                                onClicked: { root.splitMode = 0; root.activeStage = -1; root.viewStart = 0; root.viewEnd = 1; root.persist() }
                            }
                            RoundedButton {
                                width: (parent.width - Theme.spacingSmall) / 2
                                text: qsTr("Even effort")
                                highlighted: root.splitMode === 1
                                onClicked: { root.splitMode = 1; root.activeStage = -1; root.viewStart = 0; root.viewEnd = 1; root.persist() }
                            }
                        }
                        // Per-day list - tap a day to focus the map/graphs on it + forecast that day.
                        Repeater {
                            model: root.stages
                            delegate: Rectangle {
                                required property var modelData
                                width: parent.width
                                height: dayRow.implicitHeight + Theme.spacingSmall
                                radius: Theme.radiusSmall
                                color: root.activeStage === modelData.index ? Theme.primary : Theme.cardNested
                                border.color: Theme.border; border.width: 1
                                Row {
                                    id: dayRow
                                    anchors.left: parent.left; anchors.right: parent.right
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.margins: Theme.spacingSmall
                                    spacing: Theme.spacingSmall
                                    Text {
                                        width: 44
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: qsTr("Day %1").arg(modelData.index + 1)
                                        color: root.activeStage === modelData.index ? "white" : Theme.text
                                        font.pixelSize: Theme.fontSizeCaption
                                        font.bold: true
                                    }
                                    Text {
                                        width: parent.width - 44 - Theme.spacingSmall
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: modelData.distKm.toFixed(1) + " km · ↑" + modelData.ascentM + " m · ~"
                                              + modelData.hours.toFixed(1) + " h"
                                        color: root.activeStage === modelData.index ? "white" : Theme.mutedText
                                        font.pixelSize: Theme.fontSizeCaption
                                        elide: Text.ElideRight
                                    }
                                }
                                // Left-click focuses the day; right-click opens the export menu.
                                MouseArea {
                                    anchors.fill: parent
                                    cursorShape: Qt.PointingHandCursor
                                    acceptedButtons: Qt.LeftButton | Qt.RightButton
                                    onClicked: (mouse) => {
                                        if (mouse.button === Qt.RightButton) { dayMenu.stage = modelData; dayMenu.popup() }
                                        else root.selectStage(modelData.index)
                                    }
                                }
                            }
                        }
                        Text {
                            width: parent.width
                            visible: root.stages.length > 1
                            text: qsTr("Drag a numbered flag on the map to adjust a day. Tap a day to focus it; right-click a day to export its GPX or send it to the watch.")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeTiny
                            wrapMode: Text.WordWrap
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
                        // Day picker - plan ahead within the provider's ~16-day forecast window.
                        Row {
                            width: parent.width
                            spacing: Theme.spacingSmall
                            RoundedButton {
                                text: "‹"
                                enabled: root.planDate.length > 0   // can't go before today
                                onClicked: root.shiftDay(-1)
                            }
                            Text {
                                width: parent.width - 2 * (prevSpacer.implicitWidth) - Theme.spacingSmall * 2
                                anchors.verticalCenter: parent.verticalCenter
                                horizontalAlignment: Text.AlignHCenter
                                text: (root.planDate.length === 0 ? qsTr("Today") : root.planDateDisplay())
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeBody
                                font.bold: true
                            }
                            RoundedButton {
                                id: prevSpacer
                                text: "›"
                                onClicked: root.shiftDay(1)
                            }
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
                            visible: root.coloredSegments.length > 0 && root.weatherProfile.length > 1
                            text: root.overlayMode === 0 ? qsTr("Map: only climb — tap for wind & rain")
                                : root.overlayMode === 1 ? qsTr("Map: climb + wind & rain — tap for temperature")
                                : qsTr("Map: climb + temperature — tap for only climb")
                            onClicked: { root.overlayMode = (root.overlayMode + 1) % 3; root.persist() }
                        }

                        // Weather summary chips, with units (mirrors the mobile Route-weather screen)
                        Flow {
                            width: parent.width
                            visible: root.weatherProfile.length > 1
                            spacing: Theme.spacingSmall / 2
                            Repeater {
                                model: {
                                    var s = root.weatherSummary
                                    if (!s || s.distance_m === undefined) return []
                                    return [
                                        (s.distance_m / 1000).toFixed(1) + " km",
                                        qsTr("finish ") + (s.finish || "–"),
                                        (s.temp_min_c !== undefined ? s.temp_min_c + "–" + s.temp_max_c + " °C" : ""),
                                        (s.rain_max_mm !== undefined ? qsTr("rain ≤ ") + s.rain_max_mm + " mm" : ""),
                                        (s.wind_max_kmh !== undefined ? qsTr("wind ≤ ") + s.wind_max_kmh + " km/h" : "")
                                    ].filter(function(t){ return t.length > 0 })
                                }
                                delegate: Rectangle {
                                    required property string modelData
                                    height: chipTxt.implicitHeight + 6
                                    width: chipTxt.implicitWidth + Theme.spacingSmall
                                    radius: height / 2
                                    color: Theme.cardNested
                                    border.color: Theme.border
                                    border.width: 1
                                    Text {
                                        id: chipTxt
                                        anchors.centerIn: parent
                                        text: parent.modelData
                                        color: Theme.text
                                        font.pixelSize: Theme.fontSizeTiny
                                    }
                                }
                            }
                        }

                        // The weather "croqui" - a Komoot-style profile: filled terrain, an
                        // elevation line coloured by temperature (with °C labels), rain bars, a
                        // wind strip along the top (arrow up = headwind, down = tailwind, with
                        // km/h), and distance-km + time-of-day axes. Every axis carries its unit.
                        Canvas {
                            id: croqui
                            width: parent.width
                            height: 196
                            visible: root.weatherProfile.length > 1
                            readonly property real mL: 36
                            readonly property real mR: 40
                            // fraction (0..1 of the route) at a canvas x, and the inverse
                            function fracAtX(px) { return Math.max(0, Math.min(1, (px - mL) / Math.max(1, width - mL - mR))) }
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var rows = root.weatherProfile
                                if (!rows || rows.length < 2) return
                                var W = width, H = height
                                var mT = 26, mB = 26
                                var pW = W - mL - mR, pH = H - mT - mB
                                var maxD = rows[rows.length - 1].dist_m || 1
                                // visible distance window (zoom/scroll)
                                var dLo = root.viewStart * maxD, dHi = root.viewEnd * maxD
                                var dSpan = Math.max(1, dHi - dLo)
                                var X = function(d) { return mL + ((d - dLo) / dSpan) * pW }
                                var i, s, idx, r

                                // Temperature axis over the VISIBLE rows (so zooming reveals local
                                // detail), with a minimum span so near-constant temp reads flat.
                                var vis = rows.filter(function(rr){ return rr.dist_m >= dLo - 1 && rr.dist_m <= dHi + 1 })
                                if (vis.length < 2) vis = rows
                                var vals = []
                                for (i = 0; i < vis.length; i++) { vals.push(vis[i].temp_c); vals.push(vis[i].feels_c) }
                                var tLo = Math.min.apply(null, vals), tHi = Math.max.apply(null, vals)
                                var midT = (tLo + tHi) / 2
                                var half = Math.max(4, (tHi - tLo) / 2 + 1)
                                tLo = midT - half; tHi = midT + half
                                var tSpan = Math.max(1, tHi - tLo)
                                var Yt = function(t) { return mT + pH - ((t - tLo) / tSpan) * pH }
                                var rMax = Math.max(1, Math.max.apply(null,
                                            vis.map(function(rr){ return rr.rain_mm || 0 })))
                                var relCol = { headwind: "#d6453f", crosswind: "#e0912f", tailwind: "#2e9e6b" }

                                // gridlines
                                ctx.strokeStyle = Theme.border; ctx.lineWidth = 1; ctx.globalAlpha = 0.5
                                for (s = 0; s <= 2; s++) {
                                    var gy = mT + pH * s / 2
                                    ctx.beginPath(); ctx.moveTo(mL, gy); ctx.lineTo(mL + pW, gy); ctx.stroke()
                                }
                                ctx.globalAlpha = 1

                                // clip the plotted data to the window horizontally
                                ctx.save(); ctx.beginPath(); ctx.rect(mL, 0, pW, H); ctx.clip()

                                // rain bars - draw the bars, but label only the wettest one in view
                                // (labelling every bar turned a rainy stretch into an unreadable blob
                                // of overlapping numbers - André, 2026-08-31).
                                var bw = Math.max(2, (pW / Math.max(2, vis.length)) * 0.8)
                                var peakMM = 0, peakX = 0, peakH = 0
                                ctx.fillStyle = "#4e7cc4"; ctx.globalAlpha = 0.5
                                for (i = 0; i < rows.length; i++) {
                                    var rmm = rows[i].rain_mm || 0
                                    if (rmm < 0.05) continue
                                    var rh = (rmm / rMax) * (pH * 0.5)
                                    var bx = X(rows[i].dist_m)
                                    ctx.fillRect(bx - bw / 2, mT + pH - rh, bw, rh)
                                    if (rmm > peakMM) { peakMM = rmm; peakX = bx; peakH = rh }
                                }
                                ctx.globalAlpha = 1
                                if (peakMM >= 0.05) {
                                    ctx.fillStyle = "#4e7cc4"; ctx.font = "bold 9px sans-serif"; ctx.textAlign = "center"
                                    ctx.fillText(peakMM.toFixed(1) + " mm", peakX, mT + pH - peakH - 3)
                                }

                                // temperature: one full continuous line
                                ctx.lineWidth = 2.6; ctx.lineCap = "round"; ctx.lineJoin = "round"
                                ctx.strokeStyle = "#e8833a"; ctx.beginPath()
                                for (i = 0; i < rows.length; i++) {
                                    var tx = X(rows[i].dist_m), ty = Yt(rows[i].temp_c)
                                    if (i === 0) ctx.moveTo(tx, ty); else ctx.lineTo(tx, ty)
                                }
                                ctx.stroke()

                                // temp/feels numbers at several evenly-spaced points, in a compact
                                // "temp/feels" form (e.g. 21/22) - the legend explains the format
                                // (André, 2026-09-02: "put like 21/22 and put it in a legend").
                                ctx.font = "bold 10px sans-serif"; ctx.fillStyle = Theme.text; ctx.textAlign = "center"
                                var nLab = Math.min(6, vis.length)
                                for (s = 0; s < nLab; s++) {
                                    var rr = vis[Math.round(s * (vis.length - 1) / Math.max(1, nLab - 1))]
                                    var lx = Math.max(mL + 14, Math.min(mL + pW - 14, X(rr.dist_m)))
                                    ctx.fillText(Math.round(rr.temp_c) + "/" + Math.round(rr.feels_c), lx, Yt(rr.temp_c) - 6)
                                }

                                // wind strip (sampled across the window)
                                var wy = mT - 12
                                var nW = Math.min(6, vis.length)
                                for (s = 0; s < nW; s++) {
                                    r = vis[Math.round(s * (vis.length - 1) / Math.max(1, nW - 1))]
                                    var wx = X(r.dist_m); var col = relCol[r.wind_rel] || Theme.mutedText
                                    ctx.fillStyle = col; ctx.beginPath()
                                    if (r.wind_rel === "tailwind") {
                                        ctx.moveTo(wx - 3, wy - 3); ctx.lineTo(wx + 3, wy - 3); ctx.lineTo(wx, wy + 3)
                                    } else {
                                        ctx.moveTo(wx - 3, wy + 3); ctx.lineTo(wx + 3, wy + 3); ctx.lineTo(wx, wy - 3)
                                    }
                                    ctx.closePath(); ctx.fill()
                                    ctx.fillStyle = Theme.mutedText; ctx.font = "9px sans-serif"; ctx.textAlign = "center"
                                    ctx.fillText(Math.round(r.wind_kmh), wx, wy - 6)
                                }

                                // crosshair at the shared cursor, if it's in the window
                                if (root.cursorDist >= dLo && root.cursorDist <= dHi) {
                                    var cx = X(root.cursorDist)
                                    ctx.strokeStyle = Theme.primary; ctx.lineWidth = 1
                                    ctx.beginPath(); ctx.moveTo(cx, mT); ctx.lineTo(cx, mT + pH); ctx.stroke()
                                    var sm = root.sampleAt(root.cursorDist)
                                    if (sm) {
                                        ctx.beginPath(); ctx.arc(cx, Yt(sm.temp_c), 3, 0, 2 * Math.PI)
                                        ctx.fillStyle = "#e8833a"; ctx.fill()
                                    }
                                }
                                ctx.restore()

                                // left/right axis labels (outside the clip)
                                ctx.fillStyle = Theme.mutedText; ctx.font = "9px sans-serif"
                                ctx.textAlign = "right"
                                ctx.fillText(Math.round(tHi) + " °C", mL - 4, mT + 8)
                                ctx.fillText(Math.round(tLo) + " °C", mL - 4, mT + pH)
                                if (rMax >= 0.1) {
                                    ctx.textAlign = "left"
                                    ctx.fillText(rMax.toFixed(1) + " mm", mL + pW + 4, mT + pH)
                                }
                                // bottom axis: km + ETA at the window's start / mid / end
                                ctx.textAlign = "center"
                                for (s = 0; s <= 2; s++) {
                                    var d = dLo + dSpan * s / 2
                                    var smp = root.sampleAt(d)
                                    ctx.fillStyle = Theme.text
                                    ctx.fillText((d / 1000).toFixed(dSpan < 20000 ? 1 : 0) + " km", X(d), mT + pH + 11)
                                    ctx.fillStyle = Theme.mutedText
                                    ctx.fillText(smp ? smp.eta : "", X(d), mT + pH + 21)
                                }
                            }
                            // hover -> shared cursor
                            HoverHandler {
                                onPointChanged: {
                                    if (!hovered) { root.cursorDist = -1; return }
                                    var f = croqui.fracAtX(point.position.x)
                                    root.cursorDist = (root.viewStart + f * (root.viewEnd - root.viewStart)) * root.routeLenM
                                }
                            }
                            // (zoom/pan comes from the map now, so the panel keeps its own wheel-scroll)
                            Connections {
                                target: root
                                function onWeatherProfileChanged() { croqui.requestPaint() }
                                function onCursorDistChanged() { croqui.requestPaint() }
                                function onViewStartChanged() { croqui.requestPaint() }
                                function onViewEndChanged() { croqui.requestPaint() }
                            }
                            onWidthChanged: requestPaint()
                        }

                        // Live readout at the cursor (hover the graph or the route on the map).
                        Row {
                            width: parent.width
                            visible: root.weatherProfile.length > 1
                            spacing: Theme.spacingSmall
                            property var sm: root.cursorDist >= 0 ? root.sampleAt(root.cursorDist) : null
                            Text {
                                width: parent.width
                                elide: Text.ElideRight
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeTiny
                                text: {
                                    var s = parent.sm
                                    if (!s) return qsTr("Hover the graph or the route to read any point")
                                    return "@ " + (s.dist_m / 1000).toFixed(1) + " km · " + (s.eta || "") + " · "
                                        + Math.round(s.temp_c) + "°/" + Math.round(s.feels_c) + "° · "
                                        + qsTr("wind ") + Math.round(s.wind_kmh) + " km/h · "
                                        + qsTr("rain ") + (s.rain_mm || 0).toFixed(1) + " mm"
                                        + (s.ele_m != null ? " · " + Math.round(s.ele_m) + " m" : "")
                                }
                            }
                        }

                        // Temperature legend (the buckets the map/line are coloured by) + wind key
                        Flow {
                            width: parent.width
                            visible: root.weatherLegend.length > 0
                            spacing: Theme.spacingSmall
                            Repeater {
                                model: root.weatherLegend
                                delegate: Row {
                                    required property var modelData
                                    spacing: 3
                                    Rectangle {
                                        width: 10; height: 10; radius: 2
                                        anchors.verticalCenter: parent.verticalCenter
                                        color: modelData.color
                                        border.color: Theme.border; border.width: 1
                                    }
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: (modelData.label || "").replace("degC", "°C")
                                        color: Theme.mutedText
                                        font.pixelSize: Theme.fontSizeTiny
                                    }
                                }
                            }
                        }
                        Text {
                            width: parent.width
                            visible: root.weatherProfile.length > 1
                            text: qsTr("temperature line · numbers above = temp/feels (°C) · bars = rain (peak mm) · wind (km/h): ")
                                  + "<font color='#d6453f'>▲ head</font> <font color='#e0912f'>cross</font> <font color='#2e9e6b'>▼ tail</font>"
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
                            height: 104
                            readonly property real mL: 36
                            readonly property real mR: 40
                            function fracAtX(px) { return Math.max(0, Math.min(1, (px - mL) / Math.max(1, width - mL - mR))) }
                            onPaint: {
                                var ctx = getContext("2d"); ctx.reset()
                                var rows = root.profileRows
                                if (!rows || rows.length < 2) return
                                var maxD = rows[rows.length - 1].dist_m || 1
                                var dLo = root.viewStart * maxD, dHi = root.viewEnd * maxD, dSpan = Math.max(1, dHi - dLo)
                                var pW = width - mL - mR
                                var X = function(d) { return mL + ((d - dLo) / dSpan) * pW }
                                var vis = rows.filter(function(r){ return r.ele_m !== null && r.dist_m >= dLo - 1 && r.dist_m <= dHi + 1 })
                                var pool = vis.length >= 2 ? vis : rows.filter(function(r){ return r.ele_m !== null })
                                if (pool.length < 2) return
                                var eles = pool.map(function(r){ return r.ele_m })
                                var lo = Math.min.apply(null, eles), hi = Math.max.apply(null, eles)
                                var span = Math.max(1, hi - lo)
                                var baseY = height - 14
                                ctx.save(); ctx.beginPath(); ctx.rect(mL, 0, pW, height); ctx.clip()
                                var bw = Math.max(1, pW / Math.max(2, vis.length) + 0.5)
                                for (var i = 0; i < rows.length; i++) {
                                    if (rows[i].ele_m === null) continue
                                    var x = X(rows[i].dist_m)
                                    var h = ((rows[i].ele_m - lo) / span) * (baseY - 4) + 2
                                    ctx.strokeStyle = rows[i].color; ctx.lineWidth = bw
                                    ctx.beginPath(); ctx.moveTo(x, baseY); ctx.lineTo(x, baseY - h); ctx.stroke()
                                }
                                if (root.cursorDist >= dLo && root.cursorDist <= dHi) {
                                    var cx = X(root.cursorDist)
                                    ctx.strokeStyle = Theme.primary; ctx.lineWidth = 1
                                    ctx.beginPath(); ctx.moveTo(cx, 0); ctx.lineTo(cx, baseY); ctx.stroke()
                                }
                                ctx.restore()
                                ctx.fillStyle = Theme.mutedText; ctx.font = "9px sans-serif"; ctx.textAlign = "right"
                                ctx.fillText(Math.round(hi) + " m", mL - 4, 9)
                                ctx.fillText(Math.round(lo) + " m", mL - 4, baseY)
                                ctx.textAlign = "center"
                                for (var s = 0; s <= 2; s++) {
                                    var d = dLo + dSpan * s / 2
                                    ctx.fillStyle = Theme.text
                                    ctx.fillText((d / 1000).toFixed(dSpan < 20000 ? 1 : 0) + " km", X(d), height - 2)
                                }
                            }
                            HoverHandler {
                                onPointChanged: {
                                    if (!hovered) { root.cursorDist = -1; return }
                                    var f = profileCanvas.fracAtX(point.position.x)
                                    root.cursorDist = (root.viewStart + f * (root.viewEnd - root.viewStart)) * root.routeLenM
                                }
                            }
                            Connections {
                                target: root
                                function onProfileRowsChanged() { profileCanvas.requestPaint() }
                                function onCursorDistChanged() { profileCanvas.requestPaint() }
                                function onViewStartChanged() { profileCanvas.requestPaint() }
                                function onViewEndChanged() { profileCanvas.requestPaint() }
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
