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
// and shows the deltas). Water/food/services (POIs) live on the Route page and are shared via
// PlanStore.pois, which feeds the resupply summary + critical points here. Not here yet: save/load.
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
    property var sleepPlan: null     // race_sleep result: circadian sleep windows
    property var alerts: null        // race_alerts result: ranked critical points
    // POIs/resupply now live on the Route page and are shared via PlanStore.pois.
    property string calibNote: ""    // feedback after calibrating base_speed from a ride
    property var calibratedProfile: null  // set when base_speed came from a ride (personal), else null
    property bool stopUserSet: false   // the rider typed a stop-time estimate (learn from it)
    property bool autoStopDone: false  // pre-filled the stop estimate from memory once this route
    property var controlOverrides: ({})  // {controlIndex: seconds} manual per-control stop overrides
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
        // Shared sticky route: a GPX loaded here also becomes the Plan (Route) page's route, and
        // survives navigation. PlanStore is a singleton that outlives this page's Loader.
        PlanStore.plannedGpx = gpx
        PlanStore.routeName = gpxName
        PlanStore.pois = null            // new route -> stale services cleared (shared w/ Route page)
        controlOverrides = ({})          // new route -> drop per-control stop overrides
        timeline = null
        // new route -> re-suggest the stop estimate for its distance from learned memory
        autoStopDone = false
        stopUserSet = false
        stopTotalH.text = ""
        statusMsg = qsTr("Reading route…")
        computeTimeline()   // first pass -> reveals distance -> pre-fills stop estimate -> recompute
    }

    // Calibrate base_speed from one ride (a FIT) instead of guessing a number.
    function calibrateFromFit(fileUrl) {
        var p = fileUrl.toString().replace("file://", "")
        calibNote = qsTr("Calibrating from your ride…")
        api("POST", "/api/race/calibrate", { fit_path: decodeURIComponent(p) }, function(status, res) {
            if (status === 200 && res && res.ok && res.profile) {
                calibratedProfile = res.profile
                baseSpeed.text = "" + res.profile.base_speed_kmh
                calibNote = qsTr("Calibrated from your ride (%1 km / %2 m): base %3 km/h.")
                    .arg(res.ride.distance_km).arg(res.ride.ascent_m).arg(res.profile.base_speed_kmh)
                if (gpxText) computeTimeline()   // re-run with the calibrated speed
            } else {
                calibNote = qsTr("Couldn't calibrate: ") + ((res && res.error) ? res.error : status)
            }
        })
    }

    // "HH:MM" -> the first Date strictly after `afterMs` with that clock time (handles multi-day
    // closing times monotonically). Returns null if the text isn't a valid HH:MM.
    function clockToDt(afterMs, hhmm) {
        var m = /^(\d{1,2}):(\d{2})$/.exec(hhmm || "")
        if (!m) return null
        var hh = parseInt(m[1]), mm = parseInt(m[2])
        if (hh > 23 || mm > 59) return null
        var b = new Date(afterMs)
        var d = new Date(b.getFullYear(), b.getMonth(), b.getDate(), hh, mm, 0)
        while (d.getTime() <= afterMs) d = new Date(d.getTime() + 86400000)
        return d
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

        // Checkpoints: the rider types the CLOSING CLOCK TIME (HH:MM) from their brevet card.
        // Resolve each to the first occurrence after the previous cutoff (so multi-day closing
        // times land on the right day without any hours-from-start mental math).
        var cutoffs = []
        var prevMs = startMs
        for (var i = 0; i < controlsModel.count; i++) {
            var c = controlsModel.get(i)
            var km = parseFloat(c.km)
            if (isNaN(km)) continue
            var co = { label: c.label || ("Control " + (i + 1)), distance_km: km }
            var dt = clockToDt(prevMs, (c.hours || "").trim())
            if (dt) { co.cutoff_dt = dt.toISOString(); prevMs = dt.getTime() }
            cutoffs.push(co)
        }

        // Speed profile (audit fix #1/#3): ALWAYS give one so cold-start isn't the flat 15 km/h
        // placeholder. Calibrated-from-a-ride = personal; a typed number = a generic guess (not a
        // "personal calibrated" prediction); nothing typed = a plausible generic default.
        var athlete = { weight_kg: 75.0 }
        var base = parseFloat(baseSpeed.text)
        if (calibratedProfile && !isNaN(base) && base > 0) {
            athlete.speed_profile = calibratedProfile
        } else if (!isNaN(base) && base > 0) {
            athlete.speed_profile = { base_speed_kmh: base, confidence: "low",
                                      model_source: "generic", n_recent_rides: 0 }
        } else {
            athlete.speed_profile = { base_speed_kmh: 22.0, confidence: "low",
                                      model_source: "generic", n_recent_rides: 0 }
        }

        var payload = {
            event: { name: eventName.text || "Race", event_type: "BRM",
                     start_dt: startIso(), gpx: gpxText, points: [], cutoffs: cutoffs },
            athlete: athlete,
            bike: { bike_weight_kg: 10.0, load_weight_kg: 5.0, bike_type: "road" }
        }
        // Stops: the rider's own TOTAL off-bike estimate for this distance (food, rest, sleep) —
        // that IS the budget, so sleep is inside it (we don't add circadian sleep on top). Empty
        // field falls back to the learned/default ratio.
        var T = parseFloat(stopTotalH.text)
        if (!isNaN(T) && T >= 0 && stopTotalH.text.length > 0) {
            payload.stop_total_s = T * 3600
        } else {
            payload.stop_profile = { ratio: 0.18, source: "default", confidence: "low" }
        }
        if (Object.keys(controlOverrides).length > 0)
            payload.control_overrides = controlOverrides   // expert per-control stop overrides

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
                sleepPlan = null
                alerts = null
                // First time on a fresh route with no stop estimate: pre-fill from the learned
                // per-distance memory, then recompute once with it.
                if (!stopTotalH.text.length && !autoStopDone) {
                    autoStopDone = true
                    api("POST", "/api/race/stop-suggest", { distance_km: timeline.distance_km },
                        function(s, r) {
                            if (s === 200 && r && r.ok) stopTotalH.text = "" + r.hours
                            computeTimeline()
                        })
                    return
                }
                // Learn from the rider only when THEY set the estimate (not the auto-prefill).
                if (stopUserSet && stopTotalH.text.length) {
                    api("POST", "/api/race/stop-record",
                        { distance_km: timeline.distance_km, hours: parseFloat(stopTotalH.text) },
                        function() {})
                }
                fetchWeather()                  // weather + daylight, then sleep + alerts (chained)
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
            fetchSleep()    // sleep plan can now fold in per-km temperature (cold)
            fetchAlerts()   // climbs + cutoff + darkness (water/food added later if POIs fetched)
        })
    }

    // Circadian sleep windows: where/when to sleep (dark + moonlight + body clock + cold), safe
    // against cutoffs. Offline via the backend. Uses the timeline's control ETAs + margins.
    function fetchSleep() {
        if (!timeline || !gpxText) { sleepPlan = null; return }
        var ctrls = []
        for (var i = 0; i < timeline.controls.length; i++)
            ctrls.push({ label: timeline.controls[i].label,
                         distance_km: timeline.controls[i].distance_km,
                         arrival_dt: timeline.controls[i].arrival_dt,
                         margin_s: timeline.controls[i].margin_s })
        var payload = { gpx: gpxText, controls: ctrls, start_dt: startIso(),
                        suggested_total_s: timeline.sleep_suggested_s || 0 }
        if (weather && weather.controls) payload.weather = weather.controls
        api("POST", "/api/race/sleep", payload, function(status, res) {
            sleepPlan = (status === 200 && res && res.ok) ? res : null
            foldSleepIntoEta()   // planned sleep must count toward the finish ETA
        })
    }

    // Re-run the timeline with the planned sleep windows folded in, so the finish ETA + elapsed +
    // per-control margins all include sleep (the whole point: a realistic multi-day finish time).
    // Does NOT re-fetch weather/sleep/pois — avoids a loop.
    function foldSleepIntoEta() {
        if (!basePayload) return
        // If the rider gave a TOTAL off-bike time, sleep is already inside it — don't add it again;
        // the sleep plan then only advises WHEN/WHERE to spend that sleep. Only fold sleep as extra
        // time in the ratio-based path (no user total).
        if (basePayload.stop_total_s) return
        var windows = (sleepPlan && sleepPlan.windows) ? sleepPlan.windows : []
        var p = JSON.parse(JSON.stringify(basePayload))
        p.sleep_windows = windows.map(function(w) { return { km: w.km, duration_s: w.duration_s } })
        // keep basePayload in sync so what-if diffs are relative to the sleep-inclusive plan
        basePayload = p
        if (p.sleep_windows.length === 0) return   // nothing to fold; pass-1 timeline already shown
        api("POST", "/api/race/timeline", p, function(status, res) {
            if (status === 200 && res && res.ok && res.timeline) timeline = res.timeline
        })
    }

    // Consolidated critical points: climbs (from the route) + cutoff/water/food/darkness folded in.
    function fetchAlerts() {
        if (!timeline || !gpxText) { alerts = null; return }
        var body = { gpx: gpxText, timeline: timeline }
        if (weather) body.weather = weather
        if (PlanStore.pois) body.pois = PlanStore.pois   // water/food gaps found on the Route page
        api("POST", "/api/race/alerts", body, function(status, res) {
            alerts = (status === 200 && res && res.ok) ? res : null
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
        if (whatifStopMin !== 0) {
            if (p.stop_total_s !== undefined)
                p.stop_total_s = Math.max(0, p.stop_total_s + whatifStopMin * 60)
            else if (p.stops_s)
                for (var i = 0; i < p.stops_s.length; i++)
                    p.stops_s[i] = Math.max(0, p.stops_s[i] + whatifStopMin * 60)
        }
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
    // ISO -> "Sun 03:30" (weekday helps on multi-day rides)
    function fmtClockDay(iso) {
        if (!iso) return "—"
        var d = new Date(iso)
        var days = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"]
        return days[d.getDay()] + " " + ("0" + d.getHours()).slice(-2) + ":" + ("0" + d.getMinutes()).slice(-2)
    }
    // the plain-language answer atop the result (audit's highest-value change)
    function verdictText() {
        if (!timeline) return ""
        var s = qsTr("You'd finish ~%1").arg(fmtClockDay(timeline.finish_eta_dt))
        var fin = null
        for (var i = timeline.controls.length - 1; i >= 0; i--)
            if (timeline.controls[i].margin_s !== null) { fin = timeline.controls[i]; break }
        if (fin) {
            var m = fin.margin_s
            s += m >= 0 ? qsTr(" — about %1 inside the limit.").arg(fmtDur(m))
                        : qsTr(" — about %1 OVER the limit.").arg(fmtDur(-m))
        } else {
            s += "."
        }
        if (timeline.worst_margin_s !== null && timeline.worst_margin_control) {
            var w = timeline.worst_margin_s
            s += qsTr(" Tightest: %1, %2.").arg(timeline.worst_margin_control)
                  .arg(w >= 0 ? qsTr("%1 spare").arg(fmtDur(w)) : qsTr("%1 short").arg(fmtDur(-w)))
        }
        return s
    }
    // Plain-text roadbook / cue sheet of the whole plan, for printing or a bar bag.
    function buildRoadbook() {
        if (!timeline) return ""
        var L = []
        L.push((eventName.text || "Race") + " — " + timeline.distance_km + " km, " + timeline.total_ascent_m + " m climb")
        L.push("Start: " + startIso().replace("T", " ").slice(0, 16))
        L.push(verdictText())
        L.push("Moving " + fmtDur(timeline.moving_time_s) + "  ·  stops " + fmtDur(timeline.stop_time_s)
               + (timeline.sleep_time_s > 0 ? "  ·  sleep " + fmtDur(timeline.sleep_time_s) : "")
               + "  ·  elapsed " + fmtDur(timeline.elapsed_time_s))
        L.push("")
        L.push("checkpoint            km    arrive   ride    km/h  stop   margin   temp  wind")
        for (var i = 0; i < timeline.controls.length; i++) {
            var c = timeline.controls[i]
            var w = wxFor(i)
            function pad(s, n) { s = "" + s; while (s.length < n) s += " "; return s }
            function padL(s, n) { s = "" + s; while (s.length < n) s = " " + s; return s }
            var m = c.margin_s === null ? "—" : (c.margin_s < 0 ? "-" + fmtDur(-c.margin_s) : "+" + fmtDur(c.margin_s))
            L.push(pad(c.label, 20) + " " + padL(c.distance_km, 5) + "  " + pad(fmtClock(c.arrival_dt), 7)
                   + " " + padL(fmtDur(c.moving_time_s), 6) + "  " + padL(c.avg_speed_kmh, 5)
                   + " " + padL(Math.round(c.stop_s / 60) + "m", 5) + " " + padL(m, 7)
                   + (w ? "  " + padL(Math.round(w.temp_c) + "°", 4) + "  " + w.wind_rel + (w.is_dark ? " (dark)" : "") : ""))
        }
        if (PlanStore.pois && PlanStore.pois.summary && PlanStore.pois.summary.length) {
            L.push(""); L.push("Resupply:")
            for (var j = 0; j < PlanStore.pois.summary.length; j++) L.push("  " + PlanStore.pois.summary[j])
        }
        if (sleepPlan && sleepPlan.windows && sleepPlan.windows.length) {
            L.push(""); L.push("Sleep:")
            for (var k = 0; k < sleepPlan.windows.length; k++) {
                var s = sleepPlan.windows[k]
                L.push("  " + s.start_local + "–" + s.end_local + " (~" + (s.duration_s / 3600).toFixed(1) + "h) near km " + s.km + " — " + s.reason)
            }
        }
        if (alerts && alerts.alerts && alerts.alerts.length) {
            L.push(""); L.push("Critical points:")
            for (var a = 0; a < alerts.alerts.length; a++)
                L.push("  [" + alerts.alerts[a].severity + "] km " + Math.round(alerts.alerts[a].km) + " — " + alerts.alerts[a].text)
        }
        return L.join("\n")
    }
    function verdictIsBad() {
        if (!timeline) return false
        for (var i = timeline.controls.length - 1; i >= 0; i--)
            if (timeline.controls[i].margin_s !== null) return timeline.controls[i].margin_s < 0
        return false
    }

    // Adopt a route already loaded on the Plan (Route) page, so the GPX "sticks" across screens.
    Component.onCompleted: {
        if (!gpxText && PlanStore.hasRoute) {
            gpxText = PlanStore.plannedGpx
            gpxName = PlanStore.routeName
            computeTimeline()
        }
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
            Text { text: qsTr("Plan when you'll reach each checkpoint — and whether you'll beat the time limits."); color: Theme.mutedText
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
                    Text { text: qsTr("Start"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                           Layout.preferredWidth: 40 }
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
                                       placeholderText: qsTr("Your steady speed on a flat road, e.g. 25 km/h")
                                       inputMethodHints: Qt.ImhFormattedNumbersOnly
                                       onTextEdited: { root.calibratedProfile = null; root.calibNote = "" } }
                    RoundedButton { text: qsTr("From a ride"); onClicked: fitDialog.open() }
                    RoundedTextField { id: stopTotalH; Layout.preferredWidth: 200
                                       placeholderText: qsTr("Total off bike, h (food/rest/sleep)")
                                       inputMethodHints: Qt.ImhFormattedNumbersOnly
                                       onTextEdited: root.stopUserSet = true }
                }
                Text {
                    Layout.fillWidth: true
                    visible: calibNote.length > 0
                    text: calibNote; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                    wrapMode: Text.WordWrap
                }

                // --- Controls ---
                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSmall
                    Text { text: qsTr("Checkpoints & time limits"); color: Theme.text
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
                    Text { text: qsTr("must arrive by"); Layout.preferredWidth: 80; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
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
                            Layout.preferredWidth: 90
                            text: model.hours; placeholderText: qsTr("by HH:MM")
                            onTextChanged: controlsModel.setProperty(index, "hours", text)
                        }
                        RoundedButton { text: "✕"; Layout.preferredWidth: 32
                                        onClicked: controlsModel.remove(index) }
                    }
                }
                Text {
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                    visible: controlsModel.count === 0
                    text: qsTr("Add your checkpoints (km + the time they close, from your brevet card) to see cutoff margins — or just Compute for the finish time.")
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                }

                RowLayout {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSmall
                    RoundedButton {
                        text: busy ? qsTr("Computing…") : qsTr("Compute timeline")
                        enabled: !busy && gpxText.length > 0
                        onClicked: computeTimeline()
                    }
                    RoundedButton {
                        text: qsTr("Export roadbook")
                        visible: timeline && timeline.ok
                        onClicked: roadbookDialog.open()
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

                        // plain-language verdict — the answer, before the numbers
                        Text {
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            text: verdictText()
                            color: verdictIsBad() ? marginBad : marginGood
                            font.pixelSize: Theme.fontSizeSubtitle
                            font.weight: Font.Bold
                        }

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
                            Column { visible: timeline && timeline.sleep_time_s > 0
                                     Text { text: qsTr("SLEEP"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                     Text { text: timeline ? fmtDur(timeline.sleep_time_s) : "—"; color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle } }
                            Column { Text { text: qsTr("ELAPSED"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                     Text { text: timeline ? fmtDur(timeline.elapsed_time_s) : "—"; color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle } }
                            Column { Text { text: qsTr("TIGHTEST CUTOFF"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
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
                            // Only the generic tier hint until the circadian plan resolves; once it
                            // returns windows the Sleep plan block below is authoritative, and if it
                            // returns none (e.g. dawn finish) we say nothing here (audit fix #4).
                            visible: timeline && timeline.sleep_suggested_s > 0 && !sleepPlan
                            text: qsTr("Long ride — a sleep stop may be worth planning; checking the best window…")
                            color: Theme.text; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
                        }
                        Text {
                            Layout.fillWidth: true
                            // circadian plan ran but found no worthwhile window (short enough / dawn finish)
                            visible: timeline && timeline.sleep_suggested_s > 0 && sleepPlan
                                     && (!sleepPlan.windows || sleepPlan.windows.length === 0)
                            text: qsTr("You can ride this one through — no sleep stop needed.")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
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
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            visible: weather && weather.summary
                            text: qsTr("Head/tailwind is shown per checkpoint but is not yet folded into the times.")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }

                        Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }

                        // per-control table header
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            Text { text: qsTr("Control"); Layout.fillWidth: true; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("km"); Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("arrive"); Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("ride"); Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("km/h"); Layout.preferredWidth: 50; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("stop m"); Layout.preferredWidth: 52; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
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
                                // editable per-control stop (minutes); blank = model's own split. Finish has no stop.
                                RoundedTextField {
                                    Layout.preferredWidth: 52
                                    enabled: index < (timeline ? timeline.controls.length - 1 : 0)
                                    text: enabled ? "" + Math.round(modelData.stop_s / 60) : "—"
                                    horizontalAlignment: Text.AlignRight
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    onEditingFinished: {
                                        var v = parseFloat(text)
                                        if (!isNaN(v) && v >= 0) {
                                            var o = root.controlOverrides; o[index] = v * 60
                                            root.controlOverrides = o; computeTimeline()
                                        }
                                    }
                                }
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
                            text: qsTr("Per-control times are approximate.")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
                        }

                        // --- Sleep plan (circadian: dark + moonlight + body clock + cold) ---
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.topMargin: Theme.spacingSmall
                            spacing: 4
                            visible: sleepPlan && sleepPlan.windows && sleepPlan.windows.length > 0

                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                            Text { text: qsTr("Sleep plan"); color: Theme.text; font.weight: Font.Bold
                                   font.pixelSize: Theme.fontSizeCaption }
                            Repeater {
                                model: sleepPlan ? sleepPlan.windows : []
                                delegate: Text {
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    font.pixelSize: Theme.fontSizeCaption
                                    color: modelData.cutoff_ok ? Theme.text : marginBad
                                    text: qsTr("Night %1: sleep %2–%3 (~%4h) near km %5 (%6) — %7%8%9")
                                        .arg(modelData.night)
                                        .arg(modelData.start_local).arg(modelData.end_local)
                                        .arg((modelData.duration_s / 3600).toFixed(1))
                                        .arg(modelData.km).arg(modelData.near_control || "")
                                        .arg(modelData.reason)
                                        .arg(modelData.temp_c !== null ? (", " + Math.round(modelData.temp_c) + "°C") : "")
                                        .arg(modelData.cutoff_ok ? "" : qsTr("  ⚠ tightens a cutoff"))
                                }
                            }
                            Text {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                text: qsTr("Suggestion only — placed in the hours worst for riding (darkest, coldest, body-clock low).")
                            }
                        }

                        // --- Resupply gaps (found on the Route page, shared via PlanStore) ---
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.topMargin: Theme.spacingSmall; spacing: 2
                            visible: PlanStore.pois && PlanStore.pois.summary && PlanStore.pois.summary.length > 0
                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                            Text { text: qsTr("Resupply"); color: Theme.text; font.weight: Font.Bold
                                   font.pixelSize: Theme.fontSizeCaption }
                            Repeater {
                                model: PlanStore.pois ? PlanStore.pois.summary : []
                                delegate: Text {
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    text: modelData
                                    color: modelData.indexOf("⚠") >= 0 ? marginBad : Theme.text
                                    font.pixelSize: Theme.fontSizeCaption
                                }
                            }
                        }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            visible: timeline && !(PlanStore.pois && PlanStore.pois.summary && PlanStore.pois.summary.length > 0)
                            text: qsTr("Tip: find water, food & services on the Route page — they'll appear here and in the critical points.")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                            Layout.topMargin: Theme.spacingSmall
                        }

                        // --- Critical points (climbs + cutoff + water/food + darkness) ---
                        ColumnLayout {
                            Layout.fillWidth: true; Layout.topMargin: Theme.spacingSmall; spacing: 2
                            visible: alerts && alerts.alerts && alerts.alerts.length > 0
                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: qsTr("Critical points"); color: Theme.text; font.weight: Font.Bold
                                       font.pixelSize: Theme.fontSizeCaption }
                                Item { Layout.fillWidth: true }
                                RoundedCheckBox { id: alertsImportantOnly; text: qsTr("Only warnings")
                                                  checked: false }
                            }
                            Repeater {
                                model: alerts ? alerts.alerts : []
                                delegate: RowLayout {
                                    width: results.width; spacing: Theme.spacingSmall
                                    visible: !alertsImportantOnly.checked || modelData.severity !== "info"
                                    Text { text: "km " + Math.round(modelData.km); Layout.preferredWidth: 56
                                           color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                    Text {
                                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                                        text: modelData.text; font.pixelSize: Theme.fontSizeCaption
                                        color: modelData.severity === "critical" ? marginBad
                                              : (modelData.severity === "warn" ? "#e0912f" : Theme.mutedText)
                                    }
                                }
                            }
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
                            Text { text: (root.whatifStopMin > 0 ? "+" : "") + root.whatifStopMin + " min"; color: Theme.text
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
                                Text { text: qsTr("finish change"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                Text { text: root.scenario ? deltaTxt(root.scenario.elapsed_time_s, timeline.elapsed_time_s) : "—"
                                       color: (root.scenario && root.scenario.elapsed_time_s > timeline.elapsed_time_s + 30) ? marginBad : marginGood
                                       font.pixelSize: Theme.fontSizeSubtitle }
                            }
                            Column {
                                Text { text: qsTr("cutoff change"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
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

    FileDialog {
        id: fitDialog
        title: qsTr("Choose a ride (FIT) to calibrate your speed")
        nameFilters: [qsTr("FIT files (*.fit *.FIT)"), qsTr("All files (*)")]
        onAccepted: root.calibrateFromFit(selectedFile)
    }

    FileDialog {
        id: roadbookDialog
        title: qsTr("Save roadbook")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("Text files (*.txt)"), qsTr("All files (*)")]
        currentFile: "file://" + (eventName.text ? eventName.text.replace(/[^\w-]+/g, "_") : "roadbook") + ".txt"
        onAccepted: {
            var err = LocalFileService.saveText(selectedFile, buildRoadbook())
            statusMsg = err && err.length ? (qsTr("Save failed: ") + err) : qsTr("Roadbook saved.")
        }
    }
}
