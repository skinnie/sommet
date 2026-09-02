import QtQuick
import AmbitApp

// Step 6, rebuilt 2026-08-07. Originally QtLocation's real MapView + a plugin (first
// maplibre-native-qt, then Qt's built-in "osm" plugin) - both abandoned after real hardware
// testing: maplibre-native-qt crashed outright (std::bad_alloc, reproducible even under
// forced software OpenGL, so not a driver quirk), and Qt's "osm" plugin kept showing a
// "missing API key" watermark that two separate parameter-based fixes (osm.mapping.host,
// then double-checked against Qt's own current docs) failed to clear - `strings` on the
// actual installed plugin binary (libqtgeoservices_osm.so) found zero occurrences of any
// "osm.mapping.*" parameter name at all, meaning this Qt 6.12 build's real configuration
// surface doesn't match what's documented, and guessing a third time wasn't worth it.
//
// This is a plain, direct XYZ slippy-tile renderer instead - no GeoServices plugin, no
// proxy, no undocumented configuration surface. Standard Web Mercator tile math, real and
// unremarkable (the same formula every slippy map uses). Every caller (route previews,
// activity maps, POI previews) keeps the exact same public API (latitude/longitude/
// zoomLevel/trackPoints/showMarker) - none of them needed to change for the tile renderer
// itself.
//
// Real request 2026-08-07 ("missing zoom +/-, improve track visibility, zoom in more by
// default so the trace is visible"): three real, related fixes, all here so every caller
// gets them at once rather than duplicating fit/zoom logic per page -
//   1. showZoomControls (opt-in - small thumbnails don't want +/- buttons cluttering them)
//   2. the track polyline is drawn with a white halo underneath a high-contrast color,
//      not Theme.primary - that teal blends into OSM/CyclOSM's own parks-and-water palette
//   3. currentZoom auto-fits to the track's real bounding box when one exists, instead of
//      the caller's own averaged trackCenter() + a fixed guessed zoomLevel
Item {
    id: root

    property real latitude: 46.8
    property real longitude: 8.2   // Alps-ish default - genuinely arbitrary, real center
                                    // comes from whatever real content (a route, an
                                    // activity) each caller actually has to show
    property real zoomLevel: 12    // only used when there's no track to fit to
    property var trackPoints: []   // [{lat, lon}, ...] - draws a line if 2+ points given
    property bool showMarker: false  // draws a single pin at (latitude, longitude)
    property bool showZoomControls: false
    // Real, 2026-08-09 ("Add a world map with the points were kailash has been") - discrete
    // pins at real positions, genuinely different from trackPoints (a connected line - right
    // for a continuous GPS breadcrumb trail, wrong for a list of separately-visited places)
    // and from showMarker (always exactly one pin, always centered - right for "here's the
    // one place being edited," wrong for "here's everywhere a watch has been"). Each entry
    // may carry an optional `label` (shown nowhere yet - reserved for a future tap/tooltip).
    property var markers: []       // [{lat, lon, label?}, ...] - each drawn as its own pin

    // Climb-coloured route overlay (the offline planner, PlanRoutePage) - each entry is
    // { color: "#rrggbb", coords: [[lat, lon], ...] }, one contiguous stretch of the route
    // in its gradient-bucket colour. Drawn with the same white-halo-then-stroke cartography
    // as trackPoints, but one stroke per segment so a single route changes colour along its
    // length. Empty by default, so every existing MapView caller renders exactly as before.
    property var coloredSegments: []

    // Wind arrows overlay (PlanRoutePage weather layer) - one short arrow per sampled point,
    // pointing the way the wind blows (meteorological "from" direction + 180), sized by speed
    // and coloured by its relation to your heading (head/cross/tail), each labelled with its
    // speed. Additive: empty by default, so no other MapView caller is affected.
    property var windArrows: []   // [{lat, lon, wind_kmh, wind_dir_deg, rel, color}]

    // Rain markers overlay (PlanRoutePage weather layer) - a raindrop wherever meaningful rain
    // is forecast along the route (André, 2026-08-31: "some rain icon if they appear"). Additive:
    // empty by default.
    property var rainMarks: []    // [{lat, lon, rain_mm}]

    // Temperature labels overlay (PlanRoutePage "climb + temperature" mode) - °C / feels-like at
    // points along the route (André, 2026-08-31). Additive: empty by default.
    property var tempMarks: []    // [{lat, lon, temp_c, feels_c}]

    // Route-cursor sync (PlanRoutePage graph<->map, André 2026-08-31). `highlightDist` = metres
    // along the route to mark with a dot; the page sets it from a graph hover. Reverse: hovering
    // the route emits routeHovered(dist) so the page can move the graph crosshair. Additive.
    property real highlightDist: -1
    signal routeHovered(real dist)
    signal routeHoverEnded()
    // Whole route flattened to points + cumulative metres, so a distance maps to a coordinate
    // (and back). Rebuilt when coloredSegments changes.
    property var _routePts: []    // [[lat, lon], ...]
    property var _routeCum: []    // cumulative metres, same length
    function _haversine(la1, lo1, la2, lo2) {
        var R = 6371000, d = Math.PI / 180
        var dLa = (la2 - la1) * d, dLo = (lo2 - lo1) * d
        var a = Math.sin(dLa / 2) * Math.sin(dLa / 2)
              + Math.cos(la1 * d) * Math.cos(la2 * d) * Math.sin(dLo / 2) * Math.sin(dLo / 2)
        return 2 * R * Math.atan2(Math.sqrt(a), Math.sqrt(1 - a))
    }
    function _rebuildRouteMetrics() {
        var pts = [], cum = [], total = 0
        var segs = coloredSegments || []
        for (var s = 0; s < segs.length; s++) {
            var cs = segs[s].coords || []
            for (var i = 0; i < cs.length; i++) {
                if (pts.length > 0) {
                    var a = pts[pts.length - 1]
                    total += _haversine(a[0], a[1], cs[i][0], cs[i][1])
                }
                pts.push(cs[i]); cum.push(total)
            }
        }
        _routePts = pts; _routeCum = cum
        _rangeTimer.restart()
    }
    function _coordAtDist(dist) {
        var pts = _routePts, cum = _routeCum
        if (pts.length === 0) return null
        if (dist <= 0) return pts[0]
        if (dist >= cum[cum.length - 1]) return pts[pts.length - 1]
        for (var i = 1; i < cum.length; i++) {
            if (cum[i] >= dist) {
                var t = (dist - cum[i - 1]) / Math.max(1e-6, cum[i] - cum[i - 1])
                return [pts[i - 1][0] + (pts[i][0] - pts[i - 1][0]) * t,
                        pts[i - 1][1] + (pts[i][1] - pts[i - 1][1]) * t]
            }
        }
        return pts[pts.length - 1]
    }

    // The fraction of the route [startFrac, endFrac] currently visible in the viewport, emitted
    // whenever the map pans/zooms so the Plan graphs can follow the map (André, 2026-08-31).
    signal routeVisibleRange(real startFrac, real endFrac)
    function _emitVisibleRange() {
        var pts = _routePts, cum = _routeCum
        if (pts.length < 2) return
        var total = cum[cum.length - 1]
        if (total <= 0) return
        var minC = -1, maxC = -1
        for (var i = 0; i < pts.length; i++) {
            var px = lonToWorldX(pts[i][1]) - originX
            var py = latToWorldY(pts[i][0]) - originY
            if (px >= 0 && px <= width && py >= 0 && py <= height) {
                if (minC < 0 || cum[i] < minC) minC = cum[i]
                if (cum[i] > maxC) maxC = cum[i]
            }
        }
        if (minC < 0) return    // route not on screen - leave the graphs where they are
        routeVisibleRange(minC / total, maxC / total)
    }
    Timer { id: _rangeTimer; interval: 50; onTriggered: root._emitVisibleRange() }
    onOriginXChanged: _rangeTimer.restart()
    onOriginYChanged: _rangeTimer.restart()

    clip: true

    readonly property int tileSize: 256

    // The real bounding box (in degrees) of everything there is to fit to - trackPoints AND
    // markers together, so a world map of scattered visited-place pins with no track at all
    // still auto-fits correctly (trackPoints alone was the only input before markers
    // existed). Null with fewer than 2 points total - nothing to fit to; falls back to
    // latitude/longitude/zoomLevel as before.
    readonly property var _trackBounds: {
        const points = (trackPoints || []).concat(markers || [])
        // A coloured route (planner) has no trackPoints - fold its own vertices in so the
        // view still auto-fits to the whole planned route, not just its waypoint pins.
        for (const seg of (coloredSegments || []))
            for (const c of (seg.coords || []))
                points.push({ lat: c[0], lon: c[1] })
        if (points.length < 2) return null
        let minLat = 90, maxLat = -90, minLon = 180, maxLon = -180
        for (const p of points) {
            minLat = Math.min(minLat, p.lat); maxLat = Math.max(maxLat, p.lat)
            minLon = Math.min(minLon, p.lon); maxLon = Math.max(maxLon, p.lon)
        }
        return { minLat, maxLat, minLon, maxLon }
    }

    // A single real point (no bbox to compute - _trackBounds needs 2+) still deserves to be
    // the real center, not the arbitrary Alps-ish latitude/longitude default - falls back to
    // that default only when there's truly nothing real to show at all.
    readonly property var _singlePoint: {
        const points = (trackPoints || []).concat(markers || [])
        return points.length === 1 ? points[0] : null
    }
    readonly property real _centerLat:
        _trackBounds ? (_trackBounds.minLat + _trackBounds.maxLat) / 2
        : _singlePoint ? _singlePoint.lat : latitude
    readonly property real _centerLon:
        _trackBounds ? (_trackBounds.minLon + _trackBounds.maxLon) / 2
        : _singlePoint ? _singlePoint.lon : longitude

    // World-pixel span of a degree box at a given zoom - the same projection tileZ/
    // tilesPerSide/lonToWorldX/latToWorldY use below, parameterized so it can be tried at
    // several candidate zooms before one is picked (those can't be reused directly: they're
    // derived *from* the already-chosen zoom, and this runs before that choice is made).
    function _spanPxAt(z, bounds) {
        const tilesPerSide = Math.pow(2, z)
        const lonSpan = Math.max(0.00001, bounds.maxLon - bounds.minLon)
        const pxPerLon = tilesPerSide * tileSize / 360
        const mercAt = (lat) => Math.log(Math.tan(Math.PI / 4 + (lat * Math.PI / 180) / 2))
        const spanLatPx = Math.abs(mercAt(bounds.maxLat) - mercAt(bounds.minLat))
                           / (2 * Math.PI) * tilesPerSide * tileSize
        return { w: lonSpan * pxPerLon, h: spanLatPx }
    }

    // Auto-fit zoom: the highest zoom where the track's bbox (with real padding around it)
    // still fits inside this view. Only takes over when there's a real track and real size
    // to fit into - a thumbnail card and the large detail view naturally end up at
    // different zooms for the same track, which is correct (more screen space fits a
    // tighter view). Real request 2026-08-08 ("make the default zoom level enough so we
    // can see the full trace") - margin loosened twice (0.8 -> 0.65 -> 0.5, i.e. 50%
    // padding) after real eTrex hardware testing kept showing a real track still slightly
    // cropped - erring toward showing clearly more than the bare minimum by default.
    // Component.onCompleted now defers its first fit via Qt.callLater(): a real, standard
    // QML technique for "wait until this item's own layout pass (anchors.fill against a
    // parent whose own size may still be settling through a Card/Column chain) has actually
    // finished" - calling _refitZoom() synchronously here could still see a stale width on
    // a items several layout levels deep, which onWidthChanged then only partially
    // recovers from since it re-fits from whatever *that* stale width was, not a fully
    // resolved one.
    property int currentZoom: zoomLevel
    function _refitZoom() {
        if (userControlled)
            return
        if (!_trackBounds || width <= 0 || height <= 0) {
            currentZoom = zoomLevel
            return
        }
        const margin = 0.5
        let z = 18
        for (; z > 1; z--) {
            const span = _spanPxAt(z, _trackBounds)
            if (span.w <= width * margin && span.h <= height * margin) break
        }
        currentZoom = z
    }
    Component.onCompleted: { _rebuildRouteMetrics(); Qt.callLater(_refitZoom) }
    onTrackPointsChanged: Qt.callLater(_refitZoom)
    onMarkersChanged: Qt.callLater(_refitZoom)
    onColoredSegmentsChanged: { _rebuildRouteMetrics(); Qt.callLater(_refitZoom) }
    onWidthChanged: _refitZoom()
    onHeightChanged: _refitZoom()

    // NOT named "z" - that's Item's own built-in stacking-order property (FINAL, can't be
    // overridden) - this exact collision was a real, severe bug: it made this whole
    // component fail to load at all ("Cannot override FINAL property"), which is why every
    // page embedding a MapView (Activities, Routes, POIs) went white - found via the real
    // app log, 2026-08-07.
    readonly property int tileZ: Math.max(0, Math.min(19, Math.round(currentZoom)))
    readonly property real tilesPerSide: Math.pow(2, tileZ)

    // Standard Web Mercator projection - lon/lat to "world pixel" coordinates at this zoom
    // (i.e. pixel position if every tile at this zoom were laid out in one giant image).
    function lonToWorldX(lon) {
        return (lon + 180) / 360 * tilesPerSide * tileSize
    }
    function latToWorldY(lat) {
        const rad = lat * Math.PI / 180
        const merc = Math.log(Math.tan(rad) + 1 / Math.cos(rad))
        return (1 - merc / Math.PI) / 2 * tilesPerSide * tileSize
    }

    // The inverse - item coordinates back to lon/lat. Needed to place a POI by clicking the
    // map (André, 2026-08-11, item 18: "search on a map, like we do for google maps, and once
    // we select it inputs coordinates"). Same projection as above, solved the other way, so a
    // point clicked and then re-drawn lands back exactly where it was clicked.
    function lonAtX(x) {
        return (x + originX) / (tilesPerSide * tileSize) * 360 - 180
    }
    function latAtY(y) {
        const merc = (1 - 2 * (y + originY) / (tilesPerSide * tileSize)) * Math.PI
        return Math.atan(Math.sinh(merc)) * 180 / Math.PI
    }

    // Manual panning, in world pixels at the CURRENT zoom - real request 2026-08-11
    // (André, R2: a bigger map you can move around). Zero by default, so every existing
    // caller (the thumbnails on Activities/Routes/POIs) renders exactly as before. Applied
    // here rather than by moving latitude/longitude because originX/originY is the single
    // place world coordinates become item coordinates: tiles, the track line and the
    // markers all derive from it, so they pan together for free.
    property real panX: 0
    property real panY: 0
    // Once the user has panned or zoomed, stop re-fitting the view under them.
    property bool userControlled: false

    readonly property real originX: lonToWorldX(_centerLon) - width / 2 + panX
    readonly property real originY: latToWorldY(_centerLat) - height / 2 + panY

    // Scroll to zoom - real request, 2026-08-11 (André): "inside every map with zoom, can we
    // alocate scroll up to zoom in and scroll down to zoom out?". Lives here rather than
    // being repeated in each map's owner, so every map behaves the same way and a new map
    // gets it for free.
    //
    // OPT-IN, and that is the point rather than an oversight: several of this app's maps are
    // small previews sitting inside a scrolling page (the activity cards' grid, Home's own
    // preview). A wheel handler on those would swallow the scroll meant for the page, so the
    // list would stop scrolling whenever the pointer crossed a thumbnail. Enabled on maps
    // the user actually navigates; left off on maps that are pictures.
    property bool scrollZoom: false

    WheelHandler {
        enabled: root.scrollZoom
        acceptedDevices: PointerDevice.Mouse | PointerDevice.TouchPad
        // Up (positive angleDelta) zooms in, down zooms out - the direction every map
        // application uses.
        onWheel: (event) => root.zoomBy(event.angleDelta.y > 0 ? 1 : -1)
    }

    // Trackpad pinch to zoom (André, 2026-08-29: "allow zoom by trackpad pinch"). macOS
    // delivers a native magnify gesture, which a PinchHandler picks up; we translate its
    // continuous scale into the same discrete zoom steps as the wheel, so pinch and scroll
    // agree and the pan-anchoring in zoomBy() is reused. Same opt-in as scrollZoom (a picture
    // thumbnail shouldn't grab the gesture). target:null so it zooms the map, not transforms
    // the Item.
    PinchHandler {
        enabled: root.scrollZoom
        target: null
        property int _startZoom: 12
        onActiveChanged: if (active) _startZoom = root.currentZoom
        onActiveScaleChanged: {
            if (!active) return
            const want = Math.round(_startZoom + Math.log2(activeScale))
            const steps = want - root.currentZoom
            if (steps !== 0) root.zoomBy(steps)
        }
    }

    // Zoom a step, keeping the panned view anchored: pan is measured in pixels at the
    // current zoom, so it has to be rescaled when that zoom changes or the map jumps.
    function zoomBy(steps) {
        const next = Math.max(1, Math.min(19, currentZoom + steps))
        if (next === currentZoom)
            return
        const factor = Math.pow(2, next - currentZoom)
        panX *= factor
        panY *= factor
        userControlled = true
        currentZoom = next
    }

    function resetView() {
        panX = 0
        panY = 0
        userControlled = false
        _refitZoom()
    }

    // Center the view on a specific coordinate at an optional zoom, regardless of any track
    // the map is currently auto-fitting to - used by the Plan page's "locate me" button to
    // jump to the detected location (André, 2026-08-29: "center by localization"). Works by
    // panning in world pixels relative to the current fit-center, then marking the view
    // user-controlled so the auto-fit doesn't snap back under it.
    function centerOn(lat, lon, zoom) {
        if (zoom !== undefined)
            currentZoom = Math.max(1, Math.min(19, zoom))
        userControlled = true
        panX = lonToWorldX(lon) - lonToWorldX(_centerLon)
        panY = latToWorldY(lat) - latToWorldY(_centerLat)
    }

    Repeater {
        model: {
            if (root.width <= 0 || root.height <= 0) return []
            const minTx = Math.floor(root.originX / root.tileSize)
            const maxTx = Math.floor((root.originX + root.width) / root.tileSize)
            const minTy = Math.floor(root.originY / root.tileSize)
            const maxTy = Math.floor((root.originY + root.height) / root.tileSize)
            const tiles = []
            for (let tx = minTx; tx <= maxTx; tx++) {
                for (let ty = minTy; ty <= maxTy; ty++) {
                    if (tx >= 0 && ty >= 0 && tx < root.tilesPerSide && ty < root.tilesPerSide) {
                        tiles.push({ x: tx, y: ty })
                    }
                }
            }
            return tiles
        }
        delegate: Image {
            x: modelData.x * root.tileSize - root.originX
            y: modelData.y * root.tileSize - root.originY
            width: root.tileSize
            height: root.tileSize
            // Through MapService's own builder - see its comment on why the provider owns
            // the URL shape (IGN's is query parameters, not path segments).
            source: MapService.tileUrl(root.tileZ, modelData.x, modelData.y)
            asynchronous: true
            cache: true
        }
    }

    // Track polyline - plain Canvas, not QtQuick.Shapes: a long-stable QML API, no risk of
    // the same kind of version-specific surprise that just cost two build cycles above.
    // Drawn twice on the same path (a white halo, then the real color on top) - a real
    // cartography technique, not decoration: a flat Theme.primary teal was found to blend
    // into OSM/CyclOSM's own parks-and-water palette, real bug reported 2026-08-07.
    Canvas {
        id: trackCanvas
        anchors.fill: parent
        visible: root.trackPoints.length > 1
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            if (root.trackPoints.length < 2) return
            ctx.lineJoin = "round"
            ctx.lineCap = "round"
            ctx.beginPath()
            for (let i = 0; i < root.trackPoints.length; i++) {
                const p = root.trackPoints[i]
                const px = root.lonToWorldX(p.lon) - root.originX
                const py = root.latToWorldY(p.lat) - root.originY
                if (i === 0) ctx.moveTo(px, py)
                else ctx.lineTo(px, py)
            }
            ctx.strokeStyle = "rgba(255, 255, 255, 0.85)"
            ctx.lineWidth = 6
            ctx.stroke()
            // Theme.primary - real request 2026-08-08: "same green as Left buttons when
            // selected" (NavItem.qml's own selected-row color), for brand consistency with
            // the rest of the app. The white halo above still does the actual contrast work
            // against whatever's under it, same technique, just recolored.
            // Theme.mapAccent, not Theme.primary: this is drawn on map tiles, which are
            // light in either theme - see Theme.qml's own comment.
            ctx.strokeStyle = Theme.mapAccent
            ctx.lineWidth = 3.5
            ctx.stroke()
        }
        Connections {
            target: root
            function onTrackPointsChanged() { trackCanvas.requestPaint() }
            function onOriginXChanged() { trackCanvas.requestPaint() }
            function onOriginYChanged() { trackCanvas.requestPaint() }
            function onWidthChanged() { trackCanvas.requestPaint() }
            function onHeightChanged() { trackCanvas.requestPaint() }
        }
    }

    // Climb-coloured route (coloredSegments) - same halo-then-stroke technique as
    // trackCanvas, but the halo is drawn for every segment first so the coloured strokes
    // then sit on one continuous white outline (no halo seams where two colours meet), and
    // each segment strokes in its own gradient-bucket colour. Sits above trackCanvas; a
    // caller uses one or the other, not both.
    Canvas {
        id: colorCanvas
        anchors.fill: parent
        visible: root.coloredSegments.length > 0
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const segs = root.coloredSegments
            if (!segs || segs.length === 0) return
            ctx.lineJoin = "round"
            ctx.lineCap = "round"
            const trace = (seg) => {
                const coords = seg.coords
                if (!coords || coords.length < 2) return false
                ctx.beginPath()
                for (let i = 0; i < coords.length; i++) {
                    const px = root.lonToWorldX(coords[i][1]) - root.originX
                    const py = root.latToWorldY(coords[i][0]) - root.originY
                    if (i === 0) ctx.moveTo(px, py)
                    else ctx.lineTo(px, py)
                }
                return true
            }
            ctx.strokeStyle = "rgba(255, 255, 255, 0.85)"
            ctx.lineWidth = 7
            for (const seg of segs) { if (trace(seg)) ctx.stroke() }
            ctx.lineWidth = 4
            for (const seg of segs) {
                if (trace(seg)) { ctx.strokeStyle = seg.color || Theme.mapAccent; ctx.stroke() }
            }

            // Direction-of-travel chevrons (André, 2026-08-31: "we need direction of the route").
            // Flatten the whole route to screen points, then drop a ">" chevron every ~75 px,
            // rotated to the local heading. White halo then dark so it reads over any colour.
            const pts = []
            for (const seg of segs) {
                const cs = seg.coords || []
                for (let i = 0; i < cs.length; i++)
                    pts.push([root.lonToWorldX(cs[i][1]) - root.originX,
                              root.latToWorldY(cs[i][0]) - root.originY])
            }
            if (pts.length >= 2) {
                const step = 75, arm = 6, spread = 2.5
                let dist = 0, next = step * 0.6
                for (let i = 1; i < pts.length; i++) {
                    let dx = pts[i][0] - pts[i - 1][0], dy = pts[i][1] - pts[i - 1][1]
                    const segLen = Math.hypot(dx, dy)
                    if (segLen < 0.001) continue
                    dx /= segLen; dy /= segLen
                    const ang = Math.atan2(dy, dx)
                    while (dist + segLen >= next) {
                        const t = next - dist
                        const cx = pts[i - 1][0] + dx * t, cy = pts[i - 1][1] + dy * t
                        for (let pass = 0; pass < 2; pass++) {
                            ctx.strokeStyle = pass === 0 ? "rgba(255,255,255,0.9)" : "rgba(20,24,28,0.85)"
                            ctx.lineWidth = pass === 0 ? 4 : 2
                            ctx.beginPath()
                            ctx.moveTo(cx - Math.cos(ang - spread) * arm, cy - Math.sin(ang - spread) * arm)
                            ctx.lineTo(cx, cy)
                            ctx.lineTo(cx - Math.cos(ang + spread) * arm, cy - Math.sin(ang + spread) * arm)
                            ctx.stroke()
                        }
                        next += step
                    }
                    dist += segLen
                }
            }
        }
        Connections {
            target: root
            function onColoredSegmentsChanged() { colorCanvas.requestPaint() }
            function onOriginXChanged() { colorCanvas.requestPaint() }
            function onOriginYChanged() { colorCanvas.requestPaint() }
            function onWidthChanged() { colorCanvas.requestPaint() }
            function onHeightChanged() { colorCanvas.requestPaint() }
        }
    }

    // Wind arrows (weather layer) - drawn above the coloured route with the same
    // world-pixel projection, so they track pan/zoom. Halo-then-stroke like the route, plus a
    // small speed label. Empty by default; only PlanRoutePage's weather mode fills it.
    Canvas {
        id: windCanvas
        anchors.fill: parent
        visible: root.windArrows.length > 0
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const arrows = root.windArrows
            if (!arrows || arrows.length === 0) return
            ctx.lineJoin = "round"
            ctx.lineCap = "round"
            ctx.font = "600 10px monospace"
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            for (const a of arrows) {
                const px = root.lonToWorldX(a.lon) - root.originX
                const py = root.latToWorldY(a.lat) - root.originY
                const beta = (a.wind_dir_deg + 180) * Math.PI / 180   // "blows toward" bearing
                const dx = Math.sin(beta), dy = -Math.cos(beta)       // screen unit vector (N up)
                const L = 10 + Math.min(a.wind_kmh, 40) * 0.5
                const x0 = px - dx * L / 2, y0 = py - dy * L / 2
                const x1 = px + dx * L / 2, y1 = py + dy * L / 2
                const ang = Math.atan2(dy, dx), hl = 5
                const b1 = ang + Math.PI - 0.44, b2 = ang + Math.PI + 0.44
                const col = a.color || Theme.mapAccent
                for (let pass = 0; pass < 2; pass++) {           // halo pass, then colour pass
                    ctx.strokeStyle = pass === 0 ? "rgba(255,255,255,0.9)" : col
                    ctx.lineWidth = pass === 0 ? 4 : 2.2
                    ctx.beginPath(); ctx.moveTo(x0, y0); ctx.lineTo(x1, y1); ctx.stroke()
                    ctx.beginPath()
                    ctx.moveTo(x1, y1); ctx.lineTo(x1 + Math.cos(b1) * hl, y1 + Math.sin(b1) * hl)
                    ctx.moveTo(x1, y1); ctx.lineTo(x1 + Math.cos(b2) * hl, y1 + Math.sin(b2) * hl)
                    ctx.stroke()
                }
                const label = Math.round(a.wind_kmh).toString()
                ctx.lineWidth = 3
                ctx.strokeStyle = "rgba(255,255,255,0.95)"
                ctx.strokeText(label, px, py - 11)
                ctx.fillStyle = col
                ctx.fillText(label, px, py - 11)
            }
        }
        Connections {
            target: root
            function onWindArrowsChanged() { windCanvas.requestPaint() }
            function onOriginXChanged() { windCanvas.requestPaint() }
            function onOriginYChanged() { windCanvas.requestPaint() }
            function onWidthChanged() { windCanvas.requestPaint() }
            function onHeightChanged() { windCanvas.requestPaint() }
        }
    }

    // Rain icons (weather layer) - a small blue raindrop wherever rain is forecast along the
    // route, so wet stretches jump out over the climb-coloured route (André, 2026-08-31). Same
    // world-pixel projection as everything else, so they track pan/zoom.
    Canvas {
        id: rainCanvas
        anchors.fill: parent
        visible: root.rainMarks.length > 0
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const marks = root.rainMarks
            if (!marks || marks.length === 0) return
            const r = 5
            for (const m of marks) {
                const px = root.lonToWorldX(m.lon) - root.originX
                const py = root.latToWorldY(m.lat) - root.originY
                // teardrop: tip up, rounded belly
                ctx.beginPath()
                ctx.moveTo(px, py - r * 1.7)
                ctx.bezierCurveTo(px + r, py - r * 0.3, px + r, py + r * 0.7, px, py + r)
                ctx.bezierCurveTo(px - r, py + r * 0.7, px - r, py - r * 0.3, px, py - r * 1.7)
                ctx.closePath()
                ctx.lineWidth = 2; ctx.strokeStyle = "rgba(255,255,255,0.95)"; ctx.stroke()
                ctx.fillStyle = "#4e7cc4"; ctx.fill()
            }
        }
        Connections {
            target: root
            function onRainMarksChanged() { rainCanvas.requestPaint() }
            function onOriginXChanged() { rainCanvas.requestPaint() }
            function onOriginYChanged() { rainCanvas.requestPaint() }
            function onWidthChanged() { rainCanvas.requestPaint() }
            function onHeightChanged() { rainCanvas.requestPaint() }
        }
    }

    // Temperature labels (weather layer) - a small pill "16°/14°" (temp / feels-like) at points
    // along the route, so you can read the temperature you'll meet where you'll meet it.
    Canvas {
        id: tempCanvas
        anchors.fill: parent
        visible: root.tempMarks.length > 0
        onPaint: {
            const ctx = getContext("2d")
            ctx.reset()
            const marks = root.tempMarks
            if (!marks || marks.length === 0) return
            ctx.font = "600 10px sans-serif"
            ctx.textAlign = "center"
            ctx.textBaseline = "middle"
            for (const m of marks) {
                const px = root.lonToWorldX(m.lon) - root.originX
                const py = root.latToWorldY(m.lat) - root.originY
                const label = Math.round(m.temp_c) + "°/" + Math.round(m.feels_c) + "°"
                const w = ctx.measureText(label).width + 8
                const h = 15, ry = py - 13
                // pill
                ctx.beginPath()
                if (ctx.roundRect) ctx.roundRect(px - w / 2, ry - h / 2, w, h, 7)
                else ctx.rect(px - w / 2, ry - h / 2, w, h)
                ctx.fillStyle = "rgba(255,255,255,0.92)"; ctx.fill()
                ctx.lineWidth = 1; ctx.strokeStyle = "#e8833a"; ctx.stroke()
                ctx.fillStyle = "#b4531a"
                ctx.fillText(label, px, ry)
            }
        }
        Connections {
            target: root
            function onTempMarksChanged() { tempCanvas.requestPaint() }
            function onOriginXChanged() { tempCanvas.requestPaint() }
            function onOriginYChanged() { tempCanvas.requestPaint() }
            function onWidthChanged() { tempCanvas.requestPaint() }
            function onHeightChanged() { tempCanvas.requestPaint() }
        }
    }

    // Route-cursor highlight - a dot at highlightDist metres along the route, kept in sync with
    // the graph hover on PlanRoutePage.
    Canvas {
        id: highlightCanvas
        anchors.fill: parent
        visible: root.highlightDist >= 0 && root._routePts.length > 0
        onPaint: {
            const ctx = getContext("2d"); ctx.reset()
            if (root.highlightDist < 0) return
            const c = root._coordAtDist(root.highlightDist)
            if (!c) return
            const px = root.lonToWorldX(c[1]) - root.originX
            const py = root.latToWorldY(c[0]) - root.originY
            ctx.beginPath(); ctx.arc(px, py, 8, 0, 2 * Math.PI)
            ctx.fillStyle = "rgba(255,255,255,0.95)"; ctx.fill()
            ctx.beginPath(); ctx.arc(px, py, 5.5, 0, 2 * Math.PI)
            ctx.fillStyle = Theme.primary; ctx.fill()
        }
        Connections {
            target: root
            function onHighlightDistChanged() { highlightCanvas.requestPaint() }
            function onOriginXChanged() { highlightCanvas.requestPaint() }
            function onOriginYChanged() { highlightCanvas.requestPaint() }
            function onWidthChanged() { highlightCanvas.requestPaint() }
            function onHeightChanged() { highlightCanvas.requestPaint() }
        }
    }

    // Reverse sync: hovering near the route emits routeHovered(dist) so the graphs can move their
    // crosshair to the same spot. Only active when there's a route (empty for other MapView users).
    HoverHandler {
        id: routeHover
        enabled: root._routePts.length > 0
        onPointChanged: {
            if (!hovered) { root.routeHoverEnded(); return }
            const mx = point.position.x, my = point.position.y
            let best = -1, bestD = 24 * 24    // must be within ~24 px of the line
            for (let i = 0; i < root._routePts.length; i++) {
                const px = root.lonToWorldX(root._routePts[i][1]) - root.originX
                const py = root.latToWorldY(root._routePts[i][0]) - root.originY
                const dx = px - mx, dy = py - my, d = dx * dx + dy * dy
                if (d < bestD) { bestD = d; best = i }
            }
            if (best >= 0) root.routeHovered(root._routeCum[best])
            else root.routeHoverEnded()
        }
    }

    // Marker - always exactly centered, since every caller that sets showMarker also
    // centers the map on that same coordinate (POIs' Add form). Real request 2026-08-08
    // ("change the color of POI point to be more visible", then "same green as Left
    // buttons when selected") - a plain Theme.error glyph (red) could sit right on top of
    // OSM's own red/orange road cartography with almost no contrast; a white halo behind
    // Theme.primary (NavItem.qml's own selected-row color, for brand consistency) stays
    // visible over any tile color, not just some of them.
    Item {
        visible: root.showMarker
        width: 36
        height: 44
        x: root.width / 2 - width / 2
        y: root.height / 2 - height

        Rectangle {
            width: 30
            height: 30
            radius: 15
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 6
            color: "white"
            border.color: Theme.mapAccent
            border.width: 2
        }
        Icon {
            glyph: Icons.pois
            size: 20
            color: Theme.mapAccent
            anchors.horizontalCenter: parent.horizontalCenter
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 12
        }
    }

    // Multiple discrete pins, each at its own real projected position - see `markers`
    // property's own comment above for why this exists separately from the single centered
    // marker above. Same visual (white halo + Theme.primary POI glyph), just positioned via
    // the same lonToWorldX/latToWorldY projection the track polyline already uses instead of
    // being fixed at screen center.
    Repeater {
        model: root.markers
        delegate: Item {
            required property var modelData
            width: 30
            height: 38
            x: root.lonToWorldX(modelData.lon) - root.originX - width / 2
            y: root.latToWorldY(modelData.lat) - root.originY - height

            Rectangle {
                width: 26
                height: 26
                radius: 13
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 4
                color: "white"
                border.color: Theme.mapAccent
                border.width: 2
            }
            Icon {
                glyph: Icons.pois
                size: 16
                color: Theme.mapAccent
                anchors.horizontalCenter: parent.horizontalCenter
                anchors.bottom: parent.bottom
                anchors.bottomMargin: 9
            }
        }
    }

    // Real request 2026-08-07: "missing the zoom + and zoom -" - opt-in (showZoomControls)
    // since a small card thumbnail doesn't want two more tap targets on it.
    Column {
        visible: root.showZoomControls
        anchors.right: parent.right
        anchors.top: parent.top
        anchors.margins: 8
        spacing: 4

        Rectangle {
            width: 28; height: 28; radius: 4
            color: "#CCFFFFFF"
            Text {
                anchors.centerIn: parent
                text: "+"
                font.pixelSize: Theme.fontSizeHeading
                font.bold: true
                color: "#333333"
            }
            TapHandler {
                onTapped: root.currentZoom = Math.min(19, root.currentZoom + 1)
            }
        }
        Rectangle {
            width: 28; height: 28; radius: 4
            color: "#CCFFFFFF"
            Text {
                anchors.centerIn: parent
                text: "−"
                font.pixelSize: Theme.fontSizeHeading
                font.bold: true
                color: "#333333"
            }
            TapHandler {
                onTapped: root.currentZoom = Math.max(1, root.currentZoom - 1)
            }
        }
    }

    // Required, not decorative - OpenStreetMap's (and CyclOSM's) tile usage policy expects
    // visible attribution on anything showing its tiles.
    // Required, not decorative - OpenStreetMap's (and CyclOSM's) tile usage policy expects
    // visible attribution on anything showing its tiles.
    Rectangle {
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.margins: 4
        color: "#CCFFFFFF"
        radius: 6
        width: attributionText.implicitWidth + 8
        height: attributionText.implicitHeight + 4

        Text {
            id: attributionText
            anchors.centerIn: parent
            text: MapService.attribution
            font.pixelSize: Theme.fontSizeTiny
            color: "#333333"
        }
    }
}
