import QtQuick
import QtQuick.Layouts
import AmbitApp

// Two-watch "freefly" sync. Copy settings (and, as their write paths are wired in,
// POIs/routes/sport-mode layouts) between two watches of a compatible model - two Kailashes,
// two Ambit3 Peaks, etc. Only one watch connects at a time over the cable, so the whole page
// is built around a sequential A/B flow: snapshot the plugged watch into a slot, swap the
// cable, snapshot the other into the second slot, preview the diff, swap to the target watch
// and apply. Every write is the same 0x1101 the Watch Settings page uses; the backend
// re-checks the connected serial before writing, so the wrong watch can never be touched.
//
// Kailash "countries visited" is deliberately absent: it is a firmware-computed query object
// with no writable region (docs/explanation/kailash-history-write-probe.md).
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    // Page-local choices. Categories is settings-only for now; the rest are shown greyed so
    // the shape of the feature is visible before their write paths land.
    property string mode: "mirror"          // "mirror" | "merge"
    property string direction: "AtoB"       // "AtoB" | "BtoA"

    readonly property var slotA: SyncService.slotA
    readonly property var slotB: SyncService.slotB
    readonly property bool haveA: slotA && slotA.serial !== undefined
    readonly property bool haveB: slotB && slotB.serial !== undefined
    readonly property string connectedSerial: DeviceService.serial
    // The slot the swap flow is aiming to write to, given the current direction.
    readonly property string targetSlot: direction === "AtoB" ? "B" : "A"
    readonly property var targetSummary: direction === "AtoB" ? slotB : slotA
    readonly property bool targetPlugged: (targetSummary && targetSummary.serial !== undefined)
                                          && connectedSerial === targetSummary.serial

    Component.onCompleted: SyncService.refreshState()

    function settingsCount(slot) {
        if (!slot || !slot.categories || !slot.categories.settings)
            return 0;
        return slot.categories.settings.count || 0;
    }

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: Math.min(560, root.width - Theme.spacingLarge * 2)
        spacing: Theme.spacingMedium

        // ---- Intro / how it works ------------------------------------------------------
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    text: qsTr("Sync two watches")
                    font.bold: true
                    font.pixelSize: Theme.fontSizeTitle
                    color: Theme.text
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Give two watches of the same kind matching settings. Only one " +
                                "watch plugs in at a time, so: snapshot the first as A, swap the " +
                                "cable, snapshot the second as B, preview what would change, then " +
                                "plug the target watch back in and apply. Nothing is written " +
                                "until you apply, and only to the watch a plan was built for.")
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    // Honest limit, per the hardware probe - keep users from expecting it here.
                    text: qsTr("Note: a Kailash's visited cities/countries can't be copied - the " +
                                "watch computes those itself and there's no way to write them.")
                }
            }
        }

        // ---- The two slots -------------------------------------------------------------
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    text: qsTr("The two watches")
                    font.bold: true
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeHeading
                }
                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingMedium
                    SlotPanel { slotLabel: "A"; Layout.fillWidth: true }
                    SlotPanel { slotLabel: "B"; Layout.fillWidth: true }
                }
                Text {
                    visible: SyncService.lastActionText.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: SyncService.lastActionOk ? Theme.success : Theme.error
                    text: SyncService.lastActionText
                }
            }
        }

        // ---- Mode & direction ----------------------------------------------------------
        Card {
            width: parent.width
            visible: root.haveA && root.haveB
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    text: qsTr("How to sync")
                    font.bold: true
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeHeading
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: qsTr("Mirror")
                        checkable: true
                        checked: root.mode === "mirror"
                        onClicked: root.mode = "mirror"
                    }
                    RoundedButton {
                        text: qsTr("Two-way merge")
                        checkable: true
                        checked: root.mode === "merge"
                        onClicked: root.mode = "merge"
                    }
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: root.mode === "mirror"
                        ? qsTr("Mirror: make the target watch match the source for every setting.")
                        : qsTr("Two-way merge combines lists (POIs, routes) so both watches get " +
                                "everything - coming with those categories. Settings are single " +
                                "values, so they still follow the direction you pick below.")
                }
                // Direction. Relevant to mirror always, and to merge's scalar settings.
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: qsTr("A → B")
                        checkable: true
                        checked: root.direction === "AtoB"
                        onClicked: root.direction = "AtoB"
                    }
                    RoundedButton {
                        text: qsTr("B → A")
                        checkable: true
                        checked: root.direction === "BtoA"
                        onClicked: root.direction = "BtoA"
                    }
                }
                // Categories. Settings is live; the rest are placeholders for now.
                Flow {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    CategoryChip { label: qsTr("Settings"); on: true }
                    CategoryChip { label: qsTr("POIs"); on: false }
                    CategoryChip { label: qsTr("Routes"); on: false }
                    CategoryChip { label: qsTr("Sport modes"); on: false }
                }
                RoundedButton {
                    text: SyncService.busy ? qsTr("Working…") : qsTr("Preview changes")
                    enabled: !SyncService.busy
                    onClicked: SyncService.buildPlan(root.mode, root.direction)
                }
            }
        }

        // ---- Plan / diff ---------------------------------------------------------------
        Card {
            width: parent.width
            visible: SyncService.plan && SyncService.plan.changeCount !== undefined
            Column {
                id: planCol
                width: parent.width
                spacing: Theme.spacingSmall

                readonly property var plan: SyncService.plan
                readonly property var settingsCat: plan && plan.categories && plan.categories.length > 0
                                                    ? plan.categories[0] : null
                readonly property var changes: settingsCat && settingsCat.changes ? settingsCat.changes : []
                readonly property var skipped: settingsCat && settingsCat.skipped ? settingsCat.skipped : []
                readonly property string targetName: plan && plan.target ? plan.target.displayName : ""

                Text {
                    width: planCol.width
                    wrapMode: Text.WordWrap
                    font.bold: true
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeHeading
                    text: planCol.changes.length === 0
                        ? qsTr("Both watches already match")
                        : qsTr("%1 change%2 to write to %3")
                            .arg(planCol.changes.length)
                            .arg(planCol.changes.length === 1 ? "" : "s")
                            .arg(planCol.targetName)
                }

                // One row per changed setting: label, from → to (human text).
                Repeater {
                    model: planCol.changes
                    delegate: Rectangle {
                        required property var modelData
                        width: planCol.width
                        height: rowCol.height + Theme.spacingSmall
                        radius: Theme.radiusSmall
                        color: Theme.cardNested
                        Column {
                            id: rowCol
                            x: Theme.spacingSmall
                            y: Theme.spacingSmall / 2
                            width: parent.width - Theme.spacingSmall * 2
                            Text {
                                text: modelData.label
                                color: Theme.text
                                font.pixelSize: Theme.fontSizeLabel
                                font.bold: true
                            }
                            Text {
                                text: qsTr("%1  →  %2")
                                    .arg(modelData.fromText !== undefined ? modelData.fromText : modelData.from)
                                    .arg(modelData.toText !== undefined ? modelData.toText : modelData.to)
                                color: Theme.mutedText
                                font.pixelSize: Theme.fontSizeCaption
                            }
                        }
                    }
                }

                Text {
                    visible: planCol.skipped.length > 0
                    width: planCol.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("%1 setting(s) skipped (read-only on the target or not present).")
                        .arg(planCol.skipped.length)
                }

                // Target-watch prompt / apply.
                Text {
                    visible: planCol.changes.length > 0 && !root.targetPlugged
                    width: planCol.width
                    wrapMode: Text.WordWrap
                    color: Theme.warning
                    font.pixelSize: Theme.fontSizeCaption
                    text: root.targetSummary && root.targetSummary.displayName !== undefined
                        ? qsTr("Plug in %1 (the target) to apply.").arg(root.targetSummary.displayName)
                        : qsTr("Plug in the target watch to apply.")
                }
                RoundedButton {
                    visible: planCol.changes.length > 0
                    enabled: root.targetPlugged && !SyncService.busy
                    text: SyncService.busy ? qsTr("Writing…")
                        : qsTr("Apply to %1").arg(root.targetSummary && root.targetSummary.displayName !== undefined
                                                  ? root.targetSummary.displayName : qsTr("target"))
                    onClicked: SyncService.apply(root.mode, root.direction, true)
                }
                Text {
                    visible: SyncService.mismatchText.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.error
                    font.pixelSize: Theme.fontSizeCaption
                    text: SyncService.mismatchText
                }
            }
        }
    }

    // ---- Inline component: one slot panel ---------------------------------------------
    component SlotPanel: Rectangle {
        property string slotLabel: "A"
        readonly property var summary: slotLabel === "A" ? root.slotA : root.slotB
        readonly property bool filled: summary && summary.serial !== undefined
        readonly property bool pluggedNow: filled && root.connectedSerial === summary.serial

        radius: Theme.radiusSmall
        color: Theme.cardNested
        border.width: pluggedNow ? 2 : 1
        border.color: pluggedNow ? Theme.primary : Theme.border
        implicitHeight: panelCol.height + Theme.spacingMedium

        Column {
            id: panelCol
            x: Theme.spacingSmall
            y: Theme.spacingSmall
            width: parent.width - Theme.spacingSmall * 2
            spacing: Theme.spacingSmall / 2

            Text {
                text: qsTr("Watch %1").arg(slotLabel)
                font.bold: true
                color: Theme.text
                font.pixelSize: Theme.fontSizeLabel
            }
            Text {
                visible: filled
                width: parent.width
                elide: Text.ElideRight
                text: filled ? summary.displayName : ""
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
            }
            Text {
                visible: filled
                width: parent.width
                elide: Text.ElideRight
                text: filled ? qsTr("SN %1").arg(summary.serial) : ""
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
            }
            Text {
                visible: filled
                text: filled ? qsTr("%1 settings").arg(root.settingsCount(summary)) : ""
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
            }
            Text {
                visible: !filled
                width: parent.width
                wrapMode: Text.WordWrap
                text: qsTr("Empty. Plug a watch in and snapshot it here.")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
            }
            Text {
                visible: pluggedNow
                text: qsTr("Plugged in now")
                color: Theme.primary
                font.pixelSize: Theme.fontSizeCaption
                font.bold: true
            }

            Row {
                spacing: Theme.spacingSmall / 2
                RoundedButton {
                    text: filled ? qsTr("Re-snapshot") : qsTr("Snapshot")
                    enabled: HomeViewModel.connected && !SyncService.busy
                    onClicked: SyncService.snapshot(slotLabel)
                }
                RoundedButton {
                    visible: filled
                    text: qsTr("Clear")
                    enabled: !SyncService.busy
                    onClicked: SyncService.clearSlot(slotLabel)
                }
            }
        }
    }

    // ---- Inline component: a category chip --------------------------------------------
    component CategoryChip: Rectangle {
        property string label: ""
        property bool on: false
        implicitWidth: chipRow.width + Theme.spacingMedium
        implicitHeight: 26
        radius: 13
        color: on ? Theme.primary : Theme.cardNested
        border.width: 1
        border.color: on ? Theme.primary : Theme.border
        Row {
            id: chipRow
            anchors.centerIn: parent
            spacing: 4
            Text {
                text: label
                color: on ? Theme.card : Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                font.bold: on
            }
            Text {
                visible: !on
                text: qsTr("(soon)")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeTiny
            }
        }
    }
}
