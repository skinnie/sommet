import QtQuick
import QtQuick.Controls
import AmbitApp

// Totals - real request, 2026-08-11 (André): "Create a new screen: Totals (For the year) -
// Hours outside (gps acitities, sum up hours) ... Kms by #activity (person can choose),
// include two default ones, run and bike ... Energy spent (sum up all kcals)".
//
// Everything here is derived from the activities already read off the watch - no new device
// traffic and no stored history, so it is correct the moment Activities is. The year is the
// calendar year, and the year picker only offers years that actually have activities, so it
// can never land on an empty screen by accident.
//
// On "allow to add two sports modes for 1 activity, if you find a better more automatized
// way propose": the automated way is here. An activity carries its sport mode's NAME, and
// ActivityTypes already maps names to a known activity id (the same mapping the badges use,
// including the Ambit/Kailash/Garmin name aliases - see gen_activity_qml.py's own
// _nameAliases comment). So rather than making the user pair up modes by hand, activities are
// grouped by that resolved id - "Running" and "Trail running" land in one bucket on their
// own, and a custom mode a user renamed still resolves through the same table. The manual
// picker below is kept for the case the automatic grouping cannot cover: choosing WHICH
// groups to feature.
Item {
    id: root

    // Activities come from whichever device is connected, exactly as the Activities page
    // decides it - one source of truth for "what have I got", reused rather than re-derived.
    readonly property var allActivities: ActivityViewModel.feed

    Component.onCompleted: {
        ActivityService.refresh()
        GarminService.refreshActivities()
        KailashService.refreshHistory()
    }

    function yearOf(activity) {
        if (!activity || !activity.startTime)
            return 0
        const d = new Date(activity.startTime)
        return isNaN(d.getTime()) ? 0 : d.getFullYear()
    }

    // Years that actually have data, newest first.
    readonly property var years: {
        const seen = {}
        for (const a of allActivities) {
            const y = yearOf(a)
            if (y > 0)
                seen[y] = true
        }
        const list = Object.keys(seen).map(function(y) { return parseInt(y) })
        list.sort(function(a, b) { return b - a })
        return list
    }

    property int selectedYear: 0
    // Default to the most recent year with data rather than to "now", so a January visit
    // does not open on an empty screen.
    onYearsChanged: {
        if (years.length > 0 && years.indexOf(selectedYear) === -1)
            selectedYear = years[0]
    }

    readonly property var yearActivities:
        allActivities.filter(function(a) { return root.yearOf(a) === root.selectedYear })

    // --- the three totals -------------------------------------------------------------

    // "Hours outside (gps activities)": only activities that actually recorded a track, so
    // an indoor session on the same watch does not count as time outside. A Kailash session
    // whose track fell outside TrackLog coverage is excluded too - that is honest, if
    // slightly conservative, and better than counting a treadmill hour as fresh air.
    readonly property real hoursOutside: {
        let seconds = 0
        for (const a of yearActivities) {
            // Outdoor/GPS proxy: a recorded GPS track, OR any positive distance. Imported rides
            // from intervals.icu / Garmin carry distance + duration but NO track array (they were
            // never read off the watch), so counting only track activities dropped them entirely
            // - a year full of imported GPS rides read as 0 h (André, 2026-08-25). Pure indoor /
            // HRV / strength entries (no track, no distance) are still excluded.
            if ((a.track && a.track.length > 0) || (a.distanceMeters || 0) > 0)
                seconds += a.durationSeconds || 0
        }
        return seconds / 3600
    }

    readonly property real totalKcal: {
        let sum = 0
        for (const a of yearActivities)
            sum += a.energyKcal || 0
        return sum
    }

    // Distance grouped by resolved activity id - the automatic grouping described above.
    readonly property var byActivity: {
        const groups = {}
        for (const a of yearActivities) {
            const type = ActivityTypes.forName(a.name)
            const id = type ? type.id : 1
            if (!groups[id])
                groups[id] = {id: id, name: type ? type.name : qsTr("Other"),
                              meters: 0, count: 0}
            groups[id].meters += a.distanceMeters || 0
            groups[id].count += 1
        }
        const list = []
        for (const key in groups)
            list.push(groups[key])
        list.sort(function(x, y) { return y.meters - x.meters })
        return list
    }

    // Which groups are featured. Defaults to running and cycling per the request; falls back
    // to whatever the two biggest groups are for someone who does neither.
    property var featured: []
    onByActivityChanged: {
        if (featured.length > 0)
            return
        const wanted = []
        for (const g of byActivity) {
            const n = g.name.toLowerCase()
            if (n.indexOf("run") >= 0 || n.indexOf("cycl") >= 0 || n.indexOf("bik") >= 0)
                wanted.push(g.id)
        }
        featured = wanted.length > 0
                   ? wanted : byActivity.slice(0, 2).map(function(g) { return g.id })
    }

    function toggleFeatured(id) {
        const next = featured.slice()
        const at = next.indexOf(id)
        if (at >= 0)
            next.splice(at, 1)
        else
            next.push(id)
        featured = next
    }

    PageFlickable {
        anchors.fill: parent
        anchors.margins: Theme.spacingLarge
        contentWidth: width
        contentHeight: column.height + Theme.spacingLarge
        clip: true

        Column {
            id: column
            width: parent.width
            spacing: Theme.spacingLarge

            Row {
                width: parent.width
                spacing: Theme.spacingMedium

                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Totals")
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeTitle
                    font.bold: true
                }

                Repeater {
                    model: root.years
                    delegate: RoundedButton {
                        required property var modelData
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData
                        highlighted: modelData === root.selectedYear
                        onClicked: root.selectedYear = modelData
                    }
                }
            }

            Text {
                visible: root.yearActivities.length === 0
                width: parent.width
                wrapMode: Text.WordWrap
                text: root.allActivities.length === 0
                      ? qsTr("Nothing to add up yet - read your activities off the watch " +
                              "first and this fills in.")
                      : qsTr("No activities in this year.")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeBody
            }

            // --- Hours outside ---
            TotalsCard {
                visible: root.yearActivities.length > 0
                width: parent.width
                title: qsTr("Hours outside")
                headline: qsTr("%1 h").arg(root.hoursOutside.toFixed(0))
                subtitle: qsTr("Across %1 GPS activities")
                          .arg(root.yearActivities.filter(function(a) {
                              return (a.track && a.track.length > 0) || (a.distanceMeters || 0) > 0 }).length)
                lines: TotalsFacts.hoursLines(root.hoursOutside)
            }

            // --- Distance by activity ---
            Card {
                visible: root.yearActivities.length > 0
                width: parent.width
                Column {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Text {
                        text: qsTr("Distance")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBodyLarge
                        font.bold: true
                    }
                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: qsTr("Activities are grouped by sport automatically. Click one " +
                                    "to feature it.")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }

                    Flow {
                        width: parent.width
                        spacing: Theme.spacingSmall
                        Repeater {
                            model: root.byActivity
                            delegate: Rectangle {
                                required property var modelData
                                readonly property bool on:
                                    root.featured.indexOf(modelData.id) >= 0
                                width: chipRow.width + Theme.spacingMedium * 2
                                height: 34
                                radius: 17
                                color: Theme.card
                                border.width: 1
                                border.color: on
                                              ? Theme.primary
                                              : Qt.rgba(Theme.mutedText.r, Theme.mutedText.g,
                                                        Theme.mutedText.b, 0.3)

                                Row {
                                    id: chipRow
                                    anchors.centerIn: parent
                                    spacing: Theme.spacingSmall
                                    ActivityBadge {
                                        anchors.verticalCenter: parent.verticalCenter
                                        activityId: modelData.id
                                        size: 20
                                    }
                                    Text {
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: qsTr("%1 · %2").arg(modelData.name)
                                              .arg(WatchUnits.distance(modelData.meters))
                                        color: Theme.text
                                        font.pixelSize: Theme.fontSizeBody
                                        font.bold: parent.parent.on
                                    }
                                }
                                HoverHandler { cursorShape: Qt.PointingHandCursor }
                                TapHandler { onTapped: root.toggleFeatured(modelData.id) }
                            }
                        }
                    }
                }
            }

            // One card per featured sport, with its own equivalents.
            Repeater {
                model: root.byActivity.filter(function(g) {
                    return root.featured.indexOf(g.id) >= 0 && g.meters > 0 })
                delegate: TotalsCard {
                    required property var modelData
                    width: column.width
                    title: modelData.name
                    headline: WatchUnits.distance(modelData.meters)
                    subtitle: qsTr("%1 activities").arg(modelData.count)
                    lines: TotalsFacts.distanceLines(modelData.meters)
                }
            }

            // --- Energy ---
            TotalsCard {
                visible: root.yearActivities.length > 0 && root.totalKcal > 0
                width: parent.width
                title: qsTr("Energy spent")
                headline: WatchUnits.energy(root.totalKcal)
                subtitle: qsTr("Straight off the watch")
                lines: TotalsFacts.energyLines(root.totalKcal)
            }

            Text {
                visible: root.yearActivities.length > 0 && root.totalKcal <= 0
                width: parent.width
                wrapMode: Text.WordWrap
                // Rather than an empty "0 kcal" card, which would read as "you burned
                // nothing" instead of "this was never recorded".
                text: qsTr("No energy figures in this year's activities - re-read them off " +
                            "the watch to pick them up.")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
            }

            Text {
                visible: root.yearActivities.length > 0
                width: parent.width
                text: qsTr("More to come!")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeBody
            }
        }
    }
}
