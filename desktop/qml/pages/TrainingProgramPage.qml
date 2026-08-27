import QtQuick
import QtQuick.Controls
import AmbitApp

// Training Program - real request, 2026-08-12 (André: "how can we build from scratch a
// workout planner that installs a scheduled (date) workout", then "movescount era called
// them training program. 2 on a screen on our app"). The Movescount-era feature name, on
// its own page: place workouts on real calendar dates, then install the whole program onto
// the watch as date-gated Suunto Apps (tools/training_plan.py's mechanism - the watch's own
// clock picks the day's workout; see that file's docstring). Authoring and saving plans is
// fully offline; "Preview packing" needs internet (live community compiler); "Install to
// watch" needs the connected Ambit.
//
// Month grid adapted from CalendarPage.qml's own (same Monday-first layout, same
// today-pill), showing planned workouts instead of recorded activities - the two pages are
// deliberate siblings: Calendar looks back, Training Program looks forward.
Item {
    id: root

    // ---- plan state (a JS mirror of tools/training_plan.py's schema) ----------------
    property string planName: qsTr("My Program")
    // [{date: "YYYY-MM-DD", workout: {name, steps: [...]}}]
    property var entries: []
    property bool dirty: false
    property string importStatus: ""

    // Merge intervals.icu-imported entries into the plan, replacing any existing entry on the
    // same date (dedupe by date) and keeping the list date-sorted. Imported entries carry their
    // own {mode}, which the guided-workout rotation sync uses.
    // The plan entries with a sport mode guaranteed on each: imported entries already carry one;
    // hand-added ones inherit the mode chosen in the Install card. training_calendar needs it.
    function entriesWithMode(fallbackMode) {
        return root.entries.map(function (e) {
            return { date: e.date, mode: e.mode || fallbackMode, workout: e.workout }
        })
    }

    function mergeImported(imported) {
        const byDate = {}
        for (const e of root.entries) byDate[e.date] = e
        let added = 0
        for (const e of imported) { if (!(e.date in byDate)) added++; byDate[e.date] = e }
        root.entries = Object.keys(byDate).sort().map(d => byDate[d])
        root.dirty = true
        return added
    }

    Connections {
        target: TrainingProgramService
        function onIntervalsImported(imported, skipped, resolvedToWatch) {
            const added = root.mergeImported(imported)
            root.importStatus = qsTr("Added %1 of %2 workout(s)").arg(added).arg(imported.length)
                + (resolvedToWatch ? qsTr(" — HR matched to the watch's zones.")
                                   : qsTr(" — HR from intervals.icu zones."))
                + (skipped && skipped.length ? qsTr(" Skipped %1.").arg(skipped.length) : "")
        }
    }

    function entryForDate(iso) {
        for (const e of root.entries)
            if (e.date === iso) return e
        return null
    }

    function isoFor(y, m, day) {
        const mm = String(m + 1).padStart(2, "0")
        const dd = String(day).padStart(2, "0")
        return y + "-" + mm + "-" + dd
    }

    Component.onCompleted: TrainingProgramService.refreshPlans()

    // ---- month navigation (CalendarPage's own model) --------------------------------
    readonly property date today: new Date()
    property int viewYear: today.getFullYear()
    property int viewMonth: today.getMonth()  // 0-11

    function goPrevMonth() {
        if (viewMonth === 0) { viewMonth = 11; viewYear -= 1 } else { viewMonth -= 1 }
    }
    function goNextMonth() {
        if (viewMonth === 11) { viewMonth = 0; viewYear += 1 } else { viewMonth += 1 }
    }
    function goToday() { viewYear = today.getFullYear(); viewMonth = today.getMonth() }
    readonly property bool isCurrentMonth:
        viewYear === today.getFullYear() && viewMonth === today.getMonth()

    readonly property var weeks: {
        const first = new Date(root.viewYear, root.viewMonth, 1)
        const daysInMonth = new Date(root.viewYear, root.viewMonth + 1, 0).getDate()
        const leading = (first.getDay() + 6) % 7

        const cells = []
        for (let i = 0; i < leading; i++) cells.push(null)
        for (let day = 1; day <= daysInMonth; day++) {
            const iso = root.isoFor(root.viewYear, root.viewMonth, day)
            cells.push({
                day: day,
                iso: iso,
                isToday: root.isCurrentMonth && day === root.today.getDate(),
                entry: root.entryForDate(iso),
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
                text: qsTr("Training Program")
                color: Theme.text
                font.pixelSize: Theme.fontSizeTitle
                font.bold: true
            }

            ErrorBanner {
                width: parent.width
                detail: TrainingProgramService.lastError
                context: qsTr("saving, compiling or installing a training program")
            }

            // ---- plan name + saved plans -------------------------------------------
            Card {
                width: parent.width

                Column {
                    width: parent.width
                    spacing: Theme.spacingMedium

                    Row {
                        width: parent.width
                        spacing: Theme.spacingMedium

                        RoundedTextField {
                            id: nameField
                            width: parent.width * 0.4
                            text: root.planName
                            placeholderText: qsTr("Program name")
                            onTextEdited: { root.planName = text; root.dirty = true }
                        }
                        RoundedButton {
                            text: qsTr("Save")
                            enabled: root.entries.length > 0
                            onClicked: {
                                TrainingProgramService.savePlan({
                                    name: root.planName, entries: root.entries })
                                root.dirty = false
                            }
                        }
                        RoundedButton {
                            text: qsTr("New")
                            onClicked: {
                                root.planName = qsTr("My Program")
                                root.entries = []
                                root.dirty = false
                            }
                        }
                        RoundedComboBox {
                            id: savedPlans
                            width: parent.width * 0.25
                            model: [qsTr("Load a saved plan…")].concat(
                                TrainingProgramService.plans.map(p => p.name))
                            onActivated: (index) => {
                                if (index <= 0) return
                                const plan = TrainingProgramService.plans[index - 1]
                                root.planName = plan.name
                                root.entries = plan.entries
                                root.dirty = false
                                currentIndex = 0
                            }
                        }
                        RoundedButton {
                            text: qsTr("Delete")
                            enabled: TrainingProgramService.plans.some(
                                p => p.name === root.planName)
                            onClicked: {
                                const plan = TrainingProgramService.plans.find(
                                    p => p.name === root.planName)
                                if (plan) TrainingProgramService.deletePlan(plan.id)
                            }
                        }
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: qsTr("Click a day to plan a workout for that date. The watch "
                                   + "runs it from its own clock - it does not need to be "
                                   + "connected on the day.")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }
            }

            // ---- import from intervals.icu ------------------------------------------
            Card {
                width: parent.width

                Column {
                    width: parent.width
                    spacing: Theme.spacingMedium

                    Text {
                        text: qsTr("Import from intervals.icu")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeHeading
                        font.bold: true
                    }
                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: ConnectionsService.intervalsIcuConnected
                            ? qsTr("Pull your planned workouts straight from intervals.icu into "
                                   + "the calendar below. HR targets are matched to the watch's "
                                   + "own zones when it's connected.")
                            : qsTr("Connect intervals.icu in Settings first, then your planned "
                                   + "workouts can be pulled in here.")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }

                    Row {
                        width: parent.width
                        spacing: Theme.spacingMedium

                        RoundedTextField {
                            id: importFrom
                            width: parent.width * 0.22
                            placeholderText: qsTr("From YYYY-MM-DD")
                            text: root.isoFor(root.today.getFullYear(),
                                              root.today.getMonth(), root.today.getDate())
                        }
                        RoundedTextField {
                            id: importTo
                            width: parent.width * 0.22
                            placeholderText: qsTr("To YYYY-MM-DD")
                            text: {
                                const d = new Date(root.today)
                                d.setDate(d.getDate() + 56)
                                return root.isoFor(d.getFullYear(), d.getMonth(), d.getDate())
                            }
                        }
                        RoundedComboBox {
                            id: importMode
                            width: parent.width * 0.28
                            model: CustomModesService.modes.map(m => m.name)
                            Component.onCompleted: CustomModesService.refresh()
                        }
                        RoundedButton {
                            text: TrainingProgramService.loading
                                  ? qsTr("Fetching…") : qsTr("Fetch")
                            enabled: ConnectionsService.intervalsIcuConnected
                                     && !TrainingProgramService.loading
                                     && importMode.currentText.length > 0
                            onClicked: {
                                root.importStatus = ""
                                TrainingProgramService.importFromIntervals(
                                    importFrom.text, importTo.text, importMode.currentText,
                                    ConnectionsService.intervalsIcuAthleteId,
                                    ConnectionsService.intervalsIcuApiKey())
                            }
                        }
                        LoadingPill {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: TrainingProgramService.loading
                        }
                    }

                    Text {
                        width: parent.width
                        visible: root.importStatus.length > 0
                        wrapMode: Text.WordWrap
                        text: root.importStatus
                        color: Theme.primary
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }
            }

            // ---- the calendar -------------------------------------------------------
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
                            width: parent.width - 40
                                   - (todayButton.visible ? todayButton.width + Theme.spacingSmall : 0)
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
                                text: qsTr("%1 planned workouts").arg(root.entries.length)
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
                                        readonly property bool planned:
                                            modelData !== null && modelData.entry !== null
                                        width: parent.width / 7
                                        height: 48

                                        Item {
                                            anchors.centerIn: parent
                                            width: 44
                                            height: width
                                            visible: dayCell.modelData !== null

                                            // Today: same solid pill CalendarPage uses.
                                            Rectangle {
                                                anchors.centerIn: parent
                                                visible: dayCell.modelData
                                                         && dayCell.modelData.isToday
                                                         && !dayCell.planned
                                                width: 26
                                                height: width
                                                radius: width / 2
                                                color: Theme.primary
                                            }
                                            // Today WITH a plan: ring around the plan dot.
                                            Rectangle {
                                                anchors.centerIn: parent
                                                visible: dayCell.modelData
                                                         && dayCell.modelData.isToday
                                                         && dayCell.planned
                                                width: 42
                                                height: width
                                                radius: width / 2
                                                color: "transparent"
                                                border.width: 2
                                                border.color: Theme.primary
                                            }
                                            // A planned day: a filled dot, same visual
                                            // vocabulary as Calendar's activity dots.
                                            Rectangle {
                                                anchors.centerIn: parent
                                                visible: dayCell.planned
                                                width: 32
                                                height: width
                                                radius: width / 2
                                                color: Theme.primary
                                                opacity: 0.9

                                                HoverHandler { id: dotHover }
                                                ToolTip.visible: dotHover.hovered
                                                ToolTip.delay: 300
                                                ToolTip.text: dayCell.planned
                                                    ? dayCell.modelData.entry.workout.name
                                                    : ""
                                            }

                                            Text {
                                                anchors.centerIn: parent
                                                text: dayCell.modelData
                                                      ? dayCell.modelData.day : ""
                                                color: dayCell.planned
                                                       || (dayCell.modelData
                                                           && dayCell.modelData.isToday)
                                                       ? Theme.card : Theme.text
                                                font.pixelSize: Theme.fontSizeBody
                                                font.bold: dayCell.modelData
                                                           && dayCell.modelData.isToday
                                            }

                                            HoverHandler {
                                                cursorShape: Qt.PointingHandCursor
                                            }
                                            TapHandler {
                                                onTapped: {
                                                    if (!dayCell.modelData) return
                                                    editor.openFor(dayCell.modelData.iso)
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

            // ---- install to watch ---------------------------------------------------
            Card {
                width: parent.width

                Column {
                    width: parent.width
                    spacing: Theme.spacingMedium

                    Text {
                        text: qsTr("Install to watch")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeHeading
                        font.bold: true
                    }

                    Text {
                        width: parent.width
                        wrapMode: Text.WordWrap
                        text: qsTr("Each workout installs as a native guided workout in the "
                                   + "sport mode's WORKOUT menu (hold [Next] to pick it). On "
                                   + "every sync the watch keeps what's still upcoming and "
                                   + "erases anything dated before today - a sport mode holds "
                                   + "only a few at once, so the calendar rotates them in as "
                                   + "their dates approach.")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }

                    Row {
                        width: parent.width
                        spacing: Theme.spacingMedium

                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: qsTr("Install into")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        RoundedComboBox {
                            id: modePicker
                            width: parent.width * 0.3
                            model: CustomModesService.modes.map(m => m.name)
                            Component.onCompleted: CustomModesService.refresh()
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            width: parent.width * 0.5
                            wrapMode: Text.WordWrap
                            text: qsTr("Workouts imported from intervals.icu keep their own "
                                       + "sport mode; this is the mode for any you add by hand.")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }

                    Row {
                        spacing: Theme.spacingMedium

                        RoundedButton {
                            text: qsTr("Preview sync")
                            enabled: root.entries.length > 0
                                     && !TrainingProgramService.installing
                            onClicked: TrainingProgramService.syncCalendar(
                                root.entriesWithMode(modePicker.currentText), false)
                        }
                        RoundedButton {
                            text: TrainingProgramService.installing
                                  ? qsTr("Working…") : qsTr("Sync to watch")
                            enabled: root.entries.length > 0
                                     && !TrainingProgramService.installing
                                     && HomeViewModel.anyDevice
                            onClicked: TrainingProgramService.syncCalendar(
                                root.entriesWithMode(modePicker.currentText), true)
                        }
                        LoadingPill {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: TrainingProgramService.installing
                        }
                    }

                    // Result of the last preview/sync - the rotation diff (erase past, install
                    // upcoming) reported by training_calendar.py.
                    Column {
                        width: parent.width
                        spacing: Theme.spacingSmall
                        visible: TrainingProgramService.lastInstallResult.rotation === true

                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            text: {
                                const r = TrainingProgramService.lastInstallResult
                                const inst = (r.added || [])
                                return qsTr("Install: ") + (inst.length ? inst.join(", ")
                                                                        : qsTr("(none)"))
                            }
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            text: {
                                const r = TrainingProgramService.lastInstallResult
                                const rem = (r.removed || [])
                                return qsTr("Erase past: ") + (rem.length ? rem.join(", ")
                                                                          : qsTr("(none)"))
                            }
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            visible: {
                                const r = TrainingProgramService.lastInstallResult
                                return (r.failed || []).length > 0
                            }
                            text: {
                                const r = TrainingProgramService.lastInstallResult
                                return qsTr("Could not compile: ")
                                    + (r.failed || []).map(f => f.name).join(", ")
                            }
                            color: Theme.warning
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        Text {
                            visible: {
                                const r = TrainingProgramService.lastInstallResult
                                return r.ok === true && r.dryRun === false
                            }
                            text: qsTr("Synced to the watch's WORKOUT menu.")
                            color: Theme.primary
                            font.pixelSize: Theme.fontSizeCaption
                            font.bold: true
                        }
                    }
                }
            }
        }
    }

    // ---- per-day workout editor ----------------------------------------------------
    ThemedDialog {
        id: editor
        title: qsTr("Workout for %1").arg(editDate)
        anchors.centerIn: parent
        width: Math.min(parent.width - Theme.spacingLarge * 2, 720)

        property string editDate: ""
        property string workoutName: ""
        // Editable step rows: {stepType, durationKind, durationValue, targetKind,
        // targetMin, targetMax, repeatCount} - converted to/from workout.py's schema on
        // open/save. Reassigned wholesale after each mutation so the Repeater updates.
        property var editSteps: []

        readonly property var stepTypes: ["warmup", "interval", "recovery", "cooldown",
                                          "repeatStart", "repeatEnd"]
        readonly property var stepTypeLabels: [qsTr("Warm up"), qsTr("Interval"),
                                               qsTr("Recovery"), qsTr("Cool down"),
                                               qsTr("Repeat start"), qsTr("Repeat end")]
        readonly property var durationKinds: ["time_min", "time_s", "distance_km",
                                              "distance_m", "ascent_m", "lap"]
        readonly property var durationLabels: [qsTr("Time (min)"), qsTr("Time (s)"),
                                               qsTr("Distance (km)"), qsTr("Distance (m)"),
                                               qsTr("Ascent (m)"), qsTr("Lap press")]
        readonly property var targetKinds: ["none", "hr", "pace", "speed",
                                            "vertical_speed", "power"]
        readonly property var targetLabels: [qsTr("No target"), qsTr("Heart rate"),
                                             qsTr("Pace"), qsTr("Speed"),
                                             qsTr("Vertical speed"), qsTr("Power")]

        function openFor(iso) {
            editDate = iso
            const entry = root.entryForDate(iso)
            workoutName = entry ? entry.workout.name : qsTr("Workout")
            editSteps = entry ? entry.workout.steps.map(editor.fromSchema)
                              : [editor.defaultStep("warmup"),
                                 editor.defaultStep("interval"),
                                 editor.defaultStep("cooldown")]
            open()
        }

        function defaultStep(type) {
            return { stepType: type, durationKind: "time_min", durationValue: 10,
                     targetKind: "none", targetMin: 0, targetMax: 0, repeatCount: 2 }
        }

        function fromSchema(s) {
            const type = s.type.typeName
            if (type === "repeatStart")
                return { stepType: type, repeatCount: s.type.value || 2,
                         durationKind: "time_min", durationValue: 0,
                         targetKind: "none", targetMin: 0, targetMax: 0 }
            if (type === "repeatEnd")
                return { stepType: type, repeatCount: 0, durationKind: "time_min",
                         durationValue: 0, targetKind: "none", targetMin: 0, targetMax: 0 }
            let kind = "time_s", value = s.duration ? s.duration.value : 0
            if (s.duration && s.duration.durationName === "time") {
                if (value % 60 === 0) { kind = "time_min"; value = value / 60 }
            } else if (s.duration && s.duration.durationName === "distance") {
                if (value % 1000 === 0) { kind = "distance_km"; value = value / 1000 }
                else kind = "distance_m"
            } else if (s.duration && s.duration.durationName === "ascent") {
                kind = "ascent_m"
            } else if (s.duration && s.duration.durationName === "lap") {
                kind = "lap"
            }
            const target = s.target && s.target.targetName ? s.target.targetName : "none"
            return { stepType: type, durationKind: kind, durationValue: value,
                     targetKind: target,
                     targetMin: s.target && s.target.valueRange ? s.target.valueRange.min : 0,
                     targetMax: s.target && s.target.valueRange ? s.target.valueRange.max : 0,
                     repeatCount: 0 }
        }

        function toSchema(row) {
            if (row.stepType === "repeatStart")
                return { type: { typeName: "repeatStart",
                                 value: Math.max(1, Math.round(row.repeatCount)) } }
            if (row.stepType === "repeatEnd")
                return { type: { typeName: "repeatEnd" } }
            const step = { type: { typeName: row.stepType } }
            const v = Number(row.durationValue)
            if (row.durationKind === "lap")
                step.duration = { durationName: "lap" }
            else if (row.durationKind === "time_min")
                step.duration = { durationName: "time", value: Math.round(v * 60) }
            else if (row.durationKind === "time_s")
                step.duration = { durationName: "time", value: Math.round(v) }
            else if (row.durationKind === "distance_km")
                step.duration = { durationName: "distance", value: Math.round(v * 1000) }
            else if (row.durationKind === "distance_m")
                step.duration = { durationName: "distance", value: Math.round(v) }
            else
                step.duration = { durationName: "ascent", value: Math.round(v) }
            if (row.targetKind !== "none")
                step.target = { targetName: row.targetKind,
                                valueRange: { min: Number(row.targetMin),
                                              max: Number(row.targetMax) } }
            return step
        }

        // Repeat markers must pair up and not nest - the same rule the generator's own
        // expand_steps() enforces; checked here so the Save button can refuse politely
        // instead of the compile failing later.
        readonly property bool repeatsBalanced: {
            let depth = 0
            for (const row of editSteps) {
                if (row.stepType === "repeatStart") { depth++; if (depth > 1) return false }
                if (row.stepType === "repeatEnd") { depth--; if (depth < 0) return false }
            }
            return depth === 0
        }

        function mutate(index, patch) {
            const next = editSteps.slice()
            next[index] = Object.assign({}, next[index], patch)
            editSteps = next
        }

        contentItem: Column {
            spacing: Theme.spacingMedium

            RoundedTextField {
                width: parent.width
                text: editor.workoutName
                placeholderText: qsTr("Workout name")
                onTextEdited: editor.workoutName = text
            }

            Repeater {
                model: editor.editSteps
                delegate: Row {
                    id: stepRow
                    required property var modelData
                    required property int index
                    width: parent.width
                    spacing: Theme.spacingSmall

                    readonly property bool isRepeat:
                        modelData.stepType === "repeatStart"
                        || modelData.stepType === "repeatEnd"

                    RoundedComboBox {
                        width: parent.width * 0.19
                        model: editor.stepTypeLabels
                        currentIndex: editor.stepTypes.indexOf(stepRow.modelData.stepType)
                        onActivated: (i) => editor.mutate(stepRow.index,
                                                          { stepType: editor.stepTypes[i] })
                    }
                    RoundedComboBox {
                        visible: !stepRow.isRepeat
                        width: parent.width * 0.19
                        model: editor.durationLabels
                        currentIndex: editor.durationKinds.indexOf(
                            stepRow.modelData.durationKind)
                        onActivated: (i) => editor.mutate(stepRow.index,
                                                          { durationKind: editor.durationKinds[i] })
                    }
                    RoundedTextField {
                        visible: !stepRow.isRepeat
                                 && stepRow.modelData.durationKind !== "lap"
                        width: parent.width * 0.1
                        text: String(stepRow.modelData.durationValue)
                        validator: DoubleValidator { bottom: 0 }
                        onTextEdited: editor.mutate(stepRow.index,
                                                    { durationValue: Number(text) })
                    }
                    RoundedTextField {
                        visible: stepRow.modelData.stepType === "repeatStart"
                        width: parent.width * 0.1
                        text: String(stepRow.modelData.repeatCount)
                        validator: IntValidator { bottom: 1; top: 99 }
                        onTextEdited: editor.mutate(stepRow.index,
                                                    { repeatCount: Number(text) })
                    }
                    RoundedComboBox {
                        visible: !stepRow.isRepeat
                        width: parent.width * 0.19
                        model: editor.targetLabels
                        currentIndex: editor.targetKinds.indexOf(
                            stepRow.modelData.targetKind)
                        onActivated: (i) => editor.mutate(stepRow.index,
                                                          { targetKind: editor.targetKinds[i] })
                    }
                    RoundedTextField {
                        visible: !stepRow.isRepeat
                                 && stepRow.modelData.targetKind !== "none"
                        width: parent.width * 0.08
                        text: String(stepRow.modelData.targetMin)
                        placeholderText: qsTr("min")
                        validator: DoubleValidator { bottom: 0 }
                        onTextEdited: editor.mutate(stepRow.index,
                                                    { targetMin: Number(text) })
                    }
                    RoundedTextField {
                        visible: !stepRow.isRepeat
                                 && stepRow.modelData.targetKind !== "none"
                        width: parent.width * 0.08
                        text: String(stepRow.modelData.targetMax)
                        placeholderText: qsTr("max")
                        validator: DoubleValidator { bottom: 0 }
                        onTextEdited: editor.mutate(stepRow.index,
                                                    { targetMax: Number(text) })
                    }
                    Icon {
                        anchors.verticalCenter: parent.verticalCenter
                        glyph: Icons.close
                        size: 18
                        color: Theme.mutedText
                        HoverHandler { cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: {
                                const next = editor.editSteps.slice()
                                next.splice(stepRow.index, 1)
                                editor.editSteps = next
                            }
                        }
                    }
                }
            }

            Row {
                spacing: Theme.spacingMedium
                RoundedButton {
                    text: qsTr("Add step")
                    onClicked: editor.editSteps =
                        editor.editSteps.concat([editor.defaultStep("interval")])
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: !editor.repeatsBalanced
                    text: qsTr("Repeat start/end markers must pair up (no nesting)")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                }
            }
        }

        // Same Item+Row-of-RoundedButton shape as ThemedDialog's own standardButtons
        // footer (see its header comment for why never a DialogButtonBox) - rebuilt here
        // because this dialog needs a third, destructive action the standard set lacks.
        footer: Item {
            implicitHeight: editorFooterRow.implicitHeight + Theme.spacingMedium

            Row {
                id: editorFooterRow
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.rightMargin: Theme.spacingMedium
                spacing: Theme.spacingSmall

                RoundedButton {
                    text: qsTr("Remove workout")
                    visible: root.entryForDate(editor.editDate) !== null
                    onClicked: {
                        root.entries = root.entries.filter(e => e.date !== editor.editDate)
                        root.dirty = true
                        editor.close()
                    }
                }
                RoundedButton {
                    text: qsTr("Cancel")
                    onClicked: editor.close()
                }
                RoundedButton {
                    text: qsTr("Save workout")
                    enabled: editor.editSteps.length > 0 && editor.repeatsBalanced
                    onClicked: {
                        const workout = { name: editor.workoutName,
                                          steps: editor.editSteps.map(editor.toSchema) }
                        const next = root.entries.filter(e => e.date !== editor.editDate)
                        next.push({ date: editor.editDate, workout: workout })
                        next.sort((a, b) => a.date < b.date ? -1 : 1)
                        root.entries = next
                        root.dirty = true
                        editor.close()
                    }
                }
            }
        }
    }
}
