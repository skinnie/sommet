import QtQuick
import QtQuick.Controls
import AmbitApp

// Step 3: the real navigation shell. Home/Activities/Routes/POIs/Backup/Settings pages are
// still placeholders (their own steps fill them in) - this is about the shell itself:
// selection, layout, and Sport Modes staying hidden until FeatureFlags.sportModes flips.
ApplicationWindow {
    id: window
    visible: true
    width: 1200
    height: 800
    title: qsTr("Sommet")
    // Real, 2026-08-10 ("To recall: implement logo on desktop mode") - verified live:
    // ApplicationWindow has no `icon` property in this Qt build (6.12 - confirmed against
    // QtQuick.Templates' own plugins.qmltypes, not just a typo here), so this QML-side
    // attempt failed to even load ("Cannot assign to non-existent property icon"). Not
    // needed anyway - main.cpp's own QGuiApplication::setWindowIcon() (see its header
    // comment) already sets the same packaging/icon.png application-wide, which covers the
    // taskbar/dock entry AND every window's own icon, this one included.
    color: Theme.background
    // Real, 2026-08-09 ("general desktop polish pass") - without this, toggling
    // Settings' light/dark override snapped every color in the app instantly; this is the
    // one place that's genuinely app-wide (every page sits on this window's background).
    Behavior on color { ColorAnimation { duration: 150; easing.type: Easing.OutCubic } }

    // Collapsible nav rail (2026-08-29, André): the ☰ at the top of NavRail hides it to reclaim
    // the width; a floating ☰ over the content brings it back. Docked-collapse on desktop, the
    // same "one hamburger, hide the sidebar" idea the Android app uses (as an overlay drawer on
    // phones, a docked collapse on tablets). Expanded by default - there's room on the desktop.
    property bool navExpanded: true

    readonly property var pageSources: ({
        home: "pages/HomePage.qml",
        activities: "pages/ActivitiesPage.qml",
        routes: "pages/RoutesPage.qml",
        planRoute: "pages/PlanRoutePage.qml",
        pois: "pages/PoisPage.qml",
        backup: "pages/BackupPage.qml",
        firmware: "pages/FirmwarePage.qml",
        watchSettings: "pages/WatchSettingsPage.qml",
        smartSensor: "pages/SmartSensorPage.qml",
        settings: "pages/SettingsPage.qml",
        sportModes: "pages/SportModesPage.qml",
        appZone: "pages/AppZonePage.qml",
        totals: "pages/TotalsPage.qml",
        calendar: "pages/CalendarPage.qml",
        gear: "pages/GearPage.qml",
        coach: "pages/CoachPage.qml",
        weight: "pages/WeightPage.qml",
        health: "pages/HealthPage.qml",
        ember: "pages/EmberPage.qml",
        gpsTrackPod: "pages/GpsTrackPodPage.qml",
        suuntoT6: "pages/SuuntoT6Page.qml",
        trainingProgram: "pages/TrainingProgramPage.qml",
    })

    // Testing mode's simulated eTrex, wired here rather than in Settings: the device stays
    // simulated while you walk around Activities, Routes and POIs, so the binding has to
    // outlive whichever page is loaded. GarminService then discovers the fixture folder with
    // its own real scan - Settings only decides which device is selected, it does not reach
    // into the Garmin path itself.
    Binding {
        target: GarminService
        property: "demoRoot"
        value: DeviceService.demoGarminRoot
    }

    // Auto-export eTrex activities to intervals.icu when the export scope opts in (etrex/all).
    // Fires whenever GarminService finishes a device scan; exportActivitiesToIntervals() dedups
    // by a stored per-activity key, so re-scans don't re-upload. (Watch moves auto-export from
    // ActivityService itself; this covers the eTrex half.) André, 2026-08-24.
    // Garmin Connect login can happen on the Weight page or in Settings; either way, reflect it
    // in ConnectionsService so the Settings connection status + toggles update app-wide.
    Connections {
        target: WeightService
        function onGarminLoggedIn(email) { ConnectionsService.setGarminConnected(true, email) }
    }

    Connections {
        target: GarminService
        function onActivitiesChanged() {
            var scope = ConnectionsService.exportScope
            if ((scope === "etrex" || scope === "all")
                    && GarminService.activities.length > 0)
                ActivityService.exportActivitiesToIntervals(GarminService.activities)
        }
    }

    // Recalculate the watch's activity class from the athlete's latest intervals.icu training
    // on every connect/sync (André, 2026-08-18: "recalculate activity level on each sync usb
    // and bluetooth"). Fires once on the false->true deviceInfoOk transition, only when
    // intervals.icu is connected; the backend recomputes the 4-week class and writes
    // Personal.ActivityLevel ONLY if it changed (idempotent), over whichever transport is
    // live (USB or BLE). Fire-and-forget - a background refresh, not a user action.
    property bool _wasConnectedForClass: false
    Connections {
        target: DeviceService
        function onDeviceInfoChanged() {
            var nowConnected = DeviceService.deviceInfoOk
            if (nowConnected && !window._wasConnectedForClass
                    && ConnectionsService.intervalsIcuConnected) {
                // The two watch-WRITES (profile stats + activity level), done once per connect -
                // not on the periodic timer below, since re-writing the watch every few minutes
                // is pointless. This is the "watch settings sync with intervals.icu, automatic"
                // André asked for (2026-08-25): it now happens on every connect instead of only
                // when he pressed Settings → Sync now.
                if (ConnectionsService.syncActivityLevel)
                    window.intervalsPostBg("/api/intervals/activity-level")
                if (ConnectionsService.syncStatsToWatch)
                    window.intervalsPostBg("/api/intervals/stats-to-watch")
            }
            window._wasConnectedForClass = nowConnected
        }
    }

    // Automatic intervals.icu sync (André, 2026-08-25: "make the sync automatic"). Runs the same
    // CLOUD operations as Settings → Sync now (import gear/activities, export activities), on a
    // timer while the app is open, so he never has to press it. Interval is 15 minutes, NOT the
    // 1 minute he floated: intervals.icu is a cloud API and activity/gear data changes slowly, so
    // minute polling would be wasteful and risks rate-limiting for no benefit (the NAS stack
    // already runs a 15-minute cadence). Watch-writes are handled on connect above, not here.
    // Respects every per-item toggle, so anything switched off in Settings stays off.
    function intervalsPostBg(path) {
        var xhr = new XMLHttpRequest()
        xhr.open("POST", "http://127.0.0.1:8766" + path)
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({ athlete_id: ConnectionsService.intervalsIcuAthleteId,
                                  api_key: ConnectionsService.intervalsIcuApiKey(), confirm: true }))
    }
    function autoIntervalsCloudSync() {
        if (!ConnectionsService.intervalsIcuConnected) return
        if (ConnectionsService.syncImportGear) GearService.importFromIntervals()
        if (ConnectionsService.syncImportActivities)
            ActivityService.importFromIntervals(ConnectionsService.syncImportDays)
        var scope = ConnectionsService.exportScope
        if (scope === "suunto" || scope === "all") ActivityService.exportToIntervals()
        if (scope === "etrex" || scope === "all")
            ActivityService.exportActivitiesToIntervals(GarminService.activities)
    }
    Timer {   // periodic cloud sync
        interval: 15 * 60 * 1000; running: true; repeat: true
        onTriggered: window.autoIntervalsCloudSync()
    }
    Timer {   // one sync shortly after launch, once the backend is up
        interval: 20000; running: true; repeat: false
        onTriggered: window.autoIntervalsCloudSync()
    }

    Row {
        anchors.fill: parent

        NavRail {
            id: navRail
            height: parent.height
            width: window.navExpanded ? 220 : 0
            Behavior on width { NumberAnimation { duration: 200; easing.type: Easing.OutCubic } }
            currentPage: "home"
            onPageSelected: (pageId) => currentPage = pageId
            onCollapseRequested: window.navExpanded = false

            // Pages navigating on their own (Home's Last Activity -> Activities, This
            // year -> Totals) - see NavBus.qml's own header for why a bus and not a
            // threaded callback.
            Connections {
                target: NavBus
                function onNavigate(pageId) {
                    if (pageId in window.pageSources)
                        navRail.currentPage = pageId
                }
            }
        }

        // 2026-08-25 tune-up: the content region now sits on `surface`, one step up from the
        // window `background` the NavRail keeps. This is what lets a page's cards read as
        // resting ON something instead of floating on the same flat colour as the sidebar -
        // the separation the light theme was missing. Pages are transparent over this.
        Rectangle {
            width: parent.width - navRail.width
            height: parent.height
            color: Theme.surface
            Behavior on color { ColorAnimation { duration: 150; easing.type: Easing.OutCubic } }

            Loader {
                anchors.fill: parent
                source: window.pageSources[navRail.currentPage]
            }
        }
    }

    // Floating ☰ to bring the rail back once it's collapsed. Sits over the content's top-left
    // (where the rail's own ☰ was), on a card chip with a hairline so it reads as a control over
    // whatever page is loaded. Only shown while collapsed; three bars, same as the rail's ☰.
    Rectangle {
        id: floatingMenu
        visible: !window.navExpanded
        z: 100
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.margins: Theme.spacingSmall
        width: 40; height: 40
        radius: Theme.radiusSmall
        color: floatMenuHover.hovered ? Theme.cardNested : Theme.card
        border.width: 1
        border.color: Theme.border
        Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
        HoverHandler { id: floatMenuHover }
        TapHandler { onTapped: window.navExpanded = true }
        Column {
            anchors.centerIn: parent
            spacing: 4
            Repeater {
                model: 3
                Rectangle { width: 18; height: 2; radius: 1; color: Theme.text }
            }
        }
    }
}
