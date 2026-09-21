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
    // Refill/food gaps counting only places open at the planned ETAs (race_pois.open_refill_analysis).
    property var openGaps: null
    onTimelineChanged: { fetchOpenGaps(); fetchDays() }
    Connections { target: PlanStore; function onPoisChanged() { root.fetchOpenGaps() } }
    function fetchOpenGaps() {
        if (!timeline || !PlanStore.pois || !timeline.controls) { openGaps = null; return }
        var eta = [{ km: 0, dt: timeline.start_dt }]
        for (var i = 0; i < timeline.controls.length; i++)
            eta.push({ km: timeline.controls[i].distance_km, dt: timeline.controls[i].arrival_dt })
        api("POST", "/api/race/pois", { pois: PlanStore.pois, eta: eta }, function(status, res) {
            openGaps = (status === 200 && res && res.ok) ? res : null
        })
    }
    property var sleepOpts: null     // race_sleepopt result: ranked sleep plans
    property bool sleepOptsBusy: false
    function suggestSleep() {
        if (!basePayload) return
        var body = JSON.parse(JSON.stringify(basePayload))
        delete body.sleep_windows; delete body.sleep; delete body.no_ride; delete body.fatigue
        body.habit = { bed_h: hhmmToHours(usualBed.text, 22), wake_h: hhmmToHours(usualWake.text, 6) }
        if (PlanStore.pois) body.pois = PlanStore.pois
        sleepOptsBusy = true; sleepOpts = null
        api("POST", "/api/race/sleep-options", body, function(status, res) {
            sleepOptsBusy = false
            sleepOpts = (status === 200 && res && res.ok) ? res : null
            if (!sleepOpts) statusMsg = qsTr("Couldn't compute sleep options: ") + ((res && res.error) ? res.error : status)
        })
    }
    function useSleepOption(o) {
        noRideOn = true; fatigueOn = true
        noRideFrom.text = o.bed; noRideTo.text = o.wake
        noRideStartH = hhmmToHours(o.bed, 23); noRideEndH = hhmmToHours(o.wake, 3.5)
        computeTimeline()
    }
    property var daysPlan: null      // race_days result: nights, days, comparison with an equal split
    Connections { target: PlanStore; function onPoisChanged() { root.fetchDays() } }
    function fetchDays() {
        if (!timeline || !showResults) { daysPlan = null; return }
        var body = { timeline: timeline }
        if (PlanStore.pois) body.pois = PlanStore.pois
        api("POST", "/api/race/days", body, function(status, res) {
            daysPlan = (status === 200 && res && res.ok && res.nights && res.nights.length > 0) ? res : null
        })
    }
    // Hand the plan's days to the Route page (its "Split into days" then follows where you sleep
    // instead of equal distances). The Route page reads these when it opens.
    function useDaysOnRoute() {
        if (!daysPlan) return
        var b = []
        for (var i = 0; i < daysPlan.days.length - 1; i++) b.push(daysPlan.days[i].to_km * 1000)
        PlanStore.numDays = daysPlan.days.length
        PlanStore.dayBounds = b
        statusMsg = qsTr("Done — open the Route page: its days now end where you sleep.")
    }
    property var weather: null       // race_weather result: per-control temp/wind/rain + daylight
    property var sleepPlan: null     // race_sleep result: circadian sleep windows
    property var alerts: null        // race_alerts result: ranked critical points
    // POIs/resupply now live on the Route page and are shared via PlanStore.pois.
    property string calibNote: ""    // feedback after calibrating base_speed from a ride
    property var calibratedProfile: null  // set when base_speed came from a ride (personal), else null
    // True when the plan is running on the fallback generic pace (no calibration, no typed speed).
    readonly property bool usingGenericPace: {
        if (calibratedProfile) return false
        var b = parseFloat(baseSpeed.text)
        return isNaN(b) || b <= 0
    }
    // Hours the rider never rides (a personal rule, or a curfew): riding stops, the rest is sleep.
    property bool noRideOn: false
    property real noRideStartH: 23
    property real noRideEndH: 3.5
    function hhmmToHours(t, dflt) {
        var m = /^\s*(\d{1,2})(?::(\d{2}))?\s*$/.exec(t || "")
        if (!m) return dflt
        var h = parseInt(m[1]), mi = m[2] ? parseInt(m[2]) : 0
        return (h < 24 && mi < 60) ? h + mi / 60 : dflt
    }
    property bool fatigueOn: false     // model multi-day sleep-debt fatigue (for PBP-length rides)
    // Named off-bike stops the rider plans (meals, a cafe, resupply) - each {label, km, min}. They're
    // drawn from the food/rest budget; whatever's left is spread across the ride (2nd-half weighted).
    property var plannedStops: []
    property bool stopUserSet: false   // the rider typed a stop-time estimate (learn from it)
    property bool autoStopDone: false  // pre-filled the stop estimate from memory once this route
    property var controlOverrides: ({})  // {controlIndex: seconds} manual per-control stop overrides
    property bool windFold: false      // fold the prevailing wind into the ETA (off by default)
    // what-if: the payload that produced `timeline` (the baseline), the adjusted result, and knobs.
    property var basePayload: null
    property var scenario: null
    property int whatifSpeed: 0      // km/h added to base speed
    property int whatifStopMin: 0    // minutes added to each control stop
    property real whatifSleepH: 0    // hours of sleep added on top

    // Guided setup (André, 2026-09-20: "show up kinda like questions, one by one"). One question
    // per step with Back/Next; nothing is shown as "the plan" until the last step's "See my plan",
    // so the confusing "fields visible while it's already computing" is gone. The background
    // computeTimeline() that runs on GPX load only fills distance + the stop suggestion; the weather/
    // sleep/alerts fetch and the results screen wait for showResults (set on See-my-plan).
    readonly property int wizardStepCount: 6
    property int wizardStep: 0
    property bool showResults: false
    onShowResultsChanged: fetchDays()
    readonly property var wizardTitles: [qsTr("Your route"), qsTr("When do you start?"),
        qsTr("How fast do you ride?"), qsTr("Time off the bike"), qsTr("Hours you never ride"),
        qsTr("Checkpoints & time limits")]
    function wizardCanAdvance() {
        if (wizardStep === 0) return gpxText.length > 0   // must have a route to plan
        return true
    }
    function wizardNext() {
        if (wizardStep < wizardStepCount - 1) { wizardStep++; return }
        showResults = true
        computeTimeline()          // now fetches weather/sleep/alerts too (gated on showResults)
    }
    function wizardBack() { if (wizardStep > 0) wizardStep-- }
    function editAnswers() { showResults = false }

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
        PlanStore.reversed = false       // a freshly loaded route starts un-reversed (set BEFORE plannedGpx:
                                         // changing it triggers the POI import, which reads this flag)
        PlanStore.plannedGpx = gpx
        PlanStore.routeName = gpxName
        PlanStore.pois = null            // new route -> stale services cleared (shared w/ Route page)
        controlOverrides = ({})          // new route -> drop per-control stop overrides
        plannedStops = []                // new route -> drop planned meal/cafe stops
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

    // Bulk-add checkpoints pasted as "km, HH:MM" (optionally ", label") per line.
    function pasteControls(txt) {
        var lines = (txt || "").split("\n")
        for (var i = 0; i < lines.length; i++) {
            var ln = lines[i].trim()
            if (!ln) continue
            var m = ln.split(/[,;\t]+/)
            var km = parseFloat(m[0])
            if (isNaN(km)) continue
            var time = m.length > 1 ? m[1].trim() : ""
            var label = m.length > 2 ? m.slice(2).join(" ").trim() : ""
            controlsModel.append({ label: label, km: "" + km, hours: time, opens: "" })
        }
    }

    // Import a whole brevet roadbook (the official control table, e.g. "C1 - FROIDCHAPELLE ...
    // 112,5 ... 12:30") in one shot. `payload` is {text} (pasted table) or {pdf} (a local PDF,
    // parsed with pdftotext by the backend). Replaces the control list with the parsed controls,
    // using the roadbook's OFFICIAL closing time (fermeture) as each "must arrive by".
    function importRoadbook(payload) {
        statusMsg = qsTr("Reading roadbook…")
        api("POST", "/api/race/roadbook", payload, function(status, res) {
            if (status !== 200 || !res || !res.ok || !res.controls) {
                console.warn("[roadbook] import failed: HTTP", status, JSON.stringify(res || {}).slice(0, 200))
                statusMsg = qsTr("Couldn't read roadbook: ") + ((res && res.error) ? res.error : ("no answer from the backend (HTTP " + status + ")"))
                return
            }
            controlsModel.clear()
            var cs = res.controls
            for (var i = 0; i < cs.length; i++) {
                var name = cs[i].name ? (cs[i].label + " " + cs[i].name) : cs[i].label
                // skip the start control (km 0) as a cutoff row; it's the start time, not a checkpoint
                if ((cs[i].km || 0) <= 0.05) continue
                controlsModel.append({ label: name, km: "" + cs[i].km, hours: cs[i].close || "",
                                       opens: cs[i].open || "" })
            }
            statusMsg = qsTr("Imported %1 controls from the roadbook.").arg(controlsModel.count)
            if (gpxText.length > 0) computeTimeline()
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

    // Local-NAIVE ISO ("YYYY-MM-DDTHH:MM:SS", no timezone) to match startIso(). Date.toISOString()
    // returns UTC with a 'Z', which the backend then reads as tz-AWARE while start_dt is naive -
    // subtracting the two threw "can't subtract offset-naive and offset-aware datetimes" (André,
    // 2026-09-20, computing the BRM600 plan). Everything the planner sends must be naive local.
    function toLocalIso(dt) {
        function p(n) { return (n < 10 ? "0" : "") + n }
        return dt.getFullYear() + "-" + p(dt.getMonth() + 1) + "-" + p(dt.getDate())
             + "T" + p(dt.getHours()) + ":" + p(dt.getMinutes()) + ":" + p(dt.getSeconds())
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
        var prevOpenMs = startMs
        for (var i = 0; i < controlsModel.count; i++) {
            var c = controlsModel.get(i)
            var km = parseFloat(c.km)
            if (isNaN(km)) continue
            var co = { label: c.label || ("Control " + (i + 1)), distance_km: km }
            var dt = clockToDt(prevMs, (c.hours || "").trim())
            if (dt) { co.cutoff_dt = toLocalIso(dt); prevMs = dt.getTime() }
            // Opening time (ouverture): the "arrive not before" for a manned control. Resolved the
            // same monotonic way as the closing time (opens increase control-to-control too).
            var od = clockToDt(prevOpenMs, (c.opens || "").trim())
            if (od) { co.open_dt = toLocalIso(od); prevOpenMs = od.getTime() }
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
        // Named planned stops (meals/cafe) are drawn FROM the food/rest budget; the remainder is the
        // "spread" pool distributed across the ride. Sleep is separate (folded in applyFolds).
        var events = []
        var namedSecs = 0
        for (var si = 0; si < plannedStops.length; si++) {
            var km = parseFloat(plannedStops[si].km)
            var mins = parseFloat(plannedStops[si].min)
            if (!isNaN(km) && !isNaN(mins) && mins > 0) {
                events.push({ km: km, duration_s: mins * 60 }); namedSecs += mins * 60
            }
        }
        if (events.length > 0) payload.stop_events = events
        var T = parseFloat(stopTotalH.text)
        if (!isNaN(T) && T >= 0 && stopTotalH.text.length > 0) {
            payload.stop_total_s = Math.max(0, T * 3600 - namedSecs)   // leftover after named stops
        } else {
            payload.stop_profile = { ratio: 0.18, source: "default", confidence: "low" }
        }
        if (fatigueOn)     // sleepiness slowdown: sleep pressure + a body clock shifted to the rider's usual sleep
            payload.fatigue = { enabled: true, model: "twoprocess",
                                bed_h: hhmmToHours(usualBed.text, 22), wake_h: hhmmToHours(usualWake.text, 6) }
        if (noRideOn) {
            noRideStartH = hhmmToHours(noRideFrom.text, noRideStartH)
            noRideEndH = hhmmToHours(noRideTo.text, noRideEndH)
            payload.no_ride = { start_h: noRideStartH, end_h: noRideEndH }
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
                if (showResults)
                    fetchWeather()              // weather + daylight, then sleep + alerts (chained)
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
            applyFolds()   // planned sleep (and prevailing wind, if toggled) must reach the ETA
        })
    }

    // Re-run the timeline with the second-order effects folded into the finish ETA + margins:
    // (a) planned sleep windows (unless the rider's total-off-bike already includes sleep), and
    // (b) an optional prevailing-wind uniform speed shift. Doesn't re-fetch weather/sleep/pois.
    function applyFolds() {
        if (!basePayload) return
        var p = JSON.parse(JSON.stringify(basePayload))
        // Sleep is a BLOCK, separate from the rolling food/rest stops (stop_total_s). The rider sets
        // HOW LONG (sleepH); the circadian planner sets WHERE (windows[].km — dark + body-clock +
        // cutoff-safe). Place the rider's hours at the planner's night control(s), scaled to total
        // sleepH so checkpoints before the block are unaffected and later ones shift by the sleep
        // (André, 2026-09-21: "sleep should appear between the cps, not spread across all").
        var sleepSecs = (parseFloat(sleepH.text) > 0 && !noRideOn) ? parseFloat(sleepH.text) * 3600 : 0
        p.sleep_windows = []
        if (sleepSecs > 0) {
            var ws = (sleepPlan && sleepPlan.windows) ? sleepPlan.windows : []
            if (ws.length > 0) {
                var planned = 0
                for (var wi = 0; wi < ws.length; wi++) planned += ws[wi].duration_s
                var scale = planned > 0 ? sleepSecs / planned : 1
                p.sleep_windows = ws.map(function(w) { return { km: w.km, duration_s: Math.round(w.duration_s * scale) } })
            } else if (timeline && timeline.distance_km > 0) {
                // No circadian window (e.g. weather/sleep not available): drop the block at ~60%
                // of the route as a reasonable overnight fallback.
                p.sleep_windows = [{ km: timeline.distance_km * 0.6, duration_s: sleepSecs }]
            }
        }
        // prevailing wind: net headwind slows (k 0.5), tailwind helps less (k 0.3) — off by default
        p.wind_speed_delta_kmh = 0
        if (windFold && weather && weather.summary && weather.summary.net_head_kmh !== undefined) {
            var net = weather.summary.net_head_kmh
            p.wind_speed_delta_kmh = -(net > 0 ? 0.5 : 0.3) * net
        }
        basePayload = p
        var need = (p.sleep_windows && p.sleep_windows.length > 0)
                   || (p.wind_speed_delta_kmh && Math.abs(p.wind_speed_delta_kmh) > 0.05)
        if (!need) return
        api("POST", "/api/race/timeline", p, function(status, res) {
            if (status === 200 && res && res.ok && res.timeline) timeline = res.timeline
        })
    }

    // --- Planned stops (named meal/cafe stops at a km; the rest of the budget is spread) ---
    function addPlannedStop() {
        var a = plannedStops.slice()
        a.push({ label: qsTr("Stop"), km: timeline ? Math.round(timeline.distance_km / 2) : "", min: "20" })
        plannedStops = a
        if (gpxText) computeTimeline()
    }
    function removePlannedStop(i) {
        var a = plannedStops.slice(); a.splice(i, 1); plannedStops = a
        if (gpxText) computeTimeline()
    }
    function setPlannedStop(i, field, val) {
        var a = plannedStops.slice(); a[i] = Object.assign({}, a[i]); a[i][field] = val; plannedStops = a
    }
    // Seed sensible named stops from the current timeline: lunch (~13:00), dinner (~20:00), and a
    // café mid-second-half — placed at whatever km you're near at that clock time. All editable.
    function suggestStops() {
        if (!timeline || !timeline.controls) return
        function kmAtClock(hh) {
            for (var i = 0; i < timeline.controls.length; i++) {
                var d = new Date(timeline.controls[i].arrival_dt)
                if (d.getHours() >= hh) return Math.round(timeline.controls[i].distance_km)
            }
            return null
        }
        var a = []
        var lunch = kmAtClock(13); if (lunch) a.push({ label: qsTr("Lunch"), km: "" + lunch, min: "30" })
        var dinner = kmAtClock(20); if (dinner && (!lunch || dinner > lunch)) a.push({ label: qsTr("Dinner"), km: "" + dinner, min: "40" })
        plannedStops = a
        if (gpxText) computeTimeline()
    }
    // Minutes left for short stops = your food/rest budget minus the named ones (sleep is separate).
    function shortStopsLeftMin() {
        var T = parseFloat(stopTotalH.text); if (isNaN(T)) return -1
        var named = 0
        for (var i = 0; i < plannedStops.length; i++) { var m = parseFloat(plannedStops[i].min); if (!isNaN(m)) named += m }
        return Math.max(0, Math.round(T * 60 - named))
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
        L.push("checkpoint            km   opens   arrive   ride    km/h  stop   margin   temp  wind")
        for (var i = 0; i < timeline.controls.length; i++) {
            var c = timeline.controls[i]
            var w = wxFor(i)
            function pad(s, n) { s = "" + s; while (s.length < n) s += " "; return s }
            function padL(s, n) { s = "" + s; while (s.length < n) s = " " + s; return s }
            var m = c.margin_s === null ? "—" : (c.margin_s < 0 ? "-" + fmtDur(-c.margin_s) : "+" + fmtDur(c.margin_s))
            var opensTxt = c.opens_dt ? fmtClock(c.opens_dt) : "—"
            var tooEarly = (c.early_s !== undefined && c.early_s !== null && c.early_s > 600)
            L.push(pad(c.label, 20) + " " + padL(c.distance_km, 5) + "  " + padL(opensTxt, 5)
                   + "  " + pad(fmtClock(c.arrival_dt), 7)
                   + " " + padL(fmtDur(c.moving_time_s), 6) + "  " + padL(c.avg_speed_kmh, 5)
                   + " " + padL(Math.round(c.stop_s / 60) + "m", 5) + " " + padL(m, 7)
                   + (w ? "  " + padL(Math.round(w.temp_c) + "°", 4) + "  " + w.wind_rel + (w.is_dark ? " (dark)" : "") : "")
                   + (tooEarly ? "  [too early: wait " + fmtDur(c.early_s) + "]" : ""))
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

    // --- Save & compare scenarios ---
    property var scenarios: []       // saved-scenario summaries for the compare table
    function refreshScenarios() {
        api("POST", "/api/race/scenario-list", {}, function(s, r) {
            scenarios = (s === 200 && r && r.ok) ? r.scenarios : []
        })
    }
    function currentUiState() {
        var ctrls = []
        for (var i = 0; i < controlsModel.count; i++) {
            var c = controlsModel.get(i)
            ctrls.push({ label: c.label, km: c.km, hours: c.hours, opens: c.opens })
        }
        return { gpx: gpxText, gpxName: gpxName, startDate: startDate.text, startTime: startTime.text,
                 eventName: eventName.text, baseSpeed: baseSpeed.text, stopTotal: stopTotalH.text,
                 sleep: sleepH.text, fatigueOn: fatigueOn, plannedStops: plannedStops,
                 usualBed: usualBed.text, usualWake: usualWake.text,
                 noRideOn: noRideOn, noRideFrom: noRideFrom.text, noRideTo: noRideTo.text,
                 calibratedProfile: calibratedProfile, controls: ctrls, controlOverrides: controlOverrides }
    }
    function currentSummary() {
        if (!timeline) return {}
        return { distance_km: timeline.distance_km, finish: fmtClockDay(timeline.finish_eta_dt),
                 moving_time_s: timeline.moving_time_s, stop_time_s: timeline.stop_time_s,
                 sleep_time_s: timeline.sleep_time_s, elapsed_time_s: timeline.elapsed_time_s,
                 worst_margin_s: timeline.worst_margin_s, worst_margin_control: timeline.worst_margin_control }
    }
    function saveScenario(name) {
        api("POST", "/api/race/scenario-save",
            { name: name, ui: currentUiState(), summary: currentSummary() },
            function(s, r) { refreshScenarios(); statusMsg = (s === 200 && r && r.ok) ? qsTr("Scenario saved.") : qsTr("Save failed.") })
    }
    function deleteScenario(name) {
        api("POST", "/api/race/scenario-delete", { name: name }, function() { refreshScenarios() })
    }
    function openScenario(name) {
        api("POST", "/api/race/scenario-get", { name: name }, function(s, r) {
            if (!(s === 200 && r && r.ok && r.scenario)) return
            var u = r.scenario.ui || {}
            gpxText = u.gpx || ""; gpxName = u.gpxName || ""
            if (gpxText) { PlanStore.plannedGpx = gpxText; PlanStore.routeName = gpxName }
            startDate.text = u.startDate || startDate.text
            startTime.text = u.startTime || startTime.text
            eventName.text = u.eventName || ""
            baseSpeed.text = u.baseSpeed || ""
            stopTotalH.text = u.stopTotal || ""
            sleepH.text = u.sleep || ""
            fatigueOn = u.fatigueOn || false
            usualBed.text = u.usualBed || "22:00"; usualWake.text = u.usualWake || "06:00"
            noRideOn = u.noRideOn || false
            noRideFrom.text = u.noRideFrom || "23:00"; noRideTo.text = u.noRideTo || "03:30"
            noRideStartH = hhmmToHours(noRideFrom.text, 23); noRideEndH = hhmmToHours(noRideTo.text, 3.5)
            plannedStops = u.plannedStops || []
            calibratedProfile = u.calibratedProfile || null
            controlOverrides = u.controlOverrides || ({})
            controlsModel.clear()
            var cs = u.controls || []
            for (var i = 0; i < cs.length; i++) controlsModel.append({ label: cs[i].label || "", km: cs[i].km || "", hours: cs[i].hours || "", opens: cs[i].opens || "" })
            stopUserSet = (stopTotalH.text.length > 0); autoStopDone = true
            if (gpxText) { root.showResults = true; computeTimeline() }   // a saved scenario opens straight to its plan
        })
    }

    // Adopt a route already loaded on the Plan (Route) page, so the GPX "sticks" across screens.
    Component.onCompleted: {
        refreshScenarios()
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

                // ==== Guided setup — one question per step (visible until "See my plan") ====
                ColumnLayout {
                    Layout.fillWidth: true
                    visible: !root.showResults
                    spacing: Theme.spacingMedium

                    // progress: "Step N of M" + dots
                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("Step %1 of %2").arg(root.wizardStep + 1).arg(root.wizardStepCount)
                               color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                        Item { Layout.fillWidth: true }
                        Row {
                            spacing: 6
                            Repeater {
                                model: root.wizardStepCount
                                Rectangle {
                                    width: 9; height: 9; radius: 4.5
                                    color: index <= root.wizardStep ? Theme.primary : Theme.border
                                    opacity: index === root.wizardStep ? 1.0 : (index < root.wizardStep ? 0.85 : 0.5)
                                }
                            }
                        }
                    }
                    Text { text: root.wizardTitles[root.wizardStep]; color: Theme.text
                           font.pixelSize: Theme.fontSizeTitle; font.weight: Font.Bold }

                    // --- Step 0: Route ---
                    ColumnLayout {
                        Layout.fillWidth: true; visible: root.wizardStep === 0
                        spacing: Theme.spacingSmall
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: gpxName ? ("✓ " + gpxName + (routeDistanceKm > 0 ? "  ·  " + routeDistanceKm + " km" : ""))
                                          : qsTr("Load the route you're planning — a GPX file, or the one already on the Route page.")
                            color: gpxName ? Theme.text : Theme.mutedText
                            font.pixelSize: gpxName ? Theme.fontSizeSubtitle : Theme.fontSizeBody
                        }
                        RoundedButton { text: gpxName ? qsTr("Change route") : qsTr("Load GPX")
                                        onClicked: gpxDialog.open() }
                        // Water / food / cemetery stops come from PitStopper's export, loaded HERE as the
                        // route (same card as the Route page). Shown until the route carries POIs.
                        PoiHowTo { Layout.fillWidth: true; Layout.topMargin: Theme.spacingSmall
                                   visible: !PlanStore.pois }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            visible: !!PlanStore.pois
                            text: qsTr("✓ %1 places along the route (water, food, cemeteries…) — they'll show up in your plan's warnings.")
                                  .arg(PlanStore.pois ? (PlanStore.pois.imported || 0) : 0)
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                    }

                    // --- Step 1: Start ---
                    ColumnLayout {
                        Layout.fillWidth: true; visible: root.wizardStep === 1
                        spacing: Theme.spacingSmall
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            RoundedTextField { id: startDate; Layout.preferredWidth: 150
                                               placeholderText: qsTr("YYYY-MM-DD")
                                               text: new Date().toISOString().split("T")[0] }
                            RoundedTextField { id: startTime; Layout.preferredWidth: 100
                                               placeholderText: qsTr("HH:MM"); text: "06:00" }
                        }
                        RoundedTextField { id: eventName; Layout.fillWidth: true
                                           placeholderText: qsTr("Event name (optional)") }
                    }

                    // --- Step 2: Pace ---
                    ColumnLayout {
                        Layout.fillWidth: true; visible: root.wizardStep === 2
                        spacing: Theme.spacingSmall
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            RoundedTextField { id: baseSpeed; Layout.fillWidth: true
                                               placeholderText: qsTr("Your steady speed on a flat road, e.g. 25 km/h")
                                               inputMethodHints: Qt.ImhFormattedNumbersOnly
                                               onTextEdited: { root.calibratedProfile = null; root.calibNote = "" } }
                            RoundedButton { text: qsTr("From a ride"); onClicked: fitDialog.open() }
                        }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: calibNote.length > 0 ? calibNote
                                  : qsTr("Type your flat-road cruising speed, or pick a past ride (FIT) and we'll work it out. Leave it blank and we'll assume a typical ~22 km/h — you can always come back and refine it.")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                    }

                    // --- Step 3: Time off the bike (rolling stops + sleep, kept separate) ---
                    ColumnLayout {
                        Layout.fillWidth: true; visible: root.wizardStep === 3
                        spacing: Theme.spacingSmall

                        Text { text: qsTr("Food & rest stops"); color: Theme.text
                               font.pixelSize: Theme.fontSizeLabel; font.weight: Font.Bold }
                        RoundedTextField { id: stopTotalH; Layout.preferredWidth: 220
                                           placeholderText: qsTr("Hours, all short stops added up")
                                           inputMethodHints: Qt.ImhFormattedNumbersOnly
                                           onTextEdited: root.stopUserSet = true }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: qsTr("Your control/food/rest stops, added up — spread across the checkpoints. NOT sleep (that's below). Leave blank for a typical estimate.")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }

                        Text { text: qsTr("Sleep"); color: Theme.text
                               font.pixelSize: Theme.fontSizeLabel; font.weight: Font.Bold
                               Layout.topMargin: Theme.spacingSmall }
                        RoundedTextField { id: sleepH; Layout.preferredWidth: 220
                                           placeholderText: qsTr("Hours of sleep (0 if none)")
                                           inputMethodHints: Qt.ImhFormattedNumbersOnly }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: (timeline && timeline.sleep_suggested_s > 0)
                                  ? ("🌙 " + qsTr("Placed as ONE block at night, at the best control — so checkpoints before it are unaffected and later ones shift by your sleep. Typical for this distance: about %1.").arg(fmtDur(timeline.sleep_suggested_s)))
                                  : qsTr("Placed as one block at night, at the best control. Enter 0 for a ride you'll do without sleeping.")
                            color: (timeline && timeline.sleep_suggested_s > 0) ? "#e0912f" : Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        RoundedCheckBox {
                            Layout.topMargin: Theme.spacingSmall
                            text: qsTr("Multi-day ride — model fatigue")
                            checked: root.fatigueOn
                            onToggled: root.fatigueOn = checked
                        }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            visible: root.fatigueOn
                            text: qsTr("For rides that run into the night (or several): you slow down as sleep pressure builds and your body clock dips, and a short night leaves some of it behind. Uses your usual sleep (next step). Leave off for a day ride.")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                    }

                    // --- Step 4: Hours you never ride ---
                    ColumnLayout {
                        Layout.fillWidth: true; visible: root.wizardStep === 4
                        spacing: Theme.spacingSmall
                        Text { text: qsTr("Your usual sleep"); color: Theme.text
                               font.pixelSize: Theme.fontSizeLabel; font.weight: Font.Bold }
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            Text { text: qsTr("Bed"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            RoundedTextField { id: usualBed; Layout.preferredWidth: 90; text: "22:00"; placeholderText: "HH:MM" }
                            Text { text: qsTr("Wake"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            RoundedTextField { id: usualWake; Layout.preferredWidth: 90; text: "06:00"; placeholderText: "HH:MM" }
                        }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                            text: qsTr("Your body clock and how fast you get sleepy follow this. Used when \"model fatigue\" is on, and by \"Suggest my sleep\" in your plan.")
                        }
                        Text { text: qsTr("Hours you never ride"); color: Theme.text
                               font.pixelSize: Theme.fontSizeLabel; font.weight: Font.Bold
                               Layout.topMargin: Theme.spacingSmall }
                        RoundedCheckBox {
                            text: qsTr("There are hours I never ride (night, curfew…)")
                            checked: root.noRideOn
                            onToggled: root.noRideOn = checked
                        }
                        RowLayout {
                            visible: root.noRideOn
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            Text { text: qsTr("From"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            RoundedTextField { id: noRideFrom; Layout.preferredWidth: 90; text: "23:00"
                                               placeholderText: "HH:MM"
                                               onEditingFinished: root.noRideStartH = root.hhmmToHours(text, 23) }
                            Text { text: qsTr("to"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            RoundedTextField { id: noRideTo; Layout.preferredWidth: 90; text: "03:30"
                                               placeholderText: "HH:MM"
                                               onEditingFinished: root.noRideEndH = root.hhmmToHours(text, 3.5) }
                        }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                            text: root.noRideOn
                                  ? qsTr("The plan never has you riding in these hours: you stop and rest until they end. That rest IS your sleep, so the sleep field above is ignored. Examples: 23:00 to 03:30 (what you did on the BRM600 Verdun), or 00:00 to 03:00, or 21:00 to 06:00 (a curfew like Bikingman Corsica 2021). Your plan then shows the days and nights this creates — and how they compare with cutting the route into equal days.")
                                  : qsTr("Optional. Tick it if you refuse to ride at certain hours, or the event forbids it. Leave it off and sleep is placed where the body clock says it is worst to ride.")
                        }
                    }

                    // --- Step 4: Checkpoints ---
                    ColumnLayout {
                        Layout.fillWidth: true; visible: root.wizardStep === 5
                        spacing: Theme.spacingSmall
                        Text { text: qsTr("From your brevet card — import the roadbook, or skip and just get a finish time.")
                               color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                               Layout.fillWidth: true; wrapMode: Text.WordWrap }
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            RoundedButton { text: qsTr("Import roadbook (PDF)"); onClicked: rbPdfDialog.open() }
                            RoundedButton { text: qsTr("+ Add"); onClicked: controlsModel.append({ label: "", km: "", hours: "", opens: "" }) }
                            Item { Layout.fillWidth: true }
                        }
                        // header
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            visible: controlsModel.count > 0
                            Text { text: qsTr("Label"); Layout.fillWidth: true; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("km"); Layout.preferredWidth: 70; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("opens"); Layout.preferredWidth: 76; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("must arrive by"); Layout.preferredWidth: 90; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Item { Layout.preferredWidth: 32 }
                        }
                        Repeater {
                            model: controlsModel
                            delegate: RowLayout {
                                Layout.fillWidth: true
                                spacing: Theme.spacingSmall
                                RoundedTextField { Layout.fillWidth: true; text: model.label
                                    placeholderText: qsTr("Control %1").arg(index + 1)
                                    onTextChanged: controlsModel.setProperty(index, "label", text) }
                                RoundedTextField { Layout.preferredWidth: 70; text: model.km; placeholderText: qsTr("km")
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    onTextChanged: controlsModel.setProperty(index, "km", text) }
                                RoundedTextField { Layout.preferredWidth: 76; text: model.opens; placeholderText: qsTr("opens")
                                    onTextChanged: controlsModel.setProperty(index, "opens", text) }
                                RoundedTextField { Layout.preferredWidth: 90; text: model.hours; placeholderText: qsTr("by HH:MM")
                                    onTextChanged: controlsModel.setProperty(index, "hours", text) }
                                RoundedButton { text: "✕"; Layout.preferredWidth: 32; onClicked: controlsModel.remove(index) }
                            }
                        }
                    }

                    // nav row: Back / Next / See my plan
                    RowLayout {
                        Layout.fillWidth: true
                        Layout.topMargin: Theme.spacingMedium
                        RoundedButton { text: qsTr("◂ Back"); visible: root.wizardStep > 0; onClicked: root.wizardBack() }
                        Item { Layout.fillWidth: true }
                        RoundedButton {
                            accent: true      // primary action — clearly clickable, not greyed
                            text: root.wizardStep < root.wizardStepCount - 1 ? qsTr("Next ▸")
                                  : (busy ? qsTr("Computing…") : qsTr("See my plan ▸"))
                            enabled: root.wizardCanAdvance() && !busy
                            onClicked: root.wizardNext()
                        }
                    }
                    // Show EVERY status message, not only ones starting with "Error": a failed roadbook
                    // import ("Couldn't read roadbook: ...") was invisible, so it just looked like the
                    // import did nothing (André: "import roadbook is broken"). Failures are red.
                    Text {
                        visible: statusMsg.length > 0
                        text: statusMsg
                        readonly property bool bad: /^(Error|Couldn't|No )/.test(statusMsg)
                        color: bad ? marginBad : Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption; Layout.fillWidth: true; wrapMode: Text.WordWrap
                    }
                }

                // Results toolbar: shown with the plan, to tweak inputs or export/save.
                RowLayout {
                    Layout.fillWidth: true
                    visible: root.showResults
                    RoundedButton { text: qsTr("◂ Edit answers"); onClicked: root.editAnswers() }
                    Item { Layout.fillWidth: true }
                    RoundedButton {
                        text: busy ? qsTr("Computing…") : qsTr("Recompute")
                        enabled: !busy && gpxText.length > 0
                        onClicked: computeTimeline()
                    }
                    RoundedButton {
                        text: qsTr("Export roadbook")
                        visible: timeline && timeline.ok
                        onClicked: roadbookDialog.open()
                    }
                    RoundedButton {
                        text: qsTr("Save scenario")
                        visible: timeline && timeline.ok
                        onClicked: { scenarioName.text = eventName.text || gpxName || "Scenario"; saveScenarioDialog.open() }
                    }
                }

                // --- Results ---
                Rectangle {
                    Layout.fillWidth: true
                    Layout.topMargin: Theme.spacingSmall
                    visible: root.showResults && timeline && timeline.ok
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
                        // Warn when this is running on the cautious generic pace, not the rider's:
                        // otherwise a blank speed field silently makes a capable rider "miss" the
                        // cutoff (André, 2026-09-21: "will finish off timing... average speed by
                        // default is not mine?").
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            visible: root.usingGenericPace
                            text: qsTr("⚠ This uses a cautious generic 22 km/h, not your pace — go back and set your speed (or “From a ride”) for your real finish time.")
                            color: "#e0912f"; font.pixelSize: Theme.fontSizeCaption
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

                        // --- Planned stops (named meal/cafe stops; rest of the budget is spread) ---
                        Rectangle { Layout.fillWidth: true; Layout.topMargin: Theme.spacingSmall; height: 1; color: Theme.border }
                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: qsTr("Planned stops"); color: Theme.text; font.weight: Font.Bold
                                   font.pixelSize: Theme.fontSizeLabel }
                            Item { Layout.fillWidth: true }
                            RoundedButton { text: qsTr("Suggest"); onClicked: root.suggestStops() }
                            RoundedButton { text: qsTr("+ Add"); onClicked: root.addPlannedStop() }
                        }
                        Repeater {
                            model: root.plannedStops
                            delegate: RowLayout {
                                Layout.fillWidth: true; spacing: Theme.spacingSmall
                                RoundedTextField { Layout.fillWidth: true; text: modelData.label
                                    placeholderText: qsTr("Stop name")
                                    onEditingFinished: root.setPlannedStop(index, "label", text) }
                                RoundedTextField { Layout.preferredWidth: 70; text: "" + modelData.km; placeholderText: qsTr("km")
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    onEditingFinished: { root.setPlannedStop(index, "km", text); if (gpxText) computeTimeline() } }
                                RoundedTextField { Layout.preferredWidth: 60; text: "" + modelData.min; placeholderText: qsTr("min")
                                    inputMethodHints: Qt.ImhFormattedNumbersOnly
                                    onEditingFinished: { root.setPlannedStop(index, "min", text); if (gpxText) computeTimeline() } }
                                RoundedButton { text: "✕"; Layout.preferredWidth: 32; onClicked: root.removePlannedStop(index) }
                            }
                        }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            text: {
                                var left = shortStopsLeftMin()
                                if (left < 0) return qsTr("Set your food & rest time to split it into planned stops + short stops.")
                                return "⏱ " + qsTr("Left for short stops: %1, spread through the ride (more in the 2nd half, when you're tired).").arg(fmtDur(left * 60))
                            }
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }

                        Text {
                            Layout.fillWidth: true
                            visible: timeline
                            text: {
                                if (!timeline) return ""
                                var src = timeline.model_source, conf = timeline.confidence
                                if (src === "personal")
                                    return qsTr("Speed: your calibrated pace (%1 confidence).").arg(conf)
                                if (src === "generic")
                                    return qsTr("Speed: a generic estimate — enter your typical average or calibrate from a ride for a personal prediction.")
                                if (src === "physics")
                                    return qsTr("Speed: a weight-based estimate (%1 confidence).").arg(conf)
                                return qsTr("Speed is a placeholder — set your typical average for a real prediction.")
                            }
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
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            visible: weather && weather.summary
                            RoundedCheckBox {
                                text: qsTr("Adjust finish for the prevailing wind")
                                checked: windFold
                                onToggled: { root.windFold = checked; root.applyFolds() }
                            }
                            Text {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: (weather && weather.summary && weather.summary.net_head_kmh !== undefined)
                                      ? (Math.abs(weather.summary.net_head_kmh) < 1
                                         ? qsTr("net wind ≈ 0 on this route")
                                         : (weather.summary.net_head_kmh > 0
                                            ? qsTr("net headwind ~%1 km/h").arg(Math.round(weather.summary.net_head_kmh))
                                            : qsTr("net tailwind ~%1 km/h").arg(Math.round(-weather.summary.net_head_kmh))))
                                      : ""
                                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                            }
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
                            Text { visible: false; text: qsTr("stop min"); Layout.preferredWidth: 56; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
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
                                // arrival time; tinted amber when you'd reach the control before it OPENS (you'd wait).
                                Text {
                                    text: fmtClock(modelData.arrival_dt)
                                    + ((modelData.early_s !== undefined && modelData.early_s !== null && modelData.early_s > 600) ? " ⏳" : "")
                                    Layout.preferredWidth: 72; horizontalAlignment: Text.AlignRight
                                    color: (modelData.early_s !== undefined && modelData.early_s !== null && modelData.early_s > 600) ? "#e0912f" : Theme.text
                                    font.pixelSize: Theme.fontSizeCaption
                                    ToolTip.visible: earlyMA.containsMouse && modelData.early_s > 600
                                    ToolTip.text: qsTr("Opens %1 — you'd arrive %2 early and wait")
                                        .arg(fmtClock(modelData.opens_dt)).arg(fmtDur(modelData.early_s))
                                    MouseArea { id: earlyMA; anchors.fill: parent; hoverEnabled: true }
                                }
                                Text { text: fmtDur(modelData.moving_time_s); Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                Text { text: modelData.avg_speed_kmh; Layout.preferredWidth: 50; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                // per-control stop split — hidden now that Planned stops own this (was
                                // misleading as a "dwell here" number); kept for a possible expert mode.
                                RoundedTextField {
                                    visible: false
                                    Layout.preferredWidth: 56
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

                        // --- Suggest my sleep: try many sleep plans, rank by finish vs how sleepy you get ---
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.topMargin: Theme.spacingSmall
                            spacing: 4
                            visible: !!timeline

                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                            RowLayout {
                                Layout.fillWidth: true
                                Text { text: qsTr("Suggest my sleep"); color: Theme.text; font.weight: Font.Bold
                                       font.pixelSize: Theme.fontSizeCaption; Layout.fillWidth: true }
                                RoundedButton {
                                    text: root.sleepOptsBusy ? qsTr("Trying plans…") : qsTr("Try sleep plans")
                                    enabled: !root.sleepOptsBusy && !!root.basePayload
                                    onClicked: root.suggestSleep()
                                }
                            }
                            Text {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                visible: !root.sleepOpts
                                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                text: qsTr("Tries different bedtimes and lengths on your ride and ranks them: finish time against how sleepy you get at your worst moment.")
                            }
                            Text {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                visible: !!root.sleepOpts
                                color: marginBad; font.pixelSize: Theme.fontSizeCaption
                                text: root.sleepOpts
                                      ? qsTr("No sleep: finish %1, and your alertness drops to %2 around %3 (%4).")
                                            .arg(root.fmtClockDay(root.sleepOpts.baseline.finish))
                                            .arg(Math.round(root.sleepOpts.baseline.min_alertness))
                                            .arg(root.fmtClockDay(root.sleepOpts.baseline.min_at))
                                            .arg(root.sleepOpts.baseline.band)
                                      : ""
                            }
                            Repeater {
                                model: root.sleepOpts ? root.sleepOpts.options : []
                                delegate: RowLayout {
                                    Layout.fillWidth: true; spacing: Theme.spacingSmall
                                    Text {
                                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                                        font.pixelSize: Theme.fontSizeCaption
                                        color: modelData.band === "fine" ? Theme.text : (modelData.band === "tired" ? "#e0912f" : marginBad)
                                        readonly property var n1: (modelData.nights && modelData.nights.length > 0) ? modelData.nights[0] : null
                                        text: qsTr("%1–%2 (%3 h): finish %4 · lowest alertness %5 (%6)%7")
                                            .arg(modelData.bed).arg(modelData.wake).arg(modelData.hours)
                                            .arg(root.fmtClockDay(modelData.finish))
                                            .arg(Math.round(modelData.min_alertness)).arg(modelData.band)
                                            .arg(n1 ? qsTr(" · night 1 at km %1, %2").arg(Math.round(n1.km))
                                                      .arg(n1.no_bed ? qsTr("no bed within 20 km")
                                                           : (n1.nearest_bed ? qsTr("bed: %1 (%2 km)").arg(n1.nearest_bed.name).arg(Math.abs(n1.nearest_bed.offset_km))
                                                                             : qsTr("beds unknown"))) : "")
                                    }
                                    RoundedButton { text: qsTr("Use"); onClicked: root.useSleepOption(modelData) }
                                }
                            }
                            Text {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                visible: !!root.sleepOpts
                                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; font.italic: true
                                text: root.sleepOpts ? root.sleepOpts.note : ""
                            }
                        }

                        // --- Days & nights: where each night falls vs cutting the GPX into equal days ---
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.topMargin: Theme.spacingSmall
                            spacing: 4
                            visible: !!daysPlan

                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                            Text { text: qsTr("Days & nights"); color: Theme.text; font.weight: Font.Bold
                                   font.pixelSize: Theme.fontSizeCaption }
                            Repeater {
                                model: daysPlan ? daysPlan.days : []
                                delegate: Text {
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    font.pixelSize: Theme.fontSizeCaption; color: Theme.text
                                    text: qsTr("Day %1: km %2 → %3 (%4 km), %5 → %6")
                                        .arg(modelData.day).arg(Math.round(modelData.from_km)).arg(Math.round(modelData.to_km))
                                        .arg(Math.round(modelData.km))
                                        .arg(root.fmtClockDay(modelData.start)).arg(root.fmtClockDay(modelData.end))
                                }
                            }
                            Repeater {
                                model: daysPlan ? daysPlan.lines : []
                                delegate: Text {
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                    font.pixelSize: Theme.fontSizeCaption
                                    color: (modelData.indexOf("No accommodation") >= 0 || modelData.indexOf("shorter") >= 0)
                                           ? marginBad : Theme.mutedText
                                    text: modelData
                                }
                            }
                            RoundedButton {
                                text: qsTr("Use these days on the Route page")
                                onClicked: root.useDaysOnRoute()
                            }
                        }

                        // --- Sleep plan (circadian: dark + moonlight + body clock + cold) ---
                        ColumnLayout {
                            Layout.fillWidth: true
                            Layout.topMargin: Theme.spacingSmall
                            spacing: 4
                            visible: sleepPlan && sleepPlan.windows && sleepPlan.windows.length > 0

                            Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border }
                            Text { text: root.noRideOn ? qsTr("What the body clock would suggest (you set your own hours)")
                                                       : qsTr("Sleep plan")
                                   color: Theme.text; font.weight: Font.Bold
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
                        Repeater {
                            model: (openGaps && PlanStore.pois) ? openGaps.lines : []
                            delegate: Text {
                                Layout.fillWidth: true; wrapMode: Text.WordWrap
                                text: modelData
                                color: Theme.text; font.pixelSize: Theme.fontSizeCaption
                            }
                        }
                        Text {
                            Layout.fillWidth: true; wrapMode: Text.WordWrap
                            visible: !!openGaps && !!PlanStore.pois
                            text: openGaps ? openGaps.note : ""
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; font.italic: true
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
                    visible: root.showResults && timeline && timeline.ok
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

                // --- Saved scenarios (compare) ---
                Rectangle {
                    Layout.fillWidth: true; Layout.topMargin: Theme.spacingSmall
                    visible: root.showResults && scenarios.length > 0
                    color: Theme.card; radius: Theme.radiusCard
                    border.color: Theme.border; border.width: 1
                    implicitHeight: scCol.implicitHeight + Theme.spacingMedium * 2
                    ColumnLayout {
                        id: scCol
                        anchors.fill: parent; anchors.margins: Theme.spacingMedium; spacing: 2
                        Text { text: qsTr("Saved scenarios"); color: Theme.text; font.weight: Font.Bold }
                        RowLayout {
                            Layout.fillWidth: true; spacing: Theme.spacingSmall
                            Text { text: qsTr("name"); Layout.fillWidth: true; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("finish"); Layout.preferredWidth: 72; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("elapsed"); Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Text { text: qsTr("tightest"); Layout.preferredWidth: 66; horizontalAlignment: Text.AlignRight; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            Item { Layout.preferredWidth: 84 }
                        }
                        Repeater {
                            model: scenarios
                            delegate: RowLayout {
                                width: scCol.width; spacing: Theme.spacingSmall
                                Text { text: modelData.name; Layout.fillWidth: true; color: Theme.text
                                       font.pixelSize: Theme.fontSizeCaption; elide: Text.ElideRight }
                                Text { text: (modelData.summary && modelData.summary.finish) || "—"; Layout.preferredWidth: 72
                                       color: Theme.text; font.pixelSize: Theme.fontSizeCaption }
                                Text { text: (modelData.summary && modelData.summary.elapsed_time_s) ? fmtDur(modelData.summary.elapsed_time_s) : "—"
                                       Layout.preferredWidth: 60; horizontalAlignment: Text.AlignRight; color: Theme.text; font.pixelSize: Theme.fontSizeCaption }
                                Text {
                                    Layout.preferredWidth: 66; horizontalAlignment: Text.AlignRight
                                    text: (modelData.summary && modelData.summary.worst_margin_s !== null && modelData.summary.worst_margin_s !== undefined)
                                          ? (modelData.summary.worst_margin_s < 0 ? "-" + fmtDur(-modelData.summary.worst_margin_s) : "+" + fmtDur(modelData.summary.worst_margin_s))
                                          : "—"
                                    color: (modelData.summary && modelData.summary.worst_margin_s < 0) ? marginBad : marginGood
                                    font.pixelSize: Theme.fontSizeCaption
                                }
                                RoundedButton { text: qsTr("Open"); Layout.preferredWidth: 52; onClicked: root.openScenario(modelData.name) }
                                RoundedButton { text: "✕"; Layout.preferredWidth: 28; onClicked: root.deleteScenario(modelData.name) }
                            }
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

    Dialog {
        id: saveScenarioDialog
        title: qsTr("Save scenario")
        modal: true
        anchors.centerIn: Overlay.overlay
        width: 380
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: if (scenarioName.text.trim().length) root.saveScenario(scenarioName.text.trim())
        ColumnLayout {
            anchors.fill: parent; spacing: Theme.spacingSmall
            Text { text: qsTr("Name this scenario (e.g. \"start 04:00, 2h sleep\") to compare later.")
                   color: Theme.text; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            RoundedTextField { id: scenarioName; Layout.fillWidth: true; placeholderText: qsTr("Scenario name") }
        }
    }

    Dialog {
        id: pasteDialog
        title: qsTr("Paste checkpoints")
        modal: true
        anchors.centerIn: Overlay.overlay
        width: 440
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: { root.pasteControls(pasteArea.text); pasteArea.text = "" }
        ColumnLayout {
            anchors.fill: parent
            spacing: Theme.spacingSmall
            Text { text: qsTr("One checkpoint per line: km, closing time (HH:MM), optional name.\nExample:\n120, 14:30, Verdun\n250, 22:10")
                   color: Theme.text; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap; Layout.fillWidth: true }
            ScrollView {
                Layout.fillWidth: true; Layout.preferredHeight: 150
                TextArea { id: pasteArea; placeholderText: qsTr("km, HH:MM per line") }
            }
        }
    }

    Dialog {
        id: importRoadbookDialog
        title: qsTr("Import roadbook")
        modal: true
        anchors.centerIn: Overlay.overlay
        width: 480
        standardButtons: Dialog.Ok | Dialog.Cancel
        onAccepted: { if (rbArea.text.trim().length) root.importRoadbook({ text: rbArea.text }); rbArea.text = "" }
        ColumnLayout {
            anchors.fill: parent
            spacing: Theme.spacingSmall
            Text {
                text: qsTr("Paste the control table from your brevet roadbook (the whole page is fine — only the \"C1 …\" control lines with a closing time are used). Each control's official closing time becomes its \"must arrive by\".")
                color: Theme.text; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap; Layout.fillWidth: true
            }
            ScrollView {
                Layout.fillWidth: true; Layout.preferredHeight: 180
                TextArea { id: rbArea; placeholderText: qsTr("C1 - FROIDCHAPELLE   …   112,5   9:19   12:30") }
            }
            RowLayout {
                Layout.fillWidth: true
                Text { text: qsTr("…or load a PDF roadbook:"); color: Theme.mutedText
                       font.pixelSize: Theme.fontSizeCaption }
                Item { Layout.fillWidth: true }
                RoundedButton { text: qsTr("From PDF…"); onClicked: rbPdfDialog.open() }
            }
        }
    }

    FileDialog {
        id: rbPdfDialog
        title: qsTr("Choose a roadbook PDF")
        nameFilters: [qsTr("PDF files (*.pdf *.PDF)"), qsTr("All files (*)")]
        onAccepted: root.importRoadbook({ pdf: decodeURIComponent(selectedFile.toString().replace("file://", "")) })
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
