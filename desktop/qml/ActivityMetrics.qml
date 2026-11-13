pragma Singleton
import QtQuick
import SommetUi

// The catalogue of metrics an activity can show, for the configurable Activities columns
// (André, 2026-08-16: "on the place of distance/duration/ascent/calories have elegant dropdown
// menus to show whatever is available from the file... in the units from the watch"). One
// place both the column headers (ActivitiesPage) and the rows (ActivityRow) read, so a header
// and its data always agree on label, width, formatting and sort order.
//
// `value()` returns a display string already in the user's unit setting (via WatchUnits), or
// "" when the move never recorded that metric - the row then shows nothing rather than a false
// zero, and `available()` lets a dropdown grey out metrics no activity in the list has.
QtObject {
    id: root

    // Ordered catalogue. `w` matches the data column's own width so headers line up.
    readonly property var all: [
        { key: "distance",    label: qsTr("Distance"),    w: 96 },
        { key: "duration",    label: qsTr("Duration"),    w: 96 },
        { key: "pace",        label: qsTr("Pace"),        w: 96 },
        { key: "avgSpeed",    label: qsTr("Avg speed"),   w: 96 },
        { key: "maxSpeed",    label: qsTr("Max speed"),   w: 96 },
        { key: "ascent",      label: qsTr("Ascent"),      w: 88 },
        { key: "descent",     label: qsTr("Descent"),     w: 88 },
        { key: "calories",    label: qsTr("Calories"),    w: 96 },
        { key: "avgHr",       label: qsTr("Avg HR"),      w: 84 },
        { key: "maxHr",       label: qsTr("Max HR"),      w: 84 },
        { key: "avgCadence",  label: qsTr("Avg cadence"), w: 100 },
        { key: "maxCadence",  label: qsTr("Max cadence"), w: 100 },
        { key: "recovery",    label: qsTr("Recovery"),    w: 92 },
        { key: "peakTe",      label: qsTr("Peak TE"),     w: 76 },
        { key: "maxAltitude", label: qsTr("Max alt."),    w: 88 },
        { key: "poolLengths", label: qsTr("Lengths"),     w: 80 },
        { key: "shots",       label: qsTr("Shots"),       w: 72 },
    ]

    function _def(key) {
        for (let i = 0; i < all.length; i++)
            if (all[i].key === key) return all[i]
        return null
    }
    function labelFor(key) { const d = _def(key); return d ? d.label : key }
    function widthFor(key) { const d = _def(key); return d ? d.w : 96 }

    // Raw numeric value (SI / watch-native), for sorting and the "is it recorded" test. 0 or
    // negative reads as "not recorded".
    function raw(a, key) {
        if (!a) return 0
        switch (key) {
        case "distance":    return a.distanceMeters || 0
        case "duration":    return a.durationSeconds || 0
        case "pace":        return a.paceSecPerKm || 0
        case "avgSpeed":    return a.avgSpeedMh || 0
        case "maxSpeed":    return a.maxSpeedMh || 0
        case "ascent":      return a.ascentMeters || 0
        case "descent":     return a.descentMeters || 0
        case "calories":    return a.energyKcal || 0
        case "avgHr":       return a.avgHr || 0
        case "maxHr":       return a.maxHr || 0
        case "avgCadence":  return a.avgCadence || 0
        case "maxCadence":  return a.maxCadence || 0
        case "recovery":    return a.recoverySeconds || 0
        case "peakTe":      return a.peakTrainingEffect || 0
        case "maxAltitude": return a.maxAltitudeMeters || 0
        case "poolLengths": return a.poolLengths || 0
        case "shots":       return a.shots || 0
        }
        return 0
    }

    // Display string in the watch's units, or "" when not recorded.
    function value(a, key) {
        if (raw(a, key) <= 0 && key !== "duration") return ""
        switch (key) {
        case "distance":    return WatchUnits.distance(a.distanceMeters)
        case "duration":    return ActivityViewModel.formatDuration(a.durationSeconds)
        case "pace":        return WatchUnits.pace(a.paceSecPerKm)
        case "avgSpeed":    return WatchUnits.speed(a.avgSpeedMh)
        case "maxSpeed":    return WatchUnits.speed(a.maxSpeedMh)
        case "ascent":      return WatchUnits.altitude(a.ascentMeters)
        case "descent":     return WatchUnits.altitude(a.descentMeters)
        case "calories":    return WatchUnits.energy(a.energyKcal)
        case "avgHr":       return qsTr("%1 bpm").arg(a.avgHr)
        case "maxHr":       return qsTr("%1 bpm").arg(a.maxHr)
        case "avgCadence":  return qsTr("%1 rpm").arg(a.avgCadence)
        case "maxCadence":  return qsTr("%1 rpm").arg(a.maxCadence)
        case "recovery":    return ActivityViewModel.formatDuration(a.recoverySeconds)
        case "peakTe":      return (a.peakTrainingEffect / 10).toFixed(1)
        case "maxAltitude": return WatchUnits.altitude(a.maxAltitudeMeters)
        case "poolLengths": return String(a.poolLengths)
        case "shots":       return String(a.shots)
        }
        return ""
    }

    // Whether at least one activity in the list recorded this metric - lets a picker grey out
    // metrics that would be blank for everything (e.g. Pool lengths on a running-only history).
    function availableIn(activities, key) {
        for (let i = 0; i < activities.length; i++)
            if (raw(activities[i], key) > 0) return true
        return false
    }
}
