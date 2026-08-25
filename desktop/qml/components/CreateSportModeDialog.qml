import QtQuick
import QtQuick.Controls
import AmbitApp

// Create one single-sport mode: give it a name, pick what sport it is.
//
// The same two things SuuntoLink asks for, in the same order, because they are the only two
// the watch needs - everything else about a new mode (its recording interval, its alti/baro
// profile, which pods it looks for, its one starting screen) comes from the sport itself,
// from SuuntoLink's own getActivityDefaults table. Asking the user would be inventing a
// question Suunto never asked.
//
// The activity list is the real catalogue - all 84 from assets/activity_types.json, the same
// file the badges are drawn from - filtered live by the search box, because 84 is too many to
// scroll. The three multisport containers are deliberately NOT offered here: a combo is made
// from modes that already exist, so it has its own editor (MultisportEditorDialog) rather
// than a slot in this list.
ThemedDialog {
    id: root

    // Emitted with what the watch should be asked for; the page does the writing.
    signal createRequested(string name, int activityId)

    title: qsTr("Create sport mode")

    // An explicit width, so the dialog does not derive one from its own contents. Without
    // this Qt warns "Binding loop detected for property implicitWidth": Dialog sizes itself
    // from contentItem while the Column inside sizes its children from the Column's width,
    // and a wrapping Text closes the circle. Fixing it here rather than unpicking the
    // wrapping, which is what makes the long explanatory lines readable.
    implicitWidth: dialogWidth + padding * 2
    standardButtons: Dialog.Cancel

    property string modeName: ""
    property int activityId: -1
    // Names already taken, so the dialog can say so before a write is attempted rather than
    // after. The tool refuses either way - this just makes the refusal arrive earlier.
    property var existingNames: []

    readonly property int dialogWidth: 420
    readonly property bool nameTaken:
        modeName.length > 0 && existingNames.indexOf(modeName) !== -1
    readonly property bool canSave: modeName.length > 0 && activityId >= 0 && !nameTaken

    // Recomputed once per keystroke rather than once per delegate.
    readonly property var filtered: {
        const all = CustomModesService.activities
        const needle = searchField.text.toLowerCase()
        const out = []
        for (let i = 0; i < all.length; i++) {
            // A multisport container is not a sport you can record on its own.
            if (all[i].isMultisport)
                continue
            if (needle.length === 0 || all[i].name.toLowerCase().indexOf(needle) !== -1)
                out.push(all[i])
        }
        return out
    }

    function openFresh(taken) {
        existingNames = taken || []
        modeName = ""
        activityId = -1
        nameField.text = ""
        searchField.text = ""
        open()
    }

    contentItem: Column {
        width: root.dialogWidth
        spacing: Theme.spacingMedium

        Column {
            width: parent.width
            spacing: 4
            Text {
                text: qsTr("Name")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeLabel
            }
            RoundedTextField {
                id: nameField
                width: parent.width
                // 63 bytes is SuuntoLink's own getMaxNameLength() for this whole watch
                // family; the field stops there rather than letting a write fail on it.
                maximumLength: 63
                placeholderText: qsTr("e.g. Trail running")
                onTextChanged: root.modeName = text
            }
            Text {
                visible: root.nameTaken
                text: qsTr("A sport mode with this name already exists.")
                color: Theme.error
                font.pixelSize: Theme.fontSizeCaption
            }
        }

        Column {
            width: parent.width
            spacing: 4
            Text {
                text: qsTr("Activity")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeLabel
            }
            RoundedTextField {
                id: searchField
                width: parent.width
                placeholderText: qsTr("Search activities")
            }
        }

        // Real, 2026-08-25 (app-wide coherence pass): Theme.background/Theme.mutedText was the
        // OLD "recessed area" pairing, predating the tune-up's own Theme.cardNested/Theme.border
        // tokens built for exactly this - a nested scroll area inside a dialog surface.
        Rectangle {
            width: parent.width
            height: 240
            radius: Theme.radiusSmall
            color: Theme.cardNested
            border.width: 1
            border.color: Theme.border

            ListView {
                id: activityList
                anchors.fill: parent
                anchors.margins: 1
                clip: true
                model: root.filtered
                boundsBehavior: Flickable.StopAtBounds
                ScrollBar.vertical: ScrollBar {}

                delegate: Item {
                    id: activityRow
                    required property var modelData
                    readonly property bool chosen: root.activityId === modelData.id
                    width: activityList.width
                    height: 40

                    Rectangle {
                        anchors.fill: parent
                        anchors.margins: 2
                        radius: Theme.radiusSmall
                        color: activityRow.chosen ? Theme.primary : "transparent"
                    }

                    Row {
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.spacingSmall
                        anchors.verticalCenter: parent.verticalCenter
                        spacing: Theme.spacingSmall

                        ActivityBadge {
                            anchors.verticalCenter: parent.verticalCenter
                            activityId: activityRow.modelData.id
                            size: 26
                        }
                        Text {
                            anchors.verticalCenter: parent.verticalCenter
                            text: activityRow.modelData.name
                            color: activityRow.chosen ? Theme.card : Theme.text
                            font.pixelSize: Theme.fontSizeBody
                        }
                    }

                    TapHandler {
                        onTapped: {
                            root.activityId = activityRow.modelData.id
                            // Naming a mode after its sport is what most people want and
                            // what SuuntoLink pre-fills; still fully editable afterwards.
                            if (nameField.text.length === 0)
                                nameField.text = activityRow.modelData.name
                        }
                    }
                }
            }
        }

        Row {
            width: parent.width
            spacing: Theme.spacingSmall
            layoutDirection: Qt.RightToLeft

            RoundedButton {
                text: qsTr("Create")
                enabled: root.canSave
                onClicked: {
                    root.createRequested(root.modeName, root.activityId)
                    root.close()
                }
            }
        }
    }
}
