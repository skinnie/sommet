import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import AmbitApp

// AMBITAPP_SPEC.md: "Selecting an activity opens: Large MapLibre map, Overview, Charts,
// Laps, Export, Upload, Notes." Overview and Export (real, 2026-08-07 - see below) are
// real; the rest stay honest placeholders - each needs its own real piece of work not yet
// done (a charting library choice for Charts, lap-boundary parsing exercise_log.py doesn't
// expose yet for Laps, real cloud auth for Upload, local persistence for Notes) rather than
// being faked here.
Item {
    id: root
    property var activity
    property string saveError: ""
    property string uploadStatus: ""
    property bool uploading: false
    signal back

    // intervals.icu per-activity upload result (see the Upload tab). exportFinished/exportError
    // are shared with the bulk export, which is fine - either way the status line reflects the
    // most recent upload outcome while this view is open.
    Connections {
        target: ActivityService
        function onExportFinished(uploaded, failed) {
            root.uploading = false
            root.uploadStatus = failed > 0 ? qsTr("Upload failed.")
                                           : qsTr("Uploaded to intervals.icu.")
        }
        function onExportError(message) { root.uploading = false; root.uploadStatus = message }
    }

    // Real request 2026-08-07: "built the export to gpx and to fit... save to file, default
    // location downloads" - the real bytes already exist on `activity` (ActivityService now
    // keeps gpxText/fitBase64, not just the fields parsed out of them), this just needed a
    // save dialog wired to LocalFileService, the same helper Routes' export uses.
    FileDialog {
        id: gpxExportDialog
        title: qsTr("Export activity as GPX")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("GPX files (*.gpx)")]
        currentFolder: LocalFileService.downloadsLocation
        onAccepted: root.saveError = LocalFileService.saveText(selectedFile, activity.gpxText)
    }
    FileDialog {
        id: fitExportDialog
        title: qsTr("Export activity as FIT")
        fileMode: FileDialog.SaveFile
        nameFilters: [qsTr("FIT files (*.fit)")]
        currentFolder: LocalFileService.downloadsLocation
        onAccepted: root.saveError = LocalFileService.saveBase64(selectedFile, activity.fitBase64)
    }

    readonly property var _center: ActivityViewModel.trackCenter(activity ? activity.track : null)
    readonly property var _tabs: [qsTr("Overview"), qsTr("Charts"), qsTr("Laps"),
                                    qsTr("Export"), qsTr("Upload"), qsTr("Notes")]
    property int currentTab: 0

    // Logged Suunto App outputs (ruleoutput1..5) for this move, as an ordered list of
    // {label, times, values} - the data the Charts tab graphs. Empty when the move was
    // recorded without any app logging (LogRule=1); see tools/app_logging.py.
    readonly property var _ruleSeries: {
        var out = [];
        var ro = activity && activity.ruleOutputs ? activity.ruleOutputs : null;
        if (ro) {
            var keys = Object.keys(ro).sort();
            for (var i = 0; i < keys.length; ++i) {
                var s = ro[keys[i]];
                if (s && s.values && s.values.length > 0)
                    out.push(s);
            }
        }
        return out;
    }

    // Gear attribution (manual per-activity picker). Key = the activity's start time (stable).
    // Sport name is decoded from the raw activity-type byte via ActivityTypes (D2-c).
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
        if (!id) id = GearService.defaultGearForSport(sportName())  // fall back to the sport default
        for (var i = 0; i < choices.length; ++i) if (choices[i].id === id) return i
        return 0
    }

    Column {
        anchors.fill: parent
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
            // Same per-sport badge as the grid/list views - see ActivityCard.qml's own
            // comment for why Kailash/Garmin "Walk" activities land on the same badge as an
            // Ambit "Walk" sport mode here too.
            ActivityBadge {
                activityId: activity ? ActivityTypes.displayId(activity.name, activity.sportTypeRaw) : 1
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
        }

        Item {
            width: parent.width - Theme.spacingLarge * 2
            x: Theme.spacingLarge
            height: 280

            MapView {
                anchors.fill: parent
                // Scroll to zoom - this map fills the detail view and is not inside a
                // scrolling page, so the wheel has nothing else to do here.
                scrollZoom: true
                visible: root._center !== null
                latitude: root._center ? root._center.lat : 0
                longitude: root._center ? root._center.lon : 0
                zoomLevel: 13
                showZoomControls: true
                trackPoints: (root.activity && root.activity.track) || []
            }
            Rectangle {
                visible: root._center === null
                anchors.fill: parent
                color: Theme.card
                radius: Theme.radiusCard
                Text {
                    anchors.centerIn: parent
                    text: qsTr("No GPS track for this activity")
                    color: Theme.mutedText
                }
            }
        }

        Row {
            x: Theme.spacingLarge
            spacing: Theme.spacingMedium

            Repeater {
                model: root._tabs
                delegate: Text {
                    text: modelData
                    font.bold: index === root.currentTab
                    color: index === root.currentTab ? Theme.primary : Theme.mutedText
                    TapHandler { onTapped: root.currentTab = index }
                }
            }
        }

        Card {
            x: Theme.spacingLarge
            width: parent.width - Theme.spacingLarge * 2

            // --- Overview: real data ---
            Column {
                width: parent.width
                visible: root.currentTab === 0
                spacing: Theme.spacingSmall

                Row {
                    width: parent.width
                    spacing: Theme.spacingLarge
                    Column {
                        spacing: 2
                        Text { text: qsTr("Distance"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: activity ? ActivityViewModel.formatDistance(activity.distanceMeters) : ""
                            color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle; font.bold: true
                        }
                    }
                    Column {
                        spacing: 2
                        Text { text: qsTr("Duration"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: activity ? ActivityViewModel.formatDuration(activity.durationSeconds) : ""
                            color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle; font.bold: true
                        }
                    }
                    Column {
                        spacing: 2
                        Text { text: qsTr("Elevation gain"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: activity ? ActivityViewModel.formatElevation(activity.ascentMeters) : ""
                            color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle; font.bold: true
                        }
                    }
                }
                Text {
                    text: activity ? qsTr("%1 GPS points recorded").arg(activity.track.length) : ""
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                }
                // Which device actually recorded this (André, 2026-08-25: "we also added from
                // what device they came from, and I don't see it"). The value was already being
                // imported and stored for every intervals row (4664/4664 have one - "GARMIN
                // FR965", "SUUNTO Suunto Race S", ...) and ActivityService already hands it to
                // QML as `device`; nothing in the UI had ever referenced it. Watch-read moves
                // leave it empty (the connected watch is implied), so this hides for those.
                Text {
                    visible: activity && (activity.device || "") !== ""
                    text: activity ? qsTr("Recorded on %1").arg(activity.device) : ""
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                }
                // Gear used — attribute this move's mileage to a bike/shoes (local tally).
                Row {
                    spacing: Theme.spacingSmall
                    visible: GearService.gears.length > 0
                    Text {
                        text: qsTr("Gear used")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeLabel
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    RoundedComboBox {
                        id: gearCombo
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

            // --- Export: real, 2026-08-07 ---
            Column {
                width: parent.width
                visible: root.currentTab === 3
                spacing: Theme.spacingSmall

                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: qsTr("Export as GPX")
                        enabled: activity && activity.gpxText && activity.gpxText.length > 0
                        onClicked: {
                            const safeName = (activity.name || "activity").replace(/[\\/:*?"<>|]/g, "_")
                            gpxExportDialog.currentFile =
                                LocalFileService.downloadsLocation + "/" + safeName + ".gpx"
                            gpxExportDialog.open()
                        }
                    }
                    RoundedButton {
                        text: qsTr("Export as FIT")
                        enabled: activity && activity.fitBase64 && activity.fitBase64.length > 0
                        onClicked: {
                            const safeName = (activity.name || "activity").replace(/[\\/:*?"<>|]/g, "_")
                            fitExportDialog.currentFile =
                                LocalFileService.downloadsLocation + "/" + safeName + ".fit"
                            fitExportDialog.open()
                        }
                    }
                }
                Text {
                    visible: activity && (!activity.fitBase64 || activity.fitBase64.length === 0)
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("No FIT data for this activity (GPS-less entries can't be " +
                                "converted - see exercise_log.py).")
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

            // --- Charts tab: logged Suunto App outputs (ruleoutput1..5) ---
            Column {
                width: parent.width
                spacing: Theme.spacingMedium
                visible: root.currentTab === 1 && root._ruleSeries.length > 0

                Text {
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

                        RuleOutputChart {
                            width: parent.width
                            height: 200
                            series: modelData
                        }

                        // Optional: also send this app's output to intervals.icu as a native
                        // stream (default = its own custom stream). Persisted per app; applied
                        // to the exported/uploaded FIT on the next sync.
                        Row {
                            width: parent.width
                            spacing: Theme.spacingSmall
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("intervals.icu:")
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            RoundedComboBox {
                                id: streamCombo
                                readonly property string appName: modelData && modelData.label
                                                                  ? modelData.label : ""
                                readonly property var _keys: ["custom", "power", "cadence",
                                                              "heartrate"]
                                model: [qsTr("Custom stream (default)"), qsTr("Power"),
                                        qsTr("Cadence"), qsTr("Heart rate")]
                                currentIndex: Math.max(0, _keys.indexOf(
                                    ActivityService.intervalsStreamFor(appName) || "custom"))
                                onActivated: {
                                    ActivityService.setIntervalsStreamFor(appName,
                                                                          _keys[currentIndex]);
                                }
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: streamCombo.currentIndex > 0
                                text: qsTr("— applies on next sync")
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }
                    }
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
                        text: root.uploading ? qsTr("Uploading…")
                                             : qsTr("Export to intervals.icu")
                        enabled: parent.parent._hasData && !root.uploading
                        onClicked: {
                            root.uploading = true
                            root.uploadStatus = ""
                            ActivityService.exportActivityToIntervals(
                                activity.name || "", activity.fitBase64 || "",
                                activity.gpxText || "")
                        }
                    }
                    RoundedButton {
                        text: qsTr("Export to Garmin")
                        enabled: parent.parent._hasData && !root.uploading
                        onClicked: {
                            root.uploading = true
                            root.uploadStatus = ""
                            ActivityService.exportActivityToGarmin(
                                activity.name || "", activity.fitBase64 || "",
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

            // --- Everything else: honest, not faked ---
            Text {
                width: parent.width
                visible: root.currentTab !== 0 && root.currentTab !== 3 && root.currentTab !== 4
                         && !(root.currentTab === 1 && root._ruleSeries.length > 0)
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                text: {
                    switch (root.currentTab) {
                    case 1: return qsTr("Charts - track-data charts (elevation/pace) still " +
                                          "need a charting-library decision. Logged Suunto " +
                                          "App outputs, when a mode has app logging on, appear " +
                                          "here as their own graph.");
                    case 2: return qsTr("Laps - not built yet. exercise_log.py doesn't " +
                                          "expose lap boundaries in its parsed output yet.");
                    case 4: return qsTr("Upload - not built yet. Needs real auth against " +
                                          "Intervals.icu/Runalyze/Strava, none of which are " +
                                          "connected yet (see the Connections card on Home).");
                    case 5: return qsTr("Notes - not built yet. Needs local persistence " +
                                          "that doesn't exist anywhere in this app yet.");
                    default: return "";
                    }
                }
            }
        }
    }
}
