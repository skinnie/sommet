import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts
import AmbitApp

// Race planner (2026-09-18): import a GPX, set the start + controls (each with a BRM time limit),
// optionally give your typical rolling-road speed, and get a control-by-control timeline with
// arrival times and cutoff margins. Speed comes from the engine's curve model via the backend
// (/api/race/timeline -> race_timeline.py -> estimate_route); this page only collects inputs and
// renders the result, plus a What-if panel (re-runs the timeline with speed/stops/sleep tweaked
// and shows the deltas). Not here yet: POIs/water gaps, and saving/loading plans.
Item {
    id: root

    readonly property string backend: "http://127.0.0.1:8766"
    readonly property color marginGood: "#2e9e6b"
    readonly property color marginBad: "#d6453f"

    property string gpxText: ""
    property string gpxName: ""
    property real routeDistanceKm: 0
    property bool busy: false
    property string statusMsg: ""
    property var timeline: null
    property var weather: null       // race_weather result: per-control temp/wind/rain + daylight
    // what-if: the payload that produced `timeline` (the baseline), the adjusted result, and knobs.
    property var basePayload: null
    property var scenario: null
    property int whatifSpeed: 0      // km/h added to base speed
    property int whatifStopMin: 0    // minutes added to each control stop
    property real whatifSleepH: 0    // hours of sleep added on top

    // --- backend call (same XMLHttpRequest idiom as PlanRoutePage / RoutesPage) ---
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

    function loadGpx(fileUrl) {
        var gpx = LocalFileService.readText(fileUrl)
        if (!gpx || gpx.length === 0) { statusMsg = qsTr("Couldn't read that file"); return }
        var s = fileUrl.toString()
        gpxName = decodeURIComponent(s.substring(s.lastIndexOf("/") + 1))
        gpxText = gpx
        timeline = null
        statusMsg = qsTr("Reading route…")
        computeTimeline()   // first pass with whatever controls exist -> reveals distance
    }

    function startIso() {
        var d = (startDate.text || "").trim()
        var t = (startTime.text || "06:00").trim()
        return d + "T" + (t.length === 5 ? t : "06:00") + ":00"
    }

    function computeTimeline() {
        if (!gpxText) { statusMsg = qsTr("Load a GPX first"); return }
        var startMs = Date.parse(startIso())
        if (isNaN(startMs)) { statusMsg = qsTr("Check the start date/time"); return }

        var cutoffs = []
        for (var i = 0; i < controlsModel.count; i++) {
            var c = controlsModel.get(i)
            var km = parseFloat(c.km)
            var h = parseFloat(c.hours)
            if (isNaN(km)) continue
            var co = { label: c.label || ("Control " + (i + 1)), distance_km: km }
            if (!isNaN(h)) co.cutoff_dt = new Date(startMs + h * 3600 * 1000).toISOString()
            cutoffs.push(co)
        }

        var athlete = { weight_kg: 75.0 }
        var base = parseFloat(baseSpeed.text)
        if (!isNaN(base) && base > 0)
            athlete.speed_profile = { base_speed_kmh: base, confidence: "medium",
                                      model_source: "personal", n_recent_rides: 0 }

        var stopMin = parseFloat(stopMinutes.text)
        var stopSec = (!isNaN(stopMin) && stopMin > 0) ? stopMin * 60 : 0
        // per-leg stop schedule; build_timeline forces the finish leg to 0. Pad generously.
        var stops = []
        for (var k = 0; k < cutoffs.length + 2; k++) stops.push(stopSec)

        var payload = {
            event: { name: eventName.text || "Race", event_type: "BRM",
                     start_dt: startIso(), gpx: gpxText, points: [], cutoffs: cutoffs },
            athlete: athlete,
            bike: { bike_weight_kg: 10.0, load_weight_kg: 5.0, bike_type: "road" },
            stops_s: stops
        }

        busy = true
        statusMsg = qsTr("Computing timeline…")
        api("POST", "/api/race/timeline", payload, function(status, res) {
            busy = false
            if (status === 200 && res && res.ok && res.timeline) {
                timeline = res.timeline
                routeDistanceKm = timeline.distance_km
                basePayload = payload            // baseline for what-if diffs
                scenario = null                 // reset any prior scenario
                whatifSpeed = 0; whatifStopMin = 0; whatifSleepH = 0
                statusMsg = ""
                weather = null
                fetchWeather()                  // weather + daylight at each control's ETA
            } else {
                statusMsg = qsTr("Error: ") + ((res && res.error) ? res.error : status)
            }
        })
    }

    // Weather + daylight at each control's real arrival time (online; Open-Meteo via backend).
    function fetchWeather() {
        if (!timeline || !gpxText) return
        var ctrls = []
        for (var i = 0; i < timeline.controls.length; i++)
            ctrls.push({ label: timeline.controls[i].label,
                         distance_km: timeline.controls[i].distance_km,
                         arrival_dt: timeline.controls[i].arrival_dt })
        api("POST", "/api/race/weather", { gpx: gpxText, controls: ctrls }, function(status, res) {
            weather = (status === 200 && res && res.ok) ? res : null
        })
    }
    // weather row for control index i (weather.controls is index-aligned with timeline.controls)
    function wxFor(i) {
        return (weather && weather.controls && i < weather.controls.length) ? weather.controls[i] : null
    }
    function windColor(rel) {
        return rel === "headwind" ? marginBad : (rel === "tailwind" ? marginGood : "#e0912f")
    }

    // Re-run the timeline with the what-if knobs layered onto the baseline payload; the panel
    // shows the scenario vs the baseline. No new backend logic - the same /api/race/timeline.
    function applyWhatif() {
        if (!basePayload || !timeline) return
        var p = JSON.parse(JSON.stringify(basePayload))
        if (whatifSpeed !== 0 && p.athlete && p.athlete.speed_profile)
            p.athlete.speed_profile.base_speed_kmh += whatifSpeed
        if (whatifStopMin !== 0 && p.stops_s)
            for (var i = 0; i < p.stops_s.length; i++)
                p.stops_s[i] = Math.max(0, p.stops_s[i] + whatifStopMin * 60)
        if (whatifSleepH > 0) p.sleep = { enabled: true, duration_s: whatifSleepH * 3600 }
        api("POST", "/api/race/timeline", p, function(status, res) {
            if (status === 200 && res && res.ok && res.timeline) scenario = res.timeline
        })
    }

    // signed duration delta, e.g. "+52 min" / "−1h10"
    function deltaTxt(scenSec, baseSec) {
        var d = Math.round(scenSec - baseSec)
        if (Math.abs(d) < 30) return qsTr("no change")
        var sign = d > 0 ? "+" : "−"
        return sign + fmtDur(Math.abs(d))
    }
    // count controls that flip from making the cutoff (baseline) to missing it (scenario)
    function newlyAtRisk() {
        if (!scenario || !timeline) return 0
        var n = 0
        for (var i = 0; i < scenario.controls.length && i < timeline.controls.length; i++) {
            var b = timeline.controls[i].margin_s, s = scenario.controls[i].margin_s
            if (b !== null && s !== null && b >= 0 && s < 0) n++
        }
        return n
    }

    // duration seconds -> "13h42"
    function fmtDur(sec) {
        if (sec === null || sec === undefined) return "—"
        var neg = sec < 0; sec = Math.abs(sec)
        var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60)
        return (neg ? "-" : "") + h + "h" + (m < 10 ? "0" + m : m)
    }
    // ISO -> "HH:MM" (and a day marker if not day 0)
    function fmtClock(iso, startIsoStr) {
        if (!iso) return "—"
        var d = new Date(iso)
        var hh = ("0" + d.getHours()).slice(-2), mm = ("0" + d.getMinutes()).slice(-2)
        return hh + ":" + mm
    }

    ListModel { id: controlsModel }

    ColumnLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingMedium
        spacing: Theme.spacingMedium

        Column {
            Layout.fillWidth: true
            spacing: 2
            Text { text: qsTr("Race Planner"); color: Theme.text
                   font.pixelSize: Theme.fontSizeTitle; font.weight: Font.Bold }
            Text { text: qsTr("BRM / ultra timeline with cutoff margins"); color: Theme.mutedText
                   font.pixelSize: Theme.fontSizeCaption }
        }

        Flickable {
            Layout.fillWidth: true
            Layout.fillHeight: true
            contentHeight: form.implicitHeight
            clip: true
            ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

            ColumnLayout {
                id: form
                width: parent.width
                spacing: Theme.spacingSmall

                // --- Event basics ---
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSmall
                    RoundedTextField { id: eventName; Layout.fillWidth: true
                                       placeholderText: qsTr("Event name (e.g. BRM 300 Lille)") }
                }
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSmall
                    RoundedTextField { id: startDate; Layout.preferredWidth: 140
                                       placeholderText: qsTr("YYYY-MM-DD")
                                       text: new Date().toISOString().split("T")[0] }
                    RoundedTextField { id: startTime; Layout.preferredWidth: 90
                                       placeholderText: qsTr("HH:MM"); text: "06:00" }
                    Item { Layout.fillWidth: true }
                    RoundedButton { text: qsTr("Load GPX"); onClicked: gpxDialog.open() }
                }
                Text {
                    Layout.fillWidth: true
                    text: gpxName ? (gpxName + (routeDistanceKm > 0 ? "  ·  " + routeDistanceKm + " km" : ""))
                                  : qsTr("No route loaded")
                    color: gpxName ? Theme.text : Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    elide: Text.ElideRight
                }

                // --- Rider + stops ---
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSmall
                    Layout.topMargin: Theme.spacingSmall
                    RoundedTextField { id: baseSpeed; Layout.fillWidth: true
                                       placeholderText: qsTr("Typical flat-road avg km/h (optional)")
                                       inputMethodHints: Qt.ImhFormattedNumbersOnly }
                    RoundedTextField { id: stopMinutes; Layout.preferredWidth: 150
                                       placeholderText: qsTr("Stop/control (min)")
                                       inputMethodHints: Qt.ImhFormattedNumbersOnly }
                }

                // --- Controls ---
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSmall
                    Text { text: qsTr("Controls & cutoffs"); color: Theme.text
                           font.pixelSize: Theme.fontSizeLabel; font.weight: Font.Medium }
                    Item { Layout.fillWidth: true }
                    RoundedButton { text: qsTr("+ Add control")
                        onClicked: controlsModel.append({ label: "", km: "", hours: "" }) }
                }
                // header
                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSmall
                    visible: controlsModel.count > 0
                    Text { text: qsTr("Label"); Layout.fillWidth: true; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    Text { text: qsTr("km"); Layout.preferredWidth: 80; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    Text { text: qsTr("limit h"); Layout.preferredWidth: 80; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    Item { Layout.preferredWidth: 32 }
                }
                Repeater {
                    model: controlsModel
                    delegate: RowLayout {
                        width: form.width
                        spacing: Theme.spacingSmall
                        RoundedTextField {
                            Layout.fillWidth: true
                            text: model.label
                            placeholderText: qsTr("Control %1").arg(index + 1)
                            onTextChanged: controlsModel.setProperty(index, "label", text)
                        }
                        RoundedTextField {
                            Layout.preferredWidth: 80
                            text: model.km; placeholderText: qsTr("km")
                            inputMethodHints: Qt.ImhFormattedNumbersOnly
                            onTextChanged: controlsModel.setProperty(index, "km", text)
                        }
                        RoundedTextField {
                            Layout.preferredWidth: 80
                            text: model.hours; placeholderText: qsTr("h")
                            inputMethodHints: Qt.ImhFormattedNumbersOnly
                            onTextChanged: controlsModel.setProperty(index, "hours", text)
                        }
                        RoundedButton { text: "✕"; Layout.preferredWidth: 32
                                        onClicked: controlsModel.remove(index) }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSmall
                    RoundedButton {
                        text: busy ? qsTr("Computing…") : qsTr("Compute timeline")
                        enabled: !busy && gpxText.length > 0
                        onClicked: computeTimeline()
                    }
                    Text { text: statusMsg; color: statusMsg.indexOf("Error") === 0 ? marginBad : Theme.mutedText
                           font.pixelSize: Theme.fontSizeCaption; Layout.fillWidth: true; wrapMode: Text.WordWrap }
                }

                // --- Results ---
                Rectangle {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSmall
                    visible: timeline && timeline.ok
                    color: Theme.card
                    radius: Theme.radiusCard
                    border.color: Theme.border
                    border.width: 1
                    implicitHeight: results.implicitHeight + Theme.spacingMedium * 2

                    ColumnLayout {
                        id: results
                        anchors.fill: parent
                        anchors.margins: Theme.spacingMedium
                        spacing: Theme.spacingSmall

                        // summary row
                        Flow {
                            Layout.fillWidth: true
                            spacing: Theme.spacingLarge
                            Column { Text { text: qsTr("FINISH"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                     Text { text: timeline ? fmtClock(timeline.finish_eta_dt) : "—"; color: Theme.text
                                            font.pixelSize: Theme.fontSizeTitle; font.weight: Font.Bold } }
                            Column { Text { text: qsTr("MOVING"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                     Text { text: timeline ? fmtDur(timeline.moving_time_s) : "—"; color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle } }
                            Column { Text { text: qsTr("STOPS"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                     Text { text: timeline ? fmtDur(timeline.stop_time_s) : "—"; color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle } }
                            Column { Text { text: qsTr("ELAPSED"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                     Text { text: timeline ? fmtDur(timeline.elapsed_time_s) : "—"; color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle } }
                            Column { Text { text: qsTr("WORST MARGIN"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                     Text { text: (timeline && timeline.worst_margin_s !== null) ? fmtDur(timeline.worst_margin_s) : "—"
                                            color: (timeline && timeline.worst_margin_s !== null && timeline.worst_margin_s < 0) ? marginBad : marginGood
                                            font.pixelSize: Theme.fontSizeSubtitle; font.weight: Font.Bold } }
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: timeline && timeline.model_source === "placeholder"
                            text: qsTr("Speed is a generic estimate — enter your typical average for a personal prediction.")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: timeline && timeline.sleep_suggested_s > 0
                            text: qsTr("Long ride — consider planning about %1 of sleep.").arg(timeline ? fmtDur(timeline.sleep_suggested_s) : "")
                            color: Theme.text; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
                        }

                        // weather summary + daylight verdict (online; may lag the timeline by a moment)
                        Text {
                            Layout.fillWidth: true
                            visible: weather && weather.summary
                            text: weather && weather.summary
                                  ? qsTr("Weather: %1–%2°C · wind ≤%3 km/h%4 · %5")
                                    .arg(weather.summary.temp_min_c).arg(weather.summary.temp_max_c)
                                    .arg(weather.summary.wind_max_kmh)
                                    .arg(weather.summary.rain_max_mm > 0 ? qsTr(" · rain ≤%1 mm").arg(weather.summary.rain_max_mm) : "")
                                    .arg(weather.verdict || "")
                                  : ""
                            color: Theme.text; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

                        // per-control table header
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            Text { text: qsTr("Control"); Layout.fillWidth: true; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("km"); Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("arrive"); Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("leg"); Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("km/h"); Layout.preferredWidth: 50; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("margin"); Layout.preferredWidth: 66; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("°C"); Layout.preferredWidth: 40; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; visible: weather }
                            Text { text: qsTr("wind/sky"); Layout.preferredWidth: 84; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; visible: weather }
                        }
                        Repeater {
                            model: timeline ? timeline.controls : []
                            delegate: RowLayout {
                                width: results.width
                                spacing: Theme.spacingSmall
                                Text { text: modelData.label; Layout.fillWidth: true; color: Theme.text; font.pixelSize: Theme.fontSizeCaption; elide: Text.ElideRight }
                                Text { text: modelData.distance_km; Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.text; font.pixelSize: Theme.fontSizeCaption }
                                Text { text: fmtClock(modelData.arrival_dt); Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.text; font.pixelSize: Theme.fontSizeCaption }
                                Text { text: fmtDur(modelData.moving_time_s); Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                Text { text: modelData.avg_speed_kmh; Layout.preferredWidth: 50; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                Text {
                                    Layout.preferredWidth: 66; horizontalAlignment: Text.AlignRight
                                    text: modelData.margin_s === null ? "—" : fmtDur(modelData.margin_s)
                                    color: modelData.margin_s === null ? Theme.mutedText
                                          : (modelData.margin_s < 0 ? marginBad : marginGood)
                                    font.pixelSize: Theme.fontSizeCaption; font.weight: Font.Medium
                                }
                                Text {
                                    Layout.preferredWidth: 40; horizontalAlignment: Text.AlignRight
                                    visible: weather
                                    text: { var w = wxFor(index); return w ? Math.round(w.temp_c) + "°" : "" }
                                    color: Theme.text; font.pixelSize: Theme.fontSizeCaption
                                }
                                RowLayout {
                                    Layout.preferredWidth: 84; spacing: 4
                                    visible: weather
                                    Text {
                                        text: { var w = wxFor(index); return w ? w.wind_rel.charAt(0).toUpperCase() + w.wind_rel.slice(1) : "" }
                                        color: { var w = wxFor(index); return w ? windColor(w.wind_rel) : Theme.mutedText }
                                        font.pixelSize: Theme.fontSizeCaption
                                    }
                                    Text {
                                        text: { var w = wxFor(index); return (w && w.is_dark) ? qsTr("night") : "" }
                                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                    }
                                }
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: timeline && timeline.per_control_provisional
                            text: qsTr("Per-control times are provisional (segment-level calibration pending).")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
                        }
                    }
                }

                // --- What if… ---
                Rectangle {
                    id: whatifCard
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSmall
                    visible: timeline && timeline.ok
                    color: Theme.card
                    radius: Theme.radiusCard
                    border.color: Theme.border
                    border.width: 1
                    implicitHeight: whatif.implicitHeight + Theme.spacingMedium * 2

                    property bool hasBase: root.basePayload && root.basePayload.athlete && root.basePayload.athlete.speed_profile

                    ColumnLayout {
                        id: whatif
                        anchors.fill: parent
                        anchors.margins: Theme.spacingMedium
                        spacing: Theme.spacingSmall

                        Text { text: qsTr("What if…"); color: Theme.text; font.weight: Font.Bold }

                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            Text { text: qsTr("Speed"); color: Theme.text; font.pixelSize: Theme.fontSizeCaption; Layout.preferredWidth: 70 }
                            RoundedButton { text: "−"; Layout.preferredWidth: 34; enabled: whatifCard.hasBase
                                onClicked: { root.whatifSpeed -= 1; root.applyWhatif() } }
                            Text { text: (root.whatifSpeed > 0 ? "+" : "") + root.whatifSpeed + " km/h"; color: Theme.text
                                   font.pixelSize: Theme.fontSizeCaption; Layout.preferredWidth: 74; horizontalAlignment: Text.AlignHCenter }
                            RoundedButton { text: "+"; Layout.preferredWidth: 34; enabled: whatifCard.hasBase
                                onClicked: { root.whatifSpeed += 1; root.applyWhatif() } }
                            Text { visible: !whatifCard.hasBase; text: qsTr("(enter a typical speed above)")
                                   color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; Layout.fillWidth: true }
                            Item { Layout.fillWidth: true; visible: whatifCard.hasBase }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            Text { text: qsTr("Stops"); color: Theme.text; font.pixelSize: Theme.fontSizeCaption; Layout.preferredWidth: 70 }
                            RoundedButton { text: "−"; Layout.preferredWidth: 34; onClicked: { root.whatifStopMin -= 15; root.applyWhatif() } }
                            Text { text: (root.whatifStopMin > 0 ? "+" : "") + root.whatifStopMin + " min/ctrl"; color: Theme.text
                                   font.pixelSize: Theme.fontSizeCaption; Layout.preferredWidth: 96; horizontalAlignment: Text.AlignHCenter }
                            RoundedButton { text: "+"; Layout.preferredWidth: 34; onClicked: { root.whatifStopMin += 15; root.applyWhatif() } }
                            Item { Layout.fillWidth: true }
                        }
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            Text { text: qsTr("Sleep"); color: Theme.text; font.pixelSize: Theme.fontSizeCaption; Layout.preferredWidth: 70 }
                            RoundedButton { text: "−"; Layout.preferredWidth: 34; onClicked: { root.whatifSleepH = Math.max(0, root.whatifSleepH - 0.5); root.applyWhatif() } }
                            Text { text: "+" + root.whatifSleepH + " h"; color: Theme.text
                                   font.pixelSize: Theme.fontSizeCaption; Layout.preferredWidth: 74; horizontalAlignment: Text.AlignHCenter }
                            RoundedButton { text: "+"; Layout.preferredWidth: 34; onClicked: { root.whatifSleepH += 0.5; root.applyWhatif() } }
                            Item { Layout.fillWidth: true }
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border; visible: root.scenario }

                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingLarge; visible: root.scenario
                            Column {
                                Text { text: qsTr("NEW FINISH"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                Text { text: root.scenario ? fmtClock(root.scenario.finish_eta_dt) : "—"; color: Theme.text
                                       font.pixelSize: Theme.fontSizeSubtitle; font.weight: Font.Bold }
                            }
                            Column {
                                Text { text: qsTr("Δ FINISH"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                Text { text: root.scenario ? deltaTxt(root.scenario.elapsed_time_s, timeline.elapsed_time_s) : "—"
                                       color: (root.scenario && root.scenario.elapsed_time_s > timeline.elapsed_time_s + 30) ? marginBad : marginGood
                                       font.pixelSize: Theme.fontSizeSubtitle }
                            }
                            Column {
                                Text { text: qsTr("Δ WORST MARGIN"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                Text {
                                    text: (root.scenario && root.scenario.worst_margin_s !== null && timeline.worst_margin_s !== null)
                                          ? deltaTxt(root.scenario.worst_margin_s, timeline.worst_margin_s) : "—"
                                    color: (root.scenario && root.scenario.worst_margin_s !== null && root.scenario.worst_margin_s < 0) ? marginBad : Theme.text
                                    font.pixelSize: Theme.fontSizeSubtitle
                                }
                            }
                        }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            visible: root.scenario && newlyAtRisk() > 0
                            text: qsTr("⚠ %1 control(s) would now miss the cutoff.").arg(newlyAtRisk())
                            color: marginBad; font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                }
            }
        }
    }

    FileDialog {
        id: gpxDialog
        title: qsTr("Choose a GPX route")
        nameFilters: [qsTr("GPX files (*.gpx)"), qsTr("All files (*)")]
        onAccepted: root.loadGpx(selectedFile)
    }
}
