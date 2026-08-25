import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AmbitApp

// Step 4: real device-hero layout. Step 5 adds real weather. Last Activity made real
// 2026-08-07 once ActivityService actually worked - see its own card comment below.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    Component.onCompleted: {
        // Which watch we are looking at - a real one, or the sample Testing mode serves.
        DeviceService.refreshDemoMode();
        DeviceService.refresh();
        // Which watches are on the USB bus, for the multi-watch picker above the device card.
        DeviceService.refreshDevices();
        // Real, 2026-08-08: Garmin detection is a cheap filesystem check (QStorageInfo +
        // one small XML file), not a USB/subprocess round trip like DeviceService's own -
        // safe to run on every Home load alongside it, not gated behind Ambit failing first.
        GarminService.detect();
        // IP-based location by default, real request 2026-08-07 (was a hardcoded central-
        // Europe default before) - detectLocationFromIp() calls refresh() itself once it has
        // real coordinates, or falls back to refresh()-with-whatever-it-had if the IP lookup
        // itself fails, so this always ends in a real fetch attempt either way.
        WeatherService.detectLocationFromIp();
        ActivityService.refresh();
        DeviceService.checkGpsOrbitStatus();
    }

    // Keep the multi-watch picker current: the heartbeat re-reads /api/device every ~10s, so
    // piggyback a cheap re-enumerate on it - plugging or unplugging a second watch while the
    // Home page is open then updates the picker without needing to leave and come back.
    Connections {
        target: DeviceService
        function onDeviceInfoChanged() { DeviceService.refreshDevices(); }
    }

    // Real, 2026-08-08 ("Yes I want to implement it both to desktop and android version").
    // isKailash only becomes true once DeviceService.refresh() (fired above) has actually
    // identified the connected watch, so this can't just be another Component.onCompleted
    // call the way ActivityService.refresh() is - it has to react to isKailash itself
    // becoming true, including on a later reconnect after the page was already loaded.
    // Real, 2026-08-09 ("Implement home city name" not showing up promptly) - the backend
    // serializes every real watch request through one lock (server.py's own WATCH_LOCK),
    // and refreshTrackLog() is a real ~1.3MB flash read (its own doc comment already calls
    // out as slow) - firing it alongside the fast history/settings requests risked queuing
    // the city-name lookup's own settings fetch behind it for a long time, not broken, just
    // stuck waiting. Fast requests (history, settings) now go first; the slow TrackLog read
    // goes last so it can't block anything else from completing promptly.
    Connections {
        target: HomeViewModel
        function onIsKailashChanged() {
            if (HomeViewModel.isKailash) {
                KailashService.refreshHistory();
                // The watch's real HomeLocation setting lives in the generic Settings
                // mechanism (same one SettingsPage.qml's own coord editor uses), not a
                // Kailash-specific endpoint - fetched here too so Home's own Home-location
                // card (below) has real coordinates without the user having to visit
                // Settings first. onSettingsChanged below picks the two fields out once
                // this reply lands.
                SettingsWriteService.device = "kailash";
                SettingsWriteService.refresh();
                KailashService.refreshTrackLog();
            }
        }
    }

    // Real, 2026-08-09 ("I believe you put a POI icon for home, name home and identify the
    // city by coordinates"). SettingsWriteService.settings is a flat list of every curated
    // setting for whichever device is connected - picks out home_latitude/home_longitude
    // specifically (see AmbitSettingsReader.ts's own field comment / the
    // ambit_app_kailash_home_location_field memory for why those two exist at all) and
    // hands them to KailashService.refreshHomeLocation() for the actual reverse-geocode.
    Connections {
        target: SettingsWriteService
        function onSettingsChanged() {
            if (!HomeViewModel.isKailash) return;
            let lat = null, lon = null;
            for (const row of SettingsWriteService.settings) {
                if (row.key === "home_latitude") lat = row.value;
                else if (row.key === "home_longitude") lon = row.value;
            }
            if (lat !== null && lon !== null
                && (lat !== KailashService.homeLatitude || lon !== KailashService.homeLongitude)) {
                KailashService.refreshHomeLocation(lat, lon);
            }
        }
    }

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        // 2026-08-11 designer pass (André: "act like a designer, audit this home page").
        // The fixed 480px column left more than half of a 1200px window blank - a hero
        // page on a desktop earns its width. First cut went to 940 and André's verdict
        // was "too noisy"; 800 keeps the two-column row and the breathing room without
        // the sprawl. Floored so a small window still gets the old single-column look.
        width: Math.max(480, Math.min(parent.width - Theme.spacingLarge * 2, 800))
        spacing: Theme.spacingMedium
        // Weather and This-year sit side by side only when both get a card of honest
        // width; below that they stack, and nothing else changes.
        readonly property bool twoColumn: width >= 760

        // Gear maintenance summary — only when something needs attention; clicks through to Gear.
        Rectangle {
            id: gearAlert
            width: parent.width
            visible: GearService.dueCount > 0 || GearService.soonCount > 0
            height: visible ? gearAlertRow.implicitHeight + 2 * Theme.spacingMedium : 0
            radius: Theme.radiusCard
            readonly property bool anyDue: GearService.dueCount > 0
            readonly property color accent: anyDue ? Theme.error : Theme.warning
            color: Qt.rgba(accent.r, accent.g, accent.b, 0.10)
            border.color: accent
            border.width: 1

            RowLayout {
                id: gearAlertRow
                anchors.fill: parent
                anchors.margins: Theme.spacingMedium
                spacing: Theme.spacingSmall
                Text {
                    text: Icons.warningAmber
                    font.family: Icons.fontFamily
                    font.pixelSize: 18
                    color: gearAlert.accent
                }
                Text {
                    Layout.fillWidth: true
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBody
                    text: GearService.dueCount > 0
                          ? qsTr("%n gear service(s) due", "", GearService.dueCount)
                          : qsTr("%n gear service(s) due soon", "", GearService.soonCount)
                }
                Text { text: Icons.chevronRight; font.family: Icons.fontFamily; color: Theme.mutedText }
            }
            MouseArea { anchors.fill: parent; cursorShape: Qt.PointingHandCursor; onClicked: NavBus.navigate("gear") }
        }

        // Testing mode is deliberately loud: a sample watch that looks like a real one is
        // only useful if it can never be mistaken for one.
        Rectangle {
            width: parent.width
            visible: DeviceService.demoMode
            height: visible ? demoText.implicitHeight + Theme.spacingMedium : 0
            radius: Theme.radiusCard
            color: Theme.card
            border.width: 1
            border.color: Theme.primary
            Text {
                id: demoText
                anchors.centerIn: parent
                width: parent.width - Theme.spacingMedium * 2
                wrapMode: Text.WordWrap
                horizontalAlignment: Text.AlignHCenter
                text: qsTr("Testing mode - this is a sample watch, not a real one. " +
                            "Turn it off in Settings.")
                color: Theme.primary
                font.pixelSize: Theme.fontSizeBody
            }
        }

        // --- Device hero card: the watch is the hero, only one, per the spec. Real,
        // 2026-08-08 ("home page: instead of ambit it detects an etrex... firmware version,
        // hwid etc like on the android version") - device-aware: Ambit and Garmin are
        // genuinely different device mechanisms (see GarminService's own header comment),
        // so this shows whichever one HomeViewModel.isGarmin/isAmbit says is actually
        // connected, not a merged view. Battery has no Garmin equivalent here (a mounted
        // mass-storage filesystem doesn't expose it), so it's simply not shown for Garmin,
        // not shown as "Not available yet" - that phrasing is for a real Ambit3 field this
        // project hasn't wired up yet, not a field that doesn't exist for this device type. ---
        Card {
            width: parent.width

            Column {
                width: parent.width
                // Small on purpose, paired with the info grid's Large rowSpacing below -
                // André, 2026-08-11: "do the same vertical space on the 3 columns of text".
                // Measured on screen: the 64px device icon overhangs the header text, so
                // header-to-row-1 read as ~35px while row-1-to-row-2 was ~19px. 8 here plus
                // 24 there makes both text gaps ~27px - equal to the eye, which is what was
                // asked; the token pair is what the theme offers closest to that.
                spacing: Theme.spacingSmall

                Row {
                    width: parent.width
                    spacing: Theme.spacingMedium
                    // Real, 2026-08-13 (André: "when no watch was unplugged it said 'ambit
                    // 3 peak'... so maybe we have to redo that UI"). deviceDisplayName's own
                    // static fallback (see HomeViewModel.qml) meant this card kept showing a
                    // specific model name/icon/status dot for a watch that was never plugged
                    // in - genuinely misleading, not just an aesthetic gap. Hidden entirely
                    // once nothing is connected; the emptyState Column below takes its place.
                    visible: HomeViewModel.connected || HomeViewModel.isGarmin

                    // Standing in for the real Ambit3 Peak Sapphire product photo the spec
                    // asks for (from Suunto's own Android app resources) - not pulled in
                    // yet on purpose: those images are proprietary Suunto assets
                    // (assets/ is gitignored for exactly this reason project-wide), so
                    // using one here needs a real licensing check first, the same care
                    // already applied to not reusing SuuntoLink's own icon for this app's
                    // icon. A real product photo replaces this once that's settled.
                    // Converged onto Theme.cardNested (2026-08-25 coherence pass) - same
                    // recessed-icon-square family as every other flat grey tile now.
                    Rectangle {
                        width: 64
                        height: 64
                        radius: Theme.radiusSmall
                        color: Theme.cardNested
                        Icon {
                            visible: !HomeViewModel.isGarmin
                            anchors.centerIn: parent
                            glyph: Icons.watch
                            size: 32
                        }
                        EtrexIcon {
                            visible: HomeViewModel.isGarmin
                            anchors.centerIn: parent
                            size: 32
                        }
                    }

                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2

                        Text {
                            text: HomeViewModel.isGarmin
                                ? (GarminService.model || qsTr("Garmin eTrex"))
                                : HomeViewModel.deviceDisplayName
                            font.pixelSize: Theme.fontSizeTitle
                            font.bold: true
                            color: Theme.text
                        }
                        Row {
                            spacing: 6
                            Rectangle {
                                width: 8; height: 8; radius: 4
                                anchors.verticalCenter: parent.verticalCenter
                                color: HomeViewModel.isGarmin
                                    ? Theme.success
                                    : HomeViewModel.connectionStatusColor
                            }
                            Text {
                                text: HomeViewModel.isGarmin
                                    ? qsTr("Connected")
                                    : HomeViewModel.connectionStatusText
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeBody
                            }
                        }
                    }
                }

                // --- Empty state, real 2026-08-13 (André, same request as the visible fix
                // above): no fake watch name/icon/data when nothing is actually connected -
                // copy only, no button (the existing Bluetooth-connect row right below
                // already covers that when Experimental Features is on; this text just says
                // where to look). Mirrors anyDevice's own house rule elsewhere on this page
                // ("pages that cannot function without a device are hidden, not shown
                // empty") applied to this card instead of a whole page. ---
                Column {
                    width: parent.width
                    spacing: Theme.spacingMedium
                    visible: !HomeViewModel.connected && !HomeViewModel.isGarmin
                             && !DeviceService.demoMode

                    Row {
                        width: parent.width
                        spacing: Theme.spacingMedium

                        Rectangle {
                            width: 64
                            height: 64
                            radius: Theme.radiusSmall
                            color: Theme.cardNested
                            Icon {
                                anchors.centerIn: parent
                                glyph: Icons.watch
                                size: 32
                                color: Theme.mutedText
                            }
                        }

                        Column {
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Text {
                                text: qsTr("No watch connected")
                                font.pixelSize: Theme.fontSizeTitle
                                font.bold: true
                                color: Theme.text
                            }
                            Text {
                                text: DeviceService.bleExperimentEnabled
                                    ? qsTr("Plug it in via USB, or search for your adventure " +
                                           "buddy over Bluetooth below.")
                                    : qsTr("Plug it in via USB to get started.")
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeBody
                            }
                        }
                    }
                }

                // --- Bluetooth connect (Linux/BlueZ only, and Experimental-only - real
                // decision, 2026-08-11: a live session that same night hit real BlueZ
                // reliability trouble and a route-write bug (both since fixed - HANDOFF.md
                // Milestone 7 items 16-19), so this stays behind Settings' "Experimental
                // Features" toggle (off by default - SettingsPage.qml) rather than being
                // part of the default cable-first Home experience. macOS/Windows BLE were
                // explicitly dropped from scope the same night, not deferred. Shown only
                // while nothing is connected: once a BLE watch subscribes, /api/device
                // answers over BLE transparently and this row's own job is done - the
                // existing "Ambit3 info rows" below just starts showing real data, no
                // separate "connected via BLE" state to maintain here. ---
                Column {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    visible: DeviceService.bleExperimentEnabled
                             && !HomeViewModel.connected && !HomeViewModel.isGarmin
                             && !DeviceService.demoMode

                    Row {
                        width: parent.width
                        spacing: Theme.spacingMedium

                        RoundedButton {
                            // Real request, 2026-08-13 (André, live testing: "it is still
                            // on 'connecting'... maybe we should put a timer no?") - a bare
                            // "Connecting…" gave no way to tell "still genuinely searching"
                            // from "stuck". Ticking count only, no hard cutoff - a fresh
                            // pairing's passkey wait can legitimately run long (see
                            // DeviceService::connectBle()'s own comment), so this never
                            // stops the attempt on its own.
                            text: DeviceService.bleAttempting
                                ? qsTr("Connecting… (%1s)").arg(DeviceService.bleAttemptSeconds)
                                : qsTr("Connect via Bluetooth")
                            enabled: !DeviceService.bleAttempting
                            onClicked: DeviceService.connectBle(false)
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            visible: DeviceService.bleAttempting && !DeviceService.bleSubscribed
                            text: qsTr("Trigger \"Pair Mobile App\" or \"Sync now\" on the " +
                                       "watch now - its window is short")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeLabel
                        }
                    }
                    Text {
                        visible: DeviceService.bleError.length > 0
                        text: DeviceService.bleError
                        color: Theme.error
                        font.pixelSize: Theme.fontSizeLabel
                    }
                }

                // --- Forget (Bluetooth bond), real 2026-08-13 (André, live BLE testing:
                // "we need to add a button to forget the watch"). Deliberately its own row,
                // not folded into the Connect row above: unlike Connect, this is useful
                // WHILE connected too - the same "always Unpair, never Replace" recovery
                // PROJECT_RULES.md already recommends on the watch's own menu, now
                // reachable from the Linux side of the same bond without a terminal. Never
                // shown for Garmin (no such concept) or in Testing mode (nothing real to
                // forget). ---
                Row {
                    width: parent.width
                    spacing: Theme.spacingMedium
                    visible: DeviceService.bleExperimentEnabled && !HomeViewModel.isGarmin
                             && !DeviceService.demoMode

                    RoundedButton {
                        text: qsTr("Forget this watch (Bluetooth)")
                        onClicked: DeviceService.forgetBle()
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Drops the Bluetooth pairing on this computer. Pair " +
                                   "again from the watch's own menu afterward.")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeLabel
                    }
                }

                // --- Ambit3 info grid - real, 2026-08-11 ("move clock up, and put the
                // manual next to hardware", then "reduce spacing between the column of
                // battery and firmware, allowing GPS orbit to stay in a column next to
                // clock"; finally "text disaligned, please align it"). ONE GridLayout for
                // both rows, not the two separate ones an earlier iteration used: separate
                // grids each computed their own column widths, so Battery/Firmware sat at
                // different x than Serial number/Hardware right below them and the card
                // read as scattered. A single grid lines the columns up by construction;
                // the tight Theme.spacingSmall (needed to fit 4 columns) applies to both
                // rows, which costs the second row nothing - it has a column to spare. ---
                GridLayout {
                    width: parent.width
                    // 2026-08-13: hidden entirely while disconnected, alongside the name/
                    // icon row above - same call as that row's own comment.
                    visible: !HomeViewModel.isGarmin && HomeViewModel.connected
                    columns: 4
                    columnSpacing: Theme.spacingSmall
                    // Large, paired with the card Column's Small - see its comment: together
                    // they make the header/row-1/row-2 text gaps visually equal.
                    rowSpacing: Theme.spacingLarge

                    Column {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Battery"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Row {
                            spacing: Theme.spacingSmall
                            Text { text: HomeViewModel.batteryText; color: Theme.text; font.pixelSize: Theme.fontSizeBody }
                            // 2026-08-11 designer pass: a number is read, a gauge is seen.
                            // Green above 30%, amber to 15%, red below - the same instinct
                            // every phone's status bar has already trained.
                            Rectangle {
                                visible: DeviceService.deviceInfoOk && DeviceService.batteryPercent >= 0
                                anchors.verticalCenter: parent.verticalCenter
                                width: 34; height: 8; radius: 4
                                color: Theme.background
                                border.width: 1
                                border.color: Theme.mutedText
                                Rectangle {
                                    anchors.left: parent.left
                                    anchors.top: parent.top
                                    anchors.bottom: parent.bottom
                                    anchors.margins: 1.5
                                    radius: 3
                                    width: Math.max(3, (parent.width - 3)
                                                        * DeviceService.batteryPercent / 100)
                                    color: DeviceService.batteryPercent > 30 ? Theme.success
                                         : DeviceService.batteryPercent > 15 ? Theme.warning
                                         : Theme.error
                                }
                            }
                        }
                    }
                    Column {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Firmware"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text { text: HomeViewModel.firmwareText; color: Theme.text; font.pixelSize: Theme.fontSizeBody }
                    }
                    // Real, 2026-08-07 (was "Not available yet" - the backend side,
                    // sgee_andre.md, was already built and hardware-verified, only this
                    // UI was missing). Passively shows the watch's own currently-stored
                    // orbit date on every Home load (checkGpsOrbitStatus(), read-only,
                    // works even offline); tapping runs the real update flow
                    // (updateGpsOrbit()) - download-if-online-and-stale, else honestly
                    // report why not, matching this app's own "explicit tap for any
                    // write" rule elsewhere (Routes/Backup) rather than writing to the
                    // watch just from loading this page.
                    Column {
                        Layout.preferredWidth: 110
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("GPS orbit"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            text: DeviceService.gpsOrbitBusy
                                ? qsTr("Checking...")
                                : (DeviceService.gpsOrbitStatusText || qsTr("Tap to check"))
                            // Status-calming: settled state is quiet; green + underline only for
                            // the actionable "Tap to check" prompt (see the Clock field above).
                            readonly property bool actionable: !DeviceService.gpsOrbitBusy
                                                               && !DeviceService.gpsOrbitStatusText
                            color: actionable ? Theme.primary : Theme.mutedText
                            font.pixelSize: Theme.fontSizeBody
                            font.underline: actionable
                            TapHandler {
                                enabled: !DeviceService.gpsOrbitBusy
                                onTapped: DeviceService.updateGpsOrbit()
                            }
                            // Real request, 2026-08-11 (André, G3): offline, the message
                            // stays exactly as it is and hovering explains why nothing
                            // happens on its own. Online, this syncs on connection now and
                            // there is nothing to excuse.
                            HoverHandler { id: orbitHover }
                            ToolTip.visible: orbitHover.hovered && !DeviceService.online
                            ToolTip.text: qsTr("This feature needs an internet connection.")
                            ToolTip.delay: 300
                        }
                    }
                    // Real, 2026-08-10 ("I connected the kailash via usb... it didn't sync
                    // time... is this function implemented in our app? if not implement it") -
                    // same "explicit tap, no rehearsal shown" pattern as GPS orbit above,
                    // tools/set_time.py's own docstring covers why this one has no rehearsal
                    // step at all (always-safe clock set, unlike flash/PMEM writes).
                    Column {
                        Layout.preferredWidth: 110
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Clock"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            // André, 2026-08-11 (item 15): "just say synced when it is
                            // synced" - the full timestamp it synced TO added nothing the
                            // user had asked for.
                            text: DeviceService.timeSyncBusy
                                ? qsTr("Syncing...")
                                : (DeviceService.timeSyncStatusText.length > 0
                                   ? qsTr("Synced") : qsTr("Tap to sync"))
                            // Status-calming (2026-08-25): a settled "Synced" is the norm, so it
                            // reads as quiet grey; green + underline is reserved for the one
                            // actionable state ("Tap to sync"). Colour follows meaning, not decoration.
                            readonly property bool actionable: !DeviceService.timeSyncBusy
                                                               && DeviceService.timeSyncStatusText.length === 0
                            color: actionable ? Theme.primary : Theme.mutedText
                            font.pixelSize: Theme.fontSizeBody
                            font.underline: actionable
                            TapHandler {
                                enabled: !DeviceService.timeSyncBusy
                                onTapped: clockSyncDialog.open()
                            }
                            // André, G2 - see the GPS orbit field above for the reasoning.
                            // Tapping still works offline (the clock is set from this
                            // machine, no network involved); what needs a connection is the
                            // automatic sync on connection.
                            HoverHandler { id: clockHover }
                            ToolTip.visible: clockHover.hovered && !DeviceService.online
                            ToolTip.text: qsTr("Syncing on connection needs an internet " +
                                                "connection. Tapping still works offline.")
                            ToolTip.delay: 300
                        }

                        // Real, 2026-08-10 ("a button to sync time... opens a menu 'from
                        // device' 'from different timezone'", then "pop up is not working" /
                        // "menu is broken again" / "menu doesn't open" through three rounds
                        // of Popup+Overlay.overlay attempts - confirmed live tonight that
                        // Overlay.overlay is genuinely null at click time in this app, not
                        // just a timing quirk to work around). A plain inline expanding
                        // Column instead - no Popup, no Overlay, no clipped-Flickable
                        // reparenting trick needed at all; it pushes the cards below it down
                        // slightly when open instead of floating over them, which sidesteps
                        // every failure mode hit tonight in one stroke.
                    }

                    Column {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Serial number"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text { text: HomeViewModel.serialText; color: Theme.text; font.pixelSize: Theme.fontSizeBody }
                    }
                    Column {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Hardware"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text { text: HomeViewModel.hardwareText; color: Theme.text; font.pixelSize: Theme.fontSizeBody }
                    }
                    // Real, 2026-08-11 (André: "correlation between the devices we support
                    // and their manual link... put the manual next to hardware"). Opens the
                    // real Suunto user-guide PDF for whichever model is connected
                    // (HomeViewModel.manualUrl, sourced from the repo-root `manualslinks`
                    // file) in the system's own PDF viewer/browser - same
                    // Qt.openUrlExternally() mechanism as any other "open outside the app"
                    // action, no in-app PDF renderer needed for hardware this old (rule 5).
                    Column {
                        // 110 like GPS orbit above it, not the 140 it had in its old
                        // separate grid - column 3's width is the max of the two, and the
                        // extra 30px only pushed Clock further right (André: "clock could
                        // be a bit closer").
                        Layout.preferredWidth: 110
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Manual"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            // "(EN)" not "(PDF)" - André, 2026-08-11: the useful notice is
                            // that Suunto/Garmin only publish these guides in English, not
                            // what file format the browser is about to get.
                            text: qsTr("View guide (EN)")
                            color: Theme.primary
                            font.pixelSize: Theme.fontSizeBody
                            font.underline: true
                            TapHandler {
                                onTapped: Qt.openUrlExternally(HomeViewModel.manualUrl)
                            }
                        }
                    }
                }

                // --- Garmin info rows - firmware/part number, matching
                // GARMIN_USB_IMPORT_SPEC.md's own "Implementation-ready: device
                // identification" section exactly (Description + firmware as the primary
                // line, part number secondary - both real fields off GarminDevice.xml, not
                // guessed). No battery row: a mounted mass-storage filesystem has no way to
                // report it, unlike the Ambit3's own 0x0000 reply. ---
                Row {
                    width: parent.width
                    spacing: Theme.spacingLarge
                    visible: HomeViewModel.isGarmin

                    Column {
                        spacing: 2
                        Text { text: qsTr("Firmware"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: GarminService.firmwareVersion || qsTr("Not available")
                            color: Theme.text; font.pixelSize: Theme.fontSizeBody
                        }
                    }
                    Column {
                        spacing: 2
                        Text { text: qsTr("Part number"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: GarminService.partNumber || qsTr("Not available")
                            color: Theme.text; font.pixelSize: Theme.fontSizeBody
                        }
                    }
                    Column {
                        spacing: 2
                        Text { text: qsTr("SD card"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: GarminService.hasSdCard ? qsTr("Present") : qsTr("Not detected")
                            color: GarminService.hasSdCard ? Theme.text : Theme.mutedText
                            font.pixelSize: Theme.fontSizeBody
                        }
                    }
                    // Real, 2026-08-11 (André: "I added etrex manuals to the files, can you
                    // link it to the supported devices?"). Same "open externally" mechanism
                    // as the Suunto Manual field above, keyed by family instead of exact
                    // codename - see HomeViewModel.garminManualUrl's own comment.
                    Column {
                        spacing: 2
                        Text { text: qsTr("Manual"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            // "(EN)" not "(PDF)" - André, 2026-08-11: the useful notice is
                            // that Suunto/Garmin only publish these guides in English, not
                            // what file format the browser is about to get.
                            text: qsTr("View guide (EN)")
                            color: Theme.primary
                            font.pixelSize: Theme.fontSizeBody
                            font.underline: true
                            TapHandler {
                                onTapped: Qt.openUrlExternally(HomeViewModel.garminManualUrl)
                            }
                        }
                    }
                }

                // Real request 2026-08-08: "you can remove the refresh button" - connection
                // status now keeps itself current on its own (DeviceService's own polling:
                // stops once connected, retries every 1s until it isn't), so a manual
                // "Refresh" button has nothing left to do that isn't already happening.
                Text {
                    visible: !HomeViewModel.isGarmin && DeviceService.lastError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeLabel
                    text: DeviceService.lastError
                }
            }
        }

        // Multi-watch picker: several Suunto watches on the USB bus at once - tap to choose
        // which one the app targets (2026-08-16, porting the Android Home picker). Every
        // backend tool then pins to the pick (see server.py's SELECTED_PRODUCT_ID), so pages
        // stop racing between watches. Sits directly under the device card (André, 2026-08-16),
        // so the current watch is the hero and the alternatives are the row beneath it. Hidden
        // when 0 or 1 watch is connected.
        Card {
            width: parent.width
            variant: "nested"   // secondary utility strip under the device card - recedes
            visible: DeviceService.connectedWatches.length > 1

            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Text {
                    text: qsTr("%1 watches connected — tap to switch:")
                          .arg(DeviceService.connectedWatches.length)
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                }

                Flow {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Repeater {
                        model: DeviceService.connectedWatches
                        delegate: Rectangle {
                            required property var modelData
                            // The active one: the explicitly pinned watch, or - when nothing is
                            // pinned - whichever the backend currently reports as connected.
                            readonly property bool active:
                                DeviceService.selectedProductId >= 0
                                    ? modelData.productId === DeviceService.selectedProductId
                                    : DeviceService.model === modelData.codename
                            radius: height / 2
                            implicitHeight: chipLabel.implicitHeight + 12
                            implicitWidth: chipLabel.implicitWidth + 24
                            color: active ? Qt.alpha(Theme.primary, 0.13) : "transparent"
                            border.width: 1
                            border.color: active ? Theme.primary : Qt.alpha(Theme.mutedText, 0.4)

                            Text {
                                id: chipLabel
                                anchors.centerIn: parent
                                text: modelData.name
                                color: active ? Theme.text : Theme.mutedText
                                font.pixelSize: Theme.fontSizeBody
                                font.bold: active
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: DeviceService.selectWatch(modelData.productId)
                            }
                        }
                    }
                }
            }
        }

        // --- This year + Weather, side by side when the width is honest (2026-08-11
        // designer pass). "This year" is the Totals page's headline numbers surfaced where
        // they get seen every day - and a doorway to that page, which was invisible from
        // Home before. Weather keeps its own card and all its own logic. ---
        GridLayout {
            width: parent.width
            columns: column.twoColumn ? 2 : 1
            columnSpacing: Theme.spacingMedium
            rowSpacing: Theme.spacingMedium

            // Wrapper Items, not bare Cards: both cards bind width to their parent, and a
            // GridLayout parent would hand them the full grid width - an Item cell sized
            // by the layout gives them an honest parent to fill.
            Item {
                id: thisYearCell
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: 1  // equal shares; real width comes from fillWidth
                implicitHeight: leftStack.height
                visible: thisYearCard.activityCount > 0 || funFactCard.factText.length > 0

                Column {
                    id: leftStack
                    width: parent.width
                    spacing: Theme.spacingMedium

                Card {
                    id: thisYearCard
                    variant: "flat"   // supporting stat, not the page hero
                    width: parent.width
                    visible: activityCount > 0

                    // Same per-device source rule as Last Activity below - the year is
                    // whatever the newest activity's year is, matching TotalsPage's own
                    // "most recent year with data" default rather than the wall clock, so
                    // a January visit still shows last year's real story instead of zeros.
                    readonly property var _activities: HomeViewModel.isKailash
                        ? (KailashService.trackLogOk ? KailashService.trackLogActivities : [])
                        : (HomeViewModel.isGarmin ? GarminService.activities
                                                  : ActivityService.activities)
                    readonly property int year: {
                        let best = -1
                        for (const a of _activities) {
                            if (!a.startTime) continue
                            const y = new Date(a.startTime).getFullYear()
                            if (y > best) best = y
                        }
                        return best
                    }
                    readonly property int activityCount: {
                        let n = 0
                        for (const a of _activities)
                            if (a.startTime && new Date(a.startTime).getFullYear() === year) n++
                        return n
                    }
                    readonly property real yearMeters: {
                        let m = 0
                        for (const a of _activities)
                            if (a.startTime && new Date(a.startTime).getFullYear() === year)
                                m += (a.distanceMeters || 0)
                        return m
                    }
                    readonly property real yearSeconds: {
                        let s = 0
                        for (const a of _activities)
                            if (a.startTime && new Date(a.startTime).getFullYear() === year)
                                s += (a.durationSeconds || 0)
                        return s
                    }

                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: NavBus.navigate("totals") }

                    Column {
                        width: parent.width
                        spacing: Theme.spacingSmall

                        Row {
                            width: parent.width
                            Text {
                                id: thisYearTitle
                                text: qsTr("This year")
                                font.bold: true
                                color: Theme.text
                            }
                            // Explicit spacer width, same as Last Activity's header: an
                            // empty Item's childrenRect is 0, so the earlier
                            // parent.width - childrenRect.width spacer filled the whole
                            // row and shoved the link off the card (caught on screenshot,
                            // 2026-08-12).
                            Item {
                                width: parent.width - thisYearTitle.width - openTotals.width
                                height: 1
                            }
                            Text {
                                id: openTotals
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Open Totals ›")
                                color: Theme.primary
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }

                        Row {
                            width: parent.width
                            spacing: Theme.spacingLarge
                            Column {
                                spacing: 2
                                Text { text: qsTr("Distance"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                                Text {
                                    text: ActivityViewModel.formatDistance(thisYearCard.yearMeters)
                                    color: Theme.text
                                    font.bold: true
                                    font.pixelSize: Theme.fontSizeBodyLarge
                                }
                            }
                            Column {
                                spacing: 2
                                Text { text: qsTr("Time"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                                Text {
                                    text: ActivityViewModel.formatDuration(thisYearCard.yearSeconds)
                                    color: Theme.text
                                    font.bold: true
                                    font.pixelSize: Theme.fontSizeBodyLarge
                                }
                            }
                            Column {
                                spacing: 2
                                Text { text: qsTr("Activities"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                                Text {
                                    text: thisYearCard.activityCount
                                    color: Theme.text
                                    font.bold: true
                                    font.pixelSize: Theme.fontSizeBodyLarge
                                }
                            }
                        }

                        // One TotalsFacts line as a teaser - the factual-playful voice of
                        // the Totals page, previewed. distanceLines() puts the best-fitting
                        // comparison first. (An equal-height-with-weather variant with more
                        // lines was tried 2026-08-12 and rejected - "back to the design
                        // with the spacing"; the space below is the fun-fact card's now.)
                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            visible: thisYearCard.yearMeters > 0
                            text: {
                                const lines = TotalsFacts.distanceLines(thisYearCard.yearMeters)
                                return lines.length > 0 ? lines[0] : ""
                            }
                            color: Theme.mutedText
                            font.italic: true
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                }

                // "create a second card underneath with some random fun fact from
                // internet" - André, 2026-08-12, filling the space under This year that
                // the rejected equal-height experiment left. uselessfacts.jsph.pl: free,
                // keyless, no tracking, one JSON field. Tap for another. Same day, on
                // hearing it was online-only ("that is really what I want!"): offline it
                // falls back to FunFacts.qml's curated local base instead of hiding, so
                // the card works on a mountain with no signal too.
                Card {
                    id: funFactCard
                    // Back to flat/white (André, 2026-08-25: "put it back again in white like
                    // the others please") - matching This year/Weather/Last Activity, not the
                    // recessed "nested" look.
                    variant: "flat"
                    width: parent.width
                    visible: factText.length > 0
                    // Bottom-aligned with the weather card - André, 2026-08-12, after
                    // checking the edges: tops matched, bottoms were 16px off. The fact
                    // card absorbs the difference (it's the flexible one - a stretched
                    // stats card looked wrong, that was the rejected equal-height
                    // experiment); natural height when stacked single-column or when
                    // either neighbour is missing.
                    height: column.twoColumn && thisYearCard.visible
                            && WeatherService.hasFetchedOnce
                        ? Math.max(implicitHeight,
                                   weatherCard.height - thisYearCard.height
                                   - Theme.spacingMedium)
                        : implicitHeight

                    property string factText: ""

                    function fetchFact() {
                        const xhr = new XMLHttpRequest()
                        xhr.onreadystatechange = function() {
                            if (xhr.readyState !== XMLHttpRequest.DONE)
                                return
                            try {
                                const fact = JSON.parse(xhr.responseText).text
                                if (fact && fact.length > 0) {
                                    funFactCard.factText = fact
                                    return
                                }
                            } catch (e) { /* fall through to the offline base */ }
                            funFactCard.factText = FunFacts.random()
                        }
                        xhr.open("GET",
                                 "https://uselessfacts.jsph.pl/api/v2/facts/random?language=en")
                        xhr.send()
                    }

                    Component.onCompleted: fetchFact()

                    HoverHandler { cursorShape: Qt.PointingHandCursor }
                    TapHandler { onTapped: funFactCard.fetchFact() }

                    Column {
                        width: parent.width
                        spacing: Theme.spacingSmall

                        Row {
                            width: parent.width
                            Text {
                                id: funFactTitle
                                text: qsTr("Did you know?")
                                font.bold: true
                                color: Theme.text
                            }
                            // Explicit spacer width - see the This year header's comment.
                            Item {
                                width: parent.width - funFactTitle.width - anotherFact.width
                                height: 1
                            }
                            Text {
                                id: anotherFact
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Another ›")
                                color: Theme.primary
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }

                        Text {
                            width: parent.width
                            wrapMode: Text.WordWrap
                            text: funFactCard.factText
                            color: Theme.mutedText
                            font.italic: true
                            font.pixelSize: Theme.fontSizeLabel
                        }
                    }
                }
                }
            }

            Item {
                Layout.fillWidth: true
                Layout.alignment: Qt.AlignTop
                Layout.preferredWidth: 1
                implicitHeight: weatherCard.height
                // The SERVICE state, deliberately not weatherCard.visible: a child's
                // `visible` reads effective visibility, so once this wrapper hides, the
                // card inside can never read true again and the pair deadlocks hidden -
                // found live on 2026-08-11 when the weather card vanished from the
                // redesigned Home.
                visible: WeatherService.hasFetchedOnce

                WeatherCard { id: weatherCard; width: parent.width }
            }
        }

        // --- Kailash travel history & activity-mode logbook - real, 2026-08-08 ("resumind:
        // 7r button, last city visit... if we could import this data which is on the watch
        // and read it to our app would be awesome"). Kailash has no routes/POIs/sport-mode
        // UI (it doesn't have sport modes at all - real, same day), so this is its one
        // Kailash-specific card: visited cities/countries and travel stats matching the
        // watch's own "7R" screen exactly, plus the real activity-mode logbook that turned
        // out to be bundled in the same query (see KailashService's own header comment for
        // where that was found - this project had separately been unable to locate it as
        // its own flash region). No place names shown - the watch itself only ever reports
        // coordinates + country code, not a city name; inventing a reverse-geocode lookup
        // here wasn't asked for. ---
        Card {
            width: parent.width
            variant: "flat"   // Kailash history panel - supporting content
            visible: HomeViewModel.isKailash
                     && (KailashService.loading || KailashService.historyOk
                         || KailashService.lastError.length > 0)

            Column {
                width: parent.width
                spacing: Theme.spacingMedium

                Text { text: qsTr("Travel History"); font.bold: true; color: Theme.text }

                Text {
                    visible: KailashService.loading && !KailashService.historyOk
                    color: Theme.mutedText
                    text: qsTr("Reading travel history off the watch...")
                }

                Text {
                    visible: !KailashService.loading && !KailashService.historyOk
                             && KailashService.lastError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeLabel
                    text: KailashService.lastError
                }

                // Real request 2026-08-09 ("Try to aligne the text on home by 'collumns'").
                // GridLayout (not used anywhere else in this codebase before now) gives each
                // column a consistent width across rows automatically - the plain Row-of-
                // Columns this replaced only aligned within its own single row, so this stat
                // block and the distance/furthest-from-home block below it didn't line up
                // with each other. One shared GridLayout fixes that for both.
                GridLayout {
                    visible: KailashService.historyOk
                    width: parent.width
                    columns: 3
                    columnSpacing: Theme.spacingLarge
                    rowSpacing: Theme.spacingSmall

                    Column {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Cities visited"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: KailashService.citiesVisited
                            color: Theme.text; font.pixelSize: Theme.fontSizeBody
                        }
                    }
                    Column {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Countries visited"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: KailashService.countriesVisited
                            color: Theme.text; font.pixelSize: Theme.fontSizeBody
                        }
                    }
                    Column {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Travel days"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: KailashService.travellingDays
                            color: Theme.text; font.pixelSize: Theme.fontSizeBody
                        }
                    }

                    Column {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Travelled distance"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: ActivityViewModel.formatDistance(KailashService.travelledDistanceMeters)
                            color: Theme.text; font.pixelSize: Theme.fontSizeBody
                        }
                    }
                    Column {
                        Layout.fillWidth: true
                        spacing: 2
                        Text { text: qsTr("Furthest from home"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Text {
                            text: ActivityViewModel.formatDistance(KailashService.furthestFromHomeMeters)
                            color: Theme.text; font.pixelSize: Theme.fontSizeBody
                        }
                    }
                }

                Row {
                    visible: KailashService.historyOk
                    width: parent.width
                    spacing: Theme.spacingMedium

                    Icon { glyph: Icons.pois; size: 24; color: Theme.primary }

                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        Text {
                            text: KailashService.hasLastKnownLocation
                                ? qsTr("%1, %2").arg(KailashService.lastKnownLatitude.toFixed(4))
                                                .arg(KailashService.lastKnownLongitude.toFixed(4))
                                : qsTr("No known location yet")
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBody
                        }
                        Text {
                            visible: KailashService.lastKnownCountry.length > 0
                                     || KailashService.lastKnownTime.length > 0
                            text: [KailashService.lastKnownCountry,
                                   KailashService.lastKnownTime
                                       ? qsTr("last seen %1").arg(
                                             ActivityViewModel.formatDate(KailashService.lastKnownTime))
                                       : ""].filter(s => s.length > 0).join(" · ")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeLabel
                        }
                    }
                }

                // Home location - real, 2026-08-09 ("I believe you put a POI icon for home,
                // name home and identify the city by coordinates"). Same real HomeLocation
                // setting SettingsPage.qml's own coord editor reads/writes (entry 0x36,
                // ambit_app_kailash_home_location_field memory) - genuinely different data
                // from "last known location" above (that's the watch's own last GPS fix,
                // this is the fixed reference point used for furthestFromHome).
                Row {
                    visible: KailashService.hasHomeLocation
                    width: parent.width
                    spacing: Theme.spacingMedium

                    Icon { glyph: Icons.pois; size: 24; color: Theme.primary }

                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        Text {
                            text: qsTr("Home")
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBody
                            font.bold: true
                        }
                        Text {
                            text: KailashService.homeCity.length > 0
                                ? qsTr("%1 (%2, %3)").arg(KailashService.homeCity)
                                                      .arg(KailashService.homeLatitude.toFixed(4))
                                                      .arg(KailashService.homeLongitude.toFixed(4))
                                : qsTr("%1, %2").arg(KailashService.homeLatitude.toFixed(4))
                                                 .arg(KailashService.homeLongitude.toFixed(4))
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeLabel
                        }
                    }
                }

                // World map of visited places - real, 2026-08-09 ("Add a world map with the
                // points were kailash has been"). MapView's own real projection math
                // auto-fits to whatever pins are given (see MapView.qml's own `markers`
                // property comment) - a single real visited place so far on this reference
                // watch (Lille) shows as a single centered pin, exactly as accurate as this
                // watch's own real travel history; more pins appear here automatically as
                // more real travel gets recorded, no further wiring needed.
                Column {
                    visible: KailashService.historyOk && KailashService.visitedPlaces.length > 0
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Text {
                        text: qsTr("Places visited (%1)").arg(KailashService.visitedPlaces.length)
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeLabel
                    }
                    // Real, 2026-08-09 ("On the map if possible put round corners"), reverted
                    // the same day: a QtQuick.Effects MultiEffect maskSource/maskEnabled
                    // attempt here left this map rendering as a real blank area (confirmed
                    // live - "Home page Places visited world map... blank/empty space"), not
                    // just square-cornered as intended. Not worth re-attempting blind (no way
                    // to visually verify a shader-based fix from here) - square corners here
                    // match every other map in the app already (ActivityCard's own map
                    // preview has no rounding either), so this is a real regression fix, not
                    // a downgrade from some established look.
                    Item {
                        width: parent.width
                        height: 200
                        clip: true
                        MapView {
                            anchors.fill: parent
                            markers: KailashService.visitedPlaces
                            zoomLevel: 4  // only used for a single pin - see MapView.qml's
                                          // own _singlePoint comment; 2+ pins auto-fit
                        }
                    }
                }

                // Activity-mode logbook - a real, separate system from the passive TrackLog
                // shown in "Last Activity" below (see kailash_history.py's own docstring):
                // explicit recorded sessions, summary stats only, no GPS track.
                Column {
                    visible: KailashService.historyOk && KailashService.sessions.length > 0
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Text {
                        text: qsTr("Activity mode logbook (%1)").arg(KailashService.sessions.length)
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeLabel
                    }

                    // Real, 2026-08-09 ("make the 3rd collumn of numbers of activity mode
                    // logbook aligned") - duration/distance had no fixed width, so each row's
                    // 2nd/3rd column landed wherever that row's own text happened to end,
                    // same class of bug the Travel History GridLayout above already fixed.
                    // A plain Row still works fine here (only 3 always-present columns, no
                    // GridLayout needed) as long as every column gets a real fixed width.
                    Repeater {
                        model: KailashService.sessions
                        delegate: Row {
                            width: parent.width
                            spacing: Theme.spacingMedium
                            Text {
                                width: 140
                                text: ActivityViewModel.formatDate(modelData.when)
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeLabel
                            }
                            Text {
                                width: 70
                                horizontalAlignment: Text.AlignRight
                                text: ActivityViewModel.formatDuration(modelData.durationSeconds)
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeLabel
                            }
                            Text {
                                width: 70
                                horizontalAlignment: Text.AlignRight
                                text: ActivityViewModel.formatDistance(modelData.distanceMeters)
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeLabel
                            }
                        }
                    }
                }
            }
        }

        // --- Last Activity - real, 2026-08-07 (was a Step 7 placeholder before
        // ActivityService actually worked; "New Activities" and the Home "Connections" card
        // were both dropped the same day - New Activities duplicated this card with nothing
        // else to say, and Connections already has a real home on Settings). ---
        Card {
            id: lastActivityCard
            variant: "flat"   // supporting content, paired with This year / Weather
            width: parent.width
            readonly property bool activityLoading:
                HomeViewModel.isGarmin ? GarminService.activitiesLoading
                : HomeViewModel.isKailash ? (KailashService.loading && !KailashService.trackLogOk)
                : ActivityService.loading
            visible: activityLoading || lastActivityColumn.activity !== null
                     || (!HomeViewModel.isGarmin && !HomeViewModel.isKailash
                         && ActivityService.lastError.length > 0)

            // 2026-08-11 designer pass: this card was a dead end - the whole card now
            // opens Activities, where the full list and the large map live.
            HoverHandler {
                enabled: lastActivityColumn.activity !== null
                cursorShape: Qt.PointingHandCursor
            }
            TapHandler {
                enabled: lastActivityColumn.activity !== null
                onTapped: NavBus.navigate("activities")
            }

            Column {
                id: lastActivityColumn
                width: parent.width
                spacing: Theme.spacingSmall

                // Real, 2026-08-09: KailashService.trackLogActivities is now one real entry
                // per DeviceHistory session (correlated against TrackLog's own GPS points,
                // see kailash_tracklog.py's split_into_activities() docstring) rather than
                // one bundled everything-activity - same list shape GarminService/
                // ActivityService already use, so mostRecent() applies here too now.
                readonly property var activity: HomeViewModel.isKailash
                    ? (KailashService.trackLogOk
                       ? ActivityViewModel.mostRecent(KailashService.trackLogActivities) : null)
                    : ActivityViewModel.mostRecent(
                          HomeViewModel.isGarmin ? GarminService.activities : ActivityService.activities)

                Row {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Text {
                        id: lastActivityTitle
                        text: HomeViewModel.isKailash ? qsTr("Recent Track") : qsTr("Last Activity")
                        font.bold: true
                        color: Theme.text
                    }
                    Text {
                        id: cachedTag
                        visible: !HomeViewModel.isGarmin && !HomeViewModel.isKailash
                                 && ActivityService.showingCachedData
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("(cached)")
                        font.italic: true
                        font.pixelSize: Theme.fontSizeCaption
                        color: Theme.mutedText
                    }
                    Item {
                        width: parent.width - lastActivityTitle.width - openActivities.width
                               - (cachedTag.visible ? cachedTag.width + Theme.spacingSmall : 0)
                               - Theme.spacingSmall * 2
                        height: 1
                    }
                    Text {
                        id: openActivities
                        visible: lastActivityColumn.activity !== null
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Open Activities ›")
                        color: Theme.primary
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }


                Text {
                    visible: HomeViewModel.isGarmin ? GarminService.activitiesLoading
                        : HomeViewModel.isKailash ? (KailashService.loading && !KailashService.trackLogOk)
                        : ActivityService.loading
                    color: Theme.mutedText
                    text: HomeViewModel.isKailash
                        ? qsTr("Reading the passive GPS track off the watch...")
                        : qsTr("Reading activities off the watch...")
                }

                Text {
                    visible: !HomeViewModel.isGarmin && !HomeViewModel.isKailash && !ActivityService.loading
                             && ActivityService.lastError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeLabel
                    text: ActivityService.lastError
                }

                Text {
                    visible: HomeViewModel.isKailash && !KailashService.loading
                             && !KailashService.trackLogOk && KailashService.lastError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeLabel
                    text: KailashService.lastError
                }

                // No map here - André, 2026-08-11 designer pass, third round: "we don't
                // need another map" (the full-width one was "too noisy", the thumbnail
                // redundant with the Activities page a click away). Info only; the card
                // itself is the doorway.
                Row {
                    visible: !lastActivityCard.activityLoading && lastActivityColumn.activity !== null
                    width: parent.width
                    spacing: Theme.spacingMedium

                    Icon { glyph: Icons.activities; size: 28; color: Theme.primary }

                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 2
                        Text {
                            text: lastActivityColumn.activity
                                  ? (lastActivityColumn.activity.name || qsTr("Untitled activity"))
                                  : ""
                            font.bold: true
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBodyLarge
                        }
                        Text {
                            text: lastActivityColumn.activity
                                  ? ActivityViewModel.formatDate(lastActivityColumn.activity.startTime)
                                  : ""
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeLabel
                        }
                    }
                }

                Row {
                    visible: !lastActivityCard.activityLoading && lastActivityColumn.activity !== null
                    width: parent.width
                    spacing: Theme.spacingLarge
                    Text {
                        text: lastActivityColumn.activity
                              ? ActivityViewModel.formatDistance(lastActivityColumn.activity.distanceMeters)
                              : ""
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeLabel
                    }
                    Text {
                        text: lastActivityColumn.activity
                              ? ActivityViewModel.formatDuration(lastActivityColumn.activity.durationSeconds)
                              : ""
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeLabel
                    }
                    Text {
                        text: lastActivityColumn.activity
                              ? ActivityViewModel.formatElevation(lastActivityColumn.activity.ascentMeters)
                              : ""
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeLabel
                    }
                }
            }
        }
    }

    // Clock sync - a dialog rather than a menu that grew inside the card (André, item 15).
    ClockSyncDialog {
        id: clockSyncDialog
        anchors.centerIn: Overlay.overlay
    }

    // Fresh-pairing passkey prompt. This watch family uses LE Legacy Passkey Entry (watch
    // displays a 6-digit code, the central types it in) - there is no way for this app to
    // read the watch's own screen, so a human has to relay it (ble_server.py's Agent
    // docstring, HANDOFF.md Milestone 7 item 16). Opens itself whenever
    // DeviceService.blePendingPasskeyDevice becomes non-empty rather than needing a
    // separate button, since that state only exists while BlueZ is actively waiting on it.
    ThemedDialog {
        id: blePasskeyDialog
        anchors.centerIn: Overlay.overlay
        title: qsTr("Enter the watch's passkey")
        standardButtons: Dialog.Ok | Dialog.Cancel
        onOpened: passkeyField.text = ""
        onAccepted: DeviceService.submitBlePasskey(parseInt(passkeyField.text, 10))

        Connections {
            target: DeviceService
            function onBleStateChanged() {
                if (DeviceService.blePendingPasskeyDevice.length > 0 && !blePasskeyDialog.visible) {
                    blePasskeyDialog.open();
                } else if (DeviceService.blePendingPasskeyDevice.length === 0 && blePasskeyDialog.visible) {
                    blePasskeyDialog.close();
                }
            }
        }

        contentItem: Column {
            spacing: Theme.spacingMedium
            width: 280

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: qsTr("The watch is showing a 6-digit code right now - type it in " +
                            "to finish pairing.")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
            }
            RoundedTextField {
                id: passkeyField
                width: parent.width
                inputMethodHints: Qt.ImhDigitsOnly
                validator: IntValidator { bottom: 0; top: 999999 }
                placeholderText: qsTr("123456")
            }
        }
    }
}
