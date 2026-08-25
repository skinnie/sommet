import QtQuick
import QtQuick.Controls
import QtQuick.Effects
import AmbitApp

// Step 7: real Apple-Photos-style activity cards, backed by ActivityService (parses the
// backend's raw GPX into distance/duration/elevation/track). Selecting a card opens
// ActivityDetail in place - a simple internal state swap, no separate nav entry needed.
Item {
    id: root
    property var selectedActivity: null

    // Right-click delete (André, 2026-08-25): a row/card asks via deleteRequested, this holds the
    // target while the centered confirm dialog below decides. The actual delete lives in
    // ActivityService.deleteActivity (local tombstone + permanent intervals.icu delete).
    property var pendingDelete: null
    function requestDelete(a) { pendingDelete = a; deleteDialog.open() }

    // The mouse's Back button returns from an open activity to the list (André, 2026-08-25).
    // Only accepts the Back/Forward side buttons, so normal left/right clicks fall straight
    // through to the rows and cards underneath.
    MouseArea {
        anchors.fill: parent
        z: 1000
        acceptedButtons: Qt.BackButton | Qt.ForwardButton
        onPressed: (mouse) => {
            if (mouse.button === Qt.BackButton && root.selectedActivity !== null) {
                root.selectedActivity = null
                mouse.accepted = true
            } else {
                mouse.accepted = false
            }
        }
    }

    // In-page sort. `activitySortKey` is "uploaded" (backend order, newest first), "name", or
    // any ActivityMetrics key (distance/pace/avgHr/…). Sorting a COPY keeps selection (by
    // object, not index) unaffected.
    property string activitySortKey: "uploaded"
    property bool activitySortDesc: true
    readonly property var sortedActivities: {
        var list = (root.activeActivities || []).slice()
        var key = root.activitySortKey
        var desc = root.activitySortDesc
        if (key === "uploaded") {
            // Backend order is already most-recent-first (= desc); reverse for ascending.
            if (!desc) list.reverse()
        } else if (key === "name") {
            list.sort(function(a, b) {
                var c = (a.name || "").localeCompare(b.name || "")
                return desc ? -c : c
            })
        } else {
            list.sort(function(a, b) {
                var c = ActivityMetrics.raw(a, key) - ActivityMetrics.raw(b, key)
                return desc ? -c : c
            })
        }
        return list
    }

    // The persisted, ordered column keys (reactive: re-reads when Theme.activityColumns
    // changes). Helpers below add/replace/remove columns, keeping them duplicate-free.
    readonly property var columns: { Theme.activityColumns; return Theme.activityColumnList() }
    function _columnsUsedExcept(idx) {
        var used = []
        for (var i = 0; i < columns.length; i++) if (i !== idx) used.push(columns[i])
        return used
    }
    function setColumn(idx, key) {
        var c = columns.slice(); c[idx] = key; Theme.setActivityColumns(c)
    }
    function removeColumn(idx) {
        if (columns.length <= 1) return
        var c = columns.slice(); c.splice(idx, 1); Theme.setActivityColumns(c)
    }
    function addColumn() {
        // First catalogue metric not already shown.
        for (var i = 0; i < ActivityMetrics.all.length; i++) {
            var k = ActivityMetrics.all[i].key
            if (columns.indexOf(k) === -1) { Theme.setActivityColumns(columns.concat([k])); return }
        }
    }
    function sortByColumn(key) {
        // Same column again toggles direction; a new column starts descending (largest first,
        // the useful default for distance/HR/etc.).
        if (activitySortKey === key) activitySortDesc = !activitySortDesc
        else { activitySortKey = key; activitySortDesc = true }
    }
    // Catalogue entries a given column may switch to: everything NOT already used by ANOTHER
    // column (its own current metric stays, so it shows checked) - keeps columns duplicate-free.
    function availableMetricsFor(idx) {
        var used = _columnsUsedExcept(idx)
        var out = []
        for (var i = 0; i < ActivityMetrics.all.length; i++) {
            var m = ActivityMetrics.all[i]
            if (used.indexOf(m.key) === -1) out.push(m)
        }
        return out
    }
    readonly property bool canAddColumn: columns.length < ActivityMetrics.all.length

    // Real, 2026-08-08 ("activities, just import the ones on the garmin device") - this
    // page is device-aware rather than duplicated: same grid/detail UI either way, sourced
    // from ActivityService (Ambit3, real watch log) or GarminService (Garmin, real GPX
    // files already sitting on the device) depending on which one HomeViewModel says is
    // actually connected. Matches the real Android app's own "no sub menu needed, just read
    // and log" simplicity for Garmin.
    //
    // Real, 2026-08-09 ("Activity logs from kailash => treat them as walks => import to
    // the activities") - Kailash has no ExerciseLog PMEM region at all (KailashService's
    // own header comment), so ActivityService has nothing to read for it. Its real
    // per-session data lives in KailashService.sessions instead (the DeviceHistory
    // "activity mode" logbook - when/durationSeconds/distanceMeters/maxSpeed, already
    // fetched on Home) - reshaped here into the exact {name, startTime, distanceMeters,
    // durationSeconds, ascentMeters, track} shape ActivityCard.qml already expects, so no
    // new QML component is needed. Every session becomes a real "Walk" card - Kailash's own
    // DeviceHistory doesn't record which activity type each session was (unlike Ambit3's
    // real ExerciseLog), and "Walk" is the closest honest default for a GPS-adventure watch
    // with no sport-mode concept at all, per this request.
    //
    // Real, 2026-08-09 ("Something is bizarre on the activities, they say no gps, but they
    // have gps") - ascentMeters/track used to be unconditionally empty here, described above
    // as "this logbook has summary stats only, no per-session GPS track" - true of
    // DeviceHistory sessions on their own, but wrong to leave it there: the watch's separate,
    // continuous TrackLog DOES cover these same real time windows. KailashService.
    // trackLogActivities now does that correlation server-side (see kailash_tracklog.py's
    // split_into_activities() docstring) and comes back index-aligned 1:1 with
    // KailashService.sessions - zipped together here so distance/duration keep coming from
    // the watch's own real reported stats (more accurate than a GPS-derived approximation)
    // while track comes from the real correlated GPS points. A session genuinely outside
    // TrackLog's coverage (predates capture start, etc.) still gets a real empty track, not a
    // wrong one - ActivityCard.qml already renders that as "No GPS track", which is correct
    // for that specific case.
    readonly property var kailashActivities: KailashService.sessions.map(function(s, i) {
        var t = KailashService.trackLogActivities[i];
        return {
            name: qsTr("Walk"),
            startTime: s.when,
            distanceMeters: s.distanceMeters,
            durationSeconds: s.durationSeconds,
            ascentMeters: 0,
            track: (t && t.track) ? t.track : [],
        };
    })

    readonly property bool loading:
        HomeViewModel.isGarmin ? GarminService.activitiesLoading
        : HomeViewModel.isKailash ? KailashService.loading
        : ActivityService.loading
    readonly property var activeActivities:
        HomeViewModel.isGarmin ? GarminService.activities
        : HomeViewModel.isKailash ? root.kailashActivities
        : ActivityService.activities

    Component.onCompleted: {
        // Opened straight into an activity (from a Calendar day click) - honour the pending
        // selection the NavBus carried, then clear it so a later plain visit shows the list.
        if (NavBus.pendingActivity) {
            root.selectedActivity = NavBus.pendingActivity
            NavBus.pendingActivity = null
        }
        ActivityService.refresh()
        GarminService.refreshActivities()
        KailashService.refreshHistory()
        // Real, 2026-08-09: needed for the real per-session track correlation above - this
        // page didn't fetch TrackLog at all before (track was always the empty placeholder).
        // A real ~1.3MB flash read (slow, see KailashService::refreshTrackLog()'s own
        // comment) but this page is only opened on demand, not part of the Home hot path.
        //
        // Real, 2026-08-09 ("activities, they take a while to load...any chance of fixing?")
        // - Component.onCompleted re-fires every time this page is (re)loaded (Main.qml's
        // Loader recreates it on navigation), so leaving this unconditional meant paying the
        // real ~39s flash read again on every single visit, even though the watch's own
        // TrackLog data can't have changed since the last read within the same connected
        // session. Skipped once a real read has already succeeded - HomePage.qml's own
        // Kailash-connect handler still does the first one.
        if (!KailashService.trackLogOk)
            KailashService.refreshTrackLog()
    }

    // Real, not a guess: the watch's ExerciseLog region is ~5.3MB, read 1024 bytes at a
    // time over USB - genuinely takes a couple of minutes. Without this, the page was a
    // blank white screen the whole time (found 2026-08-07 via real testing) - looked broken,
    // wasn't. Garmin's own read is a plain local file read - fast, and Kailash's own
    // DeviceHistory read (2026-08-09) is a single 0x1200 query, also fast - so this message
    // only applies to the Ambit3 ExerciseLog path.
    LoadingPill {
        // Real, 2026-08-11 (André, with a screenshot): "give some spacing" - the pill was
        // painting straight over the first activity rows. Exactly the bug the Kailash banner
        // below already hit ("the loading text is on the back of the activities cards"): the
        // fast session list can arrive while this is still up, and the list/grid, declared
        // later in this file, only knew to move down for that OTHER banner. Both indicators
        // are now in the anchor chain, and this one gets the same explicit z.
        id: activityLoadingPill
        z: 1
        visible: root.selectedActivity === null && root.loading
                 && !HomeViewModel.isGarmin && !HomeViewModel.isKailash
        anchors.horizontalCenter: parent.horizontalCenter
        // Floats at the BOTTOM as a status toast (André's pick, 2026-08-25). It used to sit at
        // the top on Home's line, but during a REFRESH (cached rows already on screen) the
        // header legitimately occupies that same line and the two overlapped. Down here the
        // header keeps Home's line permanently and both stay visible, never colliding - and
        // since activitiesViewToggle is anchored to parent.top at a constant, moving this pill
        // can no longer drag the List/Distance/Duration/Ascent header out of alignment.
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Theme.spacingLarge
        text: qsTr("Adventures loading...")
        // The apology went (André, 2026-08-11: "take out that part") - it explained OUR
        // constraint, which is not the user's problem. How long to expect stays: that is
        // the one thing worth knowing while waiting on a multi-minute read.
        detail: qsTr("Reading them off the watch - this can take a couple of minutes")
    }

    // Real, 2026-08-09 ("activities, they take a while to load...any chance of fixing?") -
    // sessions themselves (name/distance/duration) already show up fast once
    // KailashService.historyOk arrives (a single quick SBEM query), well before this. Without
    // this text, cards would just silently gain a GPS track/map some ~30-40s later with zero
    // explanation - looked like nothing was happening, not "still working." trackLogLoading
    // is tracked separately from KailashService.loading specifically because that shared flag
    // already clears as soon as the fast history request finishes (see its own header
    // comment) - this needed its own signal that stays true for this request's real duration.
    Rectangle {
        // Real bug, found live 2026-08-09 ("the loading text is on the back of the
        // activities cards") - unlike the other loading texts on this page, this one can be
        // visible at the same time the GridView below already has real cards to draw (the
        // fast session list arrives before this slow TrackLog read finishes), and QML stacks
        // siblings by declaration order when z isn't set - the GridView, declared later in
        // this file, was painting over this text instead of the reverse. Explicit z instead
        // of just reordering the declarations, so this stays correct regardless of future
        // edits moving things around. Also given a real opaque background (a plain Text
        // wasn't enough on its own - readable over the page's own background, but not over a
        // busy map/photo card sitting right under it) and made click-through-blocking so it
        // doesn't sit invisibly on top of a card's own TapHandler.
        id: trackLogLoadingBanner
        z: 1
        visible: root.selectedActivity === null && HomeViewModel.isKailash
                 && KailashService.trackLogLoading
        // Real, 2026-08-09 ("align the box... to be centered compared to the cards"),
        // corrected the same day after a real screenshot showed it still off - GridView
        // packs cards flush against its own left edge, it doesn't center an incomplete last
        // column, so all of (width - contentWidthUsed) is unused space on the *right*, not
        // split evenly on both sides as the first version of this assumed.
        // Real, 2026-08-09 ("replicate the design of the cards... to avoid unnecessary
        // colors") - plain Theme.card background, matching Card.qml's own look.
        // 2026-08-25 (André: "no shadows, all app"): Card.qml itself dropped its shadow, so
        // this banner (which deliberately copies Card's look) drops it too, in favour of the
        // same hairline border every card now relies on for separation from the page.
        x: activitiesGrid.x + (activitiesGrid.contentWidthUsed - width) / 2
        // Sits BELOW the fixed header line (see the loading pill above - the header no longer
        // chains off these banners, so they clear it instead).
        y: 44
        width: Math.min(parent.width - Theme.spacingLarge * 2, bannerText.implicitWidth + Theme.spacingMedium * 2)
        height: bannerText.implicitHeight + Theme.spacingSmall * 2
        radius: Theme.radiusCard
        color: Theme.card
        border.width: 1
        border.color: Theme.border

        MouseArea { anchors.fill: parent }  // absorbs clicks meant for the banner, not a card underneath

        Text {
            id: bannerText
            anchors.centerIn: parent
            width: parent.width - Theme.spacingMedium * 2
            wrapMode: Text.WordWrap
            horizontalAlignment: Text.AlignHCenter
            color: Theme.mutedText
            text: qsTr("Loading GPS tracks for these activities off the watch " +
                        "(a real ~1.3MB flash read, can take up to a minute)...")
        }
    }

    // Real request 2026-08-07: "activities... saved in the computer... loads when watch is
    // not plugged" - ActivityService now caches every successful read locally and falls
    // back to that cache when a live read fails, flagged here rather than silently shown as
    // if it were current. Garmin has no separate cache concept - its own files already live
    // on the device's own storage, read fresh every time.
    Text {
        id: cachedBanner
        // Real bug, found live 2026-08-09 ("after loaded there is still text under the
        // cards") - this is genuinely an ActivityService/Ambit3 concept (its own on-disk
        // exercise-log cache), but was never gated against Kailash, so a stale
        // ActivityService.showingCachedData left over from an earlier Ambit3 connection this
        // same app session could show it while a Kailash was the one actually connected -
        // same declaration-order-vs-GridView issue as the trackLog banner above, peeking out
        // near/under the card grid instead of being hidden outright.
        visible: root.selectedActivity === null && !root.loading && !HomeViewModel.isGarmin
                 && !HomeViewModel.isKailash && ActivityService.showingCachedData
        anchors.horizontalCenter: parent.horizontalCenter
        // Sits BELOW the fixed header line (see the loading pill above - the header no longer
        // chains off these banners, so they clear it instead).
        y: 44
        color: Theme.mutedText
        font.italic: true
        text: qsTr("Showing cached activities from the last time the watch was connected.")
    }

    Column {
        visible: root.selectedActivity === null
                 && !root.loading && root.activeActivities.length === 0
        anchors.horizontalCenter: parent.horizontalCenter
        y: Theme.spacingLarge
        spacing: Theme.spacingSmall
        Text {
            anchors.horizontalCenter: parent.horizontalCenter
            color: Theme.mutedText
            text: HomeViewModel.isGarmin
                ? qsTr("Nothing to sync.")
                : (ActivityService.ok
                    ? qsTr("Nothing to sync.")
                    : qsTr("Couldn't load activities: %1").arg(ActivityService.lastError))
        }
        // Real fix, not cosmetic: the only way to retry used to be navigating away and
        // back (which happens to re-run Component.onCompleted since Main.qml's Loader
        // recreates the page) - not discoverable, and a real problem if this page's very
        // first load raced the watch still connecting (found 2026-08-07 via real testing).
        RoundedButton {
            visible: HomeViewModel.isGarmin ? false : !ActivityService.ok
            anchors.horizontalCenter: parent.horizontalCenter
            text: qsTr("Retry")
            onClicked: ActivityService.refresh()
        }
    }

    // GridView, not a plain Repeater-in-Flow: each ActivityCard embeds a live MapView (its
    // own GeoServices plugin instance, own tile cache/GL context), and a Repeater
    // instantiates every delegate at once regardless of what's actually visible. On real
    // hardware (confirmed 2026-08-07, see V3_CHANGELOG.md) that was enough simultaneous map
    // instances to crash the app outright with the original MapLibre backend, and it would
    // only get worse as more activities accumulate on the watch over time, not stay a fixed
    // cost - kept even after switching to Qt's own lighter "osm" plugin, since the
    // scaling problem is real regardless of which plugin renders each map. GridView
    // with reuseItems does real delegate virtualization: only what's near the viewport is
    // instantiated, recycled as it scrolls, bounding the live-map count to a small constant
    // regardless of list length.
    // Sort control (André, 2026-08-16). Two shapes: a simple "Sort:" chip row for the map/card
    // GRID view (cards have no columns), and, for the LIST view, a proper column-header row
    // aligned exactly with ActivityRow's own columns (badge 32, name 42%, then 96/96/88/96
    // right-aligned) - André's feedback: the sort labels must line up with the data columns,
    // and the Calories column needs a title too.
    // Map/list view control - moved here from Settings (André, 2026-08-16). On the LEFT, on the
    // same horizontal line as the header/sort row, as a text dropdown matching the column-header
    // menus (André: "on the left side, as text, as a dropdown menu as the other stuff, aligned,
    // on the same horizontal line") - the old right-side pill overlapped the Calories column
    // header. Still persisted via Theme.activitiesView.
    ViewModeToggle {
        id: activitiesViewToggle
        z: 3
        // Anchored to the PAGE TOP always, never to whatever banner/pill happens to be showing
        // (André, 2026-08-25, after this drifted repeatedly: "for god sake align list distance
        // duration and ascent"). Chaining off `<banner>.bottom` made this row's position depend
        // on each banner's own rendered height, so every tweak to the loading pill silently
        // moved the header - and the pill's real height never matched the arithmetic (font
        // metrics), so it never landed where computed. Anchoring to parent.top with ONE literal
        // margin removes that whole coupling: 17px puts this toggle's centre (its box is 26px
        // tall, so +13) on y=30, exactly NavRail's Home centre, whatever else is on screen.
        // The banners are overlays with their own y (z:1) and simply draw over/around this.
        anchors.top: parent.top
        anchors.topMargin: 17
        anchors.left: parent.left
        // Align with the leftmost content of the rows below (badge column left edge).
        anchors.leftMargin: Theme.spacingLarge + Theme.spacingMedium
        visible: root.selectedActivity === null && (root.activeActivities || []).length > 0
        mode: Theme.activitiesView
        onChosen: (m) => Theme.activitiesView = m
    }

    Row {
        id: activitiesSortSimple
        // Starts to the right of the view dropdown so both share one horizontal line (map view).
        anchors.left: activitiesViewToggle.right
        anchors.right: parent.right
        anchors.leftMargin: Theme.spacingLarge
        anchors.rightMargin: Theme.spacingLarge
        // Share the view dropdown's horizontal line.
        anchors.verticalCenter: activitiesViewToggle.verticalCenter
        spacing: Theme.spacingSmall
        visible: root.selectedActivity === null && root.sortedActivities.length > 1
                 && Theme.activitiesView !== "list"
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("Sort:")
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
        }
        Repeater {
            model: [
                { key: "uploaded", label: qsTr("Last uploaded") },
                { key: "name", label: qsTr("Name") },
                { key: "distance", label: qsTr("Distance") },
                { key: "ascent", label: qsTr("Ascent") },
            ]
            delegate: Text {
                anchors.verticalCenter: parent.verticalCenter
                text: modelData.label
                color: root.activitySortKey === modelData.key ? Theme.primary : Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                font.bold: root.activitySortKey === modelData.key
                TapHandler { onTapped: root.sortByColumn(modelData.key) }
                HoverHandler { cursorShape: Qt.PointingHandCursor }
            }
        }
    }

    // LIST-view column headers - same widths/margins/spacing as ActivityRow so each title sits
    // over its data column. Distance/Ascent (and Name/Last uploaded) are clickable to sort;
    // Duration/Calories are plain titles (not sort keys André asked for, but shown so every
    // column is labelled).
    Row {
        id: activitiesHeader
        anchors.left: parent.left
        anchors.right: parent.right
        // ActivityRow lives inside the ListView (leftMargin spacingLarge) and its own inner Row
        // adds spacingMedium - match both so columns line up to the pixel.
        anchors.leftMargin: Theme.spacingLarge + Theme.spacingMedium
        anchors.rightMargin: Theme.spacingLarge + Theme.spacingMedium
        // Real bug fix (André, 2026-08-25: "list and distance and duration and ascent should
        // always be aligned") - this Row used to compute its OWN top/topMargin independently
        // off the same banner chain activitiesViewToggle ("List ▾") also uses, but with a
        // DIFFERENT margin value, so the two only coincidentally lined up rather than being
        // guaranteed to (they visibly drifted apart depending on which banner was showing).
        // Anchoring directly to the toggle's own verticalCenter makes them match BY
        // CONSTRUCTION - always, in every state - instead of two numbers that have to be kept
        // in sync by hand. activitiesSortSimple (the map-view Sort row) already used this exact
        // pattern for the same reason.
        anchors.verticalCenter: activitiesViewToggle.verticalCenter
        height: 44
        spacing: Theme.spacingMedium
        visible: root.selectedActivity === null && root.sortedActivities.length > 1
                 && Theme.activitiesView === "list"

        Item { width: 32; height: 1 }   // over the activity badge

        // Over the name/date column - no label now (André: "remove name/last uploaded"),
        // just a spacer so the metric columns keep lining up with the data.
        Item {
            anchors.verticalCenter: parent.verticalCenter
            width: activitiesHeader.width * 0.42
            height: 1
        }

        // One dropdown per configured metric column. Clicking opens a menu to sort by it or
        // change which metric it shows (excluding metrics already in another column, so no
        // duplicates). The active-sort column is highlighted with a direction arrow.
        Repeater {
            model: root.columns
            delegate: Item {
                id: colHeader
                required property var modelData   // metric key
                required property int index
                readonly property bool active: root.activitySortKey === modelData
                anchors.verticalCenter: parent.verticalCenter
                width: ActivityMetrics.widthFor(modelData)
                height: 26

                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: 3
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: ActivityMetrics.labelFor(colHeader.modelData)
                        color: colHeader.active ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                        font.bold: colHeader.active
                    }
                    Text {   // sort-direction arrow, only on the active column
                        anchors.verticalCenter: parent.verticalCenter
                        visible: colHeader.active
                        text: root.activitySortDesc ? "↓" : "↑"
                        color: Theme.primary
                        font.pixelSize: Theme.fontSizeCaption
                    }
                    Text {   // dropdown caret
                        anchors.verticalCenter: parent.verticalCenter
                        text: "▾"
                        color: colHeader.active ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }
                TapHandler { onTapped: colMenu.popup() }
                HoverHandler { cursorShape: Qt.PointingHandCursor }

                ThemedMenu {
                    id: colMenu
                    ThemedMenuItem {
                        text: qsTr("Sort ascending")
                        onTriggered: { root.activitySortKey = colHeader.modelData; root.activitySortDesc = false }
                    }
                    ThemedMenuItem {
                        text: qsTr("Sort descending")
                        onTriggered: { root.activitySortKey = colHeader.modelData; root.activitySortDesc = true }
                    }
                    MenuSeparator {
                        contentItem: Rectangle { implicitHeight: 1; color: Theme.mutedText; opacity: 0.3 }
                    }
                    Repeater {
                        model: root.availableMetricsFor(colHeader.index)
                        delegate: ThemedMenuItem {
                            required property var modelData
                            text: modelData.label
                            checkable: true
                            checked: modelData.key === colHeader.modelData
                            onTriggered: root.setColumn(colHeader.index, modelData.key)
                        }
                    }
                    MenuSeparator {
                        contentItem: Rectangle { implicitHeight: 1; color: Theme.mutedText; opacity: 0.3 }
                    }
                    ThemedMenuItem {
                        text: qsTr("Remove column")
                        enabled: root.columns.length > 1
                        onTriggered: root.removeColumn(colHeader.index)
                    }
                }
            }
        }

        // "+" add a column - an elegant round pill (André, 2026-08-16: "an elegant + to add
        // more field"). Adds the first metric not already shown.
        Rectangle {
            anchors.verticalCenter: parent.verticalCenter
            visible: root.canAddColumn
            width: 22; height: 22; radius: 11
            color: addHover.hovered ? Theme.primary : "transparent"
            border.width: 1
            border.color: addHover.hovered ? Theme.primary : Theme.mutedText
            Text {
                anchors.centerIn: parent
                text: "+"
                color: addHover.hovered ? Theme.card : Theme.mutedText
                font.pixelSize: Theme.fontSizeBody
            }
            HoverHandler { id: addHover; cursorShape: Qt.PointingHandCursor }
            TapHandler { onTapped: root.addColumn() }
        }
    }

    GridView {
        id: activitiesGrid
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.spacingLarge
        anchors.rightMargin: Theme.spacingLarge
        anchors.bottomMargin: Theme.spacingLarge
        // Real, 2026-08-09 ("put the cards down, give space between the text and the
        // cards") - the loading banner above used to sit at the same y as this grid's own
        // top margin, with the grid painting right up against/behind it (z: 1 on the banner
        // only fixed which one is on top, not the fact they occupied the same space). Drops
        // below the banner's own bottom edge, with real spacing, only while it's visible.
        anchors.top: activitiesSortSimple.visible ? activitiesSortSimple.bottom
                     : trackLogLoadingBanner.visible ? trackLogLoadingBanner.bottom
                     : activityLoadingPill.visible ? activityLoadingPill.bottom : parent.top
        anchors.topMargin: activitiesSortSimple.visible ? Theme.spacingMedium
                           : (trackLogLoadingBanner.visible || activityLoadingPill.visible)
                           ? Theme.spacingMedium : Theme.spacingLarge
        visible: root.selectedActivity === null && Theme.activitiesView !== "list"
        clip: true
        cellWidth: 360 + Theme.spacingMedium
        cellHeight: 280 + Theme.spacingMedium
        reuseItems: true
        // Real, 2026-08-09 ("align the box with the loading gps track to be centered
        // compared to the cards") - GridView packs cards from the left and doesn't stretch
        // to fill its own width, so centering the loading banner on the *page* only lines up
        // with the cards when they happen to fill every column exactly. This is how many
        // columns are actually occupied right now, so the banner below can center on that
        // real span instead.
        readonly property int columnsShown:
            Math.max(1, Math.min(Math.floor(width / cellWidth), Math.max(1, model ? model.length : 1)))
        readonly property real contentWidthUsed: columnsShown * cellWidth
        model: root.sortedActivities
        delegate: Item {
            width: GridView.view.cellWidth
            height: GridView.view.cellHeight
            ActivityCard {
                anchors.left: parent.left
                anchors.top: parent.top
                activity: modelData
                onOpened: root.selectedActivity = modelData
                onDeleteRequested: root.requestDelete(modelData)
            }
        }
    }

    // List view - André, item 16. A ListView rather than a Repeater for the same reason the
    // grid is a GridView: it virtualises, so a long history costs the same as a short one.
    ListView {
        id: activitiesList
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.bottom: parent.bottom
        anchors.leftMargin: Theme.spacingLarge
        anchors.rightMargin: Theme.spacingLarge
        anchors.bottomMargin: Theme.spacingLarge
        // Anchor below the column headers, with a generous gap so the headers read as a
        // separate band from the rows (André, 2026-08-16: "do more distance between the first
        // two lines of text and the activities").
        anchors.top: activitiesHeader.visible ? activitiesHeader.bottom
                     : trackLogLoadingBanner.visible ? trackLogLoadingBanner.bottom
                     : activityLoadingPill.visible ? activityLoadingPill.bottom : parent.top
        // 2px (André, 2026-08-25: "align them" with the nav rail) - matches NavItem's own
        // inter-item gap (NavRail's Column spacing: 2) exactly, now that the header above is a
        // real 44px slot and each ActivityRow is 44px too: header-bottom + 2 lands the first
        // row's center on "Activities"'s nav position, and every row after that keeps the same
        // 46px pitch as the nav rail (44 + 2), so row N lines up with nav item N+1.
        anchors.topMargin: 2
        visible: root.selectedActivity === null && Theme.activitiesView === "list"
        clip: true
        reuseItems: true
        spacing: 2
        model: root.sortedActivities
        delegate: ActivityRow {
            required property var modelData
            width: activitiesList.width
            activity: modelData
            onOpened: root.selectedActivity = modelData
            onDeleteRequested: root.requestDelete(modelData)
        }
    }

    ActivityDetail {
        anchors.fill: parent
        visible: root.selectedActivity !== null
        activity: root.selectedActivity
        onBack: root.selectedActivity = null
    }

    // Centered, modal confirm for a right-click delete. ThemedDialog dims the whole app behind
    // it (Overlay.modal scrim); anchoring to Overlay.overlay puts it in the middle of the window.
    ThemedDialog {
        id: deleteDialog
        title: qsTr("Delete this activity?")
        standardButtons: Dialog.NoButton
        width: 430
        readonly property var target: root.pendingDelete
        readonly property string targetName:
            target ? (target.name || qsTr("this activity")) : ""

        contentItem: Column {
            spacing: Theme.spacingLarge

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeBody
                text: (deleteDialog.target && deleteDialog.target.source === "intervals")
                    ? qsTr("“%1” will be removed from Sommet and permanently deleted from "
                           + "intervals.icu. This can't be undone.").arg(deleteDialog.targetName)
                    : qsTr("“%1” will be removed from Sommet and kept from coming back on the "
                           + "next sync. Your watch's own copy can't be deleted.")
                          .arg(deleteDialog.targetName)
            }

            // Cancel + Delete on one right-aligned line (André: "delete button and cancel
            // aligned"). Both 36px tall so they share a baseline; Delete is the red destructive one.
            Item {
                width: parent.width
                height: 36
                Row {
                    anchors.right: parent.right
                    anchors.verticalCenter: parent.verticalCenter
                    spacing: Theme.spacingSmall

                    RoundedButton {
                        text: qsTr("Cancel")
                        onClicked: deleteDialog.close()
                    }
                    // Destructive action - same silhouette as RoundedButton (height, padding,
                    // radius) so the two sit as one coherent pair, just outlined in red instead
                    // of neutral. Real, 2026-08-25 (André: "not good" on the first version) -
                    // that first version flipped to a SOLID red fill + white text on hover, which
                    // is exactly the "flashy" saturated-fill look the rest of the app's status-
                    // calming pass spent this whole session getting away from. Now it only
                    // deepens the tint slightly on hover - still calm, still unmistakably the
                    // one destructive control in the dialog.
                    RoundedButton {
                        id: confirmDel
                        text: qsTr("Delete")
                        background: Rectangle {
                            radius: Theme.radiusSmall
                            color: Qt.rgba(Theme.error.r, Theme.error.g, Theme.error.b,
                                           (confirmDel.pressed || confirmDel.hovered) ? 0.20 : 0.10)
                            border.width: 1
                            border.color: Theme.error
                            Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
                        }
                        contentItem: Text {
                            text: confirmDel.text
                            color: Theme.error
                            font.pixelSize: Theme.fontSizeBody
                            font.bold: true
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        onClicked: {
                            ActivityService.deleteActivity(deleteDialog.target)
                            deleteDialog.close()
                        }
                    }
                }
            }
        }
    }
}
