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
    property var dayActivities: []
    // A day strictly before today can't be planned - you don't schedule a workout for the
    // past (André, 2026-08-24). Date-only compare, ignoring time of day.
    function isPastDay(day) {
        if (day === null || day === undefined)
            return false
        const d = new Date(root.viewYear, root.viewMonth, day)
        const t = new Date(root.today.getFullYear(), root.today.getMonth(), root.today.getDate())
        return d < t
    }
    function openPlanner(day) {
        plannerDay = day
        plannerDialog.open()
    }
    // Left click: show what was recorded that day, in a themed window.
    function openDay(day) {
        plannerDay = day
        dayActivities = root.byDay[day] || []
        dayDialog.open()
    }
    // "<Activity>_dd_MM" for the chosen sport and the day being viewed - the format André asked
    // for (%activity_dd_MM). Month is the viewed month, 1-based, zero-padded.
    function plannerTitle(activity) {
        const dd = String(root.plannerDay).padStart(2, "0")
        const mm = String(root.viewMonth + 1).padStart(2, "0")
        return activity.replace(/[^A-Za-z0-9]/g, "") + "_" + dd + "_" + mm
    }
    // Builds the workout.py-schema object the Workout Builder loads, for the "Simple" shape:
    // one timed block, seeded with the title. Intervals aren't built here - they open the full
    // builder empty (see the planner's "Create workout"), where complex structure is designed.
    // A duration step MUST carry target + notify: the builder's render() reads s.target.targetName
    // on every step, so omitting it threw and nothing filled in (André: "numbers don't get
    // filled up when I open the site").
    function buildWorkout(activity, durationValue, unitKey) {
        // value is always seconds (the builder's canonical unit); unitKey ("minutes"/"hours")
        // is passed through so the builder opens on the unit the user picked. displayValue =
        // value / TIME_UNITS[unit], so e.g. 45 minutes -> value 2700, shown as 45.
        const factor = unitKey === "hours" ? 3600 : 60
        const step = { type: { typeName: "interval" },
                       duration: { durationName: "time",
                                   value: Math.max(1, Math.round(durationValue)) * factor,
                                   unit: unitKey },
                       target: { targetName: "none" },
                       notify: { beep: true, light: true } }
        return JSON.stringify({ name: root.plannerTitle(activity), steps: [step] })
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
                                        //
                                        // André, 2026-08-23: LEFT click opens the day's recorded
                                        // activities; RIGHT click plans a workout for it. The
                                        // planner (right click) is Suunto-only, since it drives
                                        // the App-Zone workout builder; viewing activities has no
                                        // such restriction.
                                        readonly property bool canPlan:
                                            dayCell.modelData !== null
                                            && !root.isPastDay(dayCell.modelData.day)
                                            && DeviceService.intervalsEnabled
                                            && !HomeViewModel.isGarmin
                                            && !HomeViewModel.isKailash
                                        HoverHandler {
                                            enabled: dayCell.modelData !== null
                                            cursorShape: Qt.PointingHandCursor
                                        }
                                        TapHandler {
                                            acceptedButtons: Qt.LeftButton
                                            enabled: dayCell.modelData !== null
                                            onTapped: root.openDay(dayCell.modelData.day)
                                        }
                                        TapHandler {
                                            acceptedButtons: Qt.RightButton
                                            enabled: dayCell.canPlan
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

                                                // Themed hover card (André, 2026-08-23: the
                                                // default ToolTip "doesn't match at all our
                                                // design"). Same card surface/typography as the
                                                // rest of the app instead of the Fusion tooltip.
                                                HoverHandler { id: dotHover }
                                                Popup {
                                                    id: dayTip
                                                    visible: dotHover.hovered
                                                             && parent.parent.activityCount > 0
                                                    x: parent.width / 2 - width / 2
                                                    y: -height - 6
                                                    padding: Theme.spacingSmall
                                                    closePolicy: Popup.NoAutoClose
                                                    background: Rectangle {
                                                        color: Theme.card
                                                        radius: Theme.radiusSmall
                                                        border.width: 1
                                                        border.color: Qt.rgba(Theme.mutedText.r,
                                                            Theme.mutedText.g, Theme.mutedText.b, 0.3)
                                                    }
                                                    contentItem: Column {
                                                        spacing: 2
                                                        Repeater {
                                                            model: dayCell.modelData
                                                                   ? dayCell.modelData.activities : []
                                                            delegate: Text {
                                                                required property var modelData
                                                                text: modelData.name + " · " +
                                                                    ActivityViewModel.formatDuration(
                                                                        modelData.durationSeconds)
                                                                color: Theme.text
                                                                font.pixelSize: Theme.fontSizeCaption
                                                            }
                                                        }
                                                    }
                                                }
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

    // Left click: the day's recorded activities, themed to match the app.
    ThemedDialog {
        id: dayDialog
        title: root.plannerDay > 0
               ? Qt.formatDate(new Date(root.viewYear, root.viewMonth, root.plannerDay),
                               Qt.locale(), Locale.LongFormat)
               : qsTr("Day")
        anchors.centerIn: Overlay.overlay
        standardButtons: Dialog.Close
        contentItem: Column {
            spacing: Theme.spacingSmall
            width: 360

            Text {
                visible: root.dayActivities.length === 0
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                text: qsTr("No activities recorded on this day.")
            }
            Repeater {
                model: root.dayActivities
                delegate: Row {
                    required property var modelData
                    width: parent.width
                    spacing: Theme.spacingSmall
                    ActivityBadge {
                        anchors.verticalCenter: parent.verticalCenter
                        activityId: ActivityTypes.forName(modelData.name).id
                        size: 30
                    }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        Text { text: modelData.name; color: Theme.text; font.bold: true }
                        Text {
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            text: ActivityViewModel.formatDuration(modelData.durationSeconds)
                                  + (modelData.distanceMeters > 0
                                     ? "  ·  " + (modelData.distanceMeters / 1000).toFixed(1) + " km"
                                     : "")
                        }
                    }
                }
            }
        }
    }

    // Right click: plan a workout. One window - activity dropdown, Simple/Intervals, the data,
    // then "Create workout" hands the whole thing to the browser builder pre-built.
    ThemedDialog {
        id: plannerDialog
        title: qsTr("Plan a workout")
        anchors.centerIn: Overlay.overlay
        // No footer - the actions live in the content Row below, so Cancel sits right next to
        // "Create workout" (André, 2026-08-24) instead of alone at the dialog's bottom.
        standardButtons: Dialog.NoButton

        property int activityIndex: 0
        property bool complex: false

        contentItem: Column {
            id: plannerCol
            spacing: Theme.spacingMedium
            width: 400

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                text: qsTr("For %1. Choose the sport and shape; \"Create workout\" opens the "
                            + "builder with it ready to fine-tune and install.")
                    .arg(Qt.formatDate(new Date(root.viewYear, root.viewMonth, root.plannerDay),
                                        Qt.locale(), Locale.LongFormat))
            }

            // Activity dropdown
            Column {
                width: parent.width
                spacing: 2
                Text { text: qsTr("Activity"); color: Theme.mutedText
                       font.pixelSize: Theme.fontSizeLabel }
                RoundedComboBox {
                    id: actBox
                    width: parent.width
                    boundsItem: plannerCol
                    model: root.plannerActivities
                    currentIndex: plannerDialog.activityIndex
                    onActivated: (i) => plannerDialog.activityIndex = i
                }
            }

            // Simple vs Intervals
            Row {
                spacing: Theme.spacingLarge
                RoundedRadioButton {
                    text: qsTr("Simple")
                    checked: !plannerDialog.complex
                    onClicked: plannerDialog.complex = false
                }
                RoundedRadioButton {
                    text: qsTr("Intervals")
                    checked: plannerDialog.complex
                    onClicked: plannerDialog.complex = true
                }
            }

            // Simple: one duration - a number plus a Minutes/Hours unit (André, 2026-08-24),
            // passed through to the builder so it opens on the same unit.
            Column {
                visible: !plannerDialog.complex
                width: parent.width
                spacing: 2
                Text { text: qsTr("Duration"); color: Theme.mutedText
                       font.pixelSize: Theme.fontSizeLabel }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedTextField {
                        id: simpleDuration
                        width: 90
                        text: "45"
                        validator: IntValidator { bottom: 1; top: 999 }
                    }
                    RoundedComboBox {
                        id: simpleUnit
                        width: 120
                        boundsItem: plannerCol
                        // Values match the builder's TIME_UNITS keys.
                        model: [qsTr("Minutes"), qsTr("Hours")]
                        currentIndex: 0
                        property string unitKey: currentIndex === 1 ? "hours" : "minutes"
                    }
                }
            }

            // Intervals: nothing to enter here (André, 2026-08-24: "on the UI of intervals
            // take out those numbers/intervals out, just a button 'open workout builder'") -
            // complex structure is designed on the full site, which the button opens.
            Row {
                spacing: Theme.spacingSmall
                RoundedButton {
                    // Simple: seed the whole one-block workout into the builder. Intervals: just
                    // open the builder (title pre-filled) - everything is built on the site.
                    text: plannerDialog.complex ? qsTr("Open Workout Builder") : qsTr("Create workout")
                    onClicked: {
                        const act = root.plannerActivities[plannerDialog.activityIndex]
                        if (plannerDialog.complex) {
                            IntervalsService.launch(root.plannerTitle(act))
                        } else {
                            IntervalsService.launchWithWorkout(
                                root.buildWorkout(act, parseInt(simpleDuration.text || "1"),
                                                  simpleUnit.unitKey))
                        }
                        plannerDialog.close()
                    }
                }
                RoundedButton {
                    text: qsTr("Cancel")
                    onClicked: plannerDialog.close()
                }
            }
        }
    }

}
