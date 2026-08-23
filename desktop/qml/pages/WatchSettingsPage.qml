import QtQuick
import QtQuick.Controls
import AmbitApp

// Watch settings - its own sidebar page (moved out of Settings 2026-08-14, André: "create a
// new menu/window for it, after sports modes"). These are the settings written to the watch
// itself over the cable (SettingsWriteService), grouped by the same screens SuuntoLink uses.
// Suunto-only; hidden for Garmin. The home-location helpers below serve the Kailash
// HomeLocation row inside the card - moved here with it, unchanged.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    // Auto-read on page load...
    Component.onCompleted: root.autoRead()

    property bool _wasConnected: false
    function autoRead() {
        // Real, 2026-08-22: this page's SettingsWriteService only speaks the Ambit3 SBEM
        // path - calling it against a connected Ambit1/2 got a real, correct "ok: false"
        // (that family predates SBEM entirely) that showed as a scary generic error banner
        // instead of the simple "not available on this watch" it actually is. Guard it the
        // same way Garmin already is, rather than ever firing a request known to fail.
        if (!HomeViewModel.isGarmin && DeviceService.deviceInfoOk && DeviceCapabilities.supportsWatchSettings) {
            SettingsWriteService.device = HomeViewModel.isKailash ? "kailash" : "";
            SettingsWriteService.refresh();
        }
    }
    // ...and again the moment a watch connects while this page is already open, so the read
    // is automatic on connect over either transport - never a manual button (André,
    // 2026-08-18). Guarded to the not-connected -> connected transition so the 10s device
    // poll (which also fires deviceInfoChanged for battery, etc.) doesn't re-read on a loop.
    Connections {
        target: DeviceService
        function onDeviceInfoChanged() {
            if (DeviceService.deviceInfoOk && !root._wasConnected) {
                root._wasConnected = true;
                root.autoRead();
            } else if (!DeviceService.deviceInfoOk) {
                root._wasConnected = false;
            }
        }
    }

    // The longitude half of Kailash's home location. The settings list is flat, so the
    // latitude row (which now presents the pair) has to look its partner up by key.
    readonly property real homeLongitudeValue: {
        const list = SettingsWriteService.settings
        for (let i = 0; i < list.length; i++) {
            if (list[i].path && list[i].path.endsWith("HomeLocation.Longitude"))
                return list[i].value
        }
        return 0
    }

    HomeLocationDialog {
        id: homePicker
        onPicked: (lat, lon) => {
            // Written one after the other, not both at once: SettingsWriteService handles a
            // single write at a time (writingKey), and firing two together would race for
            // the same USB connection. The longitude is queued and sent as soon as the
            // latitude write reports done.
            root.pendingHomeLongitude = lon
            SettingsWriteService.writeSetting("home_latitude", lat)
        }
    }

    // NaN means "nothing queued" - a real coordinate of 0 is a legitimate value (the Gulf of
    // Guinea), so 0 cannot be the sentinel here.
    property real pendingHomeLongitude: NaN

    Connections {
        target: SettingsWriteService
        function onWritingKeyChanged() {
            if (SettingsWriteService.writingKey !== "")
                return
            if (isNaN(root.pendingHomeLongitude))
                return
            const lon = root.pendingHomeLongitude
            root.pendingHomeLongitude = NaN
            SettingsWriteService.writeSetting("home_longitude", lon)
        }
    }

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 480
        spacing: Theme.spacingLarge

        // Real, 2026-08-22 (Ambit1/2): read-only personal settings via /api/legacy/settings
        // (tools/legacy_link.py -> the vendored openambit driver - see
        // tools/vendor/openambit_libambit/README.md). No write UI here: the wire format for
        // writing these has never been captured against real hardware in this project (see
        // the ambit-app-ambit12-settings-write memory) - showing an editable field that
        // can't actually be saved would be worse than this being read-only for now.
        Card {
            id: legacySettingsCard
            width: parent.width
            visible: HomeViewModel.connected && !DeviceCapabilities.supportsWatchSettings

            property bool loading: false
            property string error: ""
            property var data: null

            function refresh() {
                legacySettingsCard.loading = true
                legacySettingsCard.error = ""
                const xhr = new XMLHttpRequest()
                xhr.onreadystatechange = function() {
                    if (xhr.readyState !== XMLHttpRequest.DONE) return
                    legacySettingsCard.loading = false
                    let d = null
                    try { d = JSON.parse(xhr.responseText) } catch (e) {}
                    if (!d || !d.ok) {
                        legacySettingsCard.data = null
                        legacySettingsCard.error = (d && d.error) ? d.error : qsTr("Couldn't read settings from the watch.")
                        return
                    }
                    legacySettingsCard.data = d
                }
                xhr.open("GET", "http://127.0.0.1:8766/api/legacy/settings")
                xhr.send()
            }

            Connections {
                target: DeviceService
                function onDeviceInfoChanged() {
                    if (legacySettingsCard.visible && DeviceService.deviceInfoOk && legacySettingsCard.data === null && !legacySettingsCard.loading)
                        legacySettingsCard.refresh()
                }
            }
            onVisibleChanged: if (visible && data === null && !loading) refresh()

            Column {
                width: parent.width
                spacing: Theme.spacingMedium

                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.watch; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        text: qsTr("%1 Settings").arg(HomeViewModel.deviceDisplayName)
                        font.bold: true
                        font.pixelSize: Theme.fontSizeBodyLarge
                        color: Theme.text
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                Text {
                    visible: legacySettingsCard.loading
                    color: Theme.mutedText
                    text: qsTr("Reading settings off the watch...")
                }

                ErrorBanner {
                    width: parent.width
                    detail: legacySettingsCard.error
                    context: qsTr("reading legacy watch settings")
                    canRetry: true
                    onRetry: legacySettingsCard.refresh()
                }

                Grid {
                    visible: legacySettingsCard.data !== null
                    width: parent.width
                    columns: 2
                    columnSpacing: Theme.spacingLarge
                    rowSpacing: Theme.spacingSmall
                    readonly property var d: legacySettingsCard.data || {}

                    Text { color: Theme.mutedText; text: qsTr("Weight") }
                    Text { color: Theme.text; text: parent.d.weight_kg !== undefined ? qsTr("%1 kg").arg(parent.d.weight_kg) : "-" }
                    Text { color: Theme.mutedText; text: qsTr("Height") }
                    Text { color: Theme.text; text: parent.d.length_cm !== undefined ? qsTr("%1 cm").arg(parent.d.length_cm) : "-" }
                    Text { color: Theme.mutedText; text: qsTr("Birth year") }
                    Text { color: Theme.text; text: parent.d.birthyear !== undefined ? String(parent.d.birthyear) : "-" }
                    Text { color: Theme.mutedText; text: qsTr("Max HR") }
                    Text { color: Theme.text; text: parent.d.max_hr !== undefined ? qsTr("%1 bpm").arg(parent.d.max_hr) : "-" }
                    Text { color: Theme.mutedText; text: qsTr("Rest HR") }
                    Text { color: Theme.text; text: parent.d.rest_hr !== undefined ? qsTr("%1 bpm").arg(parent.d.rest_hr) : "-" }
                    Text { color: Theme.mutedText; text: qsTr("Gender") }
                    Text { color: Theme.text; text: parent.d.is_male === undefined ? "-" : (parent.d.is_male ? qsTr("Male") : qsTr("Female")) }
                }
            }
        }

        Card {
            width: parent.width
            visible: !HomeViewModel.isGarmin && DeviceCapabilities.supportsWatchSettings
            Column {
                id: settingsColumn
                width: parent.width
                spacing: Theme.spacingMedium

                // Real, 2026-08-10: the curated table grew from 18 to 34 fields once the
                // Unit and Personal screens were covered, and one flat run of 34 rows is
                // not a settings screen anyone can use. settings_write.py now reports the
                // `screen` each field lives on - the same three SuuntoLink itself groups
                // them into, which is the grouping the watch's owner already knows - so
                // the grouping needs no second table here to drift out of sync. Fields
                // with no screen (Kailash's whole table) fall into
                // "other" and are still shown.
                function rowsForScreen(name) {
                    const out = [];
                    for (const s of SettingsWriteService.settings) {
                        if ((s.screen ? s.screen : "other") === name) out.push(s);
                    }
                    return out;
                }

                Row {
                    spacing: Theme.spacingSmall
                    Icon { glyph: Icons.watch; size: 20; color: Theme.text; anchors.verticalCenter: parent.verticalCenter }
                    Text {
                        // Real, 2026-08-09 ("it says Ambit3 settings, please link this to
                        // the name of the device, since tomorrow we will support more
                        // devices") - was hardcoded to one of two fixed strings; now reads
                        // the real connected device's own name (HomeViewModel.
                        // deviceDisplayName, the same one Home's own device card already
                        // shows) so a future third/fourth supported device needs no new
                        // branch here at all.
                        text: qsTr("%1 Settings").arg(HomeViewModel.deviceDisplayName)
                        font.bold: true
                        font.pixelSize: Theme.fontSizeBodyLarge
                        color: Theme.text
                        anchors.verticalCenter: parent.verticalCenter
                    }
                }

                Text {
                    visible: SettingsWriteService.loading && SettingsWriteService.settings.length === 0
                    color: Theme.mutedText
                    text: qsTr("Reading settings off the watch...")
                }

                ErrorBanner {
                    width: parent.width
                    detail: SettingsWriteService.ok ? "" : SettingsWriteService.lastError
                    context: qsTr("reading or writing watch settings")
                }

                // --- Orbital data - real, 2026-08-10 (André: "let's enable by default for
                // traverse, traverse and kailash. on kailash settings, give the option to
                // disable it, name it ephemeris gps only with a little i that shows").
                //
                // Shown only when the WATCH itself declares a GlonassSGEE region
                // (DeviceService.glonassSupported, answered by sgee.py's glonass_status),
                // never from a model list - Suunto's own Devices.xml hardcodes three
                // models and forgot the Kailash, which is why that watch has never had
                // GLONASS ephemeris from any Suunto software.
                //
                // This is an APP preference, not a field on the watch, which is why it
                // sits in its own titled group rather than among the real device settings
                // the Repeater below renders from the watch's own blob.
                Column {
                    id: orbitalGroup
                    width: parent.width
                    spacing: Theme.spacingSmall
                    visible: DeviceService.glonassSupported
                    property bool infoOpen: false

                    Text {
                        text: qsTr("Orbital data")
                        color: Theme.mutedText
                        font.bold: true
                        font.pixelSize: Theme.fontSizeLabel
                        topPadding: Theme.spacingSmall
                    }

                    Row {
                        spacing: Theme.spacingSmall
                        RoundedCheckBox {
                            anchors.verticalCenter: parent.verticalCenter
                            text: qsTr("Ephemeris GPS only")
                            checked: DeviceService.ephemerisGpsOnly
                            onToggled: DeviceService.ephemerisGpsOnly = checked
                        }
                        // The "little i" - tap to expand, tap again to collapse. Not a hover
                        // tooltip: hover doesn't exist on Android, and this same pattern has
                        // to work identically there.
                        Rectangle {
                            anchors.verticalCenter: parent.verticalCenter
                            width: 18; height: 18; radius: 9
                            color: "transparent"
                            border.width: 1
                            border.color: orbitalGroup.infoOpen ? Theme.primary : Theme.mutedText
                            Text {
                                anchors.centerIn: parent
                                text: "i"
                                font.pixelSize: Theme.fontSizeCaption
                                font.bold: true
                                color: orbitalGroup.infoOpen ? Theme.primary : Theme.mutedText
                            }
                            MouseArea {
                                anchors.fill: parent
                                cursorShape: Qt.PointingHandCursor
                                onClicked: orbitalGroup.infoOpen = !orbitalGroup.infoOpen
                            }
                        }
                    }

                    Text {
                        visible: orbitalGroup.infoOpen
                        width: parent.width
                        wrapMode: Text.WordWrap
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                        text: qsTr("This watch can also use GLONASS satellites, and has its " +
                                    "own storage for their orbital data. Suunto's software " +
                                    "never sends it to this model, so those satellites start " +
                                    "cold every time. AmbitApp sends both GPS and GLONASS " +
                                    "orbital data, which can speed up getting a fix. Tick " +
                                    "this to send GPS only.")
                    }
                }

                Repeater {
                    model: [
                        { screen: "general",  title: qsTr("General settings") },
                        { screen: "units",    title: qsTr("Unit settings") },
                        { screen: "personal", title: qsTr("Personal settings") },
                        { screen: "other",    title: qsTr("Other") }
                    ]
                    delegate: Column {
                        id: screenGroup
                        width: parent.width
                        spacing: Theme.spacingSmall
                        readonly property var rows: settingsColumn.rowsForScreen(modelData.screen)
                        readonly property string groupTitle: modelData.title
                        visible: rows.length > 0

                        Text {
                            text: screenGroup.groupTitle
                            color: Theme.mutedText
                            font.bold: true
                            font.pixelSize: Theme.fontSizeLabel
                            topPadding: Theme.spacingSmall
                        }

                        Repeater {
                    model: screenGroup.rows
                    // Real, 2026-08-10 (André: "everything visual you can inspire on suunto
                    // link... it is what the watch shows"). SuuntoLink puts the field name
                    // ABOVE its control, stacks 2-3 choices as vertical radio buttons, uses a
                    // checkbox for a standalone boolean and a dropdown only for a long list -
                    // so that is what this renders, driven by the `control` hint
                    // settings_write.py now reports (AMBIT3_DISPLAY) rather than by guessing
                    // from the raw type. A device with no display metadata (Kailash) falls
                    // back to the old kind-based rendering, unchanged.
                    delegate: Column {
                        id: settingRow
                        width: parent.width
                        spacing: 4
                        bottomPadding: Theme.spacingSmall

                        readonly property var item: modelData
                        readonly property bool editable: item.writable !== false
                        readonly property bool busy: SettingsWriteService.writingKey === item.key
                        readonly property string unitSuffix: item.unit ? item.unit : ""
                        readonly property var choices: item.choices ? item.choices : []
                        readonly property bool hasRange:
                            item.min !== undefined && item.min !== null
                            && item.max !== undefined && item.max !== null
                        // André, 2026-08-11: "remove the home latitude/longitude, call it:
                        // home coordinates". The watch stores them as ONE grouped field
                        // (entry 0x36, Latitude/Longitude sub-fields), so showing two rows
                        // was the app splitting something the device keeps whole. The
                        // latitude row now presents the pair and the longitude row is
                        // hidden - it is still read and still written, just not listed
                        // twice.
                        readonly property bool isHomeCoord:
                            item.path.endsWith("HomeLocation.Latitude")
                        readonly property bool isHomeCoordPartner:
                            item.path.endsWith("HomeLocation.Longitude")
                        // SuuntoLink's own field name where we have it; otherwise the old
                        // "display_dark" -> "Display dark" formatter, still used for Kailash.
                        readonly property string label: {
                            if (isHomeCoord)
                                return qsTr("Home coordinates");
                            if (item.label)
                                return item.label;
                            const parts = item.key.split("_");
                            parts[0] = parts[0].charAt(0).toUpperCase() + parts[0].slice(1);
                            return parts.join(" ");
                        }
                        visible: !isHomeCoordPartner
                        height: isHomeCoordPartner ? 0 : implicitHeight
                        readonly property string control: {
                            if (item.control)
                                return item.control;
                            if (item.kind === "bool") return "checkbox";
                            if (item.kind === "enum") return "dropdown";
                            if (item.kind === "text") return "text";
                            if (item.kind === "number")
                                return isHomeCoord ? "coord" : (hasRange ? "slider" : "readonly");
                            return "readonly";
                        }

                        function commit(v) { SettingsWriteService.writeSetting(item.key, v) }

                        Row {
                            spacing: Theme.spacingSmall
                            Text {
                                text: settingRow.label
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeBody
                                font.bold: true
                            }
                            Text {
                                visible: settingRow.busy
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("saving...")
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                                font.italic: true
                            }
                        }

                        // --- radio: SuuntoLink stacks its 2-3 choices vertically ---
                        Column {
                            visible: settingRow.control === "radio" && settingRow.editable
                            spacing: 0
                            Repeater {
                                model: settingRow.choices
                                delegate: RoundedRadioButton {
                                    // autoExclusive:false + onClicked, never a `checked`
                                    // binding fighting QQC2's own exclusivity - the same
                                    // pattern every other exclusive choice on this page uses.
                                    autoExclusive: false
                                    checked: modelData.value === settingRow.item.value
                                    text: modelData.label
                                    enabled: !settingRow.busy
                                    onClicked: settingRow.commit(modelData.value)
                                }
                            }
                        }

                        RoundedCheckBox {
                            visible: settingRow.control === "checkbox" && settingRow.editable
                            checked: settingRow.item.value === 1 || settingRow.item.value === true
                            enabled: !settingRow.busy
                            onToggled: settingRow.commit(checked ? 1 : 0)
                        }

                        RoundedComboBox {
                            visible: settingRow.control === "dropdown" && settingRow.editable
                            width: 260
                            model: settingRow.choices
                            textRole: "label"
                            valueRole: "value"
                            enabled: !settingRow.busy
                            currentIndex: {
                                for (let i = 0; i < settingRow.choices.length; i++) {
                                    if (settingRow.choices[i].value === settingRow.item.value)
                                        return i;
                                }
                                return -1;
                            }
                            onActivated: settingRow.commit(currentValue)
                        }

                        Row {
                            visible: settingRow.control === "slider" && settingRow.editable
                            spacing: 8
                            RoundedSlider {
                                anchors.verticalCenter: parent.verticalCenter
                                width: 200
                                // Bindings are evaluated for EVERY setting row, not just the
                                // ones where this slider is visible - so on an enum or bool
                                // field, which has no min/max at all, these read undefined
                                // and Qt logs "Unable to assign [undefined] to double" on
                                // every re-read. The values are unused when hidden; the
                                // fallbacks exist purely to keep the binding well-typed.
                                from: settingRow.item.min !== undefined ? settingRow.item.min : 0
                                to: settingRow.item.max !== undefined ? settingRow.item.max : 100
                                value: settingRow.item.value !== undefined ? settingRow.item.value : 0
                                enabled: !settingRow.busy
                                onMoved: settingRow.commit(Math.round(value))
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: settingRow.item.value + " " + settingRow.unitSuffix
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeLabel
                            }
                        }

                        // --- number / year / text: typed, then committed with Set, so a
                        // half-typed value is never sent to the watch ---
                        Row {
                            visible: (settingRow.control === "number"
                                      || settingRow.control === "year"
                                      || settingRow.control === "text") && settingRow.editable
                            spacing: 8
                            RoundedTextField {
                                id: valueField
                                anchors.verticalCenter: parent.verticalCenter
                                width: settingRow.control === "text" ? 140 : 90
                                text: String(settingRow.item.value)
                                enabled: !settingRow.busy
                            }
                            Text {
                                visible: settingRow.unitSuffix.length > 0
                                anchors.verticalCenter: parent.verticalCenter
                                text: settingRow.unitSuffix
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeBody
                            }
                            RoundedButton {
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Set")
                                enabled: !settingRow.busy
                                onClicked: {
                                    if (settingRow.control === "text") {
                                        settingRow.commit(valueField.text);
                                        return;
                                    }
                                    const parsed = parseFloat(valueField.text);
                                    if (isNaN(parsed)) return;
                                    settingRow.commit(parsed);
                                }
                            }
                        }

                        // --- compass declination: SuuntoLink's own "Use compass declination"
                        // checkbox, then a West/East choice and a 0-90 magnitude. On the wire
                        // this is ONE signed float32 in radians with East positive, and Off is
                        // simply 0.0 - there is no separate enable flag in the schema at all
                        // (checked: the descriptor has exactly one declination field, and every
                        // read before the first write in `ambit3declination` shows 0.0). The
                        // tool converts degrees<->radians, so this only deals in degrees.
                        Column {
                            id: declRow
                            visible: settingRow.control === "declination"
                            spacing: 4
                            property bool useDecl: settingRow.item.value !== 0
                            property bool west: settingRow.item.value < 0
                            function send() {
                                if (!useDecl) { settingRow.commit(0); return; }
                                const mag = Math.abs(parseFloat(declField.text));
                                if (isNaN(mag)) return;
                                settingRow.commit(west ? -mag : mag);
                            }
                            RoundedCheckBox {
                                text: qsTr("Use compass declination")
                                checked: declRow.useDecl
                                enabled: !settingRow.busy
                                onToggled: { declRow.useDecl = checked; if (!checked) declRow.send(); }
                            }
                            Row {
                                visible: declRow.useDecl
                                spacing: 8
                                RoundedRadioButton {
                                    autoExclusive: false
                                    text: qsTr("West")
                                    checked: declRow.west
                                    enabled: !settingRow.busy
                                    onClicked: { declRow.west = true; declRow.send(); }
                                }
                                RoundedRadioButton {
                                    autoExclusive: false
                                    text: qsTr("East")
                                    checked: !declRow.west
                                    enabled: !settingRow.busy
                                    onClicked: { declRow.west = false; declRow.send(); }
                                }
                                RoundedTextField {
                                    id: declField
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: 70
                                    text: Math.abs(settingRow.item.value).toFixed(1)
                                    enabled: !settingRow.busy
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: "°"
                                    color: Theme.mutedText
                                    font.pixelSize: Theme.fontSizeBody
                                }
                                RoundedButton {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: qsTr("Set")
                                    enabled: !settingRow.busy
                                    onClicked: declRow.send()
                                }
                            }
                        }

                        // Kailash's HomeLocation, as a place rather than two numbers -
                        // see HomeLocationDialog.qml. The coordinates stay visible next to
                        // the button, which is what "then show the coordinates on the
                        // settings side" asked for.
                        Row {
                            visible: settingRow.control === "coord"
                            spacing: Theme.spacingSmall

                            RoundedButton {
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Pick on a map")
                                enabled: !settingRow.busy
                                onClicked: {
                                    homePicker.latitude = settingRow.item.value
                                    homePicker.longitude = root.homeLongitudeValue
                                    homePicker.open()
                                }
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("%1, %2").arg(settingRow.item.value.toFixed(6))
                                      .arg(root.homeLongitudeValue.toFixed(6))
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeBody
                            }
                        }

                        // Read-only: a field with no write path (Kailash's own
                        // enabled_navigation_systems), or a number with no confirmed range
                        // to build an editor from.
                        Text {
                            visible: settingRow.control === "readonly" || !settingRow.editable
                            // Show the enum choice's label when this value maps to one (a units
                            // mode, a language, etc.); only fall back to the raw number + unit
                            // suffix when there's no matching choice. Showing the raw value here
                            // was the "zeros on units" bug - André 2026-08-16.
                            text: {
                                for (let i = 0; i < settingRow.choices.length; i++)
                                    if (settingRow.choices[i].value === settingRow.item.value)
                                        return settingRow.choices[i].label
                                return settingRow.item.value + (settingRow.unitSuffix.length
                                                               ? " " + settingRow.unitSuffix : "")
                            }
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeBody
                        }

                        // Why a field is not editable, when the backend can say. A row that
                        // is simply greyed out reads as broken; one that says the units mode
                        // owns it, and how to take it back, reads as the watch's own rule -
                        // which it is.
                        Text {
                            visible: !settingRow.editable
                                     && settingRow.item.note !== undefined
                                     && settingRow.item.note.length > 0
                            width: parent.width
                            wrapMode: Text.WordWrap
                            text: settingRow.item.note ? settingRow.item.note : ""
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                        }
                    }
                }
            }
        }
    }
}
