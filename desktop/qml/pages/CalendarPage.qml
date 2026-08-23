import QtQuick
import QtQuick.Controls
import AmbitApp

// Calendar - real request, 2026-08-11 (André, with a reference screenshot: a month grid
// where each day carries a coloured dot for the activity recorded that day, sized by how
// much was done, small grey dot for a rest day, today picked out). Same per-sport colour
// this app already uses everywhere else (ActivityRow/ActivityCard/ActivityDetail/Totals'
// own ActivityTypes.forName() resolution - see TotalsPage.qml's own header comment for why
// that one lookup is what lets an Ambit "Walk" sport mode and a Kailash/Garmin "Walk"
// activity share a colour), so a mixed-device history reads as one consistent calendar
// rather than one look per device.
Item {
    id: root

    // Same device-aware activity source TotalsPage.qml already established as the one
    // place to read this from - not re-derived per page.
    readonly property var allActivities:
        HomeViewModel.isGarmin ? GarminService.activities
        : HomeViewModel.isKailash ? KailashService.sessions.map(function(s) {
              return {
                  name: qsTr("Walk"), startTime: s.when,
                  distanceMeters: s.distanceMeters, durationSeconds: s.durationSeconds,
                  ascentMeters: 0, energyKcal: 0, track: [],
              }
          })
        : ActivityService.activities

    Component.onCompleted: {
        ActivityService.refresh()
        GarminService.refreshActivities()
        KailashService.refreshHistory()
    }

    readonly property date today: new Date()
    property int viewYear: today.getFullYear()
    property int viewMonth: today.getMonth()  // 0-11

    // --- plan-a-workout-for-a-day (André's scheduled-workout "workaround") ----------------
    // A short list of the sports someone actually schedules, each with the activity name that
    // goes into the pre-filled title. Kept deliberately short rather than all 84 ActivityTypes -
    // this is a title seed, not the sport-mode picker.
    readonly property var plannerActivities: [
        "Running", "Trail running", "Cycling", "Mountain biking",
        "Hiking", "Trekking", "Swimming", "Other"
    ]
    property int plannerDay: 0
    function openPlanner(day) {
        plannerDay = day
        plannerDialog.open()
    }
    // "<Activity>_dd_MM" for the chosen sport and the day being viewed - the format André asked
    // for (%activity_dd_MM). Month is the viewed month, 1-based, zero-padded.
    function plannerTitle(activity) {
        const dd = String(root.plannerDay).padStart(2, "0")
        const mm = String(root.viewMonth + 1).padStart(2, "0")
        return activity.replace(/[^A-Za-z0-9]/g, "") + "_" + dd + "_" + mm
    }

    function goPrevMonth() {
        if (viewMonth === 0) { viewMonth = 11; viewYear -= 1 } else { viewMonth -= 1 }
    }
    function goNextMonth() {
        if (viewMonth === 11) { viewMonth = 0; viewYear += 1 } else { viewMonth += 1 }
    }
    function goToday() { viewYear = today.getFullYear(); viewMonth = today.getMonth() }

    readonly property bool isCurrentMonth:
        viewYear === today.getFullYear() && viewMonth === today.getMonth()

    function dayKey(y, m, d) { return y + "-" + m + "-" + d }

    function activityDate(activity) {
        if (!activity || !activity.startTime) return null
        const d = new Date(activity.startTime)
        return isNaN(d.getTime()) ? null : d
    }

    // This month's activities only - everything below is derived from this one filter.
    readonly property var monthActivities: allActivities.filter(function(a) {
        const d = root.activityDate(a)
        return d && d.getFullYear() === root.viewYear && d.getMonth() === root.viewMonth
    })

    // Activities bucketed by day-of-month, plus the month's own busiest day (by total
    // duration) - the yardstick every other day's dot size is scaled against, so "biggest
    // dot" always means "most time spent" within the month currently on screen, the same
    // relative read the reference screenshot's own dot sizing gives.
    readonly property var byDay: {
        const map = {}
        for (const a of monthActivities) {
            const d = root.activityDate(a)
            const key = d.getDate()
            if (!map[key]) map[key] = []
            map[key].push(a)
        }
        return map
    }
    readonly property real maxDaySeconds: {
        let max = 0
        for (const key in byDay) {
            let seconds = 0
            for (const a of byDay[key]) seconds += a.durationSeconds || 0
            if (seconds > max) max = seconds
        }
        return max
    }

    function colorForActivity(activity) {
        return ActivityTypes.forName(activity.name).color
    }

    // Dot diameter for a day, scaled against this month's busiest day - a fixed floor so
    // even a short activity still reads as a real dot, not a sliver.
    // Test, 2026-08-11: raised from the original 16/30 (right for a dot sitting under the
    // day number) once the circle started sitting ON the number instead - the smallest
    // circle needs to comfortably fit two digits without the text spilling past its edge.
    readonly property int minDotSize: 34
    readonly property int maxDotSize: 46
    function dotSizeFor(daySeconds) {
        if (root.maxDaySeconds <= 0) return root.minDotSize
        const t = Math.min(1, daySeconds / root.maxDaySeconds)
        return Math.round(root.minDotSize + t * (root.maxDotSize - root.minDotSize))
    }

    // Monday-first 7-wide grid, padded to full weeks - null cells before day 1 and after
    // the month's last day are simply left blank, matching the reference's own look rather
    // than bleeding in the adjacent months' dates.
    readonly property var weeks: {
        const first = new Date(root.viewYear, root.viewMonth, 1)
        const daysInMonth = new Date(root.viewYear, root.viewMonth + 1, 0).getDate()
        // JS getDay(): 0=Sunday..6=Saturday - shifted so Monday is column 0.
        const leading = (first.getDay() + 6) % 7

        const cells = []
        for (let i = 0; i < leading; i++) cells.push(null)
        for (let day = 1; day <= daysInMonth; day++) {
            const dayActivities = root.byDay[day] || []
            let seconds = 0
            for (const a of dayActivities) seconds += a.durationSeconds || 0
            cells.push({
                day: day,
                isToday: root.isCurrentMonth && day === root.today.getDate(),
                activities: dayActivities,
                dotSize: root.dotSizeFor(seconds),
            })
        }
        while (cells.length % 7 !== 0) cells.push(null)

        const result = []
        for (let i = 0; i < cells.length; i += 7)
            result.push(cells.slice(i, i + 7))
        return result
    }

    readonly property var weekdayLabels: {
        const labels = []
        // Qt.locale().dayName(1..7): 1=Monday - locale-aware, same "single letter" read as
        // the reference screenshot's own M T W T F S S, without hardcoding English.
        for (let i = 1; i <= 7; i++)
            labels.push(Qt.locale().dayName(i, Locale.NarrowFormat))
        return labels
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

            Text {
                text: qsTr("Calendar")
                color: Theme.text
                font.pixelSize: Theme.fontSizeTitle
                font.bold: true
            }

            Card {
                width: parent.width

                Column {
                    width: parent.width
                    spacing: Theme.spacingMedium

                    Row {
                        width: parent.width
                        spacing: Theme.spacingSmall

                        Icon {
                            anchors.verticalCenter: parent.verticalCenter
                            glyph: Icons.chevronRight
                            rotation: 180
                            size: 20
                            color: Theme.text
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: root.goPrevMonth() }
                        }

                        Column {
                            width: parent.width - 40 - (todayButton.visible ? todayButton.width + Theme.spacingSmall : 0)
                            anchors.verticalCenter: parent.verticalCenter
                            Text {
                                width: parent.width
                                horizontalAlignment: Text.AlignHCenter
                                text: new Date(root.viewYear, root.viewMonth, 1)
                                      .toLocaleDateString(Qt.locale(), "MMMM yyyy")
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeHeading
                                font.bold: true
                            }
                            Text {
                                width: parent.width
                                horizontalAlignment: Text.AlignHCenter
                                text: qsTr("%1 activities").arg(root.monthActivities.length)
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }

                        RoundedButton {
                            id: todayButton
                            anchors.verticalCenter: parent.verticalCenter
                            visible: !root.isCurrentMonth
                            text: qsTr("Today")
                            onClicked: root.goToday()
                        }

                        Icon {
                            anchors.verticalCenter: parent.verticalCenter
                            glyph: Icons.chevronRight
                            size: 20
                            color: Theme.text
                            HoverHandler { cursorShape: Qt.PointingHandCursor }
                            TapHandler { onTapped: root.goNextMonth() }
                        }
                    }

                    Row {
                        width: parent.width
                        Repeater {
                            model: root.weekdayLabels
                            delegate: Text {
                                required property string modelData
                                width: parent.width / 7
                                horizontalAlignment: Text.AlignHCenter
                                text: modelData
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                                font.bold: true
                            }
                        }
                    }

                    Column {
                        width: parent.width
                        spacing: Theme.spacingMedium

                        Repeater {
                            model: root.weeks
                            delegate: Row {
                                required property var modelData
                                width: column.width - Theme.spacingMedium * 2

                                Repeater {
                                    model: modelData
                                    delegate: Item {
                                        id: dayCell
                                        required property var modelData
                                        readonly property int activityCount:
                                            modelData ? modelData.activities.length : 0
                                        width: parent.width / 7
                                        height: 62

                                        // Click a day to plan a workout for it, 2026-08-23
                                        // (André's "workaround" for scheduled workouts): pick a
                                        // sport, then the Workout Builder opens with the title
                                        // pre-filled "<Activity>_dd_MM". Suunto-only, like the
                                        // builder it launches; a null padding cell does nothing.
                                        HoverHandler {
                                            enabled: dayCell.modelData !== null
                                                     && DeviceService.intervalsEnabled
                                                     && !HomeViewModel.isGarmin
                                                     && !HomeViewModel.isKailash
                                            cursorShape: Qt.PointingHandCursor
                                        }
                                        TapHandler {
                                            enabled: dayCell.modelData !== null
                                                     && DeviceService.intervalsEnabled
                                                     && !HomeViewModel.isGarmin
                                                     && !HomeViewModel.isKailash
                                            onTapped: root.openPlanner(dayCell.modelData.day)
                                        }

                                        // Test, 2026-08-11 (André: "circles being on the day,
                                        // without for sure opaquing the day, instead of
                                        // under") - the day number now sits ON the sport
                                        // circle (one overlapping item) rather than the
                                        // number above and the dot in its own row below. A
                                        // rest day's grey dot gets the same treatment at a
                                        // smaller, low-opacity size so the number always
                                        // stays legible over it (never "opaqued") - and an
                                        // activity circle uses the same white-on-colour
                                        // contrast ActivityBadge's own glyphs already rely on
                                        // elsewhere in this app, not a new contrast rule.
                                        Item {
                                            anchors.centerIn: parent
                                            width: root.maxDotSize + 12
                                            height: width
                                            visible: modelData !== null

                                            // Today, no activity yet: the same solid pill
                                            // this page always used for "today" - kept as
                                            // its own case so a not-yet-recorded today still
                                            // reads clearly rather than as a plain number.
                                            Rectangle {
                                                anchors.centerIn: parent
                                                visible: modelData && modelData.isToday
                                                         && parent.parent.activityCount === 0
                                                width: root.minDotSize
                                                height: width
                                                radius: width / 2
                                                color: Theme.primary
                                            }

                                            // Today WITH an activity: a thin ring around the
                                            // real activity circle below, rather than
                                            // replacing its colour - "today" and "what you
                                            // did" are both real information, so neither one
                                            // hides the other.
                                            Rectangle {
                                                anchors.centerIn: parent
                                                visible: modelData && modelData.isToday
                                                         && parent.parent.activityCount > 0
                                                width: modelData ? modelData.dotSize + 10 : 0
                                                height: width
                                                radius: width / 2
                                                color: "transparent"
                                                border.width: 2
                                                border.color: Theme.primary
                                            }

                                            // A second sport that day: an outer ring in its
                                            // colour rather than hidden - capped at 2 rings
                                            // so a heavy multisport day still reads cleanly.
                                            Rectangle {
                                                anchors.centerIn: parent
                                                visible: parent.parent.activityCount > 1
                                                width: modelData ? modelData.dotSize + 6 : 0
                                                height: width
                                                radius: width / 2
                                                color: "transparent"
                                                border.width: 2
                                                border.color: parent.parent.activityCount > 1
                                                              ? root.colorForActivity(modelData.activities[1])
                                                              : "transparent"
                                            }

                                            // The day itself: a quiet, low-opacity grey dot
                                            // for a rest day, or a real filled circle in the
                                            // first activity's sport colour sized by the
                                            // day's total duration relative to the month. A
                                            // today-with-no-activity-yet already has its own
                                            // solid pill above, so this is skipped there
                                            // rather than muddying it with a second, smaller
                                            // circle underneath.
                                            Rectangle {
                                                id: dayCircle
                                                anchors.centerIn: parent
                                                visible: modelData !== null
                                                         && (parent.parent.activityCount > 0
                                                             || !(modelData && modelData.isToday))
                                                width: parent.parent.activityCount > 0
                                                       ? modelData.dotSize : root.minDotSize - 4
                                                height: width
                                                radius: width / 2
                                                color: parent.parent.activityCount > 0
                                                       ? root.colorForActivity(modelData.activities[0])
                                                       : Theme.mutedText
                                                opacity: parent.parent.activityCount > 0 ? 1 : 0.25

                                                HoverHandler { id: dotHover }
                                                ToolTip.visible: dotHover.hovered
                                                                 && parent.parent.activityCount > 0
                                                ToolTip.text: parent.parent.activityCount > 0
                                                    ? modelData.activities.map(function(a) {
                                                          return a.name + " - " +
                                                              ActivityViewModel.formatDuration(a.durationSeconds)
                                                      }).join("\n")
                                                    : ""
                                                ToolTip.delay: 300
                                            }

                                            Text {
                                                anchors.centerIn: parent
                                                text: modelData ? modelData.day : ""
                                                // White reads on every real sport colour and
                                                // on Theme.primary (same contrast pair
                                                // ActivityBadge's own glyphs already use on
                                                // these exact colours) - only a bare, un-
                                                // circled rest day falls back to normal text.
                                                color: parent.parent.activityCount > 0
                                                       || (modelData && modelData.isToday)
                                                       ? Theme.card : Theme.text
                                                font.pixelSize: Theme.fontSizeBody
                                                font.bold: modelData && modelData.isToday
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    ThemedDialog {
        id: plannerDialog
        title: qsTr("Plan a workout")
        anchors.centerIn: Overlay.overlay
        standardButtons: Dialog.Cancel
        contentItem: Column {
            spacing: Theme.spacingMedium
            width: 360

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                text: qsTr("Pick a sport for %1. The Workout Builder opens with the name "
                            + "already filled in - build the steps there and install it to the "
                            + "watch's WORKOUT menu.")
                    .arg(Qt.formatDate(new Date(root.viewYear, root.viewMonth, root.plannerDay),
                                        Qt.locale(), Locale.LongFormat))
            }

            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Repeater {
                    model: root.plannerActivities
                    delegate: RoundedButton {
                        required property string modelData
                        width: parent.width
                        text: modelData + "  →  " + root.plannerTitle(modelData)
                        onClicked: {
                            IntervalsService.launch(root.plannerTitle(modelData))
                            plannerDialog.close()
                        }
                    }
                }
            }
        }
    }

}
