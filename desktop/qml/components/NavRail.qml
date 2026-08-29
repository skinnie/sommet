import QtQuick
import QtQuick.Controls
import AmbitApp

// AMBITAPP_SPEC.md, "Navigation". Reworked 2026-08-28 (UX audit, items 1+4): the rail had
// grown to ~21 flat, equal-weight entries with no scrolling - a plain top-anchored Column -
// so on 720p / tablet-portrait the bottom items (Settings included) fell off-screen with no
// way to reach them. Two fixes, both here:
//   1. Home is pinned at the top and Settings at the bottom, OUTSIDE the scroll area, so the
//      two anchors of the app can never scroll away. Everything between them lives in a
//      Flickable that scrolls when the list is taller than the rail.
//   2. The middle is grouped under three section headers - Training / Your watch / Advanced -
//      each of which hides itself when every item under it is hidden, so an empty group leaves
//      no orphan header. Selection is still by string id, not index, so grouping/reordering
//      (and items appearing/disappearing as flags flip) never shifts any item's own identity.
Rectangle {
    id: root

    property string currentPage: "home"
    signal pageSelected(string pageId)
    // 2026-08-29 (André): the rail is collapsible now. This fires when the user taps the ☰ at
    // the top of the rail; Main.qml handles it by animating the rail's width to 0 and showing a
    // floating ☰ over the content to bring it back. Same "hide the sidebar to reclaim the width"
    // idea as the Android drawer, kept as a docked collapse on the desktop's roomy layout.
    signal collapseRequested()

    implicitWidth: 220
    color: Theme.card
    clip: true   // so the label text clips cleanly instead of overflowing while the width animates

    // --- Rail header: brand + collapse (☰) ----------------------------------------------
    // The ☰ is drawn as three rounded bars rather than a font glyph: the Material Symbols font
    // is subset to only the glyphs already in use (see assets/fonts/NOTICE.md), and adding a
    // "menu" codepoint would need the font re-subsetted. Three Rectangles need no font at all.
    Item {
        id: railHeader
        anchors.top: parent.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Theme.spacingSmall
        anchors.bottomMargin: 0
        height: 40

        Rectangle {
            id: collapseBtn
            width: 36; height: 36
            radius: Theme.radiusSmall
            anchors.left: parent.left
            anchors.verticalCenter: parent.verticalCenter
            color: collapseHover.hovered ? Theme.background : "transparent"
            Behavior on color { ColorAnimation { duration: 120; easing.type: Easing.OutCubic } }
            HoverHandler { id: collapseHover }
            TapHandler { onTapped: root.collapseRequested() }
            Column {
                anchors.centerIn: parent
                spacing: 4
                Repeater {
                    model: 3
                    Rectangle { width: 18; height: 2; radius: 1; color: Theme.text }
                }
            }
        }
        Text {
            anchors.left: collapseBtn.right
            anchors.leftMargin: Theme.spacingSmall
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("Sommet")
            color: Theme.text
            font.pixelSize: Theme.fontSizeBodyLarge
            font.bold: true
        }
    }

    // --- Section-header visibility -------------------------------------------------------
    // Each header shows only when at least one of its items would show. "Training" items are
    // all always-on (Activities/Totals/Calendar/Gear/Weight/Health), so its header is always
    // shown. "Your watch" is exactly "a device is connected". "Advanced" is the OR of the
    // experimental/flagged toggles that surface its items.
    // Routes/POIs are gated on the CAPABILITY (supportsRoutes/POIs, true for everything but a
    // Kailash - so true even with no watch, matching the pre-grouping behaviour), while
    // Sport Modes / Watch settings / Backup / Firmware need a connected device. The header must
    // show when ANY of them shows, so it's the union: the two capabilities OR anyDevice (the
    // loosest of the device-gated items - all the others imply it).
    readonly property bool watchGroupVisible:
        DeviceCapabilities.supportsRoutes || DeviceCapabilities.supportsPOIs
        || HomeViewModel.anyDevice
    readonly property bool advancedGroupVisible:
        ((DeviceService.appZoneEnabled || DeviceService.intervalsEnabled)
            && HomeViewModel.anyDevice && !HomeViewModel.isGarmin && !HomeViewModel.isKailash
            && DeviceCapabilities.supportsApps)
        || (FeatureFlags.trainingProgram && HomeViewModel.anyDevice
            && !HomeViewModel.isGarmin && !HomeViewModel.isKailash)
        || DeviceService.smartSensorEnabled
        || DeviceService.gpsTrackPodExperimentEnabled
        || DeviceService.suuntoT6ExperimentEnabled

    // A muted, uppercase group label. Collapses to zero height (and a Column skips it) when
    // `shown` is false, so a hidden group leaves no gap.
    component SectionHeader: Item {
        id: sh
        property string title
        property bool shown: true
        width: parent ? parent.width : 0
        visible: shown
        height: shown ? 34 : 0
        Text {
            anchors.left: parent.left
            anchors.leftMargin: Theme.spacingMedium
            anchors.bottom: parent.bottom
            anchors.bottomMargin: 6
            text: sh.title.toUpperCase()
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
            font.bold: true
        }
    }

    // --- Home: pinned at the top ---------------------------------------------------------
    NavItem {
        id: homeItem
        anchors.top: railHeader.bottom
        anchors.topMargin: Theme.spacingSmall
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.spacingSmall
        anchors.rightMargin: Theme.spacingSmall
        glyph: Icons.home
        label: qsTr("Home")
        selected: root.currentPage === "home"
        onClicked: root.pageSelected("home")
    }

    // --- Settings: pinned at the bottom --------------------------------------------------
    // Real, 2026-08-09 ("settings at the bottom") - kept, but now literally pinned so it is
    // always reachable no matter how many items or how short the window.
    NavItem {
        id: settingsItem
        anchors.bottom: parent.bottom
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.margins: Theme.spacingSmall
        anchors.topMargin: 0
        glyph: Icons.settings
        label: qsTr("Settings")
        selected: root.currentPage === "settings"
        onClicked: root.pageSelected("settings")
    }

    // --- Everything else: scrolls between Home and Settings ------------------------------
    Flickable {
        id: scroller
        anchors.top: homeItem.bottom
        anchors.bottom: settingsItem.top
        anchors.left: parent.left
        anchors.right: parent.right
        anchors.leftMargin: Theme.spacingSmall
        anchors.rightMargin: Theme.spacingSmall
        clip: true
        contentWidth: width
        contentHeight: column.height
        boundsBehavior: Flickable.StopAtBounds
        ScrollBar.vertical: ScrollBar { policy: ScrollBar.AsNeeded }

        Column {
            id: column
            width: scroller.width
            spacing: 2

            // ===== Training - reads your own history / the cloud, no watch needed ==========
            SectionHeader { title: qsTr("Training") }

            NavItem {
                width: parent.width
                glyph: Icons.activities
                label: qsTr("Activities")
                selected: root.currentPage === "activities"
                onClicked: root.pageSelected("activities")
            }
            // Totals - André, 2026-08-11. Same data as Activities, summed; no device support to
            // gate on, so it shows for every device including none, where it explains itself.
            NavItem {
                width: parent.width
                glyph: Icons.sportModes
                label: qsTr("Totals")
                selected: root.currentPage === "totals"
                onClicked: root.pageSelected("totals")
            }
            // Coach (v2, 2026-08-21): readiness beacon + chat over local activity history.
            // Un-hidden by default 2026-08-28 (UX audit item 3) - it was gated behind an
            // experimental toggle almost nobody found; it works offline for the basics, so it
            // now shows for everyone (the Settings toggle still lets you hide it).
            NavItem {
                width: parent.width
                visible: DeviceService.coachEnabled
                glyph: Icons.coach
                label: qsTr("Coach")
                selected: root.currentPage === "coach"
                onClicked: root.pageSelected("coach")
            }
            // Calendar - real request, 2026-08-11. Same device-aware activity list; no device
            // support to gate on, shows for every device including none.
            NavItem {
                width: parent.width
                glyph: Icons.calendar
                label: qsTr("Calendar")
                selected: root.currentPage === "calendar"
                onClicked: root.pageSelected("calendar")
            }
            // Gear tracker (v3, 2026-08-18): bikes/shoes + components + service reminders,
            // imported from intervals.icu and owned locally. About your gear, not the watch.
            NavItem {
                width: parent.width
                glyph: Icons.gear
                label: qsTr("Gear")
                selected: root.currentPage === "gear"
                onClicked: root.pageSelected("gear")
            }
            // Weight (André, 2026-08-24): body-weight history. About your body, not the watch.
            NavItem {
                width: parent.width
                glyph: Icons.weight
                label: qsTr("Weight")
                selected: root.currentPage === "weight"
                onClicked: root.pageSelected("weight")
            }
            // Health (André, 2026-08-24): daily resting HR + steps.
            NavItem {
                width: parent.width
                glyph: Icons.health
                label: qsTr("Health")
                selected: root.currentPage === "health"
                onClicked: root.pageSelected("health")
            }
            // Ember (André, 2026-08-25): fasting + calorie / coffee / water tracker; logs sync
            // from the phone app. Off in the sidebar by DEFAULT (Theme.emberEnabled) - it's an
            // experimental personal companion, so it stays opt-in; the toggle + phone-install
            // link now live openly in Settings (the 10-tap easter egg was retired 2026-08-28).
            // Grouped with the other body trackers.
            NavItem {
                width: parent.width
                visible: Theme.emberEnabled
                glyph: Icons.ember
                label: qsTr("Ember")
                selected: root.currentPage === "ember"
                onClicked: root.pageSelected("ember")
            }

            // ===== Your watch - needs the connected device ================================
            SectionHeader { title: qsTr("Your watch"); shown: root.watchGroupVisible }

            NavItem {
                width: parent.width
                // Kailash excluded, real 2026-08-09 - a GPS travel/adventure watch with no
                // route-following feature. Gated on the CAPABILITY, not the model.
                visible: DeviceCapabilities.supportsRoutes
                glyph: Icons.routes
                label: qsTr("Routes")
                selected: root.currentPage === "routes"
                onClicked: root.pageSelected("routes")
            }
            NavItem {
                width: parent.width
                // Kailash excluded, real 2026-08-09, same reasoning as Routes above.
                visible: DeviceCapabilities.supportsPOIs
                glyph: Icons.pois
                label: qsTr("POIs")
                selected: root.currentPage === "pois"
                onClicked: root.pageSelected("pois")
            }
            NavItem {
                width: parent.width
                // Kailash excluded (no CustomModes region) and Garmin excluded (no on-watch
                // sport-mode concept) - real, 2026-08-08 / 2026-08-11.
                visible: FeatureFlags.sportModes && HomeViewModel.anyDevice
                         && !HomeViewModel.isKailash && !HomeViewModel.isGarmin
                glyph: Icons.sportModes
                label: qsTr("Sport Modes")
                selected: root.currentPage === "sportModes"
                onClicked: root.pageSelected("sportModes")
            }
            // Watch settings (2026-08-14) - cable-written on-watch settings. Suunto-only and
            // needs a connected watch to read/write.
            NavItem {
                width: parent.width
                visible: HomeViewModel.anyDevice && !HomeViewModel.isGarmin
                glyph: Icons.watch
                label: qsTr("Watch settings")
                selected: root.currentPage === "watchSettings"
                onClicked: root.pageSelected("watchSettings")
            }
            NavItem {
                width: parent.width
                // Nothing to back up, and nothing to restore to, without a device.
                visible: HomeViewModel.anyDevice
                glyph: Icons.backup
                label: qsTr("Backup")
                selected: root.currentPage === "backup"
                onClicked: root.pageSelected("backup")
            }
            // Firmware update / recovery (2026-08-12) - Suunto-only, cable-only (hidden over
            // BLE, see ambit_app_never_touch_firmware): a real flash write is the one mistake
            // here that can brick the watch, and flashing was never ported to BLE.
            NavItem {
                width: parent.width
                visible: HomeViewModel.anyDevice && !HomeViewModel.isGarmin
                         && !DeviceService.bleHandshakeDone
                glyph: Icons.sync
                label: qsTr("Firmware")
                selected: root.currentPage === "firmware"
                onClicked: root.pageSelected("firmware")
            }

            // ===== Advanced - App Zone builders + experimental / blind-built hardware ======
            SectionHeader { title: qsTr("Advanced"); shown: root.advancedGroupVisible }

            // Suunto Apps - ONE entry (2026-08-23): the page offers both the Interval Workout
            // Builder and the free-form App Builder. App-Zone mechanisms - no Garmin equivalent,
            // no Kailash CustomModes region, and Ambit1/2 predate the SBEM App Zone.
            NavItem {
                width: parent.width
                visible: (DeviceService.appZoneEnabled || DeviceService.intervalsEnabled)
                         && HomeViewModel.anyDevice
                         && !HomeViewModel.isGarmin && !HomeViewModel.isKailash
                         && DeviceCapabilities.supportsApps
                glyph: Icons.apps
                label: qsTr("Apps")
                selected: root.currentPage === "appZone"
                onClicked: root.pageSelected("appZone")
            }
            // Training Program (2026-08-12) - scheduled, date-gated workouts. ON HOLD, hidden
            // behind FeatureFlags.trainingProgram (default false). Same App-Zone/CustomModes
            // install mechanism, so same Suunto-only gating as Apps.
            NavItem {
                width: parent.width
                visible: FeatureFlags.trainingProgram && HomeViewModel.anyDevice
                         && !HomeViewModel.isGarmin && !HomeViewModel.isKailash
                glyph: Icons.trainingProgram
                label: qsTr("Training Program")
                selected: root.currentPage === "trainingProgram"
                onClicked: root.pageSelected("trainingProgram")
            }
            // Suunto Smart Sensor (2026-08-14) - standalone BLE HR belt, independent of any
            // watch, shown whenever its own experimental toggle is on.
            NavItem {
                width: parent.width
                visible: DeviceService.smartSensorEnabled
                glyph: Icons.activities
                label: qsTr("Smart Sensor")
                selected: root.currentPage === "smartSensor"
                onClicked: root.pageSelected("smartSensor")
            }
            // GPS Track Pod (2026-08-12) - a separate standalone Suunto GPS logger, not a
            // watch, so not gated on anyDevice. Built blind, Settings-gated, off by default.
            NavItem {
                width: parent.width
                visible: DeviceService.gpsTrackPodExperimentEnabled
                glyph: Icons.sync
                label: qsTr("GPS Track Pod")
                selected: root.currentPage === "gpsTrackPod"
                onClicked: root.pageSelected("gpsTrackPod")
            }
            // Suunto T6 (2026-08-14) - older HR training computer (no GPS). Built blind,
            // Settings-gated, off by default. Same treatment as the GPS Track Pod above.
            NavItem {
                width: parent.width
                visible: DeviceService.suuntoT6ExperimentEnabled
                glyph: Icons.activities
                label: qsTr("T6/X6")
                selected: root.currentPage === "suuntoT6"
                onClicked: root.pageSelected("suuntoT6")
            }
        }
    }
}
