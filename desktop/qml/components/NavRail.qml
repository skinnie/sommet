import QtQuick
import QtQuick.Controls
import AmbitApp

// AMBITAPP_SPEC.md, "Navigation": one flat, ordered list of destinations, each item's `visible`
// gated by device/flags; selection by string id (never index), so items appearing/disappearing
// never shift another item's identity.
//
// History: an audit added scroll + a collapsible ☰ + section grouping (Training/Your watch/
// Advanced). André kept scroll + collapse but asked to DROP the grouping (2026-08-29: "why you
// introduced advanced ... don't introduce stuff without me asking"), so this is a FLAT list again
// in the original order. What survives from the rework:
//   - Home is pinned at the top (never scrolls away); everything below it scrolls in a Flickable.
//   - A ☰ at the top collapses the rail (Main.qml animates its width to 0, floats a ☰ to reopen).
//   - Settings is the last row IN the scroll (no longer a fixed bottom pin), per André 2026-08-29.
Rectangle {
    id: root

    property string currentPage: "home"
    signal pageSelected(string pageId)
    // Fired by the ☰ at the top of the rail; Main.qml collapses the rail and shows a floating ☰.
    signal collapseRequested()

    implicitWidth: 220
    color: Theme.card
    clip: true   // label text clips cleanly while the width animates on collapse

    // --- Rail header: brand + collapse (☰) ----------------------------------------------
    // ☰ is three rounded bars, not a font glyph: the Material Symbols font is subset to only the
    // glyphs already in use (assets/fonts/NOTICE.md), so a "menu" codepoint would need re-subsetting.
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

    // --- Everything below Home scrolls (Settings is just the last row) --------------------
    Flickable {
        id: scroller
        anchors.top: homeItem.bottom
        anchors.bottom: parent.bottom
        anchors.bottomMargin: Theme.spacingSmall
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

            NavItem {
                width: parent.width
                glyph: Icons.activities
                label: qsTr("Activities")
                selected: root.currentPage === "activities"
                onClicked: root.pageSelected("activities")
            }
            NavItem {
                width: parent.width
                // Kailash excluded (a travel/adventure watch with no route-following) - gated on
                // the CAPABILITY, not the model.
                visible: DeviceCapabilities.supportsRoutes
                glyph: Icons.routes
                label: qsTr("Routes")
                selected: root.currentPage === "routes"
                onClicked: root.pageSelected("routes")
            }
            NavItem {
                width: parent.width
                // Offline route planner (André, 2026-08-28): a route-following feature, so gated
                // exactly like Routes (Kailash excluded). Reuses the route glyph - the icon font
                // is a fixed subset (Icons.qml), so no new codepoint without re-subsetting first.
                visible: DeviceCapabilities.supportsRoutes
                glyph: Icons.routes
                label: qsTr("Plan")
                selected: root.currentPage === "planRoute"
                onClicked: root.pageSelected("planRoute")
            }
            NavItem {
                width: parent.width
                visible: DeviceCapabilities.supportsPOIs
                glyph: Icons.pois
                label: qsTr("POIs")
                selected: root.currentPage === "pois"
                onClicked: root.pageSelected("pois")
            }
            // Offline maps (André, 2026-08-30): download any area of the world for use with no
            // signal. Not device-gated — reachable any time, like Totals.
            NavItem {
                width: parent.width
                glyph: Icons.cloudDownload
                label: qsTr("Offline maps")
                selected: root.currentPage === "offlineMaps"
                onClicked: root.pageSelected("offlineMaps")
            }
            // Totals - same activity data, summed; no device support to gate on, shown always.
            NavItem {
                width: parent.width
                glyph: Icons.sportModes
                label: qsTr("Totals")
                selected: root.currentPage === "totals"
                onClicked: root.pageSelected("totals")
            }
            // Coach - readiness + chat over local history. On by default since 2026-08-28.
            NavItem {
                width: parent.width
                visible: DeviceService.coachEnabled
                glyph: Icons.coach
                label: qsTr("Coach")
                selected: root.currentPage === "coach"
                onClicked: root.pageSelected("coach")
            }
            NavItem {
                width: parent.width
                glyph: Icons.calendar
                label: qsTr("Calendar")
                selected: root.currentPage === "calendar"
                onClicked: root.pageSelected("calendar")
            }
            NavItem {
                width: parent.width
                glyph: Icons.gear
                label: qsTr("Gear")
                selected: root.currentPage === "gear"
                onClicked: root.pageSelected("gear")
            }
            NavItem {
                width: parent.width
                glyph: Icons.weight
                label: qsTr("Weight")
                selected: root.currentPage === "weight"
                onClicked: root.pageSelected("weight")
            }
            NavItem {
                width: parent.width
                glyph: Icons.health
                label: qsTr("Health")
                selected: root.currentPage === "health"
                onClicked: root.pageSelected("health")
            }
            // Ember - off in the sidebar by default; the toggle + install link live in Settings.
            NavItem {
                width: parent.width
                visible: Theme.emberEnabled
                glyph: Icons.ember
                label: qsTr("Ember")
                selected: root.currentPage === "ember"
                onClicked: root.pageSelected("ember")
            }
            NavItem {
                width: parent.width
                // Nothing to back up, or restore to, without a device.
                visible: HomeViewModel.anyDevice
                glyph: Icons.backup
                label: qsTr("Backup")
                selected: root.currentPage === "backup"
                onClicked: root.pageSelected("backup")
            }
            // Apps - App-Zone builders (Workout + free-form). Suunto Ambit3/Traverse only: no
            // Garmin equivalent, no Kailash CustomModes region, and Ambit1/2 predate the App Zone.
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
            // Training Program - ON HOLD behind FeatureFlags.trainingProgram (default false).
            NavItem {
                width: parent.width
                visible: FeatureFlags.trainingProgram && HomeViewModel.anyDevice
                         && !HomeViewModel.isGarmin && !HomeViewModel.isKailash
                glyph: Icons.trainingProgram
                label: qsTr("Training Program")
                selected: root.currentPage === "trainingProgram"
                onClicked: root.pageSelected("trainingProgram")
            }
            // Sport Modes - Kailash (no CustomModes region) and Garmin (no sport-mode concept) excluded.
            NavItem {
                width: parent.width
                visible: FeatureFlags.sportModes && HomeViewModel.anyDevice
                         && !HomeViewModel.isKailash && !HomeViewModel.isGarmin
                glyph: Icons.sportModes
                label: qsTr("Sport Modes")
                selected: root.currentPage === "sportModes"
                onClicked: root.pageSelected("sportModes")
            }
            // Watch settings - cable-written on-watch settings. Suunto-only, needs a connected watch.
            NavItem {
                width: parent.width
                visible: HomeViewModel.anyDevice && !HomeViewModel.isGarmin
                glyph: Icons.watch
                label: qsTr("Watch settings")
                selected: root.currentPage === "watchSettings"
                onClicked: root.pageSelected("watchSettings")
            }
            // Firmware - Suunto-only, cable-only (hidden over BLE: a flash write is the one mistake
            // that can brick the watch, and flashing was never ported to BLE).
            NavItem {
                width: parent.width
                visible: HomeViewModel.anyDevice && !HomeViewModel.isGarmin
                         && !DeviceService.bleHandshakeDone
                glyph: Icons.sync
                label: qsTr("Firmware")
                selected: root.currentPage === "firmware"
                onClicked: root.pageSelected("firmware")
            }
            // Suunto Smart Sensor - standalone BLE HR belt, independent of any watch.
            NavItem {
                width: parent.width
                visible: DeviceService.smartSensorEnabled
                glyph: Icons.activities
                label: qsTr("Smart Sensor")
                selected: root.currentPage === "smartSensor"
                onClicked: root.pageSelected("smartSensor")
            }
            // GPS Track Pod - standalone Suunto GPS logger; built blind, Settings-gated, off by default.
            NavItem {
                width: parent.width
                visible: DeviceService.gpsTrackPodExperimentEnabled
                glyph: Icons.sync
                label: qsTr("GPS Track Pod")
                selected: root.currentPage === "gpsTrackPod"
                onClicked: root.pageSelected("gpsTrackPod")
            }
            // Suunto T6 - older HR training computer (no GPS); built blind, Settings-gated, off by default.
            NavItem {
                width: parent.width
                visible: DeviceService.suuntoT6ExperimentEnabled
                glyph: Icons.activities
                label: qsTr("T6/X6")
                selected: root.currentPage === "suuntoT6"
                onClicked: root.pageSelected("suuntoT6")
            }
            // Settings - last row in the scroll (2026-08-29: André moved it out of the fixed pin
            // into the scroll "as everything"). Always shown.
            NavItem {
                width: parent.width
                glyph: Icons.settings
                label: qsTr("Settings")
                selected: root.currentPage === "settings"
                onClicked: root.pageSelected("settings")
            }
        }
    }
}
