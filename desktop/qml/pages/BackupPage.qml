import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import AmbitApp

// Step 10. Real backup/restore, built on write_nav.py's own `nav --save` / `restore
// PREFIX --write` - "the backup that milestone 4 asked for and never had" (that file's own
// words). "Sport Modes, Settings, Profiles" (the spec's own "Future" list here) aren't
// covered by this mechanism, which only ever touched routes/waypoints - not simulated.
// Garmin backup (real, 2026-08-08) is a genuinely different, simpler mechanism - a plain
// file copy, not this flash-region save/restore - see its own Card below.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    Component.onCompleted: {
        BackupService.refresh();
    }

    FolderDialog {
        id: garminBackupDialog
        title: qsTr("Choose a backup folder")
        currentFolder: LocalFileService.downloadsLocation
        onAccepted: GarminService.backupToFolder(selectedFolder)
    }

    // Ambit "save a backup to a folder" (André, 2026-08-16) - writes the backup straight into
    // the chosen folder. Point it at a cloud-sync folder and it syncs, no OAuth/keys.
    FolderDialog {
        id: backupFolderDialog
        title: qsTr("Choose a folder to save the backup in")
        currentFolder: LocalFileService.downloadsLocation
        onAccepted: BackupService.createBackup(selectedFolder)
    }

    // Whether the watch-backup card below is showing - if it is, it already backs up Ember
    // too (the backend writes an -ember.json alongside every watch backup, silently), so the
    // dedicated Ember card would just be a confusing duplicate "Create backup now" button.
    readonly property bool watchBackupShown: !HomeViewModel.isGarmin && DeviceCapabilities.supportsWatchBackup

    Column {
        id: column
        anchors.horizontalCenter: parent.horizontalCenter
        anchors.top: parent.top
        anchors.topMargin: Theme.spacingLarge
        width: 480
        spacing: Theme.spacingMedium

        // Real, 2026-08-08 ("when a garmin device is detected, hide backup&restore and
        // existing backups, since those are suunto specific") - this mechanism only ever
        // touches the Ambit3's own flash regions (write_nav.py's nav --save/restore), so it
        // has nothing to do while a Garmin is the connected device.
        // Real, 2026-08-23: this mechanism is write_nav.py's nav --save/restore - the
        // Ambit3 SBEM flash regions, same as everything else gated on
        // DeviceCapabilities.supportsRoutes. Found live: "Create backup now" against a
        // connected Ambit1 got a real 502 (skipped every SBEM region, "this watch does not
        // declare it") - same fix as Watch Settings/Sport Modes/Routes/POIs.
        Text {
            visible: HomeViewModel.connected && !HomeViewModel.isGarmin
                     && !DeviceCapabilities.supportsWatchBackup
                     && !DeviceCapabilities.supportsTravelArchive
            width: 480
            wrapMode: Text.WordWrap
            color: Theme.mutedText
            text: qsTr("%1 doesn't support Backup & Restore on this app yet.").arg(HomeViewModel.deviceDisplayName)
        }

        // Kailash's own backup. Deliberately NOT the "Backup & Restore" card below: it saves
        // different regions, and it is one-way. See DeviceCapabilities.supportsTravelArchive.
        Card {
            width: parent.width
            visible: DeviceCapabilities.supportsTravelArchive
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Text { text: qsTr("Travel history & GPS track"); font.bold: true; color: Theme.text }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Saves this watch's visited places, travel stats and " +
                                "activity log, plus the passive GPS track as .gpx files. " +
                                "A firmware update erases all of it, so keep a copy first.")
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("This is an archive, not a restore point - there is no way to " +
                                "write either back to the watch.")
                }

                RoundedButton {
                    text: BackupService.loading ? qsTr("Working…") : qsTr("Save archive now")
                    enabled: !BackupService.loading
                    onClicked: BackupService.createBackup()
                }

                Text {
                    visible: BackupService.lastActionText.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: BackupService.lastActionOk ? Theme.success : Theme.error
                    text: BackupService.lastActionText
                }
            }
        }

        Card {
            width: parent.width
            visible: !HomeViewModel.isGarmin && DeviceCapabilities.supportsWatchBackup
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Text { text: qsTr("Backup & Restore"); font.bold: true; color: Theme.text }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Covers Routes and POIs together (the watch's whole " +
                                "navigation database) - Sport Modes, Settings, and Profiles " +
                                "are future, not part of this mechanism.")
                }

                RoundedButton {
                    text: BackupService.loading ? qsTr("Working…") : qsTr("Create backup now")
                    enabled: !BackupService.loading
                    onClicked: BackupService.createBackup()
                }

                Text {
                    visible: BackupService.lastActionText.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: BackupService.lastActionOk ? Theme.success : Theme.error
                    text: BackupService.lastActionText
                }
            }
        }

        Card {
            width: parent.width
            visible: !HomeViewModel.isGarmin && (DeviceCapabilities.supportsWatchBackup
                                                 || DeviceCapabilities.supportsTravelArchive)
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Text { text: qsTr("Existing backups"); font.bold: true; color: Theme.text }

                Text {
                    visible: BackupService.backups.length === 0
                    text: qsTr("None yet.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                }

                Repeater {
                    model: BackupService.backups
                    delegate: Column {
                        width: parent.width
                        spacing: 4
                        Text {
                            text: DateFormat.dateTime(new Date(modelData.createdAt * 1000))
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBody
                        }
                        // Ember rides along in every backup automatically (André, 2026-08-26) -
                        // this just says so, so it isn't a silent surprise on restore.
                        // Which watch this came off (André, 2026-08-27: "be sure that they
                        // are not from other device"). Backups made before the stamp existed
                        // say so honestly rather than claiming to be this watch's.
                        Text {
                            text: modelData.deviceModel
                                  ? qsTr("From %1").arg(HomeViewModel.displayNameForModel(modelData.deviceModel))
                                  : modelData.deviceHint === "kailash"
                                    ? qsTr("From a Kailash (identified by its contents)")
                                    : modelData.deviceHint === "ambit"
                                      ? qsTr("From an Ambit (identified by its contents)")
                                      : qsTr("From an unknown watch (saved before backups recorded this)")
                            color: modelData.deviceSerial && DeviceService.serial
                                   && modelData.deviceSerial !== DeviceService.serial
                                   ? Theme.error : Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        Text {
                            visible: modelData.hasKailash === true
                            text: qsTr("+ travel history & GPS track")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        Text {
                            visible: modelData.hasEmber === true
                            text: qsTr("+ Ember data")
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                        }
                        Row {
                            spacing: Theme.spacingSmall
                            // Real request 2026-08-07: "replace the rehearse restore button
                            // with open backup folder" - Restore itself already reports its
                            // own result text below, which was Rehearse's whole purpose;
                            // being able to actually see the saved files is the more useful
                            // second action here.
                            RoundedButton {
                                text: qsTr("Open backup folder")
                                onClicked: LocalFileService.openFolder(LocalFileService.backupsLocation)
                            }
                            // A Kailash archive (travel history + GPS track, no -routes.bin
                            // and no -ember.json) has no write path back to the watch - the
                            // backend refuses it with that reason, so don't offer the button.
                            // Hidden for an archive (nothing to write back) and for a backup
                            // from a DIFFERENT watch - the backend refuses that too, but the
                            // button should not be there to press in the first place.
                            RoundedButton {
                                visible: (modelData.hasRoutes === true || modelData.hasEmber === true)
                                         && !(modelData.deviceSerial && DeviceService.serial
                                              && modelData.deviceSerial !== DeviceService.serial)
                                         // Older, unstamped backups can still be told apart by
                                         // what is in them: Ambit regions have no meaning on a
                                         // Kailash, so don't offer to write them there.
                                         && !(modelData.deviceHint === "ambit"
                                              && DeviceCapabilities.supportsTravelArchive)
                                         // A Kailash archive is one-way whatever else is in it.
                                         && modelData.deviceHint !== "kailash"
                                text: qsTr("Restore")
                                onClicked: BackupService.restoreBackup(modelData.prefix, true)
                            }
                        }
                    }
                }
            }
        }

        // --- Save a copy to a folder (André, 2026-08-16): replaces the cloud-OAuth
        // destinations (no keys, no sign-in). Point it at a Dropbox/OneDrive/Drive sync folder
        // and the backup lands there; the desktop sync client carries it to the cloud. ---
        Card {
            width: parent.width
            visible: !HomeViewModel.isGarmin && (DeviceCapabilities.supportsWatchBackup
                                                 || DeviceCapabilities.supportsTravelArchive)
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                // Title + a "little i" that expands the cloud-folder hint (same affordance as
                // Settings' orbital info).
                Row {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Text {
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("Backup database to folder")
                        font.bold: true
                        color: Theme.text
                    }
                    Rectangle {
                        anchors.verticalCenter: parent.verticalCenter
                        width: 15; height: 15; radius: 7.5
                        color: "transparent"
                        border.width: 1
                        border.color: folderInfo.open ? Theme.primary : Theme.mutedText
                        Text {
                            anchors.centerIn: parent
                            text: "i"
                            font.pixelSize: Theme.fontSizeLabel
                            font.bold: true
                            color: folderInfo.open ? Theme.primary : Theme.mutedText
                        }
                        MouseArea {
                            anchors.fill: parent
                            cursorShape: Qt.PointingHandCursor
                            onClicked: folderInfo.open = !folderInfo.open
                        }
                    }
                }

                Text {
                    id: folderInfo
                    property bool open: false
                    visible: open
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("You can save it to your favourite cloud folder, so it can be " +
                                "synced if you wish.")
                }

                RoundedButton {
                    text: BackupService.loading ? qsTr("Working…")
                                                : qsTr("Save a backup to a folder…")
                    enabled: !BackupService.loading
                    onClicked: backupFolderDialog.open()
                }
            }
        }

        // Ember backup (André, 2026-08-26: "afaik there is zero GUI service to backup it
        // somewhere") - its own always-reachable card, since the watch-backup card above only
        // shows for a connected Suunto watch that supports routes, and Ember has nothing to do
        // with the watch. Same keyless mechanism as everything else on this page: point it at
        // any folder, including one Dropbox/OneDrive/Drive syncs for you - no sign-in, no API
        // keys, nothing for André (or anyone self-hosting Sommet) to register with a provider.
        Card {
            width: parent.width
            visible: Theme.emberUnlocked && !root.watchBackupShown
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Text { text: qsTr("Ember backup"); font.bold: true; color: Theme.text }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Your fasting, calories, coffee and water history. Save it to " +
                                "any folder - point it at a Dropbox/OneDrive/Drive-synced " +
                                "folder and it backs up to the cloud too, no sign-in needed.")
                }

                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        text: BackupService.loading ? qsTr("Working…") : qsTr("Create backup now")
                        enabled: !BackupService.loading
                        onClicked: BackupService.createBackup()
                    }
                    RoundedButton {
                        text: qsTr("Save to a folder…")
                        enabled: !BackupService.loading
                        onClicked: backupFolderDialog.open()
                    }
                }

                Text {
                    visible: BackupService.lastActionText.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: BackupService.lastActionOk ? Theme.success : Theme.error
                    text: BackupService.lastActionText
                }

                Text {
                    text: qsTr("Existing backups")
                    font.bold: true
                    color: Theme.text
                    topPadding: Theme.spacingSmall
                }
                Text {
                    visible: BackupService.backups.filter(function (b) { return b.hasEmber }).length === 0
                    text: qsTr("None yet.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                }
                Repeater {
                    model: BackupService.backups.filter(function (b) { return b.hasEmber })
                    delegate: Column {
                        width: parent.width
                        spacing: 4
                        Text {
                            text: DateFormat.dateTime(new Date(modelData.createdAt * 1000))
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBody
                        }
                        Row {
                            spacing: Theme.spacingSmall
                            RoundedButton {
                                text: qsTr("Open backup folder")
                                onClicked: LocalFileService.openFolder(LocalFileService.backupsLocation)
                            }
                            RoundedButton {
                                text: qsTr("Restore")
                                onClicked: BackupService.restoreBackup(modelData.prefix, true)
                            }
                        }
                    }
                }
            }
        }

        // Firmware backup ("Download for backup") moved to the Firmware page 2026-08-14
        // (André) so everything firmware lives in one place; it is still backed by the same
        // BackupService.* download logic, just rendered there now.

        // --- Garmin backup - real, 2026-08-08 ("backups gpx from Garmin\GPX ... both
        // from internal memory and sdcard to a folder that user should choose, by
        // default Downloads"). Real file copy, not a database export or a re-serialized
        // parse - GarminService.backupToFolder() copies every real .gpx file already
        // sitting in Garmin/GPX on every mounted volume (internal memory and SD card)
        // into one subfolder per volume. No separate Garmin\POI folder exists on real
        // hardware (confirmed against real hardware, GARMIN_USB_IMPORT_SPEC.md) - POI
        // files already live inside the same Garmin/GPX folder as routes, just named
        // "Waypoints*.gpx", so backing up that one real folder covers both. ---
        Card {
            width: parent.width
            visible: HomeViewModel.isGarmin
            Column {
                width: parent.width
                spacing: Theme.spacingSmall

                Text { text: qsTr("Garmin backup"); font.bold: true; color: Theme.text }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Copies every real GPX file from Garmin/GPX on this device " +
                                "- routes and POIs together, since they live in the same " +
                                "real folder on real hardware - from both internal memory " +
                                "and the SD card if one is present.")
                }

                // Real request 2026-08-08: "rename to Create backup now, to match Suunto
                // Backup and restore" - still opens the folder-choose dialog first (a real
                // difference from Suunto's own fixed ~/AmbitAppBackups location), just
                // worded the same way.
                RoundedButton {
                    text: GarminService.backingUp ? qsTr("Working…") : qsTr("Create backup now")
                    enabled: !GarminService.backingUp
                    onClicked: garminBackupDialog.open()
                }

                Text {
                    visible: GarminService.backupResultText.length > 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    font.pixelSize: Theme.fontSizeCaption
                    color: GarminService.backupOk ? Theme.success : Theme.error
                    text: GarminService.backupResultText
                }
            }
        }
    }
}
