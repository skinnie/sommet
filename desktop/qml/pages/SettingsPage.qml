import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import AmbitApp

// Step 11, the last one. General/Connections/Maps/Weather/Backup/About per the spec.
// Weather's "Manual location" is the one section that's fully real end to end - it's the
// first actual UI consumer of WeatherService's own settable latitude/longitude (built in
// Step 5 specifically so a location source could be swapped "without UI modifications" -
// this is that promise being kept, not a new mechanism).
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    // Real, 2026-08-08 ("Settings on ambit 3 - if they are already cracked to be changed by
    // cable, we will need to build a UI for it"). Cable settings-write is now confirmed
    // working for both the Ambit3 (SettingsWriteService's own header comment: André
    // confirmed Display.Invert visibly switching the watch Light -> Dark) and, checked the
    // same way right after, Kailash too - SettingsWriteService.device picks which curated
    // table the backend uses (see its own header comment). Fetched here so the Settings
    // card below has real data as soon as this page opens, matching how HomePage.qml
    // already fires its own service refreshes from Component.onCompleted.
    Component.onCompleted: {
        checkCatalogStatus();
    }

    // --- Suunto Apps Catalog (2026-08-12) - this project never ships/commits the official
    // catalog (~13,104 apps, mostly individual Movescount community members' own compiled
    // work, not something docs/PROJECT_OVERVIEW.md's "Scope and legal basis" covers
    // redistributing) - each user imports their own copy from their own SuuntoLink
    // installation instead. Same backend mechanism the Linux workout builder page
    // documents (data/suunto_apps_source/), reachable here with a real button rather than
    // a manual file copy. Plain XMLHttpRequest to the local backend, same pattern as
    // FirmwarePage.qml - no new C++ service needed for local file I/O this simple. ---
    readonly property string apiBase: "http://127.0.0.1:8766"
    property bool catalogImported: false
    property int catalogEntries: 0
    property bool catalogChecking: true
    property bool catalogImporting: false
    property string catalogError: ""

    function checkCatalogStatus() {
        catalogChecking = true;
        const xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return;
            catalogChecking = false;
            let d = null;
            try { d = JSON.parse(xhr.responseText); } catch (e) {}
            catalogImported = !!(d && d.imported);
            catalogEntries = (d && d.entries) || 0;
        };
        xhr.open("GET", apiBase + "/api/apps/catalog/status");
        xhr.send();
    }

    function importCatalogFile(fileUrl) {
        catalogImporting = true;
        catalogError = "";
        const xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return;
            catalogImporting = false;
            let d = null;
            try { d = JSON.parse(xhr.responseText); } catch (e) {}
            if (!d || !d.ok) {
                catalogError = (d && d.error) || qsTr("Import failed.");
                return;
            }
            catalogImported = true;
            catalogEntries = d.entries || 0;
        };
        xhr.open("POST", apiBase + "/api/apps/catalog/import");
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.send(JSON.stringify({ path: fileUrl.toString() }));
    }

    // --- intervals.icu "Sync now" (André, 2026-08-18): run the enabled toggles. Each flow
    // reports its own line into syncStatus as it finishes; the read-only pull (activity level)
    // and the watch write (profile) go through their backend endpoints, gear through its
    // service. Manual, one-shot - not background.
    function syncAppend(label, ok, detail) {
        var line = (ok ? "\u2713 " : "\u2717 ") + label + (detail ? " \u2014 " + detail : "");
        syncStatus.color = Theme.text;
        syncStatus.text = (syncStatus.text.length > 0 ? syncStatus.text + "\n" : "") + line;
    }
    function intervalsPost(path, label) {
        var xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return;
            var ok = false, detail = "";
            try {
                var r = JSON.parse(xhr.responseText);
                ok = (xhr.status === 200) && !!r.ok;
                if (!ok && r.error) detail = String(r.error);
            } catch (e) { detail = qsTr("no response"); }
            root.syncAppend(label, ok, detail);
        };
        xhr.open("POST", apiBase + path);
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.send(JSON.stringify({ athlete_id: ConnectionsService.intervalsIcuAthleteId,
                                  api_key: ConnectionsService.intervalsIcuApiKey(),
                                  confirm: true }));
    }
    function intervalsSyncNow() {
        syncStatus.color = Theme.mutedText;
        syncStatus.text = "";
        var any = false;
        if (ConnectionsService.syncImportGear) { any = true; GearService.importFromIntervals(); }
        if (ConnectionsService.syncActivityLevel) {
            any = true; root.intervalsPost("/api/intervals/activity-level", qsTr("Activity level"));
        }
        if (ConnectionsService.syncStatsToWatch) {
            any = true; root.intervalsPost("/api/intervals/stats-to-watch", qsTr("Profile to watch"));
        }
        if (ConnectionsService.syncImportActivities) {
            any = true; ActivityService.importFromIntervals(ConnectionsService.syncImportDays);
        }
        if (ConnectionsService.syncExportActivities) {
            any = true; ActivityService.exportToIntervals();
        }
        if (!any)
            syncStatus.text = qsTr("Nothing selected - turn on what you want to sync above.");
    }

    FileDialog {
        id: catalogFileDialog
        title: qsTr("Select suunto-apps/index.json")
        nameFilters: [qsTr("JSON files (*.json)"), qsTr("All files (*)")]
        onAccepted: root.importCatalogFile(selectedFile)
    }

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 480
        // Real, 2026-08-09 ("more coherence and simplicity") - was spacingMedium, the same
        // gap used *inside* every card between its own rows - so the whole page read as one
        // undifferentiated stack rather than distinct sections. Larger gap between cards
        // than within them is a real, deliberate hierarchy cue, not a bigger version of the
        // same thing.
        spacing: Theme.spacingLarge

        // --- Appearance - real, 2026-08-10 ("on desktop mode, put the menu on settings for
        // dark mode/system"). Theme.qml's own header comment anticipated exactly this back
        // when `override`/isDark were first built - this is that control, finally wired up.
        // Same RadioButton pattern (autoExclusive:false + onClicked, not checked bindings
        // fighting QQC2's own exclusivity - see the real bug that caused further down in
        // the Maps card) as every other exclusive-choice control on this page. ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.weatherSunny; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: qsTr("Appearance"); font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                }
                Text {
                    text: qsTr("Choose light or dark, or follow your system setting.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedRadioButton {
                        autoExclusive: false
                        checked: Theme.override === "light"
                        text: qsTr("Light")
                        onClicked: Theme.override = "light"
                    }
                    RoundedRadioButton {
                        autoExclusive: false
                        checked: Theme.override === "dark"
                        text: qsTr("Dark")
                        onClicked: Theme.override = "dark"
                    }
                    RoundedRadioButton {
                        autoExclusive: false
                        checked: Theme.override === "system"
                        text: qsTr("System")
                        onClicked: Theme.override = "system"
                    }
                }

                // The map/list view choice moved out of Settings and onto each list page
                // itself (Activities/Routes/POIs), André 2026-08-16 - a per-page toggle now,
                // still persisted via Theme.activitiesView/routesView/poisView.
            }
        }

        Card {
            width: parent.width
            visible: HomeViewModel.isGarmin
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.settings; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: qsTr("Supported devices"); font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                }
                Row {
                    spacing: 6
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.mutedText
                    }
                    Text {
                        text: qsTr("Suunto Ambit 3 (USB, via the local backend)")
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeLabel
                    }
                }
                Row {
                    spacing: 6
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.success
                    }
                    Text {
                        text: qsTr("Garmin eTrex — connected (%1)").arg(GarminService.model)
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeLabel
                    }
                }
            }
        }

        // --- Connections ---
        // Found 2026-08-07 via real testing: this used to be static, no way to click into
        // any of it. Checked what the real Android app does before building each one -
        // Intervals.icu AND Runalyze both use simple personal-API-key auth (no OAuth) - the
        // first version of this wrongly assumed Runalyze needed OAuth too, corrected once
        // actually checked (src/services/ApiRunalyze.ts). Strava genuinely does need real
        // OAuth2 (src/services/ApiStrava.ts) - built for real the same day, via a local
        // loopback HTTP callback server instead of the Android app's custom URL scheme; see
        // ConnectionsService's own header comment for why.
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.sync; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: qsTr("Connections"); font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                }

                // Real, 2026-08-09: these three rows used spacing:8 while every other
                // status-dot row on this page (General/Supported devices above) used 6 -
                // unified to 6.
                Row {
                    spacing: 6
                    TapHandler { onTapped: intervalsIcuDialog.open() }
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: ConnectionsService.intervalsIcuConnected ? Theme.success : Theme.mutedText
                    }
                    Text {
                        text: ConnectionsService.intervalsIcuConnected
                            ? qsTr("Intervals.icu — connected (athlete %1)")
                                .arg(ConnectionsService.intervalsIcuAthleteId)
                            : qsTr("Intervals.icu — tap to set up")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
                Row {
                    spacing: 6
                    TapHandler { onTapped: runalyzeDialog.open() }
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: ConnectionsService.runalyzeConnected ? Theme.success : Theme.mutedText
                    }
                    Text {
                        text: ConnectionsService.runalyzeConnected
                            ? qsTr("Runalyze — connected")
                            : qsTr("Runalyze — tap to set up")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
                Row {
                    spacing: 6
                    TapHandler { onTapped: stravaDialog.open() }
                    Rectangle {
                        width: 8; height: 8; radius: 4
                        anchors.verticalCenter: parent.verticalCenter
                        color: ConnectionsService.stravaConnected ? Theme.success : Theme.mutedText
                    }
                    Text {
                        text: ConnectionsService.stravaConnected
                            ? qsTr("Strava — connected")
                            : qsTr("Strava — tap to set up")
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
                // Cloud storage (Dropbox/Google Drive/OneDrive) used to live here as OAuth
                // "connections"; backup now just saves to a folder you pick (point it at a cloud
                // sync folder), so those rows and their key dialogs were removed - André 2026-08-16.
            }
        }

        // --- Maps ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.routes; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: qsTr("Maps"); font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                }
                Text {
                    // Straight from the provider record, so a provider added to
                    // MapService needs no second edit here to name itself correctly.
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Provider: %1").arg(MapService.providerName)
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBody
                }
                Flow {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    // autoExclusive (QQC2's default for same-parent RadioButtons) fights
                    // with these declarative `checked` bindings - it explicitly assigns
                    // `checked` on whichever button loses, which silently destroys that
                    // button's binding so it stops following MapService.provider. Exclusivity
                    // is already fully handled by the shared property (only one of these two
                    // comparisons can ever be true), so autoExclusive is switched off, and
                    // onClicked (a real user action) is used instead of onCheckedChanged
                    // (which also fires from binding evaluation, not just clicks) - real bug,
                    // 2026-08-07, likely also the cause of the earlier "clicks for CyclOSM
                    // don't do anything" report.
                    RoundedRadioButton {
                        autoExclusive: false
                        checked: MapService.provider === "osm"
                        text: qsTr("OpenStreetMap (standard)")
                        onClicked: MapService.provider = "osm"
                    }
                    RoundedRadioButton {
                        autoExclusive: false
                        checked: MapService.provider === "cyclosm"
                        text: qsTr("CyclOSM (cycling-focused)")
                        onClicked: MapService.provider = "cyclosm"
                    }
                    // André, 2026-08-11: "ok add IGN to desktop" - for parity with Android,
                    // which has had it and defaults to it. Same layer, so both versions draw
                    // the identical map.
                    RoundedRadioButton {
                        autoExclusive: false
                        checked: MapService.provider === "ign"
                        text: qsTr("IGN (France)")
                        onClicked: MapService.provider = "ign"
                    }
                }

                // Offline tile cache - real, 2026-08-11 (André: "put this offline map cache
                // in the desktop version", matching Android's own SettingsScreen.tsx cache
                // size + "Clear map cache" row). This is the SAME cache every map tile
                // (browsed or explicitly downloaded via MapWindow's own "Download for
                // offline" button) lands in - one number, one clear action, not a separate
                // "offline tiles" store to manage.
                Row {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Text {
                        width: parent.width - clearCacheButton.width - Theme.spacingSmall
                        anchors.verticalCenter: parent.verticalCenter
                        wrapMode: Text.WordWrap
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                        text: qsTr("Offline tile cache: %1 MB")
                              .arg((TileCacheService.cacheSizeBytes / (1024 * 1024)).toFixed(1))
                    }
                    RoundedButton {
                        id: clearCacheButton
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Clear")
                        enabled: TileCacheService.cacheSizeBytes > 0
                        onClicked: TileCacheService.clearCache()
                    }
                }
            }
        }

        // --- Weather: the real, functional section ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.weatherSunny; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: qsTr("Weather"); font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                }
                Text {
                    text: qsTr("Provider: Open-Meteo")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                }

                Text { text: qsTr("Location source"); color: Theme.text; font.pixelSize: Theme.fontSizeBody }
                Row {
                    spacing: Theme.spacingSmall
                    // IP-based is the real default now (Main.qml calls
                    // WeatherService.detectLocationFromIp() on startup, not refresh()) - this
                    // radio just reflects/re-triggers that, matching HomeViewModel's own
                    // startup call rather than owning the decision itself.
                    RoundedRadioButton {
                        checked: true
                        text: qsTr("This computer (IP-based)")
                        onCheckedChanged: if (checked) WeatherService.detectLocationFromIp()
                    }
                    RoundedRadioButton { text: qsTr("Manual") }
                }

                Row {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    RoundedTextField {
                        id: latField
                        width: (parent.width - Theme.spacingSmall) / 2
                        placeholderText: qsTr("Latitude")
                        text: WeatherService.latitude.toString()
                    }
                    RoundedTextField {
                        id: lonField
                        width: (parent.width - Theme.spacingSmall) / 2
                        placeholderText: qsTr("Longitude")
                        text: WeatherService.longitude.toString()
                    }
                }
                RoundedButton {
                    text: qsTr("Apply")
                    onClicked: {
                        const lat = parseFloat(latField.text);
                        const lon = parseFloat(lonField.text);
                        if (!isNaN(lat)) WeatherService.latitude = lat;
                        if (!isNaN(lon)) WeatherService.longitude = lon;
                        WeatherService.refresh();
                    }
                }
            }
        }

        // --- Suunto Apps Catalog ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.download; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: qsTr("Suunto Apps Catalog"); font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("The official catalog of ~13,100 pre-compiled Suunto Apps - " +
                                "interval timers, HR-zone displays, and thousands of " +
                                "Movescount-era community apps. This app never ships or " +
                                "downloads it (most of it is other people's own work, not " +
                                "ours to redistribute) - import your own copy from your own " +
                                "SuuntoLink installation instead. Suunto stopped updating " +
                                "this file when Movescount was retired, so any copy you have " +
                                "is the same copy - there's nothing to keep up to date. " +
                                "If you don't import it, you'll still have every app you " +
                                "create yourself in the Workout Builder - you just won't " +
                                "have this existing catalog to browse and install from too.")
                }

                Row {
                    spacing: Theme.spacingSmall
                    visible: !root.catalogChecking
                    Icon {
                        visible: root.catalogImported
                        glyph: Icons.checkCircle
                        size: 16
                        color: Theme.success
                        anchors.verticalCenter: parent.verticalCenter
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeLabel
                        text: root.catalogImported
                              ? qsTr("Imported - %1 apps available").arg(root.catalogEntries)
                              : qsTr("Not imported - only apps you create are available")
                    }
                }

                Text {
                    visible: root.catalogError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeCaption
                    text: root.catalogError
                }

                RoundedButton {
                    text: root.catalogImported ? qsTr("Import a different copy")
                                                : qsTr("Import Catalog")
                    enabled: !root.catalogImporting && !root.catalogChecking
                    onClicked: catalogFileDialog.open()
                }
            }
        }

        // --- Experimental Features. Real decision, 2026-08-11 (André, after a live BLE
        // session that same night hit real reliability trouble - see HANDOFF.md Milestone
        // 7 items 16-19): Bluetooth stays real, but opt-in and clearly labeled as still
        // being hardened, rather than part of the default cable-first Home experience.
        // "By default, only cable" - this toggle is what switches Home's own Bluetooth
        // section (HomePage.qml) on, off by default, persisted like Testing mode above. ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.watch; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: qsTr("Experimental features"); font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Bluetooth connectivity (Linux only). Still being hardened - " +
                                "cable stays the reliable default. Turning this on adds a " +
                                "\"Connect via Bluetooth\" option to the Home screen.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: DeviceService.bleExperimentEnabled
                        onToggled: DeviceService.bleExperimentEnabled = checked
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: DeviceService.bleExperimentEnabled ? qsTr("On") : qsTr("Off")
                        color: DeviceService.bleExperimentEnabled ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }

                // --- Intervals + Smart Sensor menu features (2026-08-17): one toggle each,
                // each reveals its own menu item in the side menu, matching the Android app.
                // Intervals rides the Suunto App-Zone/CustomModes mechanism (needs SuuntoLink to
                // compile), so it stays experimental; Smart Sensor is the standalone BLE HR belt.
                Item { width: 1; height: Theme.spacingSmall }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("Intervals menu (build interval workouts)")
                    font.bold: true; font.pixelSize: Theme.fontSizeBody; color: Theme.text
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: DeviceService.intervalsEnabled
                        onToggled: DeviceService.intervalsEnabled = checked
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: DeviceService.intervalsEnabled ? qsTr("On") : qsTr("Off")
                        color: DeviceService.intervalsEnabled ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
                Item { width: 1; height: Theme.spacingSmall }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("App Zone menu (build & install Suunto Apps)")
                    font.bold: true; font.pixelSize: Theme.fontSizeBody; color: Theme.text
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: DeviceService.appZoneEnabled
                        onToggled: DeviceService.appZoneEnabled = checked
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: DeviceService.appZoneEnabled ? qsTr("On") : qsTr("Off")
                        color: DeviceService.appZoneEnabled ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
                Item { width: 1; height: Theme.spacingSmall }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("Smart Sensor menu (Suunto HR belt over Bluetooth)")
                    font.bold: true; font.pixelSize: Theme.fontSizeBody; color: Theme.text
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: DeviceService.smartSensorEnabled
                        onToggled: DeviceService.smartSensorEnabled = checked
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: DeviceService.smartSensorEnabled ? qsTr("On") : qsTr("Off")
                        color: DeviceService.smartSensorEnabled ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }

                Item { width: 1; height: Theme.spacingSmall }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("Coach menu (readiness beacon + chat, v2 concept)")
                    font.bold: true; font.pixelSize: Theme.fontSizeBody; color: Theme.text
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: DeviceService.coachEnabled
                        onToggled: DeviceService.coachEnabled = checked
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: DeviceService.coachEnabled ? qsTr("On") : qsTr("Off")
                        color: DeviceService.coachEnabled ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }

                // GPS Track Pod and Suunto T6 are standalone legacy Suunto devices
                // integrated built-blind (never hardware-confirmed). Each reveals its own
                // side-menu item when turned on, off by default. NavRail gates on these.
                Item { width: 1; height: Theme.spacingSmall }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("GPS Track Pod menu (standalone Suunto GPS logger)")
                    font.bold: true; font.pixelSize: Theme.fontSizeBody; color: Theme.text
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: DeviceService.gpsTrackPodExperimentEnabled
                        onToggled: DeviceService.gpsTrackPodExperimentEnabled = checked
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: DeviceService.gpsTrackPodExperimentEnabled ? qsTr("On") : qsTr("Off")
                        color: DeviceService.gpsTrackPodExperimentEnabled ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
                Item { width: 1; height: Theme.spacingSmall }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("T6/X6 menu (legacy Suunto HR wristops)")
                    font.bold: true; font.pixelSize: Theme.fontSizeBody; color: Theme.text
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: DeviceService.suuntoT6ExperimentEnabled
                        onToggled: DeviceService.suuntoT6ExperimentEnabled = checked
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: DeviceService.suuntoT6ExperimentEnabled ? qsTr("On") : qsTr("Off")
                        color: DeviceService.suuntoT6ExperimentEnabled ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }

                // --- Mark synced workouts. Opt-in, OFF by default (André, 2026-08-16).
                // Writes the watch's own per-move synced flag after this app reads a
                // workout, so the official Suunto app / SuuntoLink treat it as already
                // synced. Deliberately spells out the tradeoff so nobody turns it on
                // without understanding the data-loss risk. ---
                Item { width: 1; height: Theme.spacingSmall }
                Text {
                    text: qsTr("Mark synced workouts as synced for Suunto app and SuuntoLink")
                    font.bold: true
                    font.pixelSize: Theme.fontSizeBody
                    color: Theme.text
                    width: parent.width
                    wrapMode: Text.WordWrap
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Once a workout has been read here, tell the watch it is already " +
                                "synced. This avoids duplicated workouts in the Suunto app and " +
                                "SuuntoLink - but it also means the workout can no longer be " +
                                "retrieved again from the watch if the Suunto app fails to keep " +
                                "it. Leave off unless you understand this tradeoff.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: DeviceService.markSyncedEnabled
                        onToggled: DeviceService.markSyncedEnabled = checked
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: DeviceService.markSyncedEnabled ? qsTr("On") : qsTr("Off")
                        color: DeviceService.markSyncedEnabled ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
            }
        }

        // --- Testing mode. Real request, 2026-08-11 (André): "add on feature on settings:
        // testing mode, where it simulates that an ambit 3 is connected, so people can test
        // it without the watch. for usability could be cool." Then, same day: "put it on the
        // bottom of the site before the about... opens a window and we can choose device,
        // based on all the characteristics we already know...always linked..and we add the
        // garmin etrex".
        //
        // "Always linked" is what makes this worth having rather than a mock: the Suunto
        // devices come from the generated capability table and the eTrex from a real folder
        // tree, so every page runs its normal code - the same decoder, encoder, round-trip
        // guard and GPX reader hardware goes through. Edits land on a sample device and are
        // thrown away when the app closes; nothing reaches a real one.
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.watch; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: qsTr("Testing mode"); font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Pretend a device is connected, so you can look around the app " +
                                "without one. Changes are made to a sample device and " +
                                "forgotten when you close the app - nothing is written to a " +
                                "real one.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: DeviceService.demoMode
                        onToggled: DeviceService.setDemoMode(checked, "")
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        // The device's name is the status: "On" alone would leave the one
                        // thing you actually want confirmed - which device - unsaid.
                        text: DeviceService.demoMode
                              ? qsTr("On - showing %1").arg(DeviceService.demoDeviceName
                                                            || qsTr("a sample device"))
                              : qsTr("Off")
                        color: DeviceService.demoMode ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
                Row {
                    spacing: Theme.spacingSmall
                    visible: DeviceService.demoMode
                    RoundedButton {
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Change device")
                        onClicked: demoPicker.open()
                    }
                }
            }

            DemoDevicePicker {
                id: demoPicker
                current: DeviceService.demoVariant
                onDeviceChosen: (variant) => DeviceService.setDemoMode(true, variant)
            }
        }

        // --- Coach (v2 concept, 2026-08-21). Two independent toggles, per André's own
        // "can we have both with a toggle?" - each backend is real code, not a stub; the
        // zero-setup default (canned chat + bundled sample catalogue) works with nothing
        // configured here at all. See coachservice.h's own header comment for what "live"
        // actually requires (a small HTTP bridge in front of wahoo-systm-mcp's stdio MCP).
        Card {
            width: parent.width
            visible: DeviceService.coachEnabled
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.coach; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text { text: qsTr("Coach"); font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                }

                Item { width: 1; height: Theme.spacingSmall }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("Chat backend: Claude API (real conversation, needs an Anthropic API key - NOT your claude.ai subscription, small per-message cost)")
                    font.bold: true; font.pixelSize: Theme.fontSizeBody; color: Theme.text
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: CoachService.chatBackend === "claude"
                        onToggled: CoachService.chatBackend = checked ? "claude" : "canned"
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: CoachService.chatBackend === "claude" ? qsTr("Claude API") : qsTr("Canned replies")
                        color: CoachService.chatBackend === "claude" ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
                Row {
                    visible: CoachService.chatBackend === "claude"
                    spacing: Theme.spacingSmall
                    RoundedTextField {
                        id: anthropicKeyField
                        width: 260
                        echoMode: TextInput.Password
                        placeholderText: CoachService.anthropicKeySet ? qsTr("Key saved (enter to replace)") : qsTr("Anthropic API key")
                    }
                    Button {
                        text: qsTr("Save")
                        enabled: anthropicKeyField.text.length > 0
                        onClicked: { CoachService.setAnthropicApiKey(anthropicKeyField.text); anthropicKeyField.text = "" }
                    }
                    Button {
                        text: qsTr("Clear")
                        visible: CoachService.anthropicKeySet
                        onClicked: CoachService.clearAnthropicApiKey()
                    }
                }

                Item { width: 1; height: Theme.spacingSmall }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    text: qsTr("Workout catalogue: live wahoo-systm-mcp (vs. the bundled offline sample)")
                    font.bold: true; font.pixelSize: Theme.fontSizeBody; color: Theme.text
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedSwitch {
                        anchors.verticalCenter: parent.verticalCenter
                        checked: CoachService.catalogueSource === "live"
                        onToggled: CoachService.catalogueSource = checked ? "live" : "sample"
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: CoachService.catalogueSource === "live" ? qsTr("Live") : qsTr("Bundled sample (55 sessions)")
                        color: CoachService.catalogueSource === "live" ? Theme.primary : Theme.mutedText
                        font.pixelSize: Theme.fontSizeBody
                    }
                }
                Row {
                    visible: CoachService.catalogueSource === "live"
                    spacing: Theme.spacingSmall
                    RoundedTextField {
                        id: mcpUrlField
                        width: 320
                        text: CoachService.systmMcpUrl
                        placeholderText: qsTr("http://127.0.0.1:PORT/workouts")
                        onEditingFinished: CoachService.systmMcpUrl = text
                    }
                }
                Text {
                    visible: CoachService.lastError.length > 0
                    width: parent.width; wrapMode: Text.WordWrap
                    text: CoachService.lastError
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeCaption
                }
            }
        }

        // --- About ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                // No icon here - unlike the other section headers, this app's own subset
                // icon font (assets/fonts/NOTICE.md) has no real "info" glyph to reuse
                // honestly, and guessing one isn't worth it for a header that's otherwise
                // just a label. Still gets the same size fix as every other header.
                Text { text: qsTr("About"); font.bold: true; font.pixelSize: Theme.fontSizeBodyLarge; color: Theme.text }
                // The Sommet "Peak" mark (SommetMark.qml) paired with the version + tagline -
                // same mark as the window/app icon. Tagline moved here 2026-08-14 when the
                // General card was removed (André); it identifies the app for the average
                // user, while the old backend status/address it shared was developer-only.
                Row {
                    spacing: Theme.spacingMedium
                    SommetMark { size: 46; anchors.verticalCenter: parent.verticalCenter }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            text: qsTr("Sommet v0.1.46")
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBody
                            font.bold: true
                        }
                        Text {
                            text: qsTr("Sommet — for Suunto Ambit")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeBody
                        }
                    }
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Independent, unofficial software - not affiliated with, " +
                                "endorsed by, or supported by Suunto or Garmin. Suunto, " +
                                "Ambit, Traverse, Kailash, Garmin and eTrex are trademarks of " +
                                "their respective owners, used here only to describe " +
                                "compatibility.")
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    // Matches LICENSE at the repo root and the Credits section of the README.
                    text: qsTr("Licensed under the GNU GPLv3, the same license as openambit, " +
                                "whose libambit this project's protocol work is checked " +
                                "against throughout. The desktop app links Qt 6 under the " +
                                "LGPLv3; the Android app uses React Native (MIT).")
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Map data © OpenStreetMap contributors, under the Open " +
                                "Database License (ODbL); tiles from CyclOSM / OpenStreetMap " +
                                "France and IGN Géoplateforme. Weather by Open-Meteo " +
                                "(CC BY 4.0). Icons: Google Material Symbols (Apache License " +
                                "2.0). GPS Track Pod support from iwanders/gps_track_pod (MIT).")
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Built on real prior work: openambit, opensportsync, " +
                                "marguslt (firmware-download recipe, gists, openmoves), " +
                                "sebchastang (published Suunto App Zone interval-training " +
                                "scripts), the Suunto forum community, and wanarun.net. " +
                                "Full credits and licenses in the project README.")
                }
            }
        }


        ThemedDialog {
            id: intervalsIcuDialog
            title: qsTr("Intervals.icu")
            modal: true
            // Declared inside the scrolled content Column, so centerIn:parent lands at that
            // Column's off-screen origin ("nothing happens", André 2026-08-16). Center on the
            // window overlay instead - same fix as HomePage's passkey dialog.
            parent: Overlay.overlay
            anchors.centerIn: Overlay.overlay
            standardButtons: Dialog.Close

            onOpened: {
                athleteIdField.text = ConnectionsService.intervalsIcuAthleteId
                apiKeyField.text = ConnectionsService.intervalsIcuConnected
                    ? ConnectionsService.intervalsIcuApiKey() : ""
            }

            Column {
                width: 320
                spacing: Theme.spacingSmall

                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Athlete ID and API key from intervals.icu → Settings → " +
                                "Developer Settings. Stored locally on this computer, not " +
                                "sent anywhere except intervals.icu itself.")
                }
                RoundedTextField {
                    id: athleteIdField
                    width: parent.width
                    placeholderText: qsTr("Athlete ID (e.g. i12345)")
                }
                RoundedTextField {
                    id: apiKeyField
                    width: parent.width
                    placeholderText: qsTr("API key")
                    echoMode: TextInput.Password
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: qsTr("Save")
                        enabled: athleteIdField.text.length > 0 && apiKeyField.text.length > 0
                        onClicked: {
                            ConnectionsService.saveIntervalsIcu(athleteIdField.text, apiKeyField.text)
                            intervalsIcuDialog.close()
                        }
                    }
                    RoundedButton {
                        text: qsTr("Disconnect")
                        visible: ConnectionsService.intervalsIcuConnected
                        onClicked: {
                            ConnectionsService.disconnectIntervalsIcu()
                            intervalsIcuDialog.close()
                        }
                    }
                }
                // --- Sync options (André, 2026-08-18): the intervals.icu "sync menu" - pick
                // what to import/export per data type, then "Sync now" runs the enabled ones.
                // Manual + toggles, not background. Shown once connected.
                Column {
                    visible: ConnectionsService.intervalsIcuConnected
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Rectangle { width: parent.width; height: 1; color: Qt.rgba(Theme.mutedText.r, Theme.mutedText.g, Theme.mutedText.b, 0.25) }

                    Text {
                        text: qsTr("Sync options")
                        font.bold: true
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeLabel
                    }

                    // One reusable toggle row (label + switch).
                    component SyncToggle: Row {
                        width: parent.width
                        property alias label: rowLabel.text
                        property bool value: false
                        signal toggled(bool checked)
                        Text {
                            id: rowLabel
                            width: parent.width - sw.width - Theme.spacingSmall
                            wrapMode: Text.WordWrap
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeCaption
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        RoundedSwitch {
                            id: sw
                            anchors.verticalCenter: parent.verticalCenter
                            checked: parent.value
                            onToggled: parent.toggled(checked)
                        }
                    }

                    SyncToggle {
                        label: qsTr("Import gear (bikes, shoes, parts)")
                        value: ConnectionsService.syncImportGear
                        onToggled: (checked) => ConnectionsService.syncImportGear = checked
                    }
                    SyncToggle {
                        label: qsTr("Keep the watch's activity level in sync")
                        value: ConnectionsService.syncActivityLevel
                        onToggled: (checked) => ConnectionsService.syncActivityLevel = checked
                    }
                    SyncToggle {
                        label: qsTr("Write my profile to the watch (weight, height, HR, class) — cable only")
                        value: ConnectionsService.syncStatsToWatch
                        onToggled: (checked) => ConnectionsService.syncStatsToWatch = checked
                    }

                    SyncToggle {
                        label: qsTr("Import activities into the app (Zwift, manual, other devices)")
                        value: ConnectionsService.syncImportActivities
                        onToggled: (checked) => ConnectionsService.syncImportActivities = checked
                    }
                    // How far back the import reaches - the user's call (André: "let user decide").
                    Row {
                        width: parent.width
                        visible: ConnectionsService.syncImportActivities
                        spacing: Theme.spacingSmall
                        Text {
                            text: qsTr("Import range")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            anchors.verticalCenter: parent.verticalCenter
                        }
                        RoundedComboBox {
                            id: importRange
                            width: 150
                            anchors.verticalCenter: parent.verticalCenter
                            model: [qsTr("Last 30 days"), qsTr("Last 90 days"), qsTr("Everything")]
                            // Map selection <-> days (0 = everything).
                            function daysFor(i) { return i === 0 ? 30 : (i === 1 ? 90 : 0) }
                            function indexFor(d) { return d === 30 ? 0 : (d === 90 ? 1 : 2) }
                            currentIndex: indexFor(ConnectionsService.syncImportDays)
                            onActivated: (i) => ConnectionsService.syncImportDays = daysFor(i)
                        }
                    }

                    SyncToggle {
                        label: qsTr("Export the watch's activities to intervals.icu")
                        value: ConnectionsService.syncExportActivities
                        onToggled: (checked) => ConnectionsService.syncExportActivities = checked
                    }

                    RoundedButton {
                        width: parent.width
                        enabled: !GearService.loading && !ActivityService.loading
                        text: (GearService.loading || ActivityService.loading)
                              ? qsTr("Syncing…") : qsTr("Sync now")
                        onClicked: root.intervalsSyncNow()
                    }

                    Text {
                        id: syncStatus
                        width: parent.width
                        visible: text.length > 0
                        wrapMode: Text.WordWrap
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                        text: ""
                    }
                    Connections {
                        target: GearService
                        function onImportFinished(count) {
                            root.syncAppend(qsTr("Gear"), true, qsTr("%1 item(s)").arg(count))
                        }
                        function onLastErrorChanged() {
                            if (GearService.lastError.length > 0)
                                root.syncAppend(qsTr("Gear"), false, GearService.lastError)
                        }
                    }
                    Connections {
                        target: ActivityService
                        function onImportFinished(count) {
                            root.syncAppend(qsTr("Activities"), true, qsTr("%1 imported").arg(count))
                        }
                        function onImportError(message) {
                            root.syncAppend(qsTr("Activities"), false, message)
                        }
                        function onExportFinished(uploaded, failed) {
                            root.syncAppend(qsTr("Export"), failed === 0,
                                failed === 0 ? qsTr("%1 uploaded").arg(uploaded)
                                             : qsTr("%1 uploaded, %2 failed").arg(uploaded).arg(failed))
                        }
                        function onExportError(message) {
                            root.syncAppend(qsTr("Export"), false, message)
                        }
                    }
                }
            }
        }

        ThemedDialog {
            id: runalyzeDialog
            title: qsTr("Runalyze")
            modal: true
            // Nested in the scrolled Column - center on the window overlay so it isn't
            // positioned at the Column's off-screen origin (André, 2026-08-16).
            parent: Overlay.overlay
            anchors.centerIn: Overlay.overlay
            standardButtons: Dialog.Close

            onOpened: {
                runalyzeApiKeyField.text = ConnectionsService.runalyzeConnected
                    ? ConnectionsService.runalyzeApiKey() : ""
            }

            Column {
                width: 320
                spacing: Theme.spacingSmall

                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("API key from your Runalyze account. Stored locally on " +
                                "this computer, not sent anywhere except runalyze.com " +
                                "itself.")
                }
                RoundedTextField {
                    id: runalyzeApiKeyField
                    width: parent.width
                    placeholderText: qsTr("API key")
                    echoMode: TextInput.Password
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: qsTr("Save")
                        enabled: runalyzeApiKeyField.text.length > 0
                        onClicked: {
                            ConnectionsService.saveRunalyze(runalyzeApiKeyField.text)
                            runalyzeDialog.close()
                        }
                    }
                    RoundedButton {
                        text: qsTr("Disconnect")
                        visible: ConnectionsService.runalyzeConnected
                        onClicked: {
                            ConnectionsService.disconnectRunalyze()
                            runalyzeDialog.close()
                        }
                    }
                }
            }
        }

        ThemedDialog {
            id: stravaDialog
            title: qsTr("Strava")
            modal: true
            // Nested in the scrolled Column - center on the window overlay so it isn't
            // positioned at the Column's off-screen origin (André, 2026-08-16).
            parent: Overlay.overlay
            anchors.centerIn: Overlay.overlay
            standardButtons: Dialog.Close

            onOpened: {
                stravaClientIdField.text = ConnectionsService.stravaClientId
                stravaClientSecretField.text = ConnectionsService.stravaConnected
                    ? ConnectionsService.stravaClientSecret() : ""
            }

            Column {
                width: 320
                spacing: Theme.spacingSmall

                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Real OAuth2, not a personal API key like the other two - " +
                                "register your own app at strava.com/settings/api first " +
                                "(Authorization Callback Domain: localhost), then paste its " +
                                "Client ID and Client Secret below. Connect opens Strava in " +
                                "your browser; approving there sends you back here " +
                                "automatically.")
                }
                RoundedTextField {
                    id: stravaClientIdField
                    width: parent.width
                    placeholderText: qsTr("Client ID")
                }
                RoundedTextField {
                    id: stravaClientSecretField
                    width: parent.width
                    placeholderText: qsTr("Client Secret")
                    echoMode: TextInput.Password
                }
                Text {
                    visible: ConnectionsService.stravaConnecting
                    text: qsTr("Waiting for you to approve in the browser...")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                }
                Text {
                    visible: !ConnectionsService.stravaConnecting
                             && ConnectionsService.stravaError.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeLabel
                    text: ConnectionsService.stravaError
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: ConnectionsService.stravaConnecting
                            ? qsTr("Connecting...") : qsTr("Connect")
                        enabled: !ConnectionsService.stravaConnecting
                                 && stravaClientIdField.text.length > 0
                                 && stravaClientSecretField.text.length > 0
                        onClicked: ConnectionsService.connectStrava(
                            stravaClientIdField.text, stravaClientSecretField.text)
                    }
                    RoundedButton {
                        text: qsTr("Disconnect")
                        visible: ConnectionsService.stravaConnected
                        onClicked: {
                            ConnectionsService.disconnectStrava()
                            stravaDialog.close()
                        }
                    }
                }
            }
        }
    }
}
