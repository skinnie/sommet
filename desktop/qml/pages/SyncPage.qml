import QtQuick
import QtQuick.Layouts
import AmbitApp

// Copy one watch's setup onto another. Only one watch connects at a time over the cable, so
// the flow is guided and sequential (André, 2026-09-02, UX fix #3):
//   1. Plug in the watch to COPY FROM  -> it's read (backed up) automatically into slot A.
//   2. Unplug it, plug in the watch to COPY TO -> it's read into slot B and a plan is built.
//   3. Review "what will be written" and confirm -> only then is anything written, and only
//      to the watch that's plugged in now.
// Reading a watch (snapshot) is harmless - it only reads settings. Writing happens solely on
// the explicit confirm. The backend re-checks the connected serial before writing, so the
// wrong watch can never be touched. Direction is always A(source) -> B(target); there is no
// mirror/merge choice any more - it copies the source's settings onto the target.
//
// Only the categories both watches actually support end up in effectiveCategories, which is
// what the plan/apply calls use - so half-finished categories simply don't appear.
// Kailash "visited cities/countries" is never copyable: the watch computes it, there's no
// writable region (docs/explanation/kailash-history-write-probe.md).
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    // Always source -> target, always a straight copy.
    readonly property string mode: "mirror"
    readonly property string direction: "AtoB"
    readonly property var allCategories: ["settings", "pois", "routes", "sportModes"]

    function catSupported(slot, cat) {
        return !!(slot && slot.categories && slot.categories[cat]
                  && slot.categories[cat].supported === true);
    }
    function bothSupport(cat) {
        return catSupported(root.slotA, cat) && catSupported(root.slotB, cat);
    }
    readonly property var effectiveCategories:
        root.allCategories.filter(function (c) { return root.bothSupport(c); })

    readonly property var slotA: SyncService.slotA
    readonly property var slotB: SyncService.slotB
    readonly property bool haveSource: slotA && slotA.serial !== undefined
    readonly property bool haveTarget: slotB && slotB.serial !== undefined
    readonly property string connectedSerial: DeviceService.serial

    // Is the watch plugged in right now the one we still need to read for this step?
    readonly property bool sourcePluggedFresh:
        HomeViewModel.connected && !root.haveSource
    readonly property bool targetPluggedFresh:
        HomeViewModel.connected && root.haveSource
        && root.connectedSerial !== (root.haveSource ? root.slotA.serial : "")
        && !root.haveTarget
    // The target is plugged in now (needed to write).
    readonly property bool targetPluggedNow:
        root.haveTarget && root.connectedSerial === root.slotB.serial

    function settingsCount(slot) {
        if (!slot || !slot.categories || !slot.categories.settings) return 0;
        return slot.categories.settings.count || 0;
    }

    Component.onCompleted: SyncService.refreshState()

    // Auto-read the connected watch at the right moment. Snapshot is read-only, so doing it
    // automatically is safe and matches "plug it in, it backs up silently". Guarded so the
    // 10s device poll can't re-trigger it in a loop.
    function _maybeAutoRead() {
        if (SyncService.busy) return;
        if (root.sourcePluggedFresh) { SyncService.snapshot("A"); return; }
        if (root.targetPluggedFresh) { SyncService.snapshot("B"); return; }
    }
    // Once the target has just been read, build the plan automatically.
    onHaveTargetChanged: {
        if (root.haveTarget && (!SyncService.plan || SyncService.plan.changeCount === undefined))
            SyncService.buildPlan(root.mode, root.direction, root.effectiveCategories);
    }
    Connections {
        target: DeviceService
        function onDeviceInfoChanged() { root._maybeAutoRead(); }
    }

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: Math.min(560, root.width - Theme.spacingLarge * 2)
        spacing: Theme.spacingMedium

        // ---- Title + how it works ------------------------------------------------------
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    text: qsTr("Copy one watch to another")
                    font.bold: true
                    font.pixelSize: Theme.fontSizeTitle
                    color: Theme.text
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Give a second watch the same setup. Plug in the watch to copy " +
                                "from, then unplug it and plug in the one to copy to. Only one " +
                                "watch is plugged at a time — you swap the cable. Nothing is " +
                                "written until you confirm the last step.")
                }
            }
        }

        // ---- Step 1: source ------------------------------------------------------------
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    text: qsTr("1 · Copy from")
                    font.bold: true; color: Theme.text
                    font.pixelSize: Theme.fontSizeHeading
                }
                Text {
                    visible: !root.haveSource
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                    text: SyncService.busy && !root.haveSource
                        ? qsTr("Reading the watch…")
                        : qsTr("Plug in the watch you want to copy the setup FROM. It's read " +
                               "automatically — this only reads, it changes nothing.")
                }
                Row {
                    visible: root.haveSource
                    spacing: Theme.spacingSmall
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "✓"; color: Theme.success; font.bold: true
                        font.pixelSize: Theme.fontSizeBody
                    }
                    Column {
                        Text {
                            text: root.haveSource ? root.slotA.displayName : ""
                            color: Theme.text; font.pixelSize: Theme.fontSizeBody
                        }
                        Text {
                            text: root.haveSource
                                ? qsTr("read · %1 settings").arg(root.settingsCount(root.slotA)) : ""
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                }
            }
        }

        // ---- Step 2: target ------------------------------------------------------------
        Card {
            width: parent.width
            visible: root.haveSource
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    text: qsTr("2 · Copy to")
                    font.bold: true; color: Theme.text
                    font.pixelSize: Theme.fontSizeHeading
                }
                Text {
                    visible: !root.haveTarget
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                    text: SyncService.busy && root.haveSource && !root.haveTarget
                        ? qsTr("Reading the watch…")
                        : qsTr("Now unplug that watch and plug in the one you want to copy the " +
                               "setup TO. It's read automatically so we can show you what would " +
                               "change.")
                }
                Row {
                    visible: root.haveTarget
                    spacing: Theme.spacingSmall
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: "✓"; color: Theme.success; font.bold: true
                        font.pixelSize: Theme.fontSizeBody
                    }
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: root.haveTarget ? root.slotB.displayName : ""
                        color: Theme.text; font.pixelSize: Theme.fontSizeBody
                    }
                }
            }
        }

        // ---- Any read error ------------------------------------------------------------
        Text {
            visible: SyncService.lastActionText.length > 0 && !SyncService.lastActionOk
            width: parent.width; wrapMode: Text.WordWrap
            color: Theme.error; font.pixelSize: Theme.fontSizeCaption
            text: SyncService.lastActionText
        }

        // ---- Step 3: review & confirm --------------------------------------------------
        Card {
            width: parent.width
            visible: SyncService.plan && SyncService.plan.changeCount !== undefined
            Column {
                id: planCol
                width: parent.width
                spacing: Theme.spacingSmall

                readonly property var plan: SyncService.plan
                readonly property var cats: plan && plan.categories ? plan.categories : []
                readonly property int changeCount: plan && plan.changeCount !== undefined ? plan.changeCount : 0
                readonly property string targetName: plan && plan.target ? plan.target.displayName : ""
                readonly property bool modelMismatch: plan && plan.modelMismatch === true

                function catTitle(name) {
                    if (name === "settings") return qsTr("Settings");
                    if (name === "pois") return qsTr("POIs");
                    if (name === "routes") return qsTr("Routes");
                    if (name === "sportModes") return qsTr("Sport modes");
                    return name;
                }

                Text {
                    text: qsTr("3 · Review and confirm")
                    font.bold: true; color: Theme.text
                    font.pixelSize: Theme.fontSizeHeading
                }

                // Different models: syncing is refused outright (sensors/hardware differ).
                Text {
                    visible: planCol.modelMismatch
                    width: planCol.width; wrapMode: Text.WordWrap
                    font.bold: true; color: Theme.error
                    font.pixelSize: Theme.fontSizeHeading
                    text: qsTr("These two watches are different models")
                }
                Text {
                    visible: planCol.modelMismatch
                    width: planCol.width; wrapMode: Text.WordWrap
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                    text: (planCol.plan && planCol.plan.modelMismatchText)
                          ? planCol.plan.modelMismatchText
                          : qsTr("Settings can only be copied between watches of the same model.")
                }

                Text {
                    visible: !planCol.modelMismatch
                    width: planCol.width; wrapMode: Text.WordWrap
                    font.bold: true; color: Theme.text
                    font.pixelSize: Theme.fontSizeHeading
                    text: planCol.changeCount === 0
                        ? qsTr("Nothing to change — they already match")
                        : qsTr("%1 change%2 will be written to %3")
                            .arg(planCol.changeCount)
                            .arg(planCol.changeCount === 1 ? "" : "s")
                            .arg(planCol.targetName)
                }

                // One section per category, each with its own changed rows.
                Repeater {
                    model: planCol.cats
                    delegate: Column {
                        required property var modelData
                        width: planCol.width
                        spacing: Theme.spacingSmall / 2
                        visible: (modelData.changes && modelData.changes.length > 0)

                        Text {
                            visible: planCol.cats.length > 1
                            text: planCol.catTitle(modelData.category)
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                            font.bold: true
                        }
                        Repeater {
                            model: modelData.changes ? modelData.changes : []
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
                                        color: Theme.text; font.pixelSize: Theme.fontSizeLabel
                                        font.bold: true
                                    }
                                    Text {
                                        text: qsTr("%1  →  %2")
                                            .arg(modelData.fromText !== undefined ? modelData.fromText : modelData.from)
                                            .arg(modelData.toText !== undefined ? modelData.toText : modelData.to)
                                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                    }
                                }
                            }
                        }
                    }
                }

                // Confirm / apply. The target must be the plugged-in watch.
                Text {
                    visible: planCol.changeCount > 0 && !root.targetPluggedNow
                    width: planCol.width; wrapMode: Text.WordWrap
                    color: Theme.warning; font.pixelSize: Theme.fontSizeCaption
                    text: root.haveTarget
                        ? qsTr("Plug %1 back in to write these changes.").arg(root.slotB.displayName)
                        : qsTr("Plug the target watch back in to write these changes.")
                }
                RoundedButton {
                    visible: planCol.changeCount > 0 && !planCol.modelMismatch
                    enabled: root.targetPluggedNow && !SyncService.busy
                    text: SyncService.busy
                        ? qsTr("Writing…")
                        : qsTr("Write %1 change%2 to %3")
                            .arg(planCol.changeCount)
                            .arg(planCol.changeCount === 1 ? "" : "s")
                            .arg(root.haveTarget ? root.slotB.displayName : qsTr("the target"))
                    onClicked: SyncService.apply(root.mode, root.direction, true, root.effectiveCategories)
                }
                Text {
                    visible: SyncService.mismatchText.length > 0
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.error; font.pixelSize: Theme.fontSizeCaption
                    text: SyncService.mismatchText
                }
            }
        }

        // ---- Success confirmation (after a write) --------------------------------------
        // lastActionText comes from the C++ service in English; "Applied ..." is only ever the
        // result of a confirmed write, so it's safe to key the green banner on it.
        Card {
            width: parent.width
            visible: SyncService.lastActionOk
                     && SyncService.lastActionText.indexOf("Applied") === 0
            Row {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    text: "✓"; color: Theme.success; font.bold: true
                    font.pixelSize: Theme.fontSizeBody
                }
                Text {
                    anchors.verticalCenter: parent.verticalCenter
                    width: parent.width - 24
                    wrapMode: Text.WordWrap
                    text: qsTr("Done — %1").arg(SyncService.lastActionText)
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody
                }
            }
        }

        // ---- Start over ----------------------------------------------------------------
        RoundedButton {
            visible: root.haveSource
            text: qsTr("Start over")
            enabled: !SyncService.busy
            onClicked: { SyncService.clearSlot("A"); SyncService.clearSlot("B"); }
        }
    }
}
