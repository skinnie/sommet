import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtCore
import AmbitApp
import "../ActivityViewLogic.js" as AVL

// One activity (André, 2026-09-27 redesign - mockup artifact 8KKpqaVBCdPUvDUAhzya8h, plan
// ACT-1..21). What each sport shows lives in shared/activity_view.json and the maths in the
// shared ActivityViewLogic.js, both used unchanged by the Android app.
//   Overview  three levels (headline, secondary + zones, "More details" folded)
//   Charts    every picked metric overlaid in one chart, main line, hover card, zoom linked
//             with the map; pool swims get one bar per length instead
//   Laps      only when you pressed laps on the watch
//   Export / Upload   unchanged
// The chart data (streams) is decoded from the activity's FIT by the backend
// (tools/activity_streams.py via ActivityService.requestStreams); an intervals.icu import's FIT
// is fetched first. The Overview shows the list's numbers straight away and fills in from the
// FIT when it arrives.
Item {
    id: root
    property var activity
    property string saveError: ""
    property string uploadStatus: ""
    property bool uploading: false
    signal back

    readonly property var cfg: ActivityService.viewConfig
    readonly property int _sportId: activity ? ActivityTypes.displayId(activity.name, activity.sportTypeRaw) : 1
    // An unrecognised type takes the sport the FIT records (AVL.refineSport; same on Android).
    readonly property string sport: cfg && cfg.sports ? AVL.refineSport(AVL.sportKey(cfg, _sportId), _st) : "other"
    readonly property var sportCfg: cfg && cfg.sports ? (cfg.sports[sport] || cfg.sports.other) : ({})
    readonly property bool _foot: sport === "run" || sport === "walk" || sport === "hike"

    // ---------------------------------------------------------------- streams
    property var _st: null
    property var _planned: null           // {name, blocks:[{t0,t1,lo,hi}]} - indoor rides only
    property string _plannedDate: ""
    property bool _stLoading: false
    property string _stError: ""
    Connections {
        target: ActivityService
        function onStreamsReady(idx, device, streams, error) {
            if (!root.activity || idx !== root.activity.index || device !== (root.activity.device || "")) return
            root._stLoading = false
            root._stError = error
            root._st = streams && streams.ok ? streams : null
            root._resetChartState()
            if (root._st && root.sportCfg.planned_workout && root._st.streams && root._st.streams.pw) {
                root._plannedDate = String(root.activity.startTime || "").slice(0, 10)
                ActivityService.requestPlanned(root._plannedDate)
            }
            if (root._st && root.sport === "pool_swim" && root._st.longest_nonstop_m)
                root._rememberSwim(root._st.longest_nonstop_m)
        }
        function onPlannedReady(date, workouts) {
            if (date !== root._plannedDate) return
            const w = (workouts || []).find(x => x.type === "Ride" || x.type === "VirtualRide") || (workouts || [])[0]
            if (!w) { root._planned = null; return }
            const blocks = AVL.workoutBlocks(w.workout_doc, AVL.ftpFrom(ActivityService.zoneGroups))
            root._planned = blocks.length ? { name: w.name || qsTr("Planned workout"), blocks: blocks } : null
        }
        function onExportFinished(uploaded, failed) {
            root.uploading = false
            root.uploadStatus = failed > 0 ? qsTr("Upload failed.") : qsTr("Uploaded to intervals.icu.")
        }
        function onExportError(message) { root.uploading = false; root.uploadStatus = message }
        function onIntervalsFitReady(idx, device, fitBase64, error) {
            if (!root.activity || idx !== root.activity.index || device !== (root.activity.device || "")) return
            if (!root._fetchingFit) return          // the charts fetch a FIT too - only Export opens the dialog
            root._fetchingFit = false
            root._fitError = error
            if (fitBase64.length > 0) { root._exportFit = fitBase64; root._openFitDialog() }
        }
    }
    // The list refreshes (sync, import) hand the open activity back as a NEW object with the same
    // identity; only a really different activity reloads (and resets tab and zoom).
    property string _loadedKey: ""
    function _load() {
        const key = activity ? String(activity.index) + "|" + (activity.device || "") + "|" + (activity.startTime || "") : ""
        if (key === _loadedKey && _st) return
        _loadedKey = key
        _st = null
        _stError = ""
        _planned = null
        _hover = -1
        chartZoom = null
        currentTab = 0
        if (!activity || activity.index === undefined) return
        _stLoading = true
        ActivityService.requestStreams(activity.index, activity.device || "")
        if (!_zonesAsked) {
            _zonesAsked = true
            ActivityService.requestZones()
        }
    }
    property bool _zonesAsked: false
    onActivityChanged: { _resolveTrack(); _load() }
    Component.onCompleted: { _resolveTrack(); _load() }

    // The list's own numbers, in the shape the shared logic expects (metric units).
    readonly property var facts: {
        const a = activity || {}
        const cadMul = _foot ? 2 : 1
        return {
            sport_id: _sportId, start: a.startTime,
            dist_m: a.distanceMeters, duration_s: a.durationSeconds,
            ascent_m: a.ascentMeters, descent_m: a.descentMeters,
            avg_hr: a.avgHr, max_hr: a.maxHr,
            avg_cad: a.avgCadence ? a.avgCadence * cadMul : null, max_cad: a.maxCadence ? a.maxCadence * cadMul : null,
            avg_speed: a.avgSpeedMh ? a.avgSpeedMh / 3600 : null, max_speed: a.maxSpeedMh ? a.maxSpeedMh / 3600 : null,
            kcal: a.energyKcal, recovery_s: a.recoverySeconds,
            pte: a.peakTrainingEffect ? a.peakTrainingEffect / 10 : null,
            pool_lengths: a.poolLengths, max_alt: a.maxAltitudeMeters
        }
    }

    // "Compared with your usual" (pool swims: longest non-stop vs earlier swims instead).
    readonly property var usual: {
        if (!activity || !cfg || !cfg.sports) return null
        if (sport === "pool_swim")
            return _st && _st.longest_nonstop_m ? AVL.swimBenchmark(_st.longest_nonstop_m, _earlierSwims()) : null
        const all = ActivityService.activities || []
        const cands = []
        for (let i = 0; i < all.length; i++) {
            const a = all[i]
            if (!a.startTime || a.startTime === activity.startTime) continue
            const d = a.distanceMeters || 0, t = a.durationSeconds || 0
            cands.push({ sport_id: ActivityTypes.displayId(a.name, a.sportTypeRaw), start: a.startTime,
                         dist_m: d, duration_s: t, ascent_m: a.ascentMeters || 0,
                         avg_speed: d > 0 && t > 0 ? d / t : null, avg_hr: a.avgHr || null })
        }
        const sm = _st && _st.summary ? _st.summary : {}
        const d = facts.dist_m || 0, t = facts.duration_s || 0
        const cur = { sport_id: _sportId, start: activity.startTime, dist_m: d, duration_s: t,
                      // the same speed the Overview tile shows (FIT distance / moving time), so the
                      // line never quotes a different pace than the tile above it
                      ascent_m: facts.ascent_m || 0,
                      avg_speed: AVL.metricRaw(cfg, "avg_speed", sport, facts, _st) || (d > 0 && t > 0 ? d / t : null),
                      avg_hr: sm.avg_hr || facts.avg_hr || null, avg_power: sm.avg_pw || null }
        return AVL.compareUsual(cfg, sport, cur, cands)
    }
    // Pool swims' longest non-stop, remembered as each swim's FIT is decoded (keyed by start).
    Settings { id: swimMemory; category: "activitySwims"; property string longest: "{}" }
    function _rememberSwim(m) {
        const map = JSON.parse(swimMemory.longest || "{}")
        map[activity.startTime] = m
        swimMemory.longest = JSON.stringify(map)
    }
    function _earlierSwims() {
        const map = JSON.parse(swimMemory.longest || "{}"), out = []
        for (const k in map) if (k < activity.startTime) out.push({ longest_nonstop_m: map[k] })
        return out
    }

    // ---------------------------------------------------------------- palette
    readonly property var zonePalette: {
        const p = cfg && cfg.palette ? cfg.palette : null
        if (!p) return {}
        const zs = Theme.isDark ? p.zones_dark : p.zones_light, out = {}
        for (let i = 0; i < zs.length; i++) out["z" + (i + 1)] = zs[i]
        out.down = Theme.isDark ? p.downhill_dark : p.downhill_light
        out.casing = Theme.isDark ? p.casing_dark : p.casing_light
        return out
    }

    // ---------------------------------------------------------------- chart state
    property var chartZoom: null          // [i0, i1] stream indexes or null
    property int _hover: -1
    property string focusId: ""
    property bool xDist: true
    property var chanOn: ({})             // channel id -> bool
    property string colourMode: ""        // "" = off
    property bool linked: true
    function _resetChartState() {
        const c = sportCfg.chart
        chanOn = AVL.defaultChannelsOn(cfg, sportCfg, _st)
        focusId = ""
        xDist = !c || c.x !== "time"
        const modes = (sportCfg.colour || []).filter(m => root._colourAvailable(m))
        colourMode = modes.length ? modes[0] : ""
    }
    readonly property var _zones: AVL.zonesFor(ActivityService.zoneGroups, sport)
    function _colourAvailable(m) {
        if (!_st || !_st.streams) return false
        const s = _st.streams
        if (m === "hrz") return !!s.hr && !!_zones.hr
        if (m === "pwz") return !!s.pw && !!_zones.power
        if (m === "pace" || m === "speed") return !!s.v
        if (m === "slope") return !!s.alt && !!s.dist
        return false
    }
    readonly property var _chans: {
        const out = []
        const c = sportCfg.chart
        if (!c || !c.channels || !_st || !_st.streams) return out
        for (const id of c.channels) {
            const def = cfg.channels[id]
            if (!def || !AVL.channelUseful(_st, def.key)) continue      // missing or flat: hidden
            const ch = Object.assign({ id: id }, def)
            if (id === "cad") ch.unit = _foot ? "spm" : "rpm"
            out.push(ch)
        }
        return out
    }
    readonly property var _chansOn: _chans.filter(c => chanOn[c.id])
    readonly property var _ribbon: {
        if (!colourMode || !_st) return null
        const cb = AVL.colourBands(cfg, colourMode, _st, _zones, sport)
        if (!cb) return null
        return { key: cfg.colour_modes[colourMode].key, bands: cb.bands, label: cb.label,
                 colourOf: b => AVL.bandColour(cb, b), shares: AVL.bandShares(cb) }
    }
    readonly property bool _hasPlot: _chans.length > 0
    readonly property bool _hasLengths: !!(_st && _st.lengths && _st.lengths.some(l => l.active && l.swim_s))
    readonly property bool _hasPressedLaps: !!(_st && _st.laps_kind === "pressed") && sport !== "pool_swim"

    // ---------------------------------------------------------------- map <-> chart
    readonly property var _coords: {
        if (!_st || !_st.streams || !_st.streams.lat) return []
        const s = _st.streams, out = []
        for (let i = 0; i < s.lat.length; i++) out.push(s.lat[i] === null ? null : [s.lat[i], s.lon[i]])
        return out
    }
    readonly property var _coloredSegments: {
        if (!_ribbon || !_coords.length) return []
        const out = []
        let cur = null, curBand
        for (let i = 0; i < _coords.length; i++) {
            const c = _coords[i]
            if (!c) { cur = null; continue }
            const b = _ribbon.bands[i]
            if (cur && b === curBand) { cur.coords.push(c); continue }
            const col = b === null || b === undefined ? Theme.borderStrong : zonePalette[_ribbon.colourOf(b)]
            const seg = { color: String(col), coords: cur ? [cur.coords[cur.coords.length - 1], c] : [c] }
            out.push(seg)
            cur = seg; curBand = b
        }
        return out
    }
    property bool _mapSyncGuard: false
    Timer { id: mapGuardTimer; interval: 400; onTriggered: root._mapSyncGuard = false }
    function _setZoomFromChart(range) {
        chartZoom = range
        if (!linked || !_coords.length) return
        _mapSyncGuard = true
        mapGuardTimer.restart()
        if (range) {
            const pts = []
            for (let i = range[0]; i <= range[1]; i++) if (_coords[i]) pts.push(_coords[i])
            map.fitToCoords(pts)
        } else {
            map.resetView()
        }
    }
    function _chartFromMap() {
        if (!linked || _mapSyncGuard || !_coords.length) return
        if (!map.userControlled) { chartZoom = null; return }
        const b = map.visibleBounds()
        const flags = _coords.map(c => !!c && c[0] >= b.minLat && c[0] <= b.maxLat && c[1] >= b.minLon && c[1] <= b.maxLon)
        const run = AVL.longestRun(flags, 2)
        if (!run) return
        chartZoom = (run[0] === 0 && run[1] >= _coords.length - 1) ? null : [run[0], Math.max(run[1], run[0] + 2)]
    }
    readonly property var _highlight: {
        if (!chartZoom || !_coords.length) return []
        const out = []
        for (let i = chartZoom[0]; i <= chartZoom[1]; i++) if (_coords[i]) out.push(_coords[i])
        return out
    }
    readonly property var _cursorPoint: _hover >= 0 && _coords[_hover] ? { lat: _coords[_hover][0], lon: _coords[_hover][1] } : null

    // ---------------------------------------------------------------- export
    FileDialog {
        id: gpxExportDialog
        title: qsTr("Export activity as GPX")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("GPX files (*.gpx)")]
        currentFolder: LocalFileService.downloadsLocation
        onAccepted: root.saveError = LocalFileService.saveText(selectedFile, root._exportGpx)
    }
    FileDialog {
        id: fitExportDialog
        title: qsTr("Export activity as FIT")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("FIT files (*.fit)")]
        currentFolder: LocalFileService.downloadsLocation
        onAccepted: root.saveError = LocalFileService.saveBase64(selectedFile, root._exportFit)
    }
    // GPS track is loaded on demand: recent rides keep it inline, older ones are deferred by the
    // DB layer for speed (ActivityService, 2026-09-20) and fetched here the instant the activity opens.
    property var _resolvedTrack: []
    property string _exportGpx: ""
    readonly property bool _hasGpxFile: !!(activity && activity.gpxText && activity.gpxText.length > 0)
    readonly property bool _hasFit: !!(activity && activity.fitBase64 && activity.fitBase64.length > 0)
    readonly property bool _fitFromIntervals: !!(activity && activity.source === "intervals")
    property string _exportFit: ""
    property bool _fetchingFit: false
    property string _fitError: ""
    function _openFitDialog() {
        const safeName = (activity.name || "activity").replace(/[\\/:*?"<>|]/g, "_")
        fitExportDialog.currentFile = LocalFileService.downloadsLocation + "/" + safeName + ".fit"
        fitExportDialog.open()
    }
    property int _resolvedCount: 0
    function _resolveTrack() {
        if (activity && activity.track && activity.track.length > 0) {
            _resolvedTrack = activity.track
            _resolvedCount = activity.trackPointCount || activity.track.length
        } else if (activity && activity.hasGps && activity.index !== undefined) {
            var r = ActivityService.trackFor(activity.index, activity.device || "")
            _resolvedTrack = (r && r.track) ? r.track : []
            _resolvedCount = (r && r.count) ? r.count : 0
        } else {
            _resolvedTrack = []
            _resolvedCount = 0
        }
    }
    readonly property var _center: ActivityViewModel.trackCenter(_resolvedTrack)

    // ---------------------------------------------------------------- tabs
    property int currentTab: 0
    readonly property var availableTabs: {
        const out = [{ label: qsTr("Overview"), idx: 0 }]
        const chartsWanted = sportCfg.chart !== null && sportCfg.chart !== undefined
        if ((chartsWanted && (_hasPlot || (sport === "pool_swim" && _hasLengths))) || _ruleSeries.length > 0)
            out.push({ label: qsTr("Charts"), idx: 1 })
        if (_hasPressedLaps) out.push({ label: qsTr("Laps"), idx: 2 })
        out.push({ label: qsTr("Export"), idx: 3 })
        out.push({ label: qsTr("Upload"), idx: 4 })
        return out
    }
    onAvailableTabsChanged: {
        for (let i = 0; i < availableTabs.length; i++) if (availableTabs[i].idx === currentTab) return
        currentTab = 0
    }
    // Logged Suunto App outputs (ruleoutput1..5) for this move - see tools/app_logging.py.
    readonly property var _ruleSeries: {
        var out = [];
        var ro = activity && activity.ruleOutputs ? activity.ruleOutputs : null;
        if (ro) {
            var keys = Object.keys(ro).sort();
            for (var i = 0; i < keys.length; ++i) {
                var s = ro[keys[i]];
                if (s && s.values && s.values.length > 0) out.push(s);
            }
        }
        return out;
    }

    // Gear attribution (manual per-activity picker). Key = the activity's start time (stable).
    function gearKey() { return activity ? (activity.startTime || activity.name || "") : "" }
    function sportName() {
        if (!activity) return ""
        var st = ActivityTypes.byId[activity.sportTypeRaw]
        return st ? st.name : ""
    }
    function gearChoices() {
        var out = [{ text: qsTr("None"), id: "" }]
        var all = GearService.gears
        for (var i = 0; i < all.length; ++i)
            if (!all[i].parentId && !all[i].retired) out.push({ text: all[i].name, id: all[i].id })
        return out
    }
    function currentGearIndex(choices) {
        var id = GearService.activityGearId(gearKey())
        if (!id) id = GearService.defaultGearForSport(sportName())
        for (var i = 0; i < choices.length; ++i) if (choices[i].id === id) return i
        return 0
    }

    component Seg: Rectangle {
        property bool on: false
        property string label: ""
        signal picked()
        width: segText.implicitWidth + 20; height: 26; radius: 13
        color: on ? Theme.cardNested : Theme.card
        border.color: on ? Theme.borderStrong : Theme.border
        Text { id: segText; anchors.centerIn: parent; text: parent.label; color: parent.on ? Theme.text : Theme.mutedText
               font.pixelSize: Theme.fontSizeLabel; font.bold: parent.on }
        TapHandler { onTapped: parent.picked() }
        HoverHandler { cursorShape: Qt.PointingHandCursor }
    }
    component ColourRow: Flow {
        spacing: 10
        Row {
            spacing: 4
            Repeater {
                model: (root.sportCfg.colour || []).filter(m => root._colourAvailable(m))
                delegate: Seg { label: root.cfg.colour_modes[modelData].label; on: root.colourMode === modelData
                                onPicked: root._pickColour(modelData) }
            }
            Seg { label: qsTr("Off"); on: root.colourMode === ""; onPicked: root.colourMode = "" }
        }
        Repeater {
            model: root._ribbon ? root._ribbon.shares : []
            delegate: Row {
                spacing: 5
                height: 26
                Rectangle { width: 10; height: 10; radius: 3; anchors.verticalCenter: parent.verticalCenter
                            color: root.zonePalette[modelData.colour] || Theme.primary }
                Text { text: modelData.name + "  " + modelData.pct + "%"; color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                       anchors.verticalCenter: parent.verticalCenter }
            }
        }
    }
    function _pickColour(m) {
        colourMode = m
        // the coloured metric becomes the main line (and is switched on)
        const key = cfg.colour_modes[m].key
        const ch = _chans.find(c => c.key === key)
        if (ch) { const on = Object.assign({}, chanOn); on[ch.id] = true; chanOn = on; focusId = ch.id }
    }
    function _zoomBy(f) {
        if (!_st) return
        const n = _st.streams.t.length - 1
        const a = chartZoom ? chartZoom[0] : 0, b = chartZoom ? chartZoom[1] : n
        const c = _hover >= 0 ? _hover : Math.round((a + b) / 2)
        const w = Math.max(8, Math.round((b - a) * f))
        if (w >= n) { _setZoomFromChart(null); return }
        let na = Math.max(0, c - Math.round(w / 2)), nb = Math.min(n, na + w)
        na = Math.max(0, nb - w)
        _setZoomFromChart([na, nb])
    }

    PageFlickable {
        anchors.fill: parent
        contentHeight: body.height + Theme.spacingLarge * 2
        clip: true

        Column {
            id: body
            width: parent.width
            spacing: Theme.spacingMedium

            Row {
                width: parent.width
                spacing: Theme.spacingSmall
                leftPadding: Theme.spacingLarge
                topPadding: Theme.spacingLarge
                Icon {
                    glyph: Icons.arrowBack
                    size: 20
                    anchors.verticalCenter: parent.verticalCenter
                    TapHandler { onTapped: root.back() }
                }
                // Same per-sport badge as the grid/list views (see ActivityCard.qml).
                ActivityBadge {
                    activityId: root._sportId
                    size: 24
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    text: activity ? (ActivityTypes.displayName(activity.name, activity.sportTypeRaw) || qsTr("Untitled activity")) : ""
                    font.pixelSize: Theme.fontSizeTitle
                    font.bold: true
                    color: Theme.text
                    anchors.verticalCenter: parent.verticalCenter
                }
                Text {
                    visible: root._stLoading
                    text: qsTr("reading the activity file…")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    anchors.verticalCenter: parent.verticalCenter
                }
            }

            Item {
                width: parent.width - Theme.spacingLarge * 2
                x: Theme.spacingLarge
                height: root._center !== null ? 300 : 36
                MapView {
                    id: map
                    anchors.fill: parent
                    // Scroll to zoom, at the pointer; the chart follows (see _chartFromMap).
                    scrollZoom: true
                    zoomAtCursor: true
                    visible: root._center !== null
                    latitude: root._center ? root._center.lat : 0
                    longitude: root._center ? root._center.lon : 0
                    zoomLevel: 13
                    showZoomControls: true
                    trackPoints: root._coloredSegments.length > 0 ? [] : root._resolvedTrack
                    coloredSegments: root._coloredSegments
                    cursorPoint: root._cursorPoint
                    highlightCoords: root._highlight
                    onViewportChanged: root._chartFromMap()
                }
                Rectangle {
                    visible: root._coords.length > 0
                    anchors.left: parent.left; anchors.top: parent.top; anchors.margins: 8
                    width: linkText.implicitWidth + 20; height: 24; radius: 12
                    color: Theme.card
                    border.color: root.linked ? Theme.primary : Theme.borderStrong
                    Text { id: linkText; anchors.centerIn: parent; text: root.linked ? qsTr("Linked to chart") : qsTr("Not linked")
                           color: root.linked ? Theme.primary : Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                    TapHandler { onTapped: root.linked = !root.linked }
                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                }
                Rectangle {
                    visible: root._center === null
                    anchors.fill: parent
                    color: "transparent"
                    radius: Theme.radiusSmall
                    border.color: Theme.borderStrong
                    Text { anchors.verticalCenter: parent.verticalCenter; x: 12
                           text: qsTr("No GPS track on this activity"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                }
            }
            ColourRow {
                visible: root._center !== null && (root.sportCfg.colour || []).some(m => root._colourAvailable(m))
                x: Theme.spacingLarge
                width: parent.width - Theme.spacingLarge * 2
            }

            // The weather it was done in (Open-Meteo history + wind along the track).
            ActivityWeather {
                x: Theme.spacingLarge
                activity: root.activity
                track: root._resolvedTrack
            }

            Row {
                x: Theme.spacingLarge
                spacing: Theme.spacingMedium
                Repeater {
                    model: root.availableTabs
                    delegate: Text {
                        text: modelData.label
                        font.bold: modelData.idx === root.currentTab
                        color: modelData.idx === root.currentTab ? Theme.primary : Theme.mutedText
                        TapHandler { onTapped: root.currentTab = modelData.idx }
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                    }
                }
            }

            Card {
                x: Theme.spacingLarge
                width: parent.width - Theme.spacingLarge * 2

                // A Column, so the card sizes to the VISIBLE tab only (Card uses childrenRect,
                // which would otherwise count the hidden tabs' heights too).
                Column {
                width: parent.width

                // --- Overview ---
                Column {
                    width: parent.width
                    visible: root.currentTab === 0
                    spacing: Theme.spacingMedium
                    ActivityOverview {
                        width: parent.width
                        sport: root.sport
                        facts: root.facts
                        st: root._st
                        advanced: Theme.advancedPowerNumbers
                        zonePalette: root.zonePalette
                        usual: root.usual
                    }
                    // Which device recorded this (intervals.icu imports; watch moves leave it empty).
                    Text {
                        visible: activity && (activity.device || "") !== ""
                        // intervals.icu repeats the brand ("SUUNTO Suunto Ambit3 Peak"): drop a doubled first word.
                        text: activity ? qsTr("Recorded on %1").arg(
                            String(activity.device).replace(/^(\S+)\s+(?=\1\b)/i, "")) : ""
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeLabel
                    }
                    // Gear used — attribute this move's mileage to a bike/shoes (local tally).
                    Row {
                        spacing: Theme.spacingSmall
                        visible: GearService.gears.length > 0
                        Text { text: qsTr("Gear used"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                               anchors.verticalCenter: parent.verticalCenter }
                        RoundedComboBox {
                            model: root.gearChoices()
                            textRole: "text"
                            currentIndex: root.currentGearIndex(model)
                            onActivated: GearService.attributeActivity(
                                root.gearKey(), model[currentIndex].id,
                                activity ? activity.distanceMeters : 0,
                                activity ? activity.durationSeconds : 0)
                        }
                    }
                }

                // --- Charts ---
                Column {
                    width: parent.width
                    visible: root.currentTab === 1
                    spacing: Theme.spacingMedium

                    SwimLengthsChart {
                        visible: root.sport === "pool_swim" && root._hasLengths
                        width: parent.width
                        st: root._st
                        zonePalette: root.zonePalette
                    }

                    Column {
                        visible: root._hasPlot && !(root.sport === "pool_swim" && root._hasLengths)
                        width: parent.width
                        spacing: Theme.spacingSmall
                        Flow {
                            width: parent.width
                            spacing: 6
                            Repeater {
                                model: root._chans
                                delegate: Seg {
                                    label: modelData.label
                                    on: !!root.chanOn[modelData.id]
                                    onPicked: {
                                        const on = Object.assign({}, root.chanOn)
                                        on[modelData.id] = !on[modelData.id]
                                        root.chanOn = on
                                        if (on[modelData.id]) root.focusId = modelData.id        // switched on = main line
                                        else if (root.focusId === modelData.id) root.focusId = ""
                                    }
                                }
                            }
                            Item { width: 12; height: 1 }
                            Seg { visible: !!(root._st && root._st.streams && root._st.streams.dist) && !!root.sportCfg.chart && root.sportCfg.chart.x !== "time"
                                  label: qsTr("Distance"); on: root.xDist; onPicked: root.xDist = true }
                            Seg { visible: !!(root._st && root._st.streams && root._st.streams.dist) && !!root.sportCfg.chart && root.sportCfg.chart.x !== "time"
                                  label: qsTr("Time"); on: !root.xDist; onPicked: root.xDist = false }
                            Item { width: 12; height: 1 }
                            Seg { label: "−"; onPicked: root._zoomBy(2) }
                            Seg { label: "+"; onPicked: root._zoomBy(0.5) }
                        }
                        ColourRow {
                            visible: root._center === null && (root.sportCfg.colour || []).some(m => root._colourAvailable(m))
                            width: parent.width
                        }
                        Item {
                            width: parent.width
                            height: Math.max(28, zoomInfo.implicitHeight)
                            Text {
                                id: zoomInfo
                                width: parent.width - (showAll.visible ? showAll.width + 10 : 0)
                                wrapMode: Text.WordWrap
                                anchors.verticalCenter: parent.verticalCenter
                                color: root.chartZoom ? Theme.text : Theme.mutedText
                                font.pixelSize: Theme.fontSizeLabel
                                text: root.chartZoom && root._st
                                      ? qsTr("Selected: %1").arg(AVL.sectionSummary(root._st, root._chansOn, root.chartZoom[0], root.chartZoom[1]))
                                      : qsTr("Scroll over the chart to zoom at the cursor, or drag across it to pick a stretch and see its numbers. Click a name above the chart to make it the main line.")
                            }
                            Seg {
                                id: showAll
                                visible: root.chartZoom !== null
                                anchors.right: parent.right
                                anchors.verticalCenter: parent.verticalCenter
                                label: qsTr("Show all")
                                onPicked: root._setZoomFromChart(null)
                            }
                        }
                        OverlayChart {
                            width: parent.width
                            height: implicitHeight
                            st: root._st
                            channels: root._chansOn
                            focusId: root.focusId !== "" ? root.focusId
                                   : (root._ribbon ? ((root._chansOn.find(c => c.key === root._ribbon.key) || {}).id || "") : "")
                            isDist: root.xDist && !!(root._st && root._st.streams && root._st.streams.dist)
                            zoom: root.chartZoom
                            ribbon: root._ribbon
                            hoverIdx: root._hover
                            zonePalette: root.zonePalette
                            target: root._planned
                            onHovered: (idx) => root._hover = idx
                            onZoomRequested: (range) => root._setZoomFromChart(range)
                            onFocusRequested: (id) => root.focusId = id
                        }
                        Text {
                            visible: root._planned !== null
                            width: parent.width
                            wrapMode: Text.WordWrap
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeLabel
                            text: root._planned ? qsTr("Planned on intervals.icu: %1 (%2). Its power targets are the shaded blocks behind your power, on the time axis.")
                                  .arg(root._planned.name).arg(AVL.fmtClock(root._planned.blocks[root._planned.blocks.length - 1].t1)) : ""
                        }
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            text: qsTr("All picked lines share one chart, each stretched to fill it: where two lines cross means nothing. Hover to read every value at once%1. Double-click the chart to see all of it again.")
                                  .arg(root._coords.length ? qsTr(" (the dot follows on the map)") : "")
                        }
                    }

                    Text {
                        visible: !root._stLoading && root._stError.length > 0 && root._ruleSeries.length === 0
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: root._stError
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeLabel
                    }

                    // Logged Suunto App outputs (ruleoutput1..5), when the move has them.
                    Text {
                        visible: root._ruleSeries.length > 0
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                        text: qsTr("Logged Suunto App output, recorded into the move (LogRule).")
                    }
                    Repeater {
                        model: root._ruleSeries
                        delegate: Column {
                            width: parent.width
                            spacing: Theme.spacingSmall
                            RuleOutputChart { width: parent.width; height: 200; series: modelData }
                            // Optional: also send this app's output to intervals.icu as a native stream.
                            Row {
                                width: parent.width
                                spacing: Theme.spacingSmall
                                Text { anchors.verticalCenter: parent.verticalCenter; text: qsTr("intervals.icu:")
                                       color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                                RoundedComboBox {
                                    id: streamCombo
                                    readonly property string appName: modelData && modelData.label ? modelData.label : ""
                                    readonly property var _keys: ["custom", "power", "cadence", "heartrate"]
                                    model: [qsTr("Custom stream (default)"), qsTr("Power"), qsTr("Cadence"), qsTr("Heart rate")]
                                    currentIndex: Math.max(0, _keys.indexOf(ActivityService.intervalsStreamFor(appName) || "custom"))
                                    onActivated: ActivityService.setIntervalsStreamFor(appName, _keys[currentIndex])
                                }
                                Text { anchors.verticalCenter: parent.verticalCenter; visible: streamCombo.currentIndex > 0
                                       text: qsTr("— applies on next sync"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                            }
                        }
                    }
                }

                // --- Laps (only laps you pressed on the watch) ---
                Column {
                    id: lapsTab
                    width: parent.width
                    visible: root.currentTab === 2
                    spacing: 0
                    readonly property var laps: root._st && root._st.laps ? root._st.laps : []
                    readonly property var heads: [qsTr("Lap"), qsTr("Time"), qsTr("Distance"), root._foot ? qsTr("Pace") : qsTr("Speed"),
                                                  qsTr("Avg HR"), qsTr("Max HR"), root._foot ? qsTr("Cadence") : qsTr("Power")]
                    Row {
                        width: parent.width
                        height: 30
                        Repeater {
                            model: lapsTab.heads
                            delegate: Text { width: lapsTab.width / 7; text: modelData; color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                                             horizontalAlignment: index === 0 ? Text.AlignLeft : Text.AlignRight }
                        }
                    }
                    Repeater {
                        model: lapsTab.laps
                        delegate: Rectangle {
                            id: lapRow
                            width: lapsTab.width
                            height: 32
                            radius: 6
                            color: lapHover.hovered ? Theme.cardNested : "transparent"
                            readonly property var lap: modelData
                            readonly property var cells: [
                                String(index + 1),
                                AVL.fmtClock(lap.timer_s || 0),
                                lap.dist_m ? (lap.dist_m >= 1000 ? (lap.dist_m / 1000).toFixed(2) + " km" : Math.round(lap.dist_m) + " m") : "",
                                lap.avg_speed ? (root._foot ? AVL.paceText(lap.avg_speed, 1000) + " /km" : (lap.avg_speed * 3.6).toFixed(1) + " km/h") : "",
                                lap.avg_hr ? String(lap.avg_hr) : "",
                                lap.max_hr ? String(lap.max_hr) : "",
                                root._foot ? (lap.avg_cad ? Math.round(lap.avg_cad) + " spm" : "") : (lap.avg_pw ? lap.avg_pw + " W" : "")
                            ]
                            // Hover a lap: the chart and map show that stretch.
                            HoverHandler {
                                id: lapHover
                                onHoveredChanged: {
                                    if (!hovered) { root._setZoomFromChart(null); return }
                                    if (!root._st || lapRow.lap.start_s === null || lapRow.lap.start_s === undefined) return
                                    const t = root._st.streams.t, t0 = t[0], end = lapRow.lap.start_s + (lapRow.lap.elapsed_s || lapRow.lap.timer_s || 0)
                                    let a = 0, b = t.length - 1
                                    for (let i = 0; i < t.length; i++) { if (t[i] - t0 <= lapRow.lap.start_s) a = i; if (t[i] - t0 <= end) b = i }
                                    root._setZoomFromChart([a, b])
                                }
                            }
                            Row {
                                anchors.fill: parent
                                Repeater {
                                    model: lapRow.cells
                                    delegate: Text { width: lapRow.width / 7; text: modelData; color: Theme.text; font.pixelSize: Theme.fontSizeLabel
                                                     anchors.verticalCenter: parent.verticalCenter
                                                     horizontalAlignment: index === 0 ? Text.AlignLeft : Text.AlignRight }
                                }
                            }
                        }
                    }
                    Text {
                        width: parent.width
                        topPadding: 8
                        wrapMode: Text.WordWrap
                        text: qsTr("Hover a lap to see it on the map and in the chart.")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }

                // --- Export: real, 2026-08-07 ---
                Column {
                    width: parent.width
                    visible: root.currentTab === 3
                    spacing: Theme.spacingSmall
                    Row {
                        spacing: Theme.spacingSmall
                        RoundedButton {
                            text: root._hasGpxFile ? qsTr("Export as GPX") : qsTr("Export track as GPX")
                            enabled: root._hasGpxFile || root._resolvedCount > 0
                            onClicked: {
                                root._exportGpx = root._hasGpxFile ? activity.gpxText
                                    : ActivityService.trackGpx(activity.index, activity.device || "", activity.name || "")
                                const safeName = (activity.name || "activity").replace(/[\\/:*?"<>|]/g, "_")
                                gpxExportDialog.currentFile = LocalFileService.downloadsLocation + "/" + safeName + ".gpx"
                                gpxExportDialog.open()
                            }
                        }
                        RoundedButton {
                            text: root._fetchingFit ? qsTr("Getting FIT from intervals.icu…") : qsTr("Export as FIT")
                            enabled: !root._fetchingFit && (root._hasFit || root._fitFromIntervals)
                            onClicked: {
                                root._fitError = ""
                                if (root._hasFit) {
                                    root._exportFit = activity.fitBase64
                                    root._openFitDialog()
                                } else {
                                    root._fetchingFit = true
                                    ActivityService.fetchIntervalsFit(activity.index, activity.device || "")
                                }
                            }
                        }
                    }
                    Text {
                        visible: activity && !root._hasFit && !root._fitFromIntervals
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                        text: root._hasGpxFile ? qsTr("No FIT file for this activity - GPX only.")
                            : root._resolvedCount > 0
                              ? qsTr("This activity came without its original file, so there's no FIT. "
                                     + "The GPX holds the route only: positions and elevation, no times "
                                     + "or heart rate.")
                              : qsTr("This activity has no GPS track and no file to export.")
                    }
                    Text {
                        visible: root._fitFromIntervals && !root._hasFit && root._fitError.length === 0
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                        text: qsTr("This activity came from intervals.icu: its FIT is downloaded from there "
                                   + "when you export it (the original file when it has one), then kept here.")
                    }
                    Text {
                        visible: root._fitError.length > 0
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.error
                        font.pixelSize: Theme.fontSizeLabel
                        text: root._fitError
                    }
                    Text {
                        visible: root.saveError.length > 0
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.error
                        font.pixelSize: Theme.fontSizeLabel
                        text: qsTr("Couldn't save: %1").arg(root.saveError)
                    }
                }

                // --- Upload tab: push this activity to intervals.icu ---
                Column {
                    width: parent.width
                    visible: root.currentTab === 4
                    spacing: Theme.spacingMedium
                    readonly property bool _hasData: activity
                        && ((activity.fitBase64 && activity.fitBase64.length > 0)
                            || (activity.gpxText && activity.gpxText.length > 0))
                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                        text: qsTr("Push this activity to intervals.icu. A watch move uploads its " +
                                   "FIT — including the logged Suunto App graphs, which Suunto's " +
                                   "own sync doesn't carry. An eTrex move uploads its GPX.")
                    }
                    Row {
                        width: parent.width
                        spacing: Theme.spacingSmall
                        RoundedButton {
                            text: root.uploading ? qsTr("Uploading…") : qsTr("Export to intervals.icu")
                            enabled: parent.parent._hasData && !root.uploading
                            onClicked: {
                                root.uploading = true
                                root.uploadStatus = ""
                                ActivityService.exportActivityToIntervals(activity.name || "", activity.fitBase64 || "",
                                                                          activity.gpxText || "")
                            }
                        }
                        RoundedButton {
                            text: qsTr("Export to Garmin")
                            enabled: parent.parent._hasData && !root.uploading
                            onClicked: {
                                root.uploading = true
                                root.uploadStatus = ""
                                ActivityService.exportActivityToGarmin(activity.name || "", activity.fitBase64 || "",
                                                                       activity.gpxText || "")
                            }
                        }
                    }
                    Text {
                        visible: root.uploadStatus.length > 0
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: root.uploadStatus
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    Text {
                        visible: !parent._hasData
                        text: qsTr("This activity has no FIT or GPX file to upload.")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }
                }
            }
        }
    }
}
