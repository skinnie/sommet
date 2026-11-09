import QtQuick
import QtQuick.Controls
import SommetUi

// Edge Screens — editor for a Garmin Edge activity profile's data screens, over USB (no Garmin
// Connect, no cloud). Modelled on Garmin's own "Edit Screen" UX (a screen list, then a per-screen
// editor with a grid preview + a layout row + a "Field N -> metric" list), rendered in the app's
// visual language (Theme tokens + RoundedComboBox/RoundedButton, like BrytonScreensPanel/
// MageneScreensPanel). Backend: EdgeScreensService -> tools/edge_screens_device.py.
Item {
    id: root

    // Field catalogue as parallel arrays (BrytonScreensPanel pattern): "(none)" + every field.
    property var fieldNames: [qsTr("(none)")]
    property var fieldIds: [65535]
    // Draft edits per screen index -> [fieldId per slot]. Holds the FULL metric list (like the device's
    // field-7 array); layout changes never truncate it, so shrinking then growing restores the originals.
    property var draft: ({})
    // Active field count per screen index (like the device's field-3): how many of draft's slots show.
    property var counts: ({})
    // Arrangement variant per screen index (device field-8): 0=A, 1=B. Seeded from the device; only
    // counts 3-9 offer an A/B/C choice (Edge 1040). Saved verbatim so an untouched screen keeps its arrangement.
    property var variants: ({})
    // Screen toggle values per screen index -> {settingsField: value} (built-in screens' mesg-13 toggles).
    property var toggleDraft: ({})
    // Which screen is open in the editor (-1 = the list, newScreen = an unsaved new screen being added).
    property int editing: -1
    readonly property int newScreen: -2

    function rebuildCatalog() {
        const names = [qsTr("(none)")], ids = [65535]
        const cat = EdgeScreensService.catalog
        for (let i = 0; i < cat.length; ++i) { names.push(cat[i].name); ids.push(cat[i].id) }
        root.fieldNames = names; root.fieldIds = ids
    }
    function rebuildDraft() {
        const d = {}, c = {}, v = {}
        const scr = EdgeScreensService.screens
        for (let i = 0; i < scr.length; ++i) {
            d[scr[i].index] = scr[i].fields.map(f => f.id)
            c[scr[i].index] = scr[i].fields.length
            v[scr[i].index] = scr[i].variant || 0
        }
        root.draft = d
        root.counts = c
        root.variants = v
    }
    function screenByIndex(idx) {
        const scr = EdgeScreensService.screens
        for (let i = 0; i < scr.length; ++i) if (scr[i].index === idx) return scr[i]
        return null
    }
    function specialByIndex(idx) {
        const sp = EdgeScreensService.specialList
        for (let i = 0; i < sp.length; ++i) if (sp[i].index === idx) return sp[i]
        return null
    }
    function isSpecial(idx) { return root.specialByIndex(idx) !== null }
    // Open a built-in (special) screen in the editor to edit its metrics. Count is fixed for these types,
    // so there's no layout picker; the draft is seeded from the device's current fields.
    function openSpecial(idx) {
        const sp = root.specialByIndex(idx)
        if (!sp || !sp.editable) return
        const ids = sp.fields.map(f => f.id)
        while (ids.length < sp.count) ids.push(root.defaultFieldId())  // pad to the rendered count
        ids.length = sp.count                                          // and no more (count 0 => empty)
        const d = JSON.parse(JSON.stringify(root.draft)); d[idx] = ids; root.draft = d
        const c = JSON.parse(JSON.stringify(root.counts)); c[idx] = sp.count; root.counts = c
        const v = JSON.parse(JSON.stringify(root.variants)); v[idx] = sp.variant; root.variants = v
        const t = JSON.parse(JSON.stringify(root.toggleDraft)); const tm = {}
        for (const tg of (sp.toggles || [])) tm[tg.field] = tg.value
        t[idx] = tm; root.toggleDraft = t
        root.editing = idx
    }
    function toggleValue(idx, field) {
        const m = root.toggleDraft[idx]
        return (m && m[field] !== undefined) ? m[field] : 0
    }
    function setToggleValue(idx, field, value) {
        const t = JSON.parse(JSON.stringify(root.toggleDraft))
        if (!t[idx]) t[idx] = {}
        t[idx][field] = value
        root.toggleDraft = t
    }
    // Build the "field:value,field:value" CSV of the open screen's toggles for saving.
    function togglesCsv(idx) {
        const m = root.toggleDraft[idx]
        if (!m) return ""
        const out = []
        for (const f in m) out.push(f + ":" + m[f])
        return out.join(",")
    }
    // Does an open special screen offer a count choice (a layout picker)? (Locked types have one state.)
    function specialHasChoice(idx) {
        const sp = root.specialByIndex(idx)
        return sp && sp.editable && sp.countStates && sp.countStates.length > 1
    }
    function numberFor(idx) {
        const scr = EdgeScreensService.screens
        for (let i = 0; i < scr.length; ++i) if (scr[i].index === idx) return scr[i].number
        return idx + 1
    }
    function fieldNameFor(id) {
        const at = root.fieldIds.indexOf(id)
        return at >= 0 ? root.fieldNames[at] : ("id " + id)
    }
    // Physical slot count of a screen's field-7 array — the max the layout can grow to (Garmin caps at 10).
    function capacityFor(idx) {
        const s = root.screenByIndex(idx)
        return s && s.capacity ? Math.min(s.capacity, 10) : 10
    }
    // Metric a newly-added cell defaults to (Garmin uses Distance); fall back to the first real field.
    function defaultFieldId() {
        const at = root.fieldNames.indexOf(qsTr("Distance"))
        if (at >= 0) return root.fieldIds[at]
        return root.fieldIds.length > 1 ? root.fieldIds[1] : 65535
    }
    // Begin adding a new data screen: a fresh 2-field draft (Distance, like Garmin), opened in the editor.
    function startNewScreen() {
        const dfl = root.defaultFieldId()
        const d = JSON.parse(JSON.stringify(root.draft)); d[root.newScreen] = [dfl, dfl]
        root.draft = d
        const c = JSON.parse(JSON.stringify(root.counts)); c[root.newScreen] = 2
        root.counts = c
        const v = JSON.parse(JSON.stringify(root.variants)); v[root.newScreen] = 0
        root.variants = v
        root.editing = root.newScreen
    }
    // The active arrangement variant of the open screen (0=A, 1=B).
    function activeVariant(idx) {
        const v = root.variants[idx]
        return v === undefined ? 0 : v
    }
    // The f8 arrangement values a given field count offers on the Edge 1040 (bigger screen than the 530
    // APE measured): counts 3-9 offer A(0)/B(1)/C(2); a single field stores f8=1; 2 and 10 store f8=0
    // (10 = ten equal fields, no variant).
    // Edge 500 family (USB drive): a fixed set of pages, 1-8 fields each, no A/B/C arrangement and
    // no "add screen" (its mesg 14 has no field 8/9/10/12) - 2026-11-05.
    readonly property bool isEdge500: EdgeScreensService.screens.length > 0
                                      && EdgeScreensService.screens[0].family === "edge500"
    function variantsForCount(c) {
        if (root.isEdge500) return [0]
        if (c >= 3 && c <= 9) return [0, 1, 2]
        if (c === 1) return [1]
        return [0]
    }
    function variantLabel(v) { return v === 2 ? "C" : (v === 1 ? "B" : "A") }
    function _countLabel(c) { return c === 0 ? qsTr("Full screen (no fields)")
                                   : c === 1 ? qsTr("1 field") : qsTr("%1 fields").arg(c) }
    // The ordered (count, variant) states the layout picker offers — a data screen's 1/2/3-A/3-B/… or,
    // for an editable built-in, that type's own allowed counts. Each entry carries a ready label.
    function layoutStates() {
        const out = []
        if (root.isSpecial(root.editing)) {
            const sp = root.specialByIndex(root.editing)
            for (const cs of (sp.countStates || []))
                out.push({ count: cs[0], variant: cs[1], label: root._countLabel(cs[0]) })
            return out
        }
        const cap = root.capacityFor(root.editing)
        for (let c = 1; c <= cap; ++c)
            for (const v of root.variantsForCount(c))
                out.push({ count: c, variant: v,
                           label: (c >= 3 && c <= 9 && !root.isEdge500) ? root._countLabel(c) + " · " + root.variantLabel(v)
                                                     : root._countLabel(c) })
        return out
    }
    // The true on-device layout geometry: rows top-to-bottom, each {cells:[field indices sharing the row],
    // h:height weight}. A/B from the Activity-Profile-Editor survey; C (+ fifth-based heights) from
    // Edge 1040 observations. Used to draw the preview to scale, like Garmin's own EDIT SCREEN. A missing
    // (count,variant) falls back to that count's variant-0 grid.
    function layoutRows(count, variant, specialStype) {
        if (specialStype) {  // a built-in type: use its own per-count grid
            const sg = root._specialGrid(specialStype, count)
            return sg ? _rowsEqual(sg) : []   // [] = no fields (e.g. Map full-screen)
        }
        const g = _gridFor(count, variant)
        if (variant === 2 && count in root._cHeights) {
            const hs = root._cHeights[count], out = []
            for (let i = 0; i < g.length; ++i) out.push({cells: g[i], h: hs[i] || 1})
            return out
        }
        if (variant === 0 && count === 3) return _weighted([[0], [1], [2]], [1.15, 1, 0.85])
        return _rowsEqual(g)
    }
    function _rowsEqual(g) { const o = []; for (const r of g) o.push({cells: r, h: 1}); return o }
    // Per-type built-in screen grids (APE-measured). count 0 => no rows (device-generated full screen).
    function _specialGrid(stype, count) {
        const S = {
            25: {0: [], 1: [[0]], 2: [[0, 1]]},                                   // Map
            56: {0: [], 2: [[0, 1]], 4: [[0, 1], [2, 3]], 6: [[0, 1], [2, 3], [4, 5]]},  // Segment
            74: {1: [[0]], 2: [[0], [1]], 3: [[0], [1, 2]], 4: [[0, 1], [2, 3]]}, // Lap Summary
            35: {2: [[0, 1]]}, 44: {2: [[0, 1]]}, 63: {2: [[0, 1]]}, 104: {2: [[0, 1]]}  // locked-2
        }
        const byCount = S[stype]
        if (!byCount) return count === 2 ? [[0, 1]] : null
        return (count in byCount) ? byCount[count] : (count === 2 ? [[0, 1]] : null)
    }
    function _weighted(g, hs) { const o = []; for (let i = 0; i < g.length; ++i) o.push({cells: g[i], h: hs[i]}); return o }
    readonly property var _cHeights: ({
        3: [2, 2, 1], 4: [2, 2, 1], 5: [1, 2, 1, 1], 6: [1, 2, 1, 1],
        7: [1, 1, 2, 1], 8: [1, 1, 1, 1, 1], 9: [1, 1, 1, 1, 1]
    })
    function _gridFor(count, variant) {
        const G = {
            1: {0: [[0]]},
            2: {0: [[0], [1]]},
            3: {0: [[0], [1], [2]], 1: [[0], [1], [2]], 2: [[0], [1], [2]]},
            4: {0: [[0], [1], [2], [3]], 1: [[0, 1], [2], [3]], 2: [[0], [1], [2, 3]]},
            5: {0: [[0], [1], [2], [3], [4]], 1: [[0], [1], [2, 3], [4]], 2: [[0], [1], [2], [3, 4]]},
            6: {0: [[0], [1], [2], [3], [4, 5]], 1: [[0, 1], [2], [3], [4, 5]], 2: [[0], [1], [2, 3], [4, 5]]},
            7: {0: [[0], [1], [2], [3, 4], [5, 6]], 1: [[0, 1], [2], [3, 4], [5, 6]], 2: [[0, 1], [2, 3], [4], [5, 6]]},
            8: {0: [[0], [1], [2, 3], [4, 5], [6, 7]], 2: [[0, 1], [2, 3], [4], [5], [6, 7]]},
            9: {0: [[0], [1, 2], [3, 4], [5, 6], [7, 8]], 2: [[0, 1], [2, 3], [4], [5, 6], [7, 8]]},
            10: {0: [[0, 1], [2, 3], [4, 5], [6, 7], [8, 9]]}
        }
        const byCount = G[count] || {1: [[0]]}
        return byCount[variant] || byCount[0]
    }
    // Apply a chosen (count, variant): resize preserving metrics, then store the variant.
    function setLayout(count, variant) {
        root.setLayoutCount(count)
        const v = JSON.parse(JSON.stringify(root.variants)); v[root.editing] = variant
        root.variants = v
    }
    // How many cells the open screen shows right now.
    function activeCount(idx) {
        const c = root.counts[idx]
        return c === undefined ? 0 : c
    }
    // Change the number of fields shown to n. The metric list is preserved: shrinking just hides the
    // trailing cells (their metrics are kept, like the device's field-7 array), and growing reveals them
    // again — only genuinely-new slots (never set before) default to Distance. So shrink-then-grow never
    // silently wipes the originals to Distance.
    function setLayoutCount(n) {
        const d = JSON.parse(JSON.stringify(root.draft))
        const cur = (d[root.editing] || []).slice()
        while (cur.length < n) cur.push(root.defaultFieldId())  // extend backing store, never truncate
        d[root.editing] = cur
        root.draft = d
        const c = JSON.parse(JSON.stringify(root.counts))
        c[root.editing] = n
        root.counts = c
    }

    Component.onCompleted: EdgeScreensService.refresh()
    Connections {
        target: EdgeScreensService
        function onCatalogChanged() { root.rebuildCatalog() }
        function onScreensChanged() { root.rebuildDraft(); root.editing = -1 }
    }

    // ---------------------------------------------------------------- LIST VIEW
    PageFlickable {
        id: listFlick
        anchors.fill: parent
        visible: root.editing === -1
        contentWidth: width
        contentHeight: listCol.y + listCol.height + Theme.spacingLarge
        clip: true

        Column {
            id: listCol
            width: listFlick.width - Theme.spacingLarge * 2
            x: Theme.spacingLarge
            y: Theme.spacingLarge
            spacing: Theme.spacingMedium

            Item {
                width: parent.width
                height: Math.max(listTitle.implicitHeight, listRefresh.implicitHeight)
                Text {
                    id: listTitle
                    anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Data Screens")
                    color: Theme.text; font.pixelSize: Theme.fontSizeTitle; font.bold: true
                }
                RoundedButton {
                    id: listRefresh
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Refresh"); enabled: !EdgeScreensService.busy
                    onClicked: EdgeScreensService.refresh()
                }
            }

            Text {
                visible: EdgeScreensService.busy
                text: qsTr("Reading the Edge…"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
            }
            Text {
                visible: !EdgeScreensService.busy && !EdgeScreensService.deviceConnected
                width: parent.width; wrapMode: Text.WordWrap
                text: qsTr("No Garmin Edge found over USB. Plug it in, unlock it, then press Refresh.")
                color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
            }
            StatusNote {
                width: parent.width
                text: EdgeScreensService.lastError
                tone: "error"
            }
            StatusNote {
                width: parent.width
                text: EdgeScreensService.lastNote
                tone: "success"
            }

            // Profile picker (real ride names)
            Row {
                visible: EdgeScreensService.deviceConnected
                spacing: Theme.spacingSmall
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("Profile"); color: Theme.text; font.pixelSize: Theme.fontSizeBody
                }
                RoundedComboBox {
                    id: profileCombo
                    anchors.verticalCenter: parent.verticalCenter
                    width: 280; textRole: "name"; model: EdgeScreensService.profiles
                    // Reflect the auto-loaded / current profile so the combo matches what's shown.
                    currentIndex: {
                        const ps = EdgeScreensService.profiles
                        for (let i = 0; i < ps.length; ++i)
                            if (ps[i].file === EdgeScreensService.currentProfile) return i
                        return 0
                    }
                    onActivated: (i) => { const p = EdgeScreensService.profiles[i]; if (p) EdgeScreensService.loadProfile(p.file) }
                }
            }

            // Screen rows — tap to edit (Garmin's Data Screens list)
            Repeater {
                model: EdgeScreensService.screens
                delegate: Rectangle {
                    id: rowItem
                    required property var modelData
                    width: listCol.width
                    height: 60
                    radius: Theme.radiusSmall
                    color: rowMouse.containsMouse ? Theme.card : Theme.cardNested
                    border.width: 1; border.color: Theme.border

                    Column {
                        anchors.left: parent.left; anchors.leftMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter; spacing: 2
                        Text {
                            text: qsTr("Screen %1").arg(rowItem.modelData.number)
                            color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.bold: true
                        }
                        Text {
                            text: {
                                const f = rowItem.modelData.fields
                                const names = f.slice(0, 3).map(x => x.name).join(", ")
                                return qsTr("%1 fields").arg(f.length) + (names ? "  ·  " + names + (f.length > 3 ? "…" : "") : "")
                            }
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                            width: rowItem.width - Theme.spacingMedium * 2 - 24; elide: Text.ElideRight
                        }
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        text: "›"; color: Theme.mutedText; font.pixelSize: Theme.fontSizeTitle
                    }
                    MouseArea {
                        id: rowMouse
                        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: root.editing = rowItem.modelData.index
                    }
                }
            }

            // Add a new data screen (Garmin's "+ Add Data Screen"): a dashed row that opens the editor.
            Rectangle {
                visible: EdgeScreensService.deviceConnected && EdgeScreensService.currentProfile.length > 0
                         && !root.isEdge500
                width: listCol.width; height: 52; radius: Theme.radiusSmall
                color: addMouse.containsMouse ? Theme.card : "transparent"
                border.width: 1; border.color: Theme.borderStrong
                Text {
                    anchors.centerIn: parent
                    text: qsTr("＋  Add data screen")
                    color: Theme.accent; font.pixelSize: Theme.fontSizeBody; font.bold: true
                }
                MouseArea {
                    id: addMouse
                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: root.startNewScreen()
                }
            }

            // Built-in (special) screens — Map/Elevation/ClimbPro/… Editable ones (locked-field types)
            // open the editor; the rest are shown for reference (managed on the device).
            Text {
                visible: EdgeScreensService.deviceConnected && EdgeScreensService.specialList.length > 0
                text: qsTr("BUILT-IN SCREENS"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                topPadding: Theme.spacingSmall
            }
            Repeater {
                model: EdgeScreensService.specialList
                delegate: Rectangle {
                    id: spRow
                    required property var modelData
                    width: listCol.width; height: 56
                    radius: Theme.radiusSmall
                    color: (modelData.editable && spMouse.containsMouse) ? Theme.card : Theme.cardNested
                    border.width: 1; border.color: Theme.border
                    opacity: modelData.editable ? 1.0 : 0.6
                    Column {
                        anchors.left: parent.left; anchors.leftMargin: Theme.spacingMedium
                        anchors.right: parent.right; anchors.rightMargin: Theme.spacingMedium + 16
                        anchors.verticalCenter: parent.verticalCenter; spacing: 2
                        Text {
                            text: spRow.modelData.name
                            color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                        }
                        Text {
                            width: parent.width; elide: Text.ElideRight
                            text: {
                                if (!spRow.modelData.hasFields) return qsTr("No data fields")
                                const f = spRow.modelData.fields
                                if (!f || f.length === 0) return qsTr("No fields set")
                                return f.map(x => x.name).join(", ")
                            }
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    Text {
                        visible: spRow.modelData.editable
                        anchors.right: parent.right; anchors.rightMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        text: "›"; color: Theme.mutedText; font.pixelSize: Theme.fontSizeTitle
                    }
                    MouseArea {
                        id: spMouse
                        anchors.fill: parent; hoverEnabled: true
                        cursorShape: spRow.modelData.editable ? Qt.PointingHandCursor : Qt.ArrowCursor
                        onClicked: if (spRow.modelData.editable) root.openSpecial(spRow.modelData.index)
                    }
                }
            }
        }
    }

    // ---------------------------------------------------------------- EDITOR VIEW
    PageFlickable {
        id: editFlick
        anchors.fill: parent
        visible: root.editing !== -1
        contentWidth: width
        contentHeight: editCol.y + editCol.height + Theme.spacingLarge
        clip: true

        readonly property var scr: root.editing >= 0 ? root.screenByIndex(root.editing) : null
        readonly property var slots: root.draft[root.editing] || []
        // How many cells to show (the active field count); the backing `slots` may hold more (hidden).
        readonly property int visibleCount: root.activeCount(root.editing)

        Column {
            id: editCol
            width: editFlick.width - Theme.spacingLarge * 2
            x: Theme.spacingLarge
            y: Theme.spacingLarge
            spacing: Theme.spacingMedium

            // Header: back · title · save
            Item {
                width: parent.width
                height: Math.max(editTitle.implicitHeight, saveBtn.implicitHeight)
                RoundedButton {
                    id: backBtn
                    anchors.left: parent.left; anchors.verticalCenter: parent.verticalCenter
                    text: qsTr("‹ Screens"); onClicked: root.editing = -1
                }
                Text {
                    id: editTitle
                    anchors.centerIn: parent
                    text: root.editing === root.newScreen ? qsTr("New Screen")
                          : root.isSpecial(root.editing) ? root.specialByIndex(root.editing).name
                          : (root.editing >= 0 ? qsTr("Screen %1").arg(root.numberFor(root.editing)) : "")
                    color: Theme.text; font.pixelSize: Theme.fontSizeTitle; font.bold: true
                }
                RoundedButton {
                    id: saveBtn
                    anchors.right: parent.right; anchors.verticalCenter: parent.verticalCenter
                    text: root.editing === root.newScreen ? qsTr("Add to device") : qsTr("Save to device")
                    accent: true
                    enabled: !EdgeScreensService.busy
                    onClicked: {
                        // Only the visible cells are saved; hidden backing slots are dropped.
                        const ids = (root.draft[root.editing] || []).slice(0, editFlick.visibleCount)
                                        .filter(id => id !== 65535)
                        // Write the arrangement byte (f8): the chosen A/B/C for data screens, or the
                        // built-in type's variant for the chosen count (seeded from the device otherwise).
                        const vr = root.activeVariant(root.editing)
                        if (root.editing === root.newScreen) {
                            EdgeScreensService.addScreen(EdgeScreensService.currentProfile, ids.join(","), vr)
                        } else {
                            const tg = root.isSpecial(root.editing) ? root.togglesCsv(root.editing) : ""
                            EdgeScreensService.saveScreen(EdgeScreensService.currentProfile, root.editing, ids.join(","), vr, tg)
                        }
                        root.editing = -1  // back to the list so the success note is visible
                    }
                }
            }

            Text {
                text: editFlick.visibleCount === 0 ? qsTr("Full-screen — no data fields")
                                                   : qsTr("Tap a field to change it")
                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
            }

            // Clickable screen preview, drawn TO SCALE from the real layout geometry (rows, side-by-side
            // pairs, and relative heights) — like Garmin's own EDIT SCREEN. Tap a cell to change its metric.
            Rectangle {
                id: previewBox
                anchors.horizontalCenter: parent.horizontalCenter
                readonly property var rows: root.layoutRows(editFlick.visibleCount,
                                                            root.activeVariant(root.editing),
                                                            root.isSpecial(root.editing)
                                                                ? root.specialByIndex(root.editing).stype : 0)
                readonly property real totalW: 300
                readonly property real totalH: 280
                readonly property real gap: 4
                readonly property real sumH: { let s = 0; for (const r of rows) s += r.h; return s || 1 }
                width: totalW + 12; height: totalH + 12
                radius: Theme.radiusSmall; color: Theme.card
                border.width: 1; border.color: Theme.borderStrong
                // Full-screen types (Map/Segment at count 0) have no field cells to draw.
                Text {
                    anchors.centerIn: parent; visible: previewBox.rows.length === 0
                    text: qsTr("Full screen"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
                }
                Column {
                    anchors.centerIn: parent
                    width: previewBox.totalW; height: previewBox.totalH
                    spacing: previewBox.gap
                    Repeater {
                        model: previewBox.rows
                        delegate: Row {
                            id: rowRow
                            required property var modelData
                            readonly property var cells: modelData.cells
                            readonly property int nCells: cells.length
                            width: previewBox.totalW
                            height: (previewBox.totalH - (previewBox.rows.length - 1) * previewBox.gap)
                                    * (modelData.h / previewBox.sumH)
                            spacing: previewBox.gap
                            Repeater {
                                model: rowRow.cells
                                delegate: Rectangle {
                                    required property var modelData    // field index for this cell
                                    readonly property int fieldIndex: modelData
                                    width: (rowRow.width - (rowRow.nCells - 1) * previewBox.gap) / rowRow.nCells
                                    height: rowRow.height
                                    radius: 6
                                    color: cm.containsMouse ? Theme.cardNested : Theme.surface
                                    border.width: 1; border.color: cm.containsMouse ? Theme.accent : Theme.border
                                    Column {
                                        anchors.centerIn: parent; width: parent.width - 10; spacing: 1
                                        Text {
                                            width: parent.width; horizontalAlignment: Text.AlignHCenter
                                            text: qsTr("Field %1").arg(fieldIndex + 1)
                                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeTiny
                                        }
                                        Text {
                                            width: parent.width; horizontalAlignment: Text.AlignHCenter
                                            elide: Text.ElideRight
                                            text: root.fieldNameFor((root.draft[root.editing] || [])[fieldIndex])
                                            color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
                                        }
                                    }
                                    MouseArea {
                                        id: cm
                                        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                                        onClicked: fieldPicker.openFor(fieldIndex)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // LAYOUT row — tap to change the layout. Shown for data screens and for built-in types that
            // offer more than one count (Map/Segment/Lap Summary); hidden for fixed-count built-ins.
            readonly property bool showLayoutRow: !root.isSpecial(root.editing) || root.specialHasChoice(root.editing)
            Text {
                visible: parent.showLayoutRow
                text: qsTr("LAYOUT"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
            }
            Rectangle {
                visible: editCol.showLayoutRow
                width: parent.width; height: 44; radius: Theme.radiusSmall
                color: layoutMouse.containsMouse ? Theme.card : Theme.cardNested
                border.width: 1; border.color: layoutMouse.containsMouse ? Theme.accent : Theme.border
                Text {
                    anchors.left: parent.left; anchors.leftMargin: Theme.spacingMedium
                    anchors.verticalCenter: parent.verticalCenter
                    text: {
                        const c = editFlick.visibleCount
                        const base = root._countLabel(c)
                        // data screens: counts 3-9 have an A/B/C arrangement choice worth showing
                        return (!root.isSpecial(root.editing) && !root.isEdge500 && c >= 3 && c <= 9)
                            ? base + " · " + root.variantLabel(root.activeVariant(root.editing)) : base
                    }
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody
                }
                Text {
                    anchors.right: parent.right; anchors.rightMargin: Theme.spacingMedium
                    anchors.verticalCenter: parent.verticalCenter
                    text: "›"; color: Theme.mutedText; font.pixelSize: Theme.fontSizeTitle
                }
                MouseArea {
                    id: layoutMouse
                    anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                    onClicked: layoutPicker.open()
                }
            }

            // SETTINGS — built-in screen toggles (mesg-13). Shown only for screens that have them.
            Text {
                visible: root.isSpecial(root.editing)
                         && (root.specialByIndex(root.editing).toggles || []).length > 0
                text: qsTr("SETTINGS"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                topPadding: Theme.spacingSmall
            }
            Repeater {
                model: root.isSpecial(root.editing) ? (root.specialByIndex(root.editing).toggles || []) : []
                delegate: Rectangle {
                    id: togRow
                    required property var modelData
                    width: editCol.width; height: 60
                    radius: Theme.radiusSmall; color: Theme.cardNested
                    border.width: 1; border.color: Theme.border
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        text: togRow.modelData.name
                        color: Theme.text; font.pixelSize: Theme.fontSizeBody
                    }
                    // Choice toggle: a row of option chips; the current one is highlighted.
                    Row {
                        anchors.right: parent.right; anchors.rightMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.spacingSmall
                        Repeater {
                            model: togRow.modelData.options || []
                            delegate: Rectangle {
                                required property var modelData
                                readonly property string optLabel: modelData[0]
                                readonly property int optVal: modelData[1]
                                readonly property bool sel: root.toggleValue(root.editing, togRow.modelData.field) === optVal
                                width: optText.implicitWidth + 20; height: 32; radius: 16
                                color: sel ? Theme.accent : "transparent"
                                border.width: 1; border.color: sel ? Theme.accent : Theme.border
                                Text {
                                    id: optText
                                    anchors.centerIn: parent
                                    text: parent.optLabel
                                    color: parent.sel ? Theme.card : Theme.text; font.pixelSize: Theme.fontSizeCaption
                                }
                                MouseArea {
                                    anchors.fill: parent; cursorShape: Qt.PointingHandCursor
                                    onClicked: root.setToggleValue(root.editing, togRow.modelData.field, parent.optVal)
                                }
                            }
                        }
                    }
                }
            }
        }
    }

    // ---------------------------------------------------------------- FIELD PICKER (popup)
    Popup {
        id: fieldPicker
        property int cell: -1
        property string filter: ""
        parent: Overlay.overlay
        anchors.centerIn: Overlay.overlay
        width: 380; height: 520
        modal: true; dim: true; padding: 0
        background: Rectangle { color: Theme.card; radius: Theme.radiusCard; border.width: 1; border.color: Theme.borderStrong }

        function openFor(cellIndex) { cell = cellIndex; filter = ""; searchField.text = ""; open() }
        function choose(fid) {
            const d = JSON.parse(JSON.stringify(root.draft))
            if (!d[root.editing]) d[root.editing] = []
            d[root.editing][cell] = fid
            root.draft = d
            close()
        }

        Column {
            anchors.fill: parent; anchors.margins: Theme.spacingMedium; spacing: Theme.spacingSmall
            Text {
                text: fieldPicker.cell >= 0 ? qsTr("Field %1 — choose a metric").arg(fieldPicker.cell + 1) : ""
                color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.bold: true
            }
            SearchField {
                id: searchField
                width: parent.width
                placeholderText: qsTr("Search fields…")
                onTextChanged: fieldPicker.filter = text.toLowerCase()
            }
            ListView {
                width: parent.width
                height: parent.height - searchField.height - 60
                clip: true
                model: {
                    const out = []
                    for (let i = 0; i < root.fieldNames.length; ++i) {
                        const nm = root.fieldNames[i]
                        if (!fieldPicker.filter || nm.toLowerCase().indexOf(fieldPicker.filter) >= 0)
                            out.push({ name: nm, fid: root.fieldIds[i] })
                    }
                    return out
                }
                delegate: Rectangle {
                    required property var modelData
                    width: ListView.view.width; height: 40
                    color: itemMouse.containsMouse ? Theme.cardNested : "transparent"
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: Theme.spacingSmall
                        anchors.verticalCenter: parent.verticalCenter
                        text: modelData.name; color: Theme.text; font.pixelSize: Theme.fontSizeBody
                    }
                    MouseArea {
                        id: itemMouse
                        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: fieldPicker.choose(modelData.fid)
                    }
                }
                ScrollBar.vertical: ScrollBar {}
            }
        }
    }

    // ---------------------------------------------------------------- LAYOUT PICKER (popup)
    // Field count (mesg14 f3) + arrangement (mesg14 f8). Counts 3-9 offer an A/B/C arrangement (1040), as
    // reverse-engineered by the Activity-Profile-Editor project (f8 measured on hardware). Garmin lists
    // these as "1 / 2 / 3-A / 3-B / …". Choosing one resizes (preserving metrics) and stores the variant.
    Popup {
        id: layoutPicker
        parent: Overlay.overlay
        anchors.centerIn: Overlay.overlay
        width: 300; height: 520
        modal: true; dim: true; padding: 0
        background: Rectangle { color: Theme.card; radius: Theme.radiusCard; border.width: 1; border.color: Theme.borderStrong }

        Column {
            anchors.fill: parent; anchors.margins: Theme.spacingMedium; spacing: Theme.spacingSmall
            Text {
                text: qsTr("Layout")
                color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge; font.bold: true
            }
            ListView {
                width: parent.width
                height: parent.height - 40
                clip: true
                model: layoutPicker.visible ? root.layoutStates() : []
                delegate: Rectangle {
                    required property var modelData
                    readonly property int count: modelData.count
                    readonly property int variant: modelData.variant
                    readonly property bool hasAB: count >= 3 && count <= 9
                    readonly property bool current: count === editFlick.visibleCount
                                                    && (!hasAB || variant === root.activeVariant(root.editing))
                    width: ListView.view.width; height: 40
                    color: current ? Theme.accent : (layMouse.containsMouse ? Theme.cardNested : "transparent")
                    radius: 6
                    Text {
                        anchors.left: parent.left; anchors.leftMargin: Theme.spacingSmall
                        anchors.verticalCenter: parent.verticalCenter
                        text: {
                            const base = count === 1 ? qsTr("1 field") : qsTr("%1 fields").arg(count)
                            return hasAB ? base + " · " + root.variantLabel(variant) : base
                        }
                        color: current ? Theme.card : Theme.text; font.pixelSize: Theme.fontSizeBody
                        font.bold: current
                    }
                    MouseArea {
                        id: layMouse
                        anchors.fill: parent; hoverEnabled: true; cursorShape: Qt.PointingHandCursor
                        onClicked: { root.setLayout(parent.count, parent.variant); layoutPicker.close() }
                    }
                }
                ScrollBar.vertical: ScrollBar {}
            }
        }
    }
}
