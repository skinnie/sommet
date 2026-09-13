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

    // Manual flow (André, 2026-09-12: "I don't want it automatically, I want a button to start
    // copy and a button to copy over"): nothing is read on plug - the user presses a button for
    // each read. These just gate which buttons are enabled.
    readonly property bool watchPlugged: HomeViewModel.connected
    // The plugged-in watch is the one already captured as the source.
    readonly property bool connectedIsSource:
        root.haveSource && root.connectedSerial === root.slotA.serial
    // A watch is plugged that we could read as the target (a source is set, and it isn't the
    // source watch itself).
    readonly property bool canReadTarget:
        root.watchPlugged && root.haveSource && !root.connectedIsSource
    // The target is plugged in now (needed to write).
    readonly property bool targetPluggedNow:
        root.haveTarget && root.connectedSerial === root.slotB.serial

    function settingsCount(slot) {
        if (!slot || !slot.categories || !slot.categories.settings) return 0;
        return slot.categories.settings.count || 0;
    }
    function catCount(slot, cat) {
        if (!slot || !slot.categories || !slot.categories[cat]) return 0;
        return slot.categories[cat].count || 0;
    }
    // "42 settings · 3 routes · 12 POIs · 6 sport modes" - only supported categories with a
    // real count, so a watch with no routes doesn't advertise "0 routes".
    function holdsText(slot) {
        if (!slot || !slot.categories) return "";
        var parts = [];
        var labels = {settings: qsTr("settings"), routes: qsTr("routes"),
                      pois: qsTr("POIs"), sportModes: qsTr("sport modes")};
        var order = ["settings", "routes", "pois", "sportModes"];
        for (var i = 0; i < order.length; ++i) {
            var c = order[i];
            var n = root.catCount(slot, c);
            if (root.catSupported(slot, c) && n > 0)
                parts.push(n + " " + labels[c]);
        }
        return parts.join(" · ");
    }
    // Backups that captured a copyable snapshot (-sync.json), newest first.
    readonly property var sourceBackups:
        (BackupService.backups || []).filter(function (b) { return b.hasSettings === true; })
    property bool showBackups: false

    Component.onCompleted: { SyncService.refreshState(); BackupService.refresh(); }

    // A copy just finished successfully (the green banner + "plug the next watch" hint key on it).
    readonly property bool justCopied:
        SyncService.lastActionOk && SyncService.lastActionText.indexOf("Applied") === 0

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
                    text: qsTr("Give your other watches the same setup. First pick what to copy " +
                                "FROM — a saved backup, or a watch you read now. Then, for each " +
                                "watch, plug it in and press one button to copy the base onto it. " +
                                "One watch at a time; swap the cable and press again for the next.")
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

                // --- No source chosen yet: choose a backup, OR read the plugged watch. ---
                Text {
                    visible: !root.haveSource
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                    text: SyncService.busy && !root.haveSource
                        ? qsTr("Reading…")
                        : qsTr("Choose a saved backup to copy from, or plug in the watch you want " +
                               "to copy the setup FROM and read it now. Reading only reads — it " +
                               "changes nothing.")
                }

                // Read the plugged-in watch as the base. Explicit button (André, 2026-09-12:
                // "I want a button to start copy") - nothing is read until it's pressed.
                RoundedButton {
                    visible: !root.haveSource
                    enabled: root.watchPlugged && !SyncService.busy
                    text: root.watchPlugged
                        ? qsTr("Start copy — read this watch as the base")
                        : qsTr("Plug in the base watch to read it")
                    onClicked: SyncService.snapshot("A")
                }

                // The "choose a backup" affordance. Only offered when at least one backup
                // actually captured a copyable snapshot (older backups have no settings).
                RoundedButton {
                    visible: !root.haveSource && root.sourceBackups.length > 0
                    enabled: !SyncService.busy
                    text: root.showBackups
                        ? qsTr("Hide backups")
                        : qsTr("…or choose a saved backup (%1)").arg(root.sourceBackups.length)
                    onClicked: root.showBackups = !root.showBackups
                }
                Text {
                    visible: !root.haveSource && root.sourceBackups.length === 0
                             && (BackupService.backups || []).length > 0
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("None of your saved backups include settings yet. Make a fresh " +
                               "backup of a watch and it can be used here as a copy source.")
                }
                Column {
                    visible: !root.haveSource && root.showBackups
                    width: parent.width
                    spacing: Theme.spacingSmall / 2
                    Repeater {
                        model: root.sourceBackups
                        delegate: Rectangle {
                            required property var modelData
                            width: parent.width
                            height: bkCol.height + Theme.spacingSmall
                            radius: Theme.radiusSmall
                            color: Theme.cardNested
                            border.width: bkTap.containsMouse ? 1 : 0
                            border.color: Theme.accent
                            MouseArea {
                                id: bkTap
                                anchors.fill: parent
                                hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor
                                enabled: !SyncService.busy
                                onClicked: SyncService.snapshotFromBackup("A", modelData.prefix)
                            }
                            Column {
                                id: bkCol
                                x: Theme.spacingSmall
                                y: Theme.spacingSmall / 2
                                width: parent.width - Theme.spacingSmall * 2
                                Text {
                                    text: (modelData.sync && modelData.sync.displayName)
                                          ? modelData.sync.displayName
                                          : (modelData.deviceModel || qsTr("Backup"))
                                    color: Theme.text; font.pixelSize: Theme.fontSizeLabel
                                    font.bold: true
                                }
                                Text {
                                    width: parent.width; wrapMode: Text.WordWrap
                                    text: {
                                        var holds = root.holdsText(modelData.sync);
                                        return modelData.label + (holds ? "  ·  " + holds : "");
                                    }
                                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                }
                            }
                        }
                    }
                }

                // --- Source chosen: show it and what it holds. ---
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
                            text: {
                                if (!root.haveSource) return "";
                                var holds = root.holdsText(root.slotA);
                                var src = root.slotA.fromBackup
                                    ? qsTr("from backup %1").arg(root.slotA.fromBackup)
                                    : qsTr("read from the watch");
                                return holds ? (src + "  ·  " + holds) : src;
                            }
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                }
            }
        }

        // ---- Step 2: copy to each watch (one button per watch) -------------------------
        // André, 2026-09-13: after the base is set, each next watch should be ONE press that
        // reads it AND copies - not read-then-review-then-copy. copyToConnected chains both;
        // the backend still rechecks the serial, refuses a cross-model write, and backs the
        // target up first, so one click is still safe. The button repeats: plug the next watch,
        // press again.
        Card {
            width: parent.width
            visible: root.haveSource
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    text: qsTr("2 · Copy onto your watches")
                    font.bold: true; color: Theme.text
                    font.pixelSize: Theme.fontSizeHeading
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                    text: SyncService.busy
                        ? qsTr("Copying — this reads the watch, backs it up, writes the setup and " +
                               "checks every byte. It can take a minute; leave it plugged in.")
                        : (root.connectedIsSource
                           ? qsTr("That's the base watch. Unplug it and plug in a watch you want to " +
                                  "copy ONTO, then press the button.")
                           : (root.watchPlugged
                              ? qsTr("Plug in each watch you want to copy onto and press the button — " +
                                     "one press reads it and copies your base across. Repeat for the next.")
                              : qsTr("Plug in a watch you want to copy ONTO, then press the button.")))
                }
                RoundedButton {
                    enabled: root.canReadTarget && !SyncService.busy
                    text: SyncService.busy
                        ? qsTr("Copying…")
                        : qsTr("Copy the base onto the plugged-in watch")
                    onClicked: SyncService.copyToConnected(root.mode, root.direction, root.allCategories)
                }
            }
        }

        // ---- Result: error / cross-model refusal / success -----------------------------
        Text {
            visible: SyncService.lastActionText.length > 0 && !SyncService.lastActionOk
            width: parent.width; wrapMode: Text.WordWrap
            color: Theme.error; font.pixelSize: Theme.fontSizeCaption
            text: SyncService.lastActionText
        }
        Text {
            visible: SyncService.mismatchText.length > 0
            width: parent.width; wrapMode: Text.WordWrap
            color: Theme.error; font.pixelSize: Theme.fontSizeCaption
            text: SyncService.mismatchText
        }
        Card {
            width: parent.width
            visible: root.justCopied
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
                    text: qsTr("Done — %1. Plug in the next watch and press the button again.")
                          .arg(SyncService.lastActionText)
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody
                }
            }
        }

        // ---- Start over (choose a different base) ---------------------------------------
        RoundedButton {
            visible: root.haveSource
            text: qsTr("Start over (choose a different base)")
            enabled: !SyncService.busy
            onClicked: { SyncService.clearSlot("A"); SyncService.clearSlot("B"); }
        }
    }
}
