import QtQuick
import QtQuick.Controls
import QtCore
import SommetUi

// Step 3: the real navigation shell. Home/Activities/Routes/POIs/Backup/Settings pages are
// still placeholders (their own steps fill them in) - this is about the shell itself:
// selection, layout, and Sport Modes staying hidden until FeatureFlags.sportModes flips.
ApplicationWindow {
    id: window
    // Opens maximized the first time, then the way it was left: maximized, or the size it had
    // (2026-11-13). Shown from Component.onCompleted, so it doesn't flash at 1200x800 first.
    // Never bigger than the screen, so it fits even where the window manager ignores the
    // maximize request.
    width: Math.min(winState.width, Screen.desktopAvailableWidth || winState.width)
    height: Math.min(winState.height, Screen.desktopAvailableHeight || winState.height)
    property QtObject winStateObj: Settings {
        id: winState
        category: "window"
        property bool maximized: true
        property int width: 1200
        property int height: 800
    }
    Component.onCompleted: winState.maximized ? showMaximized() : show()
    onClosing: {
        const big = visibility === Window.Maximized || visibility === Window.FullScreen
        winState.maximized = big
        if (!big) { winState.width = width; winState.height = height }
    }
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

    // UI zoom (André, 2026-11-07, X230 720p screen: wanted a Chrome-style "Ctrl+scroll to zoom
    // out" so more fits without scrolling). Theme.uiScale re-lays-out the whole UI crisply; these
    // bind the usual Chrome keys. Ctrl+= and Ctrl++ both zoom in (= is the unshifted + key), and
    // the keypad variants are covered too.
    Shortcut { sequences: ["Ctrl+=", "Ctrl++", "Ctrl+Plus"]; onActivated: Theme.zoomIn() }
    Shortcut { sequences: ["Ctrl+-", "Ctrl+Minus"]; onActivated: Theme.zoomOut() }
    Shortcut { sequences: ["Ctrl+0"]; onActivated: Theme.zoomReset() }
    // Ctrl+wheel and touchpad-pinch zoom, on an item above every page (z) so a scrolling page
    // can't take the event first; it holds no MouseArea, so clicks and plain scrolling go
    // through (2026-11-12). One 5 % step per full wheel notch (120): a touchpad sends many
    // small deltas per gesture, which are added up instead of each counting as a step (the
    // X230's touchpad jumped straight to 70 % or 140 %).
    Item {
        anchors.fill: parent
        z: 100000
        property real wheelAcc: 0
        WheelHandler {
            acceptedModifiers: Qt.ControlModifier
            target: null
            onWheel: function (event) {
                const p = parent
                if ((p.wheelAcc > 0) !== (event.angleDelta.y > 0))
                    p.wheelAcc = 0                  // changed direction: start over
                p.wheelAcc += event.angleDelta.y
                while (p.wheelAcc >= 120) { Theme.zoomIn(); p.wheelAcc -= 120 }
                while (p.wheelAcc <= -120) { Theme.zoomOut(); p.wheelAcc += 120 }
                event.accepted = true
            }
        }
        // Two-finger pinch on a touchpad (only reaches Qt under Wayland; on X11 the keys and
        // Ctrl+wheel do it). Touchpad only: a touchscreen pinch stays with the maps and charts.
        PinchHandler {
            target: null
            acceptedDevices: PointerDevice.TouchPad
            property real startScale: 1.0
            onActiveChanged: if (active) startScale = Theme.uiScale
            onActiveScaleChanged: {
                if (!active) return
                const s = Math.round(startScale * activeScale * 20) / 20     // 5 % steps
                if (Math.abs(s - Theme.uiScale) > 0.001)
                    Theme.setZoom(s)
            }
        }
    }

    readonly property var pageSources: ({
        home: "pages/HomePage.qml",
        activities: "pages/ActivitiesPage.qml",
        routes: "pages/PlanRoutePage.qml",      // merged into Routes (2026-09-26)
        planRoute: "pages/PlanRoutePage.qml",
        racePlan: "pages/RacePlanPage.qml",
        pois: "pages/PoisPage.qml",
        offlineMaps: "pages/OfflineMapsPage.qml",
        backup: "pages/BackupPage.qml",
        sync: "pages/SyncPage.qml",
        firmware: "pages/FirmwarePage.qml",
        watchSettings: "pages/WatchSettingsPage.qml",
        gpsSettings: "pages/GpsSettingsPage.qml",
        edgeScreens: "pages/EdgeScreensPage.qml",
        ciqUpdates: "pages/CiqUpdatesPage.qml",
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

    // Optional local-only extension pages (AppExtensions is empty in the public build; see
    // desktop/src/appextensions.h). These resolve a page id to its QML source / existence,
    // preferring an extension when one claims the id, otherwise the built-in map above.
    function pageSourceFor(id) {
        for (var i = 0; i < AppExtensions.pages.length; i++)
            if (AppExtensions.pages[i].id === id) return AppExtensions.pages[i].source
        return pageSources[id]
    }
    function hasPage(id) {
        for (var i = 0; i < AppExtensions.pages.length; i++)
            if (AppExtensions.pages[i].id === id) return true
        return id in pageSources
    }

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
    // Testing mode's sample activities are listed only while Testing mode is on.
    Binding {
        target: ActivityService
        property: "showDemo"
        value: DeviceService.demoMode
    }
    // An eTrex picked in Home's switcher stops being the pick once it's unplugged, so plugging
    // it back in later doesn't silently take over from the watch.
    Connections {
        target: GarminService
        function onDeviceChanged() { if (!GarminService.connected) HomeViewModel.garminPicked = false }
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
            // The training plan, once per plug-in (2026-10-09): past workouts off, next ones on,
            // the training day re-pointed. See TrainingPlanSync for why it cannot wait for a
            // button. Independent of intervals.icu - the plan is local.
            if (nowConnected && !window._wasConnectedForClass)
                TrainingPlanSync.watchConnected()
            else if (!nowConnected && window._wasConnectedForClass)
                TrainingPlanSync.watchDisconnected()
            window._wasConnectedForClass = nowConnected
        }
    }

    // The watch asks to sync over Bluetooth ("Sync now" on the watch, or its own check every
    // 30 min while linked): run the same sync as the Sync button, as the tablet does (🟣38).
    // Unanswered, the watch hangs up about a minute later. Not within a minute of the last one
    // (it asks several times in a row); while another watch job runs, the request waits for it,
    // up to watchSyncWait's limit, since the watch would be gone by then.
    property double _lastWatchSyncAt: 0
    function _watchJobRunning() {
        return ActivityService.loading || RouteService.loading || PoiService.loading
            || SettingsWriteService.loading || CustomModesService.loading
            || TrainingProgramService.loading || TrainingProgramService.installing
            || TrainingPlanSync.pending || KailashService.loading
    }
    function _runWatchSync() {
        if (window._watchJobRunning()) return false
        watchSyncWait.stop()
        window._lastWatchSyncAt = Date.now()
        console.info("[ble] the watch asked to sync")
        ActivityService.refresh()
        return true
    }
    Connections {
        target: DeviceService
        function onBleSyncRequested() {
            if (Date.now() - window._lastWatchSyncAt < 60000 || watchSyncWait.running) return
            if (!window._runWatchSync()) {
                console.info("[ble] the watch asked to sync; waiting for another watch job")
                watchSyncWait.tries = 0
                watchSyncWait.start()
            }
        }
    }
    Timer {
        id: watchSyncWait
        property int tries: 0
        interval: 2000
        repeat: true
        onTriggered: {
            if (window._runWatchSync()) return
            if (++tries >= 20) {       // 40 s
                stop()
                console.info("[ble] the watch's sync request lapsed: another watch job ran too long")
            }
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
        onTriggered: { window.autoIntervalsCloudSync(); ActivityService.sommetSyncNow(); GearService.sommetGearSyncNow() }
    }
    Timer {   // one sync shortly after launch, once the backend is up
        interval: 20000; running: true; repeat: false
        onTriggered: { window.autoIntervalsCloudSync(); ActivityService.sommetSyncNow(); GearService.sommetGearSyncNow() }
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
                    if (window.hasPage(pageId))
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

            // SuuntoLink warning on app open (André, 2026-09-28): only when it is installed
            // (Mac/Windows), closable for the session; pushes the page down, never covers it.
            SuuntoLinkWarning {
                id: suuntoLinkBanner
                context: "app"
                anchors.top: parent.top
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.topMargin: Theme.spacingSmall
                anchors.leftMargin: window.navExpanded ? Theme.spacingMedium : 56   // clear of the floating ☰
                anchors.rightMargin: Theme.spacingMedium
            }

            Loader {
                anchors.top: suuntoLinkBanner.visible ? suuntoLinkBanner.bottom : parent.top
                anchors.topMargin: suuntoLinkBanner.visible ? Theme.spacingSmall : 0
                anchors.left: parent.left
                anchors.right: parent.right
                anchors.bottom: parent.bottom
                source: window.pageSourceFor(navRail.currentPage)
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
