import QtQuick
import QtQuick.Controls
import AmbitApp

// Real, 2026-08-08 ("This weekend we will full debug the two watches and built the apps" -
// full-throttle continuation of the same session's CustomModes work). No longer a future
// placeholder: renaming a mode, editing Autolap/HR limits/pod search, and changing which
// data a display row shows are all real, hardware-confirmed capabilities as of this
// session (custom_modes_andre.md) - CustomModesService wraps the same backend endpoints
// already live-verified over real HTTP. Still gated behind FeatureFlags.sportModes (see
// this file's own git history) so the flag flip stays the single point of "is this ready
// to ship," matching AMBITAPP_SPEC.md's own "no redesign required later" design.
//
// Real, 2026-08-09 ("Full SuuntoLink-style redesign"): restructured from a single
// expand-in-place card list into a real List<->Detail flow (same pattern
// ActivitiesPage.qml's own selectedActivity already uses), matching SuuntoLink's own real
// "EDIT SPORT MODE" screen (assets/ambit3 pcap/v2/screens sports modes/8displaysmax.JPG) -
// a watch-face preview filmstrip through the mode's real screens (custom_modes.py's own
// screenNumber/isBuiltIn, so built-in system screens like Compass/Map are shown but not
// numbered as if they were user screens), and a real "SELECT DATA FOR ROW" picker
// (display1row1graphoptions.JPG) that now includes real Suunto App search/install
// alongside the existing field-type list. Adapted from SuuntoLink's own one-screen-at-a-
// time mobile paging to a horizontal filmstrip - the same real information and actions,
// fitted to this app's much wider desktop layout rather than a literal phone-screen clone.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: (root.selectedMode ? detailColumn.height : listColumn.height) + Theme.spacingLarge * 2
    clip: true

    property string selectedModeName: ""
    readonly property var selectedMode: {
        for (const m of CustomModesService.modes) {
            if (m.name === selectedModeName) return m
        }
        return null
    }
    readonly property int selectedModeIndex: {
        const modes = CustomModesService.modes
        for (let i = 0; i < modes.length; i++) {
            if (modes[i].name === selectedModeName) return i
        }
        return -1
    }
    property int currentDisplayIndex: 0
    // Staged edits belong to the mode they were made on, but they only get a mode name at
    // save time - so switching modes with edits pending would apply mode A's changes to
    // mode B. Dropping them here is the safe reading: nothing has been sent to the watch,
    // and silently retargeting them would be the one outcome nobody wants.
    onSelectedModeNameChanged: {
        currentDisplayIndex = 0
        root.pendingEdits = []
    }

    // Real, confirmed bits only - see custom_modes_andre.md's "Resolves hrbelt_and_pods"
    // section. Bit 0x0004 is deliberately absent: confirmed present on the reference
    // watch's own baseline but never isolated to one specific pod, so there's nothing
    // honest to label it with.
    // `cap` gates each pod on this watch's own capability record ("" = always available, like
    // the HR belt). A Traverse reports supportsFootPod/BikePod/PowerPod=false, so only the HR
    // belt shows for it - it has no ANT+ pod support at all. André, 2026-08-17.
    // Cadence (0x0080) is what makes Cycling's real 0x08C3 add up (0x0800 bike | 0x0080 cadence
    // | 0x0040 power | 0x0003); gated with the bike pod so it shows on ANT+ watches only.
    // 0x0004 is deliberately absent - it is UseAccelerometer, which SuuntoLink derives from the
    // sport itself and never offers as a checkbox.
    readonly property var podBits: [
        { bit: 0x0001, label: qsTr("HR belt"),   cap: "" },
        { bit: 0x0040, label: qsTr("Power pod"), cap: "supportsPowerPod" },
        { bit: 0x0080, label: qsTr("Cadence"),   cap: "supportsBikePod" },
        { bit: 0x0100, label: qsTr("Foot pod"),  cap: "supportsFootPod" },
        { bit: 0x0800, label: qsTr("Bike pod"),  cap: "supportsBikePod" },
    ]

    // Real, 2026-08-09 ("Implement the sport modes like suunto link") - SuuntoLink's own
    // real Sport Modes list (assets/ambit3 pcap/v2/screens sports modes/sportsmodes.JPG)
    // gives every mode a colored circular badge. This app's real CustomModes data has no
    // sport-category/color field of its own to read (each mode is just a free-text name -
    // custom_modes_andre.md) - this table is a presentational-only convenience keyed on
    // this reference watch's own real mode names (confirmed identical to SuuntoLink's own
    // defaults: Cycling/Indoor training/Pool swimming/Run a route/Running/Trekking/Walk),
    // not a hardware-confirmed per-mode field. Deliberately reuses Theme's existing named
    // colors rather than inventing new hex swatches (this app's own "never hardcode colors"
    // rule), and Icons.sportModes for every badge rather than per-sport glyphs - Material
    // Symbols Rounded is subset to only the codepoints this app actually uses (see
    // assets/fonts/NOTICE.md), and adding real per-sport icons would mean fetching real
    // codepoints from Google's own repo and regenerating that font, a real but separate
    // undertaking not worth bundling into this pass. A renamed/custom mode not in this
    // table falls back to Theme.primary rather than guessing.
    // Superseded 2026-08-10 by ActivityTypes (keyed on activityId, 84 real activities with
    // Suunto's own category colours). Kept only as a fallback for a caller that has a name
    // but no id; the badge itself no longer uses it.
    function sportBadgeColor(name) {
        return Theme.primary
    }

    // --- staged display edits (items 6 and 7) -------------------------------------------
    // The watch has no per-field command for sport modes: every save rewrites the whole
    // ~7.5 KB CustomModes region. So edits are staged here and written ONCE on Save, the
    // way SuuntoLink does - writing per click would cost a full region write per click.
    // Each entry is exactly what the backend expects:
    //   {op:"add", type:"3-row"} | {op:"remove", display:N}
    //   {op:"setType", display:N, type:"graph"} | {op:"setRow", display:N, row:"Bottom", values:[...]}
    property var pendingEdits: []
    readonly property bool hasPendingEdits: pendingEdits.length > 0

    function stageEdit(edit) {
        const next = pendingEdits.slice();
        next.push(edit);
        pendingEdits = next;
    }
    function discardEdits() { pendingEdits = [] }
    function saveEdits() {
        if (!hasPendingEdits || !root.selectedModeName)
            return;
        CustomModesService.applyDisplayEdits(root.selectedModeName, pendingEdits);
        pendingEdits = [];
    }
    // SuuntoLink's own getMaxDisplays() for this watch family.
    // Limits come from the connected watch's own record, not from this one. A Traverse
    // reports 5 sport modes / 4 displays / no multisport and the UI follows, without anyone
    // adding a per-device branch here (André, item 24).
    readonly property var caps: CustomModesService.capabilities
    // Space reserved at each end of the display strip for its scroll arrows, so they sit
    // beside the displays rather than on top of one.
    readonly property int arrowGutter: 34

    readonly property int maxDisplays: caps.maxDisplays ? caps.maxDisplays : 8
    readonly property int deviceMaxSportModes: caps.maxSportModes ? caps.maxSportModes : 10
    readonly property int deviceMaxMultisport:
        caps.maxMultisportModes !== undefined ? caps.maxMultisportModes : 0

    // The four layouts a display can take, with the template id the watch stores for each
    // and the default rows the backend fills a new one with (custom_modes_edit._DEFAULT_ROWS,
    // themselves taken from SuuntoLink's createDisplay()). Kept in step so the preview shows
    // what Save will actually write, rather than an empty display that then fills itself in.
    readonly property var displayTypes: [
        { key: "3-row", label: qsTr("3 data fields"), templateId: 260, preview: "3rows",
          rows: [["Top", [10]], ["Center", [11]], ["Bottom", [5]]] },
        { key: "2-row", label: qsTr("2 data fields"), templateId: 261, preview: "2rows",
          rows: [["Top", [10]], ["Center", [5]]] },
        { key: "1-row", label: qsTr("1 data field"), templateId: 262, preview: "1row",
          rows: [["Top", [10]]] },
        { key: "graph",  label: qsTr("Graph"),        templateId: 257, preview: "graph",
          rows: [["Top", [6]], ["Center", [32]], ["Bottom", [5]]] }
    ]

    function displayTypeByKey(key) {
        return displayTypes.find(t => t.key === key) || displayTypes[0]
    }
    function displayTypeByTemplate(templateId) {
        return displayTypes.find(t => t.templateId === templateId) || displayTypes[0]
    }
    function layoutTypeOf(display) {
        return displayTypeByTemplate(display ? display.templateId : 260).preview
    }
    function layoutLabelOf(display) {
        return displayTypeByTemplate(display ? display.templateId : 260).label
    }

    // A field id's own label, from the catalogue the backend already provides, so a staged
    // change reads the same as one that came off the watch.
    function fieldLabel(fieldId) {
        const hit = CustomModesService.fieldTypes.find(f => f.value === fieldId)
        return hit ? hit.label : "0x" + fieldId.toString(16)
    }
    function makeRows(spec) {
        return spec.map(([rowLabel, ids]) => ({
            rowLabel: rowLabel,
            values: ids.map(id => ({ type: id, label: root.fieldLabel(id) })),
            isMultiValue: ids.length > 1
        }))
    }

    // The watch's displays with the pending edits applied - what Save would produce.
    //
    // Real bug this fixes (André, 2026-08-11): every control here used to render the WATCH's
    // state, so a staged change was invisible. Choosing a layout appeared to do nothing,
    // Remove emptied the row list but left the filmstrip showing the display, and Add did
    // nothing visible at all. Projecting the edits means the screen and the write agree.
    // Open the data picker for one row of the currently selected display. Shared by the
    // row's own "Change" button and by clicking that row on the watch-face preview - André's
    // idea, 2026-08-11: "click on the numbers, to choose the data fields".
    function editRow(rowIndex) {
        const list = stagedDisplays()
        const disp = list[currentDisplayIndex]
        if (!disp || !disp.fields[rowIndex])
            return
        const field = disp.fields[rowIndex]
        dataPicker.displayIndex = currentDisplayIndex
        dataPicker.fieldIndex = rowIndex
        dataPicker.activityId = selectedMode ? selectedMode.activityId : 1
        dataPicker.displayTemplate = disp.templateId
        dataPicker.rowName = field.rowLabel ? field.rowLabel.toUpperCase() : "TOP"
        dataPicker.selected = (field.values || []).map(v => v.type)
                                                  .filter(v => v !== undefined)
        dataPicker.open()
    }

    function stagedDisplays() {
        const mode = CustomModesService.modes.find(m => m.name === root.selectedModeName)
        let list = (mode && mode.displays ? mode.displays.filter(d => !d.isBuiltIn) : [])
            .map(d => ({ templateId: d.templateId, fields: d.fields }))

        for (const e of pendingEdits) {
            if (e.op === "add") {
                const t = displayTypeByKey(e.type)
                list.push({ templateId: t.templateId, fields: makeRows(t.rows) })
            } else if (e.op === "remove") {
                if (e.display >= 0 && e.display < list.length)
                    list.splice(e.display, 1)
            } else if (e.op === "setType") {
                if (list[e.display]) {
                    const t = displayTypeByKey(e.type)
                    list[e.display] = { templateId: t.templateId, fields: makeRows(t.rows) }
                }
            } else if (e.op === "setRow") {
                const disp = list[e.display]
                if (disp) {
                    const rows = disp.fields.slice()
                    const at = rows.findIndex(r => (r.rowLabel || "").toUpperCase()
                                                   === e.row.toUpperCase())
                    if (at >= 0) {
                        rows[at] = {
                            rowLabel: rows[at].rowLabel,
                            values: e.values.map(id => ({ type: id, label: root.fieldLabel(id) })),
                            isMultiValue: e.values.length > 1
                        }
                        list[e.display] = { templateId: disp.templateId, fields: rows }
                    }
                }
            }
        }
        return list
    }

    // Real, 2026-08-09 - maps a real display's own template/field-count (custom_modes.py's
    // own decode) to WatchFacePreview's own layoutType. See custom_modes.py's
    // system_tail_length()/_displays_to_json() for isBuiltIn/screenNumber themselves.
    function displayLayoutType(disp) {
        if (disp.isBuiltIn) {
            return disp.template === "PID_RUNNER_GPS_TEMPLATE_50_MAP_DRAW" ? "map" : "builtin"
        }
        if (disp.template.indexOf("GRAPH") >= 0) return "graph"
        const n = disp.fields.length
        return n === 1 ? "1row" : n === 2 ? "2rows" : "3rows"
    }

    // Friendly name for a built-in (non-editable) system screen, from its template - so the
    // filmstrip labels these "Map"/"Compass"/... instead of "Display N". Matches the Android
    // app's builtInScreenName(). André, 2026-08-17.
    function builtInScreenName(template) {
        if (template.indexOf("MAP_DRAW") >= 0) return qsTr("Map")
        if (template.indexOf("COMPASS_NAVIGATE") >= 0)
            return template.indexOf("MILS") >= 0 ? qsTr("Compass / Navigate (mils)") : qsTr("Compass / Navigate")
        if (template.indexOf("NAVIGATION") >= 0) return qsTr("Navigation")
        if (template.indexOf("GRAPH") >= 0) return qsTr("Graph")
        return ""
    }

    // Real, 2026-08-09 ("sport mode return bad gateway") - the connected watch had become
    // Kailash, which genuinely has no CustomModes region at all (confirmed empty - see
    // custom_modes_andre.md's Kailash section), so custom_modes.py's own real flash read
    // fails with a real, correct hardware error (0x0b17 short reply) - a genuine "this watch
    // doesn't have this," not a bug, but showing that as a raw 502 is still wrong. NavRail
    // already hides this page's own nav entry for Kailash, but that alone doesn't cover
    // reaching this page some other way (open before a cable swap, deep navigation) - guard
    // here too, and directly, rather than trusting the nav item to always be the only way in.
    Component.onCompleted: root.autoRead()

    // Automatic on connect (André, 2026-08-18): read on page load AND again the moment a watch
    // connects while the page is open, never a manual "read" button. Guarded to the
    // not-connected -> connected transition so the 10s device poll doesn't re-read on a loop.
    property bool _wasConnected: false
    function autoRead() {
        if (HomeViewModel.isKailash) return
        // Real, 2026-08-22: Ambit1/2 predate the SBEM CustomModes region this service
        // reads - calling it against a connected Ambit1 got a real "ok: false" (correct -
        // that family doesn't have this mechanism), same fix as Watch Settings/Routes.
        if (!DeviceCapabilities.supportsSportModes) return
        CustomModesService.refreshFieldTypes()
        CustomModesService.refreshActivities()
        CustomModesService.refresh()
        // Ask what THIS watch can hold rather than assuming the reference watch's numbers.
        CustomModesService.refreshCapabilities(DeviceService.model)
        // Real, 2026-08-09 ("check if meters or imperial or advanced, and how the watch
        // deals with it") - needed for the real-unit Autolap display below. Explicit ""
        // (Ambit3) rather than trusting SettingsWriteService.device's already-set value -
        // this is a shared singleton, and a prior visit to Settings while a Kailash was
        // connected could otherwise leave it pointed at Kailash's own smaller table.
        SettingsWriteService.device = ""
        SettingsWriteService.refresh()
    }
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

    // Real, 2026-08-09 ("Autolap...let's make mit more elegant with a toggle => off or
    // when on value in the units of the watch (check if meters or imperial or advanced,
    // and how the watch deals with it)"). Units.Mode is the real master switch
    // (0=Metric, 1=Imperial, 2=Advanced, schema-confirmed) - Metric/Imperial force every
    // individual unit to follow the master choice; only in Advanced does the watch's own
    // separate Units.Distance (0=km, 1=mi) actually apply. Falls back to km (Metric) if
    // settings haven't loaded yet rather than showing nothing.
    function _settingValue(key, fallback) {
        for (const s of SettingsWriteService.settings) {
            if (s.key === key) return s.value
        }
        return fallback
    }
    readonly property bool autolapUsesMiles: {
        const mode = _settingValue("units_mode", 0)
        if (mode === 1) return true
        if (mode === 2) return _settingValue("distance_unit", 0) === 1
        return false
    }
    readonly property real autolapUnitDivisor: autolapUsesMiles ? 1609.344 : 1000
    readonly property string autolapUnitSuffix: autolapUsesMiles ? qsTr("mi") : qsTr("km")

    // Interval-timer time thresholds are stored as whole seconds and shown as m:ss (SuuntoLink's
    // own "mm ' ss"); distance thresholds reuse the Autolap unit handling above.
    function parseMmSs(text) {
        var s = ("" + text).trim().replace("'", ":");
        if (s.indexOf(":") >= 0) {
            var parts = s.split(":");
            return (parseInt(parts[0] || "0", 10) * 60) + parseInt(parts[1] || "0", 10);
        }
        return parseInt(s || "0", 10);
    }
    function formatMmSs(seconds) {
        var s = Math.max(0, Math.round(seconds));
        var m = Math.floor(s / 60);
        var r = s % 60;
        return m + ":" + (r < 10 ? "0" : "") + r;
    }

    Text {
        visible: HomeViewModel.isKailash
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 560
        wrapMode: Text.WordWrap
        color: Theme.mutedText
        text: qsTr("Sport Modes isn't available on Kailash - it has no CustomModes region on this watch at all.")
    }

    // Real, 2026-08-23 (André: "do like them", then "openambit2 would read first..that would be
    // kinda crazy - double check it"). Double-checked exhaustively against openambit2 itself:
    // it does NOT read sport modes off the watch, and cannot - device_driver.h has no
    // sport_mode_read slot at all, sport_mode_serialize.h has serialize with no deserialize
    // counterpart, PMEM20_SPORT_MODE_START is referenced only by the write, and the one
    // "readSportModes" symbol is readSportModesFromFile(). That is not carelessness on their
    // part, it is the architecture: the HOST holds the master copy. openambit pulled the
    // authoritative set from the Movescount cloud (syncGET /userdevices/<serial>) and wrote it
    // wholesale; openambit2, post-shutdown, swapped that dead cloud for a local
    // ~/.openambit/sport_modes.json. SuuntoLink works the same way.
    //
    // So this is built the same way, which is what makes it useful rather than a one-shot
    // preset dump: the user's own set lives host-side (LEGACY_SPORT_MODES_FILE), is edited and
    // saved here, and is PUSHED to the watch on demand. Nothing is ever "lost" once the master
    // copy exists - the one real caveat is the FIRST push, when whatever is on the watch came
    // from Movescount/SuuntoLink and was never in this app. Capped at 10 modes: SuuntoLink's
    // own getMaxSportModes(AMBIT/AMBIT2*), verified live - openambit2's own 19-preset table has
    // no per-model cap at all, a gap of theirs not worth inheriting.
    Card {
        id: legacySportModeCard
        visible: HomeViewModel.connected && !HomeViewModel.isKailash && !DeviceCapabilities.supportsSportModes
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 560

        property bool working: false
        property string resultText: ""
        property bool resultOk: false
        property var modes: []
        property int maxModes: 10
        property bool saved: false
        property bool dirty: false

        readonly property string base: "http://127.0.0.1:8766"

        Component.onCompleted: if (visible) load()
        onVisibleChanged: if (visible && modes.length === 0) load()

        function load() {
            const xhr = new XMLHttpRequest()
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE) return
                let d = null
                try { d = JSON.parse(xhr.responseText) } catch (e) {}
                if (!d || !d.ok) return
                legacySportModeCard.modes = d.modes || []
                legacySportModeCard.maxModes = d.maxModes || 10
                legacySportModeCard.saved = d.saved === true
                legacySportModeCard.dirty = false
            }
            xhr.open("GET", base + "/api/legacy/sport-modes")
            xhr.send()
        }

        function mutate(index, patch) {
            const next = modes.slice()
            next[index] = Object.assign({}, next[index], patch)
            modes = next
            dirty = true
        }
        function removeAt(index) {
            const next = modes.slice()
            next.splice(index, 1)
            modes = next
            dirty = true
        }
        function addMode() {
            if (modes.length >= maxModes) return
            let usedId = 0
            for (const m of modes) usedId = Math.max(usedId, m.modeId || 0)
            modes = modes.concat([{
                name: qsTr("New mode"), activityId: 0, modeId: usedId + 1,
                gpsInterval: 1, recordingInterval: 1, altiBaroMode: 0,
                hrBelt: false, footPod: false, bikePod: false, cadencePod: false,
                autolapM: 0 }])
            dirty = true
        }

        function save() {
            working = true
            resultText = ""
            const xhr = new XMLHttpRequest()
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE) return
                legacySportModeCard.working = false
                let d = null
                try { d = JSON.parse(xhr.responseText) } catch (e) {}
                legacySportModeCard.resultOk = !!(d && d.ok)
                if (d && d.ok) {
                    legacySportModeCard.saved = true
                    legacySportModeCard.dirty = false
                    legacySportModeCard.resultText =
                        qsTr("Saved %1 sport modes on this computer.").arg(d.count)
                } else {
                    legacySportModeCard.resultText = (d && d.error)
                        ? d.error : qsTr("Couldn't save.")
                }
            }
            xhr.open("POST", base + "/api/legacy/sport-modes")
            xhr.setRequestHeader("Content-Type", "application/json")
            xhr.send(JSON.stringify({ modes: modes }))
        }

        function push(confirm) {
            working = true
            resultText = ""
            const xhr = new XMLHttpRequest()
            xhr.onreadystatechange = function() {
                if (xhr.readyState !== XMLHttpRequest.DONE) return
                legacySportModeCard.working = false
                let d = null
                try { d = JSON.parse(xhr.responseText) } catch (e) {}
                if (!d || !d.ok) {
                    legacySportModeCard.resultOk = false
                    legacySportModeCard.resultText = (d && d.error)
                        ? d.error : qsTr("Couldn't write sport modes to the watch.")
                    return
                }
                legacySportModeCard.resultOk = true
                legacySportModeCard.resultText = confirm
                    ? qsTr("Wrote %1 sport modes to the watch.").arg(d.mode_count)
                    : qsTr("Ready to write %1: %2").arg(d.mode_count)
                        .arg((d.names || []).join(", "))
            }
            xhr.open("POST", base + "/api/legacy/sport-modes/write")
            xhr.setRequestHeader("Content-Type", "application/json")
            xhr.send(JSON.stringify({ confirm: confirm, modes: modes }))
        }

        Column {
            width: parent.width
            spacing: Theme.spacingSmall

            Text {
                text: qsTr("Sport Modes")
                font.bold: true
                font.pixelSize: Theme.fontSizeBodyLarge
                color: Theme.text
            }
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeLabel
                text: qsTr("%1 can't report its sport modes back to a computer - no app can "
                            + "read them off this watch. So your set is kept here instead, and "
                            + "written to the watch when you choose. Edit freely: this copy is "
                            + "the master, and nothing reaches the watch until you press Write.")
                    .arg(HomeViewModel.deviceDisplayName)
            }
            Text {
                visible: !legacySportModeCard.saved
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: qsTr("Starting from a factory set. The first write replaces whatever is "
                            + "on the watch now - after that, this list is what the watch has.")
            }

            Row {
                spacing: Theme.spacingSmall
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("%1/%2 modes").arg(legacySportModeCard.modes.length)
                                              .arg(legacySportModeCard.maxModes)
                    color: legacySportModeCard.modes.length >= legacySportModeCard.maxModes
                           ? Theme.error : Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    visible: legacySportModeCard.dirty
                    text: qsTr("· unsaved changes")
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeCaption
                }
            }

            Repeater {
                model: legacySportModeCard.modes
                delegate: Row {
                    id: modeRow
                    required property var modelData
                    required property int index
                    width: parent.width
                    spacing: Theme.spacingSmall

                    // Real bug, caught live 2026-08-23 while testing this very card: with
                    // onTextEdited here, typing a name kept only its FIRST character. mutate()
                    // reassigns the whole `modes` array, which rebuilds this Repeater's
                    // delegates, so the field was destroyed and recreated on every keystroke -
                    // the same declarative-vs-imperative footgun nameField's own Binding
                    // comment further up this file already warns about. Commit on
                    // editingFinished (Enter or focus loss) instead, and bind the text only
                    // while NOT focused so a rebuild can't stomp what is being typed.
                    RoundedTextField {
                        id: nameEdit
                        width: 150
                        enabled: !legacySportModeCard.working
                        Binding {
                            target: nameEdit
                            property: "text"
                            value: modeRow.modelData.name
                            when: !nameEdit.activeFocus
                        }
                        onEditingFinished: {
                            if (text !== modeRow.modelData.name)
                                legacySportModeCard.mutate(modeRow.index, { name: text })
                        }
                    }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            text: qsTr("GPS")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        RoundedComboBox {
                            width: 92
                            enabled: !legacySportModeCard.working
                            model: ["1 s", "2 s", "5 s", "10 s", "30 s", "60 s"]
                            readonly property var secs: [1, 2, 5, 10, 30, 60]
                            currentIndex: Math.max(0, secs.indexOf(modeRow.modelData.gpsInterval))
                            onActivated: (i) => legacySportModeCard.mutate(
                                modeRow.index, { gpsInterval: secs[i] })
                        }
                    }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        Text {
                            text: qsTr("Autolap")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        RoundedTextField {
                            id: autolapEdit
                            width: 74
                            enabled: !legacySportModeCard.working
                            validator: IntValidator { bottom: 0; top: 65535 }
                            // Same per-keystroke rebuild problem as the name field above.
                            Binding {
                                target: autolapEdit
                                property: "text"
                                value: String(modeRow.modelData.autolapM || 0)
                                when: !autolapEdit.activeFocus
                            }
                            onEditingFinished: {
                                const v = parseInt(text || "0", 10)
                                if (v !== (modeRow.modelData.autolapM || 0))
                                    legacySportModeCard.mutate(modeRow.index, { autolapM: v })
                            }
                        }
                    }
                    Row {
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: 4
                        RoundedCheckBox {
                            anchors.verticalCenter: parent.verticalCenter
                            checked: modeRow.modelData.hrBelt === true
                            enabled: !legacySportModeCard.working
                            onToggled: legacySportModeCard.mutate(
                                modeRow.index, { hrBelt: checked })
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: qsTr("HR")
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Remove")
                        color: rmHover.hovered ? Theme.error : Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                        font.underline: rmHover.hovered
                        HoverHandler { id: rmHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler { onTapped: legacySportModeCard.removeAt(modeRow.index) }
                    }
                }
            }

            Row {
                spacing: Theme.spacingSmall
                RoundedButton {
                    text: qsTr("Add mode")
                    enabled: !legacySportModeCard.working
                             && legacySportModeCard.modes.length < legacySportModeCard.maxModes
                    onClicked: legacySportModeCard.addMode()
                }
                RoundedButton {
                    text: legacySportModeCard.working ? qsTr("Working…") : qsTr("Save")
                    enabled: !legacySportModeCard.working
                             && legacySportModeCard.modes.length > 0
                    onClicked: legacySportModeCard.save()
                }
                RoundedButton {
                    text: qsTr("Write to watch…")
                    enabled: !legacySportModeCard.working
                             && legacySportModeCard.modes.length > 0
                    onClicked: {
                        legacySportModeCard.push(false)
                        confirmSportModeWrite.open()
                    }
                }
                RoundedButton {
                    text: qsTr("Reset to factory")
                    enabled: !legacySportModeCard.working
                    onClicked: resetFactoryConfirm.open()
                }
            }

            Text {
                visible: legacySportModeCard.resultText.length > 0
                width: parent.width
                wrapMode: Text.WordWrap
                font.pixelSize: Theme.fontSizeCaption
                color: legacySportModeCard.resultOk ? Theme.success : Theme.error
                text: legacySportModeCard.resultText
            }
        }
    }

    ThemedDialog {
        id: resetFactoryConfirm
        title: qsTr("Reset to the factory set?")
        anchors.centerIn: Overlay.overlay
        contentItem: Text {
            width: 400
            wrapMode: Text.WordWrap
            color: Theme.text
            text: qsTr("Discards your saved sport modes on this computer and starts again from "
                        + "the factory set. The watch itself isn't touched until you press "
                        + "Write to watch.")
        }
        footer: Item {
            implicitHeight: resetRow.implicitHeight + Theme.spacingMedium
            Row {
                id: resetRow
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.rightMargin: Theme.spacingMedium
                spacing: Theme.spacingSmall
                RoundedButton {
                    text: qsTr("Cancel")
                    onClicked: resetFactoryConfirm.close()
                }
                RoundedButton {
                    text: qsTr("Reset")
                    onClicked: {
                        legacySportModeCard.saved = false
                        legacySportModeCard.modes = []
                        legacySportModeCard.load()
                        resetFactoryConfirm.close()
                    }
                }
            }
        }
    }

    ThemedDialog {
        id: confirmSportModeWrite
        title: qsTr("Write sport modes to the watch?")
        anchors.centerIn: Overlay.overlay
        contentItem: Column {
            spacing: Theme.spacingSmall
            width: 420
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.text
                text: qsTr("This replaces all sport modes on %1 with the %2 in this list. "
                            + "This watch can't report what it currently has, so the list here "
                            + "is the only record - it stays on this computer, so you can "
                            + "always write it again.")
                    .arg(HomeViewModel.deviceDisplayName)
                    .arg(qsTr("%n mode(s)", "", legacySportModeCard.modes.length))
            }
            Text {
                visible: legacySportModeCard.dirty
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.error
                font.pixelSize: Theme.fontSizeCaption
                text: qsTr("You have unsaved changes - these will be written to the watch but "
                            + "not kept on this computer unless you also press Save.")
            }
            Text {
                visible: legacySportModeCard.resultText.length > 0 && !legacySportModeCard.working
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: legacySportModeCard.resultText
            }
        }
        footer: Item {
            implicitHeight: confirmRow.implicitHeight + Theme.spacingMedium
            Row {
                id: confirmRow
                anchors.right: parent.right
                anchors.top: parent.top
                anchors.rightMargin: Theme.spacingMedium
                spacing: Theme.spacingSmall
                RoundedButton {
                    text: qsTr("Cancel")
                    onClicked: confirmSportModeWrite.close()
                }
                RoundedButton {
                    text: legacySportModeCard.working ? qsTr("Writing…") : qsTr("Write to watch")
                    enabled: !legacySportModeCard.working
                    onClicked: {
                        legacySportModeCard.push(true)
                        confirmSportModeWrite.close()
                    }
                }
            }
        }
    }

    // ============================== LIST VIEW ==============================
    Column {
        id: listColumn
        visible: !HomeViewModel.isKailash && DeviceCapabilities.supportsSportModes && !root.selectedMode
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 560
        spacing: Theme.spacingMedium

        Row {
            width: parent.width
            Text { text: qsTr("Sport Modes"); font.bold: true; font.pixelSize: Theme.fontSizeTitle; color: Theme.text }
        }

        Text {
            visible: CustomModesService.loading && CustomModesService.modes.length === 0
            color: Theme.mutedText
            text: qsTr("Reading sport modes off the watch...")
        }

        ErrorBanner {
            width: parent.width
            detail: CustomModesService.ok ? "" : CustomModesService.lastError
            context: qsTr("reading or writing sport modes")
        }

        // Multisport modes live in the SPORT_MODE slots rather than the exercise modes, and
        // have no displays of their own: a multisport mode is an ORDERED list of other modes.
        // Same shape as the sport modes below - a plain header carrying the usage, then one
        // card per mode - at André's request, 2026-08-11: "make the multisport similar as sport
        // modes, in a separate part from triathlon".
        Row {
            width: parent.width
            spacing: Theme.spacingSmall
            visible: root.deviceMaxMultisport > 0
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("Multisport")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodyLarge
                font.bold: true
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("%1/%2 used").arg(CustomModesService.multisportModes.length)
                                        .arg(root.deviceMaxMultisport)
                color: CustomModesService.multisportModes.length >= root.deviceMaxMultisport
                       ? Theme.error : Theme.mutedText
                font.pixelSize: Theme.fontSizeBody
            }
        }

        Repeater {
            model: CustomModesService.multisportModes
            delegate: Card {
                id: msCard
                required property var modelData
                width: listColumn.width

                Row {
                    width: parent.width
                    spacing: Theme.spacingMedium

                    ActivityBadge {
                        anchors.verticalCenter: parent.verticalCenter
                        activityId: msCard.modelData.activityId
                        size: 36
                    }
                    Column {
                        anchors.verticalCenter: parent.verticalCenter
                        width: parent.width - 36 - Theme.spacingMedium * 2 - 20
                        spacing: 2
                        Text {
                            text: msCard.modelData.name
                            font.bold: true
                            color: Theme.text
                        }
                        Text {
                            text: qsTr("%1 sport modes").arg(msCard.modelData.legNames.length)
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                }
            }
        }


        // A section header carrying its own usage, matching the Multisport one above -
        // André, 2026-08-11: "pass the sport modes 10/10 used to near all the sport
        // modes (similar to triathlon)". Both counts now sit with the list they count
        // instead of in a separate card away from either.
        Row {
            width: parent.width
            spacing: Theme.spacingSmall
            visible: CustomModesService.modes.length > 0
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("Sport modes")
                color: Theme.text
                font.pixelSize: Theme.fontSizeBodyLarge
                font.bold: true
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("%1/%2 used").arg(CustomModesService.modes.length)
                                        .arg(root.deviceMaxSportModes)
                color: CustomModesService.modes.length >= root.deviceMaxSportModes
                       ? Theme.error : Theme.mutedText
                font.pixelSize: Theme.fontSizeBody
            }
            RoundedButton {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("Create")
                enabled: CustomModesService.modes.length < root.deviceMaxSportModes
                         && !CustomModesService.writingMode
                onClicked: createModeDialog.open()
            }
            RoundedButton {
                anchors.verticalCenter: parent.verticalCenter
                visible: root.deviceMaxMultisport > 0
                text: qsTr("Multisport")
                enabled: !CustomModesService.writingMode
                onClicked: multisportEditor.open()
            }
        }


        Repeater {
            model: CustomModesService.modes
            delegate: Card {
                id: modeCard
                width: listColumn.width
                required property var modelData
                readonly property bool busy: CustomModesService.writingMode === modelData.name

                TapHandler { onTapped: root.selectedModeName = modeCard.modelData.name }

                Item {
                    width: parent.width
                    // Content-driven, not a fixed 44: most modes are two lines (name +
                    // display count) and the badge sets the height, but a mode that is used
                    // by a multisport AND absent from the watch menu (the factory Transition)
                    // carries two extra note lines. A fixed 44 clipped those inside the Card.
                    height: Math.max(44, infoColumn.implicitHeight)

                    Row {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.spacingMedium

                        // Real, 2026-08-10: colour AND symbol both come from the mode's own
                        // activityId (ActivityTypes, generated from
                        // assets/activity_types.json), not from its English name - the name
                        // is free text the owner can rename, and no other language matched
                        // the old lookup table at all.
                        ActivityBadge {
                            activityId: modeCard.modelData.activityId !== undefined
                                ? modeCard.modelData.activityId : -1
                            size: 44
                            anchors.verticalCenter: parent.verticalCenter
                        }

                        Column {
                            id: infoColumn
                            anchors.verticalCenter: parent.verticalCenter
                            spacing: 2
                            Text {
                                text: modeCard.modelData.name
                                font.bold: true
                                font.pixelSize: Theme.fontSizeBodyLarge
                                color: Theme.text
                            }
                            Text {
                                // Real, 2026-08-09 - counts only real, user-configurable
                                // screens (custom_modes.py's own screenNumber/isBuiltIn),
                                // not the built-in system screens - matches SuuntoLink's
                                // own real reported counts exactly (see that module's
                                // system_tail_length() docstring).
                                text: qsTr("%1 display(s)").arg(
                                    modeCard.modelData.displays.filter(d => !d.isBuiltIn).length)
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                            }
                            // André, item 21: a mode a multisport uses cannot be deleted -
                            // SuuntoLink says exactly this and greys its bin. Shown now
                            // rather than when delete is built, because it also explains why
                            // "Transition" is in the list at all: it exists only to be a leg.
                            Text {
                                visible: modeCard.modelData.usedByMultisport === true
                                text: qsTr("Used by a multisport mode - can't be deleted")
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                                font.italic: true
                            }
                        }
                    }

                    Row {
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.spacingSmall

                        Text {
                            visible: modeCard.busy
                            anchors.verticalCenter: parent.verticalCenter
                            text: qsTr("saving...")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            font.italic: true
                        }
                        Icon { glyph: Icons.chevronRight; size: 20; color: Theme.mutedText; anchors.verticalCenter: parent.verticalCenter }
                    }
                }
            }
        }

    }

    // ============================= DETAIL VIEW ==============================
    // Unsaved-changes bar. Sits above the detail view so it is impossible to miss - the
    // whole point of staging is that nothing reaches the watch until Save, so the state has
    // to be visible rather than implied.
    Card {
        id: pendingBar
        visible: root.hasPendingEdits && !HomeViewModel.isKailash && root.selectedMode
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingSmall
        width: 560
        z: 10
        Row {
            width: parent.width
            spacing: Theme.spacingMedium
            Column {
                width: parent.width - 240
                anchors.verticalCenter: parent.verticalCenter
                Text {
                    text: qsTr("%1 unsaved change(s)").arg(root.pendingEdits.length)
                    color: Theme.text
                    font.bold: true
                    font.pixelSize: Theme.fontSizeBody
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Nothing has been sent to the watch yet. Saving rewrites this "
                                + "mode's displays in one go.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                }
            }
            RoundedButton {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("Discard")
                enabled: !CustomModesService.writingMode
                onClicked: root.discardEdits()
            }
            RoundedButton {
                anchors.verticalCenter: parent.verticalCenter
                text: CustomModesService.writingMode ? qsTr("Saving...") : qsTr("Save to watch")
                enabled: !CustomModesService.writingMode
                onClicked: root.saveEdits()
            }
        }
    }

    Column {
        id: detailColumn
        visible: !HomeViewModel.isKailash && root.selectedMode
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: root.hasPendingEdits ? pendingBar.bottom : parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 560
        spacing: Theme.spacingMedium

        Row {
            width: parent.width
            spacing: Theme.spacingSmall
            RoundedButton {
                text: qsTr("< Back")
                onClicked: root.selectedModeName = ""
            }
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root.selectedMode ? root.selectedMode.name : ""
                font.bold: true
                font.pixelSize: Theme.fontSizeTitle
                color: Theme.text
            }
        }

        Card {
            width: parent.width
            visible: root.selectedMode !== null
            Column {
                id: modeColumn
                width: parent.width
                spacing: Theme.spacingMedium

                readonly property var mode: root.selectedMode
                readonly property bool busy: mode && CustomModesService.writingMode === mode.name

                // --- Name ---
                Column {
                    width: parent.width
                    spacing: 2
                    Text { text: qsTr("Name"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                    Row {
                        spacing: 6
                        RoundedTextField {
                            id: nameField
                            width: 200
                            enabled: !modeColumn.busy
                            // Real bug, found 2026-08-09 from a live screenshot ("strange
                            // characters" in a mode's name field, even though the real
                            // watch data was confirmed clean by reading it directly): a
                            // plain `text: ...` binding plus an imperative assignment
                            // elsewhere is a real QML footgun - the first imperative
                            // assignment permanently severs the declarative binding. Fixed
                            // with a proper `Binding` element instead, which re-evaluates
                            // correctly on every change.
                            Binding {
                                target: nameField
                                property: "text"
                                value: modeColumn.mode ? modeColumn.mode.name : ""
                                when: !nameField.activeFocus
                            }
                        }
                        RoundedButton {
                            text: qsTr("Rename")
                            enabled: modeColumn.mode && !modeColumn.busy && nameField.text.length > 0
                                     && nameField.text !== modeColumn.mode.name
                            onClicked: {
                                CustomModesService.renameMode(modeColumn.mode.name, nameField.text)
                                root.selectedModeName = nameField.text
                            }
                        }
                    }
                }

                // --- Autolap - real, 2026-08-09 ("let's make mit more elegant with a
                // toggle => off or when on value in the units of the watch"). Confirmed
                // via SuuntoLink's own real Autolap screen (assets/ambit3 pcap/v2/screens
                // sports modes/autolap.JPG): a plain "Use autolap" checkbox, and when on,
                // a distance shown in the real unit ("1.0 km"). ---
                Column {
                    spacing: 2
                    Text { text: qsTr("Autolap"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                    Row {
                        spacing: 6
                        RoundedSwitch {
                            id: autolapSwitch
                            anchors.verticalCenter: parent.verticalCenter
                            enabled: !modeColumn.busy
                            Binding {
                                target: autolapSwitch
                                property: "checked"
                                value: modeColumn.mode ? modeColumn.mode.autolap > 0 : false
                            }
                            onToggled: {
                                if (checked) {
                                    CustomModesService.writeField(modeColumn.mode.name,
                                        { "Autolap": Math.round(1 * root.autolapUnitDivisor) })
                                } else {
                                    CustomModesService.writeField(modeColumn.mode.name, { "Autolap": 0 })
                                }
                            }
                        }
                        RoundedTextField {
                            id: autolapField
                            visible: autolapSwitch.checked
                            width: 70
                            enabled: !modeColumn.busy
                            validator: DoubleValidator { bottom: 0; decimals: 2; notation: DoubleValidator.StandardNotation }
                            Binding {
                                target: autolapField
                                property: "text"
                                value: modeColumn.mode ? (modeColumn.mode.autolap / root.autolapUnitDivisor).toFixed(2) : "0"
                                when: !autolapField.activeFocus
                            }
                        }
                        Text {
                            visible: autolapSwitch.checked
                            anchors.verticalCenter: parent.verticalCenter
                            text: root.autolapUnitSuffix
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeLabel
                        }
                        RoundedButton {
                            visible: autolapSwitch.checked
                            text: qsTr("Set")
                            enabled: !modeColumn.busy
                            onClicked: {
                                const parsed = parseFloat(autolapField.text);
                                if (isNaN(parsed) || parsed <= 0) return;
                                CustomModesService.writeField(modeColumn.mode.name,
                                    { "Autolap": Math.round(parsed * root.autolapUnitDivisor) })
                            }
                        }
                    }
                }

                // --- HR limits ---
                Row {
                    width: parent.width
                    spacing: Theme.spacingLarge

                    Column {
                        spacing: 2
                        Text { text: qsTr("HR limits"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        Row {
                            spacing: 6
                            RoundedSwitch {
                                id: hrLimitsSwitch
                                checked: modeColumn.mode ? modeColumn.mode.hrLimitsUse === 1 : false
                                enabled: !modeColumn.busy
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("enabled")
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeLabel
                            }
                        }
                    }
                    Column {
                        spacing: 2
                        Text { text: qsTr("Low (bpm)"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        RoundedTextField {
                            id: hrLowField
                            width: 70
                            validator: IntValidator { bottom: 0; top: 255 }
                            text: modeColumn.mode ? modeColumn.mode.hrLow : 0
                            enabled: !modeColumn.busy
                        }
                    }
                    Column {
                        spacing: 2
                        Text { text: qsTr("High (bpm)"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        RoundedTextField {
                            id: hrHighField
                            width: 70
                            validator: IntValidator { bottom: 0; top: 255 }
                            text: modeColumn.mode ? modeColumn.mode.hrHigh : 0
                            enabled: !modeColumn.busy
                        }
                    }
                    Column {
                        spacing: 2
                        Text { text: " "; font.pixelSize: Theme.fontSizeLabel }  // vertical alignment spacer
                        RoundedButton {
                            text: qsTr("Set")
                            enabled: !modeColumn.busy
                            onClicked: CustomModesService.writeField(modeColumn.mode.name, {
                                "HrLow": parseInt(hrLowField.text || "0"),
                                "HrHigh": parseInt(hrHighField.text || "0"),
                                "HrLimitsUse": hrLimitsSwitch.checked ? 1 : 0,
                            })
                        }
                    }
                }

                // --- Interval timer - real SuuntoLink UI (assets/pcap/screens/ambit3 screens
                // sports modes/intervaltimerseconds.JPG): "Use interval timer", a Time(m:ss) /
                // Distance selector for the High and Low thresholds, and Repetitions. The byte
                // encoding is proven byte-exact against real captures - see tools/interval_timer.py.
                // Writes immediately on Set, like Autolap/HR (staging is displays-only).
                Column {
                    id: intervalCol
                    width: parent.width
                    spacing: 6
                    readonly property var it: modeColumn.mode ? modeColumn.mode.intervalTimer : null
                    readonly property bool isTime: itTypeBox.currentIndex === 0

                    Text { text: qsTr("Interval timer"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }

                    Row {
                        spacing: 6
                        RoundedSwitch {
                            id: itSwitch
                            anchors.verticalCenter: parent.verticalCenter
                            enabled: !modeColumn.busy
                            Binding {
                                target: itSwitch; property: "checked"
                                value: intervalCol.it ? intervalCol.it.enabled : false
                            }
                            // Turning it OFF has nothing to configure, so it commits the
                            // disable right away (like Autolap's switch) rather than needing
                            // the Set button - which is why Set only shows while enabled.
                            // Turning it ON just reveals the fields; Set writes the values.
                            onToggled: {
                                if (!checked && modeColumn.mode) {
                                    var reps = parseInt(itRepsField.text || "99", 10)
                                    if (isNaN(reps) || reps < 1) reps = 99
                                    CustomModesService.setIntervalTimer(modeColumn.mode.name,
                                        false, "time", 0, 0, reps)
                                }
                            }
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: qsTr("Use interval timer")
                            color: Theme.text; font.pixelSize: Theme.fontSizeLabel
                        }
                    }

                    // Type / High / Low / Reps, shown only when enabled (as Autolap's value is).
                    Column {
                        visible: itSwitch.checked
                        spacing: 6

                        Row {
                            spacing: 6
                            Text {
                                width: 44; anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Type"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                            }
                            RoundedComboBox {
                                id: itTypeBox
                                width: 150
                                enabled: !modeColumn.busy
                                model: [qsTr("Time (m:ss)"), qsTr("Distance")]
                                Binding {
                                    target: itTypeBox; property: "currentIndex"
                                    value: (intervalCol.it && intervalCol.it.type === "distance") ? 1 : 0
                                }
                            }
                        }

                        Row {
                            spacing: 6
                            Text {
                                width: 44; anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("High"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                            }
                            RoundedTextField {
                                id: itHighField
                                width: 90
                                enabled: !modeColumn.busy
                                Binding {
                                    target: itHighField; property: "text"; when: !itHighField.activeFocus
                                    value: !intervalCol.it ? "" : (intervalCol.isTime
                                        ? root.formatMmSs(intervalCol.it.high)
                                        : (intervalCol.it.high / root.autolapUnitDivisor).toFixed(2))
                                }
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: !intervalCol.isTime
                                text: root.autolapUnitSuffix
                                color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                            }
                        }

                        Row {
                            spacing: 6
                            Text {
                                width: 44; anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Low"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                            }
                            RoundedTextField {
                                id: itLowField
                                width: 90
                                enabled: !modeColumn.busy
                                Binding {
                                    target: itLowField; property: "text"; when: !itLowField.activeFocus
                                    value: !intervalCol.it ? "" : (intervalCol.isTime
                                        ? root.formatMmSs(intervalCol.it.low)
                                        : (intervalCol.it.low / root.autolapUnitDivisor).toFixed(2))
                                }
                            }
                            Text {
                                anchors.verticalCenter: parent.verticalCenter
                                visible: !intervalCol.isTime
                                text: root.autolapUnitSuffix
                                color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                            }
                        }

                        Row {
                            spacing: 6
                            Text {
                                width: 44; anchors.verticalCenter: parent.verticalCenter
                                text: qsTr("Reps"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                            }
                            RoundedTextField {
                                id: itRepsField
                                width: 90
                                enabled: !modeColumn.busy
                                validator: IntValidator { bottom: 1; top: 999 }
                                Binding {
                                    target: itRepsField; property: "text"; when: !itRepsField.activeFocus
                                    value: intervalCol.it ? intervalCol.it.repetitions : 99
                                }
                            }
                        }
                    }

                    RoundedButton {
                        // Only shown while enabled: turning the switch off already commits the
                        // disable, so Set exists purely to write the Type/High/Low/Reps values.
                        visible: itSwitch.checked
                        text: modeColumn.busy ? qsTr("Saving...") : qsTr("Set")
                        enabled: modeColumn.mode && !modeColumn.busy
                        onClicked: {
                            var reps = parseInt(itRepsField.text || "99", 10)
                            if (isNaN(reps) || reps < 1) reps = 99
                            var isTime = itTypeBox.currentIndex === 0
                            var high = isTime ? root.parseMmSs(itHighField.text)
                                : Math.round(parseFloat(itHighField.text || "0") * root.autolapUnitDivisor)
                            var low = isTime ? root.parseMmSs(itLowField.text)
                                : Math.round(parseFloat(itLowField.text || "0") * root.autolapUnitDivisor)
                            CustomModesService.setIntervalTimer(modeColumn.mode.name, true,
                                isTime ? "time" : "distance", high, low, reps)
                        }
                    }
                }

                // --- Pods (UseHw bitmask) ---
                Column {
                    width: parent.width
                    spacing: 2
                    Text { text: qsTr("Pods"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                    // Reactive, no fixed gap (André, 2026-08-13): each pod takes an equal share of
                    // the card width, so all pods stay on ONE line at any window/device width
                    // rather than overflowing or wrapping. The label elides only if a device is
                    // genuinely too narrow. This is the multi-device-safe alternative to picking a
                    // spacing number that "just fits" one width.
                    Row {
                        id: podsRow
                        width: parent.width
                        Repeater {
                            model: root.podBits
                            delegate: Row {
                                id: podRow
                                required property var modelData
                                width: podsRow.width / root.podBits.length
                                spacing: Theme.spacingSmall
                                // Only show a pod this watch actually supports (HR belt always).
                                visible: podRow.modelData.cap === ""
                                         || root.caps[podRow.modelData.cap] === true
                                RoundedCheckBox {
                                    id: podCheck
                                    anchors.verticalCenter: parent.verticalCenter
                                    checked: modeColumn.mode ? (modeColumn.mode.useHw & podRow.modelData.bit) !== 0 : false
                                    enabled: !modeColumn.busy
                                    onToggled: {
                                        const newUseHw = checked
                                            ? (modeColumn.mode.useHw | podRow.modelData.bit)
                                            : (modeColumn.mode.useHw & ~podRow.modelData.bit)
                                        CustomModesService.writeField(modeColumn.mode.name, { "UseHw": newUseHw })
                                    }
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    width: podRow.width - podCheck.width - podRow.spacing
                                    elide: Text.ElideRight
                                    text: podRow.modelData.label
                                    color: Theme.text
                                    font.pixelSize: Theme.fontSizeLabel
                                }
                            }
                        }
                    }
                }
            }
        }

        // --- Displays -------------------------------------------------------------------
        // Rewritten 2026-08-11 after André found five faults that were all one root cause:
        // this section rendered the WATCH's displays while edits were staged elsewhere, so
        // choosing a type changed nothing on screen, Remove deleted a row but left the
        // filmstrip untouched, Add did nothing on an already-filled display, and Remove was
        // greyed out on a display that had just been added. Everything below now renders
        // `stagedDisplays()` - the watch's state with the pending edits applied - so what is
        // on screen is exactly what Save would write.
        //
        // The layout picker replaces the old row of type buttons and the Add button, at
        // André's suggestion and following SuuntoLink: click a display to change its layout,
        // click the empty "+" slot to add one. Fewer controls, and the same gesture for both.
        Card {
            width: parent.width
            visible: root.selectedMode !== null
            Column {
                id: displaysColumn
                width: parent.width
                spacing: Theme.spacingMedium

                readonly property var displays: root.stagedDisplays()

                Text {
                    text: qsTr("Displays (%1/%2)").arg(displaysColumn.displays.length)
                                                  .arg(root.maxDisplays)
                    font.bold: true
                    font.pixelSize: Theme.fontSizeBodyLarge
                    color: Theme.text
                }

                // Filmstrip - only the user's own displays, never the built-in watch screens
                // (Compass/Navigation/Map): those are not editable, do not count toward the
                // limit, and showing them made the strip look like it held more than the max.
                Item {
                    width: parent.width
                    height: 140

                Flickable {
                    id: filmStrip
                    // Inset by the arrow gutters, so the arrows have their own space and can
                    // never sit on top of a display.
                    anchors.fill: parent
                    anchors.leftMargin: root.arrowGutter
                    anchors.rightMargin: root.arrowGutter
                    contentWidth: filmRow.width
                    clip: true
                    boundsBehavior: Flickable.StopAtBounds

                    Row {
                        id: filmRow
                        spacing: Theme.spacingMedium

                        Repeater {
                            model: displaysColumn.displays
                            delegate: Column {
                                id: filmItem
                                required property var modelData
                                required property int index
                                spacing: 4
                                WatchFacePreview {
                                    diameter: 100
                                    layoutType: root.layoutTypeOf(filmItem.modelData)
                                    selected: filmItem.index === root.currentDisplayIndex
                                    // Only the selected face offers row targets - André:
                                    // "just limit the area clickable for that function". An
                                    // unselected thumbnail stays one target that selects it,
                                    // so the first click never edits a row by accident.
                                    rowsClickable: filmItem.index === root.currentDisplayIndex
                                    onRowClicked: (rowIndex) => root.editRow(rowIndex)
                                    // Real bug, 2026-08-11 (André: "I can't select it to
                                    // then change the top center bottom"). Making the
                                    // thumbnail open the layout picker took away the plain
                                    // "select this display" gesture, so the rows below could
                                    // not be reached at all. A click SELECTS; clicking the
                                    // one already selected opens the layout picker, which is
                                    // the same click-again-to-act pattern the rest of the
                                    // app uses and leaves "Change layout" as the explicit
                                    // route for anyone who does not discover it.
                                    // One handler, two gestures. TapHandler has no "taps"
                                    // property - double taps arrive as their own signal, and
                                    // onTapped still fires for the first tap of a double,
                                    // which is harmless here: it selects the display that is
                                    // about to have its layout changed anyway.
                                    TapHandler {
                                        onTapped: root.currentDisplayIndex = filmItem.index
                                        onDoubleTapped: {
                                            root.currentDisplayIndex = filmItem.index
                                            layoutPicker.openFor(filmItem.index)
                                        }
                                    }
                                }
                                Text {
                                    width: 100
                                    horizontalAlignment: Text.AlignHCenter
                                    text: filmItem.modelData.isBuiltIn
                                          ? (root.builtInScreenName(filmItem.modelData.template) || qsTr("Built-in"))
                                          : qsTr("Display %1").arg(filmItem.index + 1)
                                    color: filmItem.index === root.currentDisplayIndex
                                           ? Theme.text : Theme.mutedText
                                    font.pixelSize: Theme.fontSizeCaption
                                }
                                // Remove sits under the display it removes - André,
                                // 2026-08-11: the old full-size button in the row below "was
                                // out of place, too big compared to the text". Small, muted,
                                // and only on the selected face, so it cannot be hit while
                                // clicking rows on another one.
                                Text {
                                    width: 100
                                    horizontalAlignment: Text.AlignHCenter
                                    visible: filmItem.index === root.currentDisplayIndex
                                             && displaysColumn.displays.length > 1
                                             && !CustomModesService.writingMode
                                    text: qsTr("Remove")
                                    color: removeHover.hovered ? Theme.error : Theme.mutedText
                                    font.pixelSize: Theme.fontSizeCaption
                                    font.underline: removeHover.hovered
                                    Behavior on color { ColorAnimation { duration: 120 } }
                                    HoverHandler {
                                        id: removeHover
                                        cursorShape: Qt.PointingHandCursor
                                    }
                                    TapHandler {
                                        onTapped: {
                                            root.stageEdit({ "op": "remove",
                                                             "display": filmItem.index })
                                            if (root.currentDisplayIndex > 0)
                                                root.currentDisplayIndex--
                                        }
                                    }
                                }
                            }
                        }

                        // The free slot. Clicking it asks for a layout and adds that display,
                        // then selects it - so adding and choosing a layout are one gesture.
                        Column {
                            visible: displaysColumn.displays.length < root.maxDisplays
                            spacing: 4
                            Rectangle {
                                width: 100
                                height: 100
                                radius: 50
                                color: "transparent"
                                border.width: 2
                                border.color: Theme.mutedText
                                Text {
                                    anchors.centerIn: parent
                                    text: "+"
                                    color: Theme.mutedText
                                    font.pixelSize: 34
                                }
                                TapHandler { onTapped: layoutPicker.openFor(-1) }
                            }
                            Text {
                                width: 100
                                horizontalAlignment: Text.AlignHCenter
                                text: qsTr("Add display")
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }
                    }
                }

                    // Scroll cues - André, 2026-08-11: "add a very light shadowed right arrow
                    // on the right bottom, centered compared to the displays, so people
                    // understand they need to move... and when we move the displays, and a
                    // left one is hidden, add a left arrow on left side."
                    //
                    // A horizontal strip that scrolls with no affordance is invisible: there
                    // is nothing on screen saying more displays exist off the edge. Each arrow
                    // shows only while there is actually something that way, so they double as
                    // the indicator of where you are in the strip.
                    Rectangle {
                        anchors.left: parent.left
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.verticalCenterOffset: -14
                        width: root.arrowGutter - 4
                        height: root.arrowGutter - 4
                        radius: width / 2
                        color: Theme.card
                        opacity: leftArrowHover.hovered ? 0.95 : 0.65
                        visible: filmStrip.contentX > 1
                        Behavior on opacity { NumberAnimation { duration: 120 } }
                        // chevronRight turned around, NOT a chevronLeft: the icon font is
                        // subset to only the codepoints this app uses (assets/fonts/
                        // NOTICE.md), so adding one means regenerating the font. Rotating
                        // costs nothing and cannot render as tofu. Found live, 2026-08-11 -
                        // Icons.chevronLeft does not exist, so the arrow drew an empty circle
                        // that is nearly invisible on a dark background, which is exactly what
                        // André saw as "missing the left arrow".
                        Icon {
                            anchors.centerIn: parent
                            glyph: Icons.chevronRight
                            rotation: 180
                            size: 18
                            color: Theme.text
                        }
                        HoverHandler { id: leftArrowHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: filmStrip.contentX =
                                Math.max(0, filmStrip.contentX - (100 + Theme.spacingMedium))
                        }
                    }

                    Rectangle {
                        anchors.right: parent.right
                        anchors.verticalCenter: parent.verticalCenter
                        anchors.verticalCenterOffset: -14
                        width: root.arrowGutter - 4
                        height: root.arrowGutter - 4
                        radius: width / 2
                        color: Theme.card
                        opacity: rightArrowHover.hovered ? 0.95 : 0.65
                        visible: filmStrip.contentX < filmStrip.contentWidth - filmStrip.width - 1
                        Behavior on opacity { NumberAnimation { duration: 120 } }
                        Icon {
                            anchors.centerIn: parent
                            glyph: Icons.chevronRight
                            size: 18
                            color: Theme.text
                        }
                        HoverHandler { id: rightArrowHover; cursorShape: Qt.PointingHandCursor }
                        TapHandler {
                            onTapped: filmStrip.contentX = Math.min(
                                filmStrip.contentWidth - filmStrip.width,
                                filmStrip.contentX + (100 + Theme.spacingMedium))
                        }
                    }
                }

                Text {
                    // André's own wording, 2026-08-11.
                    text: qsTr("Click a display to change it, double click to change layout, " +
                                "click on the numbers to edit data")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                }

                // The selected display's own rows.
                Column {
                    id: currentScreenColumn
                    width: parent.width
                    spacing: Theme.spacingSmall
                    visible: displaysColumn.displays.length > root.currentDisplayIndex
                             && root.currentDisplayIndex >= 0

                    readonly property var current: visible
                        ? displaysColumn.displays[root.currentDisplayIndex] : null

                    Row {
                        width: parent.width
                        spacing: Theme.spacingSmall
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: currentScreenColumn.current
                                ? qsTr("Display %1 - %2").arg(root.currentDisplayIndex + 1)
                                    .arg(root.layoutLabelOf(currentScreenColumn.current))
                                : ""
                            font.bold: true
                            color: Theme.text
                        }
                    }

                    // What each row holds, one line per row - André, 2026-08-11: "the list
                    // needs to be vertical and listing what is inside each, so people know.
                    // we can double the function of clicking on the number there, when we
                    // click on a sports". So each line is also a click target for that row,
                    // and is numbered to match the digit on the watch face above: the two are
                    // one control seen twice, not two controls.
                    //
                    // A graph display shows its graphed value against the squiggle and its
                    // data row against "1"; the middle field is that value's generated graph
                    // and is deliberately not listed, since nobody picks it.
                    Column {
                        width: parent.width
                        spacing: 2

                        Repeater {
                            model: currentScreenColumn.current
                                   ? currentScreenColumn.current.fields : []
                            delegate: Item {
                                id: valueLine
                                required property var modelData
                                required property int index

                                readonly property bool isGraph:
                                    currentScreenColumn.current
                                    && currentScreenColumn.current.templateId === 257
                                // The generated graph field - listed nowhere.
                                readonly property bool hidden: isGraph && index === 1
                                readonly property string marker:
                                    isGraph ? (index === 0 ? qsTr("graph") : "1")
                                            : String(index + 1)

                                width: parent.width
                                height: hidden ? 0 : valueText.implicitHeight + 8
                                visible: !hidden

                                // Background only - a SIBLING of the text, not its parent.
                                // Fading the container would fade the label with it.
                                //
                                // Opacity rather than a colour swap: animating
                                // transparent-to-card is what André saw flicker while
                                // scrolling in light mode, where Theme.card is nearly the
                                // page colour anyway. The green tint reads the same in both
                                // themes and matches the watch face's own row highlight -
                                // "in the display 1 the selection is a very nice green and
                                // appears correctly".
                                Rectangle {
                                    anchors.fill: parent
                                    radius: 6
                                    color: Theme.primary
                                    opacity: lineHover.hovered ? 0.16 : 0
                                    Behavior on opacity { NumberAnimation { duration: 120 } }
                                }

                                Row {
                                    anchors.left: parent.left
                                    anchors.verticalCenter: parent.verticalCenter
                                    anchors.leftMargin: 4
                                    spacing: Theme.spacingSmall

                                    Text {
                                        width: 34
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: valueLine.marker
                                        color: Theme.mutedText
                                        font.pixelSize: Theme.fontSizeCaption
                                        font.bold: true
                                    }
                                    Text {
                                        id: valueText
                                        anchors.verticalCenter: parent.verticalCenter
                                        text: (valueLine.modelData.values || [])
                                                .map(v => v.label).join(", ")
                                        color: Theme.text
                                        font.pixelSize: Theme.fontSizeBody
                                    }
                                }

                                HoverHandler {
                                    id: lineHover
                                    cursorShape: Qt.PointingHandCursor
                                }
                                TapHandler { onTapped: root.editRow(valueLine.index) }
                            }
                        }
                    }

                    // The per-row list used to live here. Removed 2026-08-11 at André's request -
                    // "you can remove also the top center bottom stuff because it will be
                    // integrated": a row is now edited by clicking it on the watch face above,
                    // so the list was a second control for the same thing.
                }
            }
        }
    }

    // Layout picker - one dialog for "change this display's layout" and "add a display",
    // since André's design makes them the same gesture.
    LayoutPickerDialog {
        id: layoutPicker
        types: root.displayTypes
        currentTemplateId: {
            const list = root.stagedDisplays()
            const at = layoutPicker.displayIndex
            return (at >= 0 && at < list.length) ? list[at].templateId : -1
        }
        anchors.centerIn: Overlay.overlay

        onLayoutChosen: (displayIndex, typeKey) => {
            if (displayIndex >= 0) {
                root.stageEdit({ "op": "setType", "display": displayIndex, "type": typeKey })
                root.currentDisplayIndex = displayIndex
                return
            }
            // Adding. The ceiling is checked against the STAGED count, so two staged adds
            // cannot overshoot it, and the message is the one André asked for.
            if (root.stagedDisplays().length >= root.maxDisplays) {
                maxDisplaysNotice.open()
                return
            }
            root.stageEdit({ "op": "add", "type": typeKey })
            root.currentDisplayIndex = root.stagedDisplays().length - 1
        }
    }

    ThemedDialog {
        id: maxDisplaysNotice
        title: qsTr("Max 8 screens")
        standardButtons: Dialog.Ok
        anchors.centerIn: Overlay.overlay
        contentItem: Text {
            text: qsTr("This watch holds at most %1 displays per sport mode. " +
                        "Remove one before adding another.").arg(root.maxDisplays)
            wrapMode: Text.WordWrap
            color: Theme.text
            font.pixelSize: Theme.fontSizeBody
        }
    }

    // --- create / delete / multisport ---------------------------------------------------
    // Restored 2026-08-23 from the pre-scrub history line (the `appzone-compiler` worktree),
    // which is a wholly separate history - no common ancestor with main, so these never came
    // across in any merge. main already had the backend for all three
    // (/api/customodes/mode, /api/customodes/multisport, tools/sport_mode_manage.py, all
    // byte-exact and hardware-proven) but no UI to reach them - dead endpoints, the same
    // class of gap as the Training Program routes found earlier today. The dialogs and the
    // CustomModesService properties they need are ported verbatim; only the page wiring is
    // new, kept minimal so main's own newer page work is untouched.
    CreateSportModeDialog {
        id: createModeDialog
        anchors.centerIn: Overlay.overlay
        onCreateRequested: (name, activityId) =>
            CustomModesService.createMode(name, activityId, DeviceService.model)
    }

    MultisportEditorDialog {
        id: multisportEditor
        anchors.centerIn: Overlay.overlay
        onSaveRequested: (action, originalName, name, activityId, legs) => {
            // Editing sends the combo's ORIGINAL name as the one to find and the new one as a
            // rename, since a rename and a leg change arrive together from one Save.
            if (action === "edit")
                CustomModesService.saveMultisport("edit", originalName, activityId, legs,
                                                   originalName === name ? "" : name)
            else
                CustomModesService.saveMultisport("create", name, activityId, legs)
        }
    }

    DataPickerDialog {
        id: dataPicker
        modeName: root.selectedMode ? root.selectedMode.name : ""
        modeIndex: root.selectedModeIndex
        hasPendingEdits: root.hasPendingEdits
        appCount: root.selectedMode ? (root.selectedMode.appCount || 0) : 0
        anchors.centerIn: Overlay.overlay

        // Staged, not written - one Save means one region write, the same as every other
        // display edit on this page.
        onRowChosen: (displayIndex, rowName, fieldIds) => root.stageEdit({
            "op": "setRow",
            "display": displayIndex,
            "row": rowName.charAt(0) + rowName.slice(1).toLowerCase(),
            "values": fieldIds
        })
    }
}
