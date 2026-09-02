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
}
