pragma Singleton
import QtQuick

// Persists the Plan page's loaded route + weather across navigation. The nav shell loads pages
// through a Loader (Main.qml), which DESTROYS a page when you switch away and rebuilds it fresh
// when you come back - so any state living on PlanRoutePage itself is lost (André, 2026-08-31:
// "if I go to other menu and go back it doesn't stay on the gpx I was working on"). This
// singleton outlives the Loader: PlanRoutePage writes its state here after each change and
// restores from here on load. Pure QML, no backend calls - same rule as Theme/FunFacts.
QtObject {
    property string plannedGpx: ""
    property string routeName: ""
    property var coloredSegments: []
    property var legendRows: []
    property var profileRows: []
    property var summary: ({})

    property var weatherSegments: []
    property var weatherProfile: []
    property var windArrows: []
    property var rainMarks: []
    property var tempMarks: []
    property var weatherAstro: ({})
    property var weatherVerdict: ({})
    property var weatherSummary: ({})
    property var weatherLegend: []
    property int overlayMode: 1
    property int numDays: 1
    property int splitMode: 0
    property var dayBounds: []
    property bool reversed: false

    // UI inputs worth remembering too, so a return trip keeps the same start/pace.
    property string startTime: "09:00"
    property string paceText: "20"
    property string planDate: ""

    property bool hasRoute: plannedGpx.length > 0

    // Shared POIs/resupply result (race_pois): found on the Route page, read by Race Plan's
    // critical-points/water-gap analysis. Cleared when a new GPX is loaded.
    property var pois: null
    // Remembered POI query choices so a return trip keeps them.
    property var poiCategories: ["water", "food"]
    property string poiWaterRate: "2.0"
    property string poiCarryL: "1.5"
    property string poiCustom: ""   // free-text extra POI categories (comma-separated), PitStopper-style
    property string poiRadius: "250"   // POI search radius (metres) around the route

    property bool poiBusy: false

    // Map layers for the loaded POIs (André, 2026-09-21: weather + POIs crowd each other, so let the
    // rider choose). "Places" = the useful ones (water, food, cemeteries, sleep, bike shops, services);
    // "More" = everything else in the export (bike parking, bike-share, historic sites...), off by default.
    property bool showPlaces: true

    // Read POIs from a PitStopper GPX (waypoints) against the route and publish the result to `pois`
    // (map pins, Race Plan alerts). `done(ok, res)` is optional. A result with zero recognised POIs
    // is NOT published - otherwise an ordinary GPX with a few unrelated waypoints would claim
    // "no water on this route".
    function importPois(routeGpx, poiGpxText, done, reverse) {
        if (!routeGpx || !poiGpxText) return
        poiBusy = true
        var xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            poiBusy = false
            var res = null
            try { res = JSON.parse(xhr.responseText) } catch (e) { res = null }
            var ok = xhr.status === 200 && res && res.ok && (res.imported || 0) > 0
            if (ok && routeGpx === plannedGpx)      // ignore a stale answer if the route changed
                pois = res
            if (done) done(ok, res)
        }
        xhr.open("POST", "http://127.0.0.1:8766/api/race/pois")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({ gpx: routeGpx, poi_gpx: poiGpxText,
                                  reverse: !!reverse,     // km measured along the reversed route
                                  water_l_per_100km: parseFloat(poiWaterRate) || 2.0,
                                  carry_l: parseFloat(poiCarryL) || 1.5 }))
    }

    // A PitStopper export loaded AS the route (GPX with waypoints + track) carries its own POIs:
    // read them automatically, so loading that one file on either page shows them on the map
    // (André, 2026-09-21: "I uploaded the gpx from pitstopper... it doesn't show on the map").
    onPlannedGpxChanged: {
        if (plannedGpx.indexOf("<wpt") >= 0)
            importPois(plannedGpx, plannedGpx, null, reversed)
    }
}
