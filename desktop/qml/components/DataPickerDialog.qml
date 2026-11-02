import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import AmbitApp

// SuuntoLink's own "CHOOSE WHAT TO SHOW IN THIS ROW" screen (its Figure 5, and
// assets/ambit3 pcap/v2/screens sports modes/display1row1graphoptions.JPG): the values
// grouped into Suunto's own categories - Speed, Distance/GPS, Heart rate, Altitude,
// Environment, Time, Power, Cadence, Multisport - plus a Suunto Apps section.
//
// Two things this deliberately does NOT do, both of them earlier mistakes here:
//
//   * It does not list the whole 95-entry field catalogue. What may go on a row depends on
//     the mode's SPORT, the display's TYPE and WHICH row it is - a swimming mode is not
//     offered cycling power, and a graph's row is not offered what a 3-field row is. The
//     backend answers that per row from SuuntoLink's own module; showing the full catalogue
//     let a user pick a value the watch does not support for that sport, which is how a
//     display ends up reading "--".
//   * It does not write on click. Add/remove/retype already stage their changes and save
//     once, because the watch has no per-field command for sport modes - every save rewrites
//     the whole region. A row edit staging itself the same way keeps one Save button
//     meaning one write, which is also what SuuntoLink does.
ThemedDialog {
    id: root
    title: qsTr("Choose what to show in this row")
    width: 460
    height: 600
    standardButtons: Dialog.Cancel | Dialog.Ok

    property string modeName: ""
    property int modeIndex: -1
    property int activityId: 1
    property int displayIndex: -1
    property int fieldIndex: -1
    property int displayTemplate: 260
    property string rowName: "TOP"
    // Whether the page currently holds unsaved display edits. Installing an app writes the
    // CustomModes region immediately, so doing it on top of staged edits would have the
    // save afterwards rebuild from a read taken before the install - see the Suunto Apps
    // section below.
    property bool hasPendingEdits: false
    // Suunto Apps already installed on this mode. SuuntoLink's own getMaxSuuntoApps() for
    // this watch family is 5, confirmed twice: from its module, and from André's Running2
    // carrying exactly 5 rules referenced as field ids 51/52/53/107/108.
    property int appCount: 0
    readonly property int maxApps: 5

    // Field ids currently ticked, in the order chosen - the watch shows a multi-value row's
    // values in this order, so it is meaningful, not just a set.
    property var selected: []

    readonly property bool multiValue: CustomModesService.rowMenuMultiValue
    readonly property int maxValues: CustomModesService.rowMenuMaxValues

    // Emitted instead of writing: the page stages it alongside the other pending edits.
    signal rowChosen(int displayIndex, string rowName, var fieldIds)

    function isSelected(fieldId) { return selected.indexOf(fieldId) >= 0 }

    function toggle(fieldId) {
        const next = selected.slice()
        const at = next.indexOf(fieldId)
        if (!multiValue) {
            selected = [fieldId]
            return
        }
        if (at >= 0)
            next.splice(at, 1)
        else if (next.length < maxValues)
            next.push(fieldId)
        selected = next
    }

    onOpened: {
        appSearchField.text = ""
        CustomModesService.refreshRowMenu(activityId, displayTemplate, rowName)
        AppsService.refreshCatalogStatus()
        AppsService.searchCatalog("", DeviceService.model, -1)
    }

    // Import the user's own SuuntoLink catalogue (suunto-apps/index.json). AmbitApp ships no
    // Suunto app content; each user supplies their own licensed copy. Real, 2026-08-14.
    FileDialog {
        id: indexFileDialog
        title: qsTr("Select SuuntoLink suunto-apps/index.json")
        nameFilters: [qsTr("index.json (index.json)"), qsTr("JSON files (*.json)"), qsTr("All files (*)")]
        onAccepted: AppsService.importCatalog(selectedFile)
    }

    onAccepted: {
        if (selected.length > 0)
            rowChosen(displayIndex, rowName, selected)
    }

    PageFlickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: contentColumn.height
        clip: true

        Column {
            id: contentColumn
            width: parent.width
            spacing: Theme.spacingMedium

            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: root.multiValue
                    ? qsTr("This row can hold up to %1 values - the watch steps through " +
                            "them on a button press. %2 chosen.")
                        .arg(root.maxValues).arg(root.selected.length)
                    : qsTr("This row holds one value.")
            }

            Text {
                visible: CustomModesService.rowMenu.length === 0
                width: parent.width
                wrapMode: Text.WordWrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                text: qsTr("No values are offered for this row.")
            }

            // --- the values, in Suunto's own categories and order ---
            Repeater {
                model: CustomModesService.rowMenu
                delegate: Column {
                    id: categoryColumn
                    required property var modelData
                    width: contentColumn.width
                    spacing: 2

                    Text {
                        text: categoryColumn.modelData.label
                        font.bold: true
                        color: Theme.text
                        topPadding: Theme.spacingSmall
                    }

                    Repeater {
                        model: categoryColumn.modelData.values
                        delegate: Item {
                            id: valueRow
                            required property var modelData
                            width: categoryColumn.width
                            height: 30

                            Row {
                                anchors.verticalCenter: parent.verticalCenter
                                spacing: Theme.spacingSmall

                                // Radio for a one-value row, checkbox for a multi-value one -
                                // the same distinction SuuntoLink draws.
                                Rectangle {
                                    width: 14
                                    height: 14
                                    anchors.verticalCenter: parent.verticalCenter
                                    radius: root.multiValue ? 3 : 7
                                    border.width: 1
                                    border.color: Theme.mutedText
                                    color: root.isSelected(valueRow.modelData.fieldId)
                                        ? Theme.accent : "transparent"
                                }
                                Text {
                                    anchors.verticalCenter: parent.verticalCenter
                                    text: valueRow.modelData.label
                                    color: Theme.text
                                    font.pixelSize: Theme.fontSizeBody
                                }
                            }

                            MouseArea {
                                anchors.fill: parent
                                onClicked: root.toggle(valueRow.modelData.fieldId)
                            }
                        }
                    }
                }
            }

            // --- Suunto Apps ---
            // An app is not a menu row: it is picked from the catalogue and installed onto
            // the mode, then rendered on a row. That install writes BOTH the Apps region and
            // CustomModes through workout_install.py - real, hardware-confirmed flash-write
            // code with its own logic - so it applies immediately rather than staging with
            // the row edits, which only touch CustomModes.
            //
            // That difference is a real hazard, not just an inconsistency: installing while
            // display edits are staged would rewrite CustomModes from the watch's current
            // state, and the later Save would then rebuild from a read taken BEFORE that
            // install and undo it. So the section is disabled until the staged edits are
            // saved or discarded, and says why.
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text {
                    text: qsTr("Suunto Apps (%1/%2)").arg(root.appCount).arg(root.maxApps)
                    font.bold: true
                    color: Theme.text
                    topPadding: Theme.spacingSmall
                }

                // Import prompt - shown until a catalogue has been imported. AmbitApp ships no
                // Suunto app content (proprietary, and the App Zone service is dead), so the
                // user imports their own SuuntoLink suunto-apps/index.json. On Linux, where
                // SuuntoLink isn't installed, they copy that file from a Windows/Mac install.
                Text {
                    visible: !AppsService.hasCatalog
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("No Suunto App catalogue yet. Sommet ships none - they are " +
                                "Suunto's own apps. Import your own from SuuntoLink: point to " +
                                "its suunto-apps/index.json (on Linux, copy that file over from " +
                                "a Windows/Mac SuuntoLink install first).")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                }
                Row {
                    spacing: Theme.spacingSmall
                    RoundedButton {
                        enabled: !AppsService.importing
                        text: AppsService.hasCatalog
                              ? qsTr("Re-import from SuuntoLink...")
                              : qsTr("Import from SuuntoLink...")
                        busy: AppsService.importing
                        onClicked: indexFileDialog.open()
                    }
                    Text {
                        visible: AppsService.hasCatalog && !AppsService.importing
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("%1 apps").arg(AppsService.catalogCount)
                        color: Theme.mutedText
                        font.pixelSize: Theme.fontSizeCaption
                    }
                }
                Text {
                    visible: root.appCount >= root.maxApps
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("This mode already has the maximum of %1 Suunto Apps. " +
                                "Remove one on the watch before adding another.")
                            .arg(root.maxApps)
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                }
                Text {
                    visible: root.hasPendingEdits
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Save or discard your unsaved display changes first - " +
                                "installing an app writes to the watch straight away, and " +
                                "would be undone by saving those changes afterwards.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                }
                SearchField {
                    id: appSearchField
                    width: parent.width
                    visible: AppsService.hasCatalog
                    enabled: !root.hasPendingEdits && root.appCount < root.maxApps
                    placeholderText: qsTr("Search Suunto Apps...")
                    onTextChanged: AppsService.searchCatalog(text, DeviceService.model, -1)
                }
                Text {
                    visible: !root.hasPendingEdits && AppsService.searching
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("Searching...")
                }
                Text {
                    visible: AppsService.hasCatalog && !root.hasPendingEdits
                             && root.appCount < root.maxApps
                             && appSearchField.text.trim().length === 0
                    width: parent.width
                    wrapMode: Text.WordWrap
                    text: qsTr("Type to search the Suunto App catalogue.")
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                }
                Text {
                    visible: !root.hasPendingEdits && !AppsService.searching
                             && appSearchField.text.trim().length > 0
                             && AppsService.searchResults.length === 0
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeCaption
                    text: qsTr("No matching apps.")
                }
                Repeater {
                    // Narrows as you type - André, item 6. Nothing is listed until there is
                    // something to narrow BY: 13,104 apps rendered flat is not a menu.
                    model: (root.hasPendingEdits || root.appCount >= root.maxApps
                            || appSearchField.text.trim().length === 0)
                           ? [] : AppsService.searchResults
                    delegate: Column {
                        id: appRow
                        required property var modelData
                        width: contentColumn.width
                        spacing: 2
                        Text {
                            text: appRow.modelData.name
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBody
                            font.bold: true
                        }
                        Text {
                            width: parent.width
                            visible: text.length > 0
                            text: appRow.modelData.description || ""
                            color: Theme.mutedText
                            font.pixelSize: Theme.fontSizeCaption
                            wrapMode: Text.WordWrap
                            maximumLineCount: 2
                            elide: Text.ElideRight
                        }
                        MouseArea {
                            width: parent.width
                            height: 40
                            onClicked: {
                                AppsService.install(root.modeIndex, root.displayIndex,
                                                     root.fieldIndex, appRow.modelData.ruleId,
                                                     true)
                                root.close()
                            }
                        }
                    }
                }
            }
        }
    }
}
