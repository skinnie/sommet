import QtQuick
import QtQuick.Controls
import AmbitApp

// Firmware page - the GUI over the standalone flasher (tools/firmware_write.py) and its
// download step (firmware_check.py), see FIRMWARE_FLASHER_DESIGN.md. Suunto-only. Talks to
// the local backend with plain XMLHttpRequest (like HomePage's fun-fact fetch), streaming
// the flasher's --json events (/api/firmware/flash) so the ~10-minute flash shows live
// progress. The reassuring phrases during the wait are a hard-coded OFFLINE list.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    readonly property string api: "http://127.0.0.1:8766"

    // loading | idle (up to date) | update | recover | flashing | done | error
    property string mode: "loading"
    property var info: ({})
    property var known: []
    property string selectedSerial: ""
    property string phase: ""
    property real percent: -1          // <0 = indeterminate (busy)
    property string doneFw: ""
    property string errorText: ""
    property int _consumed: 0

    // Offline reassurance shown while the flash runs - no network, on purpose.
    readonly property var phrases: [
        qsTr("Don't worry, your watch will be soon ready for new adventures! Grab your favourite drink and enjoy this quiet time!"),
        qsTr("Good things take time — your watch is getting a fresh start. ☕"),
        qsTr("Hang tight! We're teaching your watch some new tricks."),
        qsTr("Perfect moment to stretch your legs — your watch is doing its thing."),
        qsTr("Almost there in watch-time. These seasoned adventurers like to take it slow. ⛰️"),
        qsTr("Keep the cable steady and relax — your watch has got this."),
        qsTr("Firmware flowing… your next summit is getting a little closer. ✨")
    ]
    property string phrase: phrases[0]

    Component.onCompleted: {
        checkFirmware();
        BackupService.checkFirmware();   // populates the "Download firmware for backup" card below
    }

    Timer {
        id: phraseTimer
        interval: 12000; repeat: true; running: false
        property int i: 0
        onTriggered: { i = (i + 1) % root.phrases.length; root.phrase = root.phrases[i]; }
    }

    // ---- backend calls -------------------------------------------------------

    function checkFirmware() {
        root.mode = "loading";
        const xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return;
            let d = null;
            try { d = JSON.parse(xhr.responseText); } catch (e) {}
            if (!d) { root.mode = "error"; root.errorText = qsTr("Couldn't reach the app backend."); return; }
            root.info = d;
            if (d.in_bsl) {
                // A watch in the bootloader. Its USB product-id usually still names the
                // model (d.model set) so we can recover it directly; only load the picker
                // when even that is unknown.
                if (!d.model)
                    loadKnown();
                root.mode = "recover";
            } else if (d.ok === false) {
                root.mode = "error";
                root.errorText = d.error || qsTr("No watch connected.");
            } else if (d.current_firmware && d.latest_firmware_version
                       && d.current_firmware !== d.latest_firmware_version) {
                root.mode = "update";
            } else {
                root.mode = "idle";
            }
        };
        xhr.open("GET", api + "/api/firmware");
        xhr.send();
    }

    function loadKnown() {
        const xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return;
            let d = null;
            try { d = JSON.parse(xhr.responseText); } catch (e) {}
            root.known = (d && d.watches) ? d.watches : [];
            if (root.known.length > 0)
                root.selectedSerial = root.known[0].serial;
        };
        xhr.open("GET", api + "/api/firmware/known");
        xhr.send();
    }

    // Download the right image, then flash. `spec` is null to use the connected watch, or
    // {model, hw, product} to recover a specific known watch that's currently in BSL.
    function downloadThenFlash(spec, expectModel) {
        root.mode = "flashing"; root.phase = qsTr("Downloading firmware…"); root.percent = -1;
        const xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return;
            let d = null;
            try { d = JSON.parse(xhr.responseText); } catch (e) {}
            if (!d || !d.ok || !d.path) {
                root.mode = "error";
                root.errorText = (d && d.error) || qsTr("Firmware download failed.");
                return;
            }
            startFlash(d.path, expectModel);
        };
        xhr.open("POST", api + "/api/firmware/download");
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.send(JSON.stringify(spec ? { model: spec.model, hw: spec.hw } : {}));
    }

    function startFlash(file, expectModel) {
        root.phase = qsTr("Starting…"); root.percent = -1; root._consumed = 0;
        phraseTimer.i = 0; root.phrase = root.phrases[0]; phraseTimer.start();
        const xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function() {
            if (xhr.readyState >= XMLHttpRequest.LOADING) {
                const text = xhr.responseText;
                let nl;
                while ((nl = text.indexOf("\n", root._consumed)) !== -1) {
                    const line = text.substring(root._consumed, nl).trim();
                    root._consumed = nl + 1;
                    if (line.length)
                        handleEvent(line);
                }
            }
            if (xhr.readyState === XMLHttpRequest.DONE) {
                phraseTimer.stop();
                if (root.mode === "flashing") {   // stream ended without done/error
                    root.mode = "error";
                    root.errorText = qsTr("The flash ended unexpectedly. Your watch is safe in "
                                          + "recovery mode — you can try again.");
                }
            }
        };
        xhr.open("POST", api + "/api/firmware/flash");
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.send(JSON.stringify({ file: file, expect_model: expectModel }));
    }

    function handleEvent(line) {
        let ev = null;
        try { ev = JSON.parse(line); } catch (e) { return; }
        switch (ev.phase) {
        case "connected":     root.phase = qsTr("Connected to your watch."); break;
        case "enter_bsl":     root.phase = qsTr("Entering recovery mode…"); break;
        case "transfer_mode":
        case "header":        root.phase = qsTr("Preparing…"); break;
        case "erase":         root.phase = qsTr("Erasing flash — this takes about a minute…"); root.percent = -1; break;
        case "streaming":     root.phase = qsTr("Writing firmware…"); root.percent = ev.percent; break;
        case "streamed":      root.phase = qsTr("Verifying…"); root.percent = 100; break;
        case "restart":       root.phase = qsTr("Little cable hiccup — retrying. Keep it still…"); root.percent = -1; break;
        case "commit":        root.phase = qsTr("Writing the final image…"); root.percent = -1; break;
        case "rebooting":     root.phase = qsTr("Rebooting your watch…"); root.percent = -1; break;
        case "done":          root.mode = "done"; root.doneFw = ev.fw || ""; break;
        case "error":         root.mode = "error"; root.errorText = ev.message || qsTr("The flash failed."); break;
        }
    }

    // ---- UI -------------------------------------------------------------------

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 520
        spacing: Theme.spacingMedium

        Text {
            text: qsTr("Firmware")
            color: Theme.text
            font.pixelSize: Theme.fontSizeTitle
            font.bold: true
        }

        // --- Loading ---
        Card {
            width: parent.width
            visible: root.mode === "loading"
            Row {
                spacing: Theme.spacingSmall
                LoadingPill {}
                Text { text: qsTr("Checking your watch…"); color: Theme.mutedText
                       anchors.verticalCenter: parent.verticalCenter }
            }
        }

        // --- Up to date / reinstall ---
        Card {
            width: parent.width
            visible: root.mode === "idle"
            Column {
                width: parent.width; spacing: Theme.spacingSmall
                Text { text: qsTr("Your watch is up to date"); font.bold: true; color: Theme.text
                       font.pixelSize: Theme.fontSizeBodyLarge }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: (root.info.product || root.info.model || qsTr("Watch"))
                          + " — " + qsTr("firmware ") + (root.info.current_firmware || "?")
                }
                RoundedButton {
                    text: qsTr("Reinstall firmware")
                    onClicked: confirm.show(qsTr("Reinstall the current firmware?"),
                                            null, root.info.model)
                }
            }
        }

        // --- Update available ---
        Card {
            width: parent.width
            visible: root.mode === "update"
            Column {
                width: parent.width; spacing: Theme.spacingSmall
                Text { text: qsTr("Firmware update available"); font.bold: true
                       color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: (root.info.product || root.info.model || qsTr("Watch")) + "\n"
                          + qsTr("Current: ") + (root.info.current_firmware || "?") + "  →  "
                          + qsTr("Latest: ") + (root.info.latest_firmware_version || "?")
                }
                RoundedButton {
                    text: qsTr("Update firmware")
                    onClicked: confirm.show(qsTr("Update to ")
                               + (root.info.latest_firmware_version || "") + "?",
                               null, root.info.model)
                }
            }
        }

        // --- Recovery (watch in BSL) ---
        Card {
            id: recoverCard
            width: parent.width
            visible: root.mode === "recover"
            // The watch's USB product-id usually names the model even in the bootloader, so
            // we can recover it in one tap; the picker is only for the rare unknown case.
            readonly property bool identified: root.info && root.info.model ? true : false
            Column {
                width: parent.width; spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Text { text: Icons.warningAmber; font.family: Icons.fontFamily
                           color: Theme.warning; font.pixelSize: Theme.fontSizeTitle }
                    Text { text: qsTr("Watch in recovery mode"); font.bold: true
                           color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge
                           anchors.verticalCenter: parent.verticalCenter }
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: recoverCard.identified
                          ? qsTr("This watch is in its bootloader after an interrupted update. "
                                 + "We recognised it as %1 — restore it to the latest firmware.")
                              .arg(root.info.model)
                          : qsTr("This watch is in its bootloader after an interrupted update. "
                                 + "It can't name itself, so pick which watch to restore from "
                                 + "the ones you've connected before.")
                }

                // Identified from the USB product id -> one tap.
                RoundedButton {
                    visible: recoverCard.identified
                    text: qsTr("Recover this watch")
                    onClicked: confirm.show(
                        qsTr("Restore %1 to its latest firmware?").arg(root.info.model),
                        { model: root.info.model, hw: root.info.hw_version }, root.info.model)
                }

                // Not identified -> pick from previously-connected watches.
                RoundedComboBox {
                    id: watchPicker
                    width: parent.width
                    visible: !recoverCard.identified && root.known.length > 0
                    model: root.known.map(function(w) {
                        return (w.product || w.codename) + "  ·  " + qsTr("serial ") + w.serial;
                    })
                    onCurrentIndexChanged: if (root.known[currentIndex])
                                               root.selectedSerial = root.known[currentIndex].serial
                }
                RoundedButton {
                    visible: !recoverCard.identified && root.known.length > 0
                    text: qsTr("Recover selected watch")
                    onClicked: {
                        const w = root.known[watchPicker.currentIndex];
                        if (w) confirm.show(qsTr("Restore ") + (w.product || w.codename)
                                            + qsTr(" to its latest firmware?"),
                                            { model: w.codename, hw: w.hw_version }, w.codename);
                    }
                }

                // Not identified and none known -> the friendly SuuntoLink message.
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    visible: !recoverCard.identified && root.known.length === 0
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody
                    text: qsTr("We can't recognise this watch yet. If it was never connected "
                               + "to this app, recover it once with SuuntoLink — after that "
                               + "we'll remember it.\n\nDon't worry, your watch will be soon "
                               + "ready for new adventures! Grab your favourite drink and "
                               + "enjoy this quiet time! 🧭")
                }
            }
        }

        // --- Inline confirm (keeps us clear of any Dialog quirks) ---
        Card {
            id: confirm
            width: parent.width
            visible: false
            property string question: ""
            property var spec: null
            property string expectModel: ""
            function show(q, s, em) { question = q; spec = s; expectModel = em || ""; visible = true; }
            Column {
                width: parent.width; spacing: Theme.spacingSmall
                Text { text: confirm.question; font.bold: true; color: Theme.text
                       width: parent.width; wrapMode: Text.WordWrap }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap; color: Theme.warning
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("This takes about 10 minutes. Keep the watch connected and "
                               + "don't unplug or move the cable until it's done.")
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: qsTr("Start")
                        onClicked: { confirm.visible = false;
                                     root.downloadThenFlash(confirm.spec, confirm.expectModel); }
                    }
                    RoundedButton {
                        text: qsTr("Cancel"); flat: true
                        onClicked: confirm.visible = false
                    }
                }
            }
        }

        // --- Flashing (live progress + offline phrases) ---
        Card {
            width: parent.width
            visible: root.mode === "flashing"
            Column {
                width: parent.width; spacing: Theme.spacingMedium
                Text { text: qsTr("Updating your watch"); font.bold: true; color: Theme.text
                       font.pixelSize: Theme.fontSizeBodyLarge }
                Text { text: root.phase; color: Theme.mutedText
                       font.pixelSize: Theme.fontSizeLabel; width: parent.width
                       wrapMode: Text.WordWrap }

                // rounded progress bar (indeterminate when percent < 0)
                Rectangle {
                    width: parent.width; height: 10; radius: 5
                    color: Theme.background
                    Rectangle {
                        height: parent.height; radius: parent.radius; color: Theme.accent
                        width: root.percent >= 0 ? parent.width * root.percent / 100 : parent.width * 0.3
                        Behavior on width { NumberAnimation { duration: 200 } }
                        SequentialAnimation on x {
                            running: root.percent < 0; loops: Animation.Infinite
                            NumberAnimation { from: 0; to: parent.parent.width * 0.7; duration: 1100; easing.type: Easing.InOutQuad }
                            NumberAnimation { from: parent.parent.width * 0.7; to: 0; duration: 1100; easing.type: Easing.InOutQuad }
                        }
                    }
                }
                Text {
                    visible: root.percent >= 0
                    text: Math.round(root.percent) + "%"
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                }

                Rectangle { width: parent.width; height: 1; color: Theme.background }

                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.text; font.pixelSize: Theme.fontSizeBody
                    text: root.phrase
                    Behavior on text { /* no-op; text set by timer */ }
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap
                    color: Theme.warning; font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Please keep the watch connected and the cable still.")
                }
            }
        }

        // --- Done ---
        Card {
            width: parent.width
            visible: root.mode === "done"
            Column {
                width: parent.width; spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Text { text: Icons.checkCircle; font.family: Icons.fontFamily
                           color: Theme.success; font.pixelSize: Theme.fontSizeTitle }
                    Text { text: qsTr("All done!"); font.bold: true; color: Theme.text
                           font.pixelSize: Theme.fontSizeBodyLarge
                           anchors.verticalCenter: parent.verticalCenter }
                }
                Text {
                    width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Your watch is back and ready for new adventures")
                          + (root.doneFw ? " — " + qsTr("firmware ") + root.doneFw : "") + "."
                }
                RoundedButton { text: qsTr("Done"); onClicked: root.checkFirmware() }
            }
        }

        // --- Error ---
        Card {
            width: parent.width
            visible: root.mode === "error"
            Column {
                width: parent.width; spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Text { text: Icons.error; font.family: Icons.fontFamily
                           color: Theme.error; font.pixelSize: Theme.fontSizeTitle }
                    Text { text: qsTr("Something went wrong"); font.bold: true; color: Theme.text
                           font.pixelSize: Theme.fontSizeBodyLarge
                           anchors.verticalCenter: parent.verticalCenter }
                }
                Text { width: parent.width; wrapMode: Text.WordWrap; color: Theme.mutedText
                       font.pixelSize: Theme.fontSizeLabel; text: root.errorText }
                RoundedButton { text: qsTr("Try again"); onClicked: root.checkFirmware() }
            }
        }

        // --- Firmware backup ("Download for backup") - moved here from the Backup page
        // 2026-08-14 (André) so all firmware lives in one place. Distinct from the flasher
        // above: this only saves a local copy of Suunto's firmware file, it never writes the
        // watch. Still backed by BackupService.* (its own download logic is unchanged). ---
        Card {
            width: parent.width
            visible: !HomeViewModel.isGarmin
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Text { text: qsTr("Download firmware for backup"); font.bold: true; color: Theme.text }

                Text {
                    // CORRECTED 2026-08-22: this claim was stale - the backend's own
                    // _handle_firmware_download() docstring says plainly "the image is a
                    // real SFI2ST firmware container, flashed by firmware_write.py" - it's
                    // the exact same file the "Reinstall firmware" flasher above already
                    // uses, not a separate backup-only format. Real flashing IS supported
                    // by this app (hardware-proven on Ambit3 Peak and, 2026-08-22, Ambit1)
                    // - this download just saves an extra local copy in case Suunto's own
                    // server ever stops serving this version, it's not the only path.
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Saves a local copy of the same firmware file the flasher above " +
                                "uses, in case Suunto's server ever stops serving this version.")
                }

                Text {
                    visible: BackupService.firmwareCheckOk
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBody
                    text: qsTr("Latest available: %1 (uploaded %2)")
                        .arg(BackupService.firmwareLatestVersion)
                        .arg(BackupService.firmwareUploadDate)
                }
                Text {
                    visible: !BackupService.firmwareCheckOk && !BackupService.firmwareLoading
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Couldn't check for firmware yet.")
                }

                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: BackupService.firmwareLoading ? qsTr("Working…") : qsTr("Check again")
                        enabled: !BackupService.firmwareLoading
                        onClicked: BackupService.checkFirmware()
                    }
                    RoundedButton {
                        text: qsTr("Download for backup")
                        enabled: !BackupService.firmwareLoading && BackupService.firmwareCheckOk
                        onClicked: BackupService.downloadFirmware()
                    }
                }

                Text {
                    visible: BackupService.firmwareActionText.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: BackupService.firmwareActionOk ? Theme.success : Theme.error
                    text: BackupService.firmwareActionText
                }
            }
        }

        // Honest coverage note - fully tested (real hardware flash) on the Ambit3 Peak and,
        // 2026-08-22, the Ambit1 (Bluebird) - same command sequence, real per-family BSL
        // difference found and handled (see LEGACY_BSL_PID in firmware_write.py). The rest
        // of the Ambit3 generation share the identical process (verified for Kailash and
        // Traverse from captures). Ambit2 not yet confirmed - same legacy family as Ambit1,
        // untested hardware.
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
            text: qsTr("Hardware-tested on the Ambit3 Peak and the Ambit1. The other Ambit3-family, "
                       + "Traverse and Kailash watches use the same firmware process. Ambit2 not yet confirmed.")
        }
    }
}
