import QtQuick
import QtQuick.Controls
import AmbitApp

// Build a multisport combo: a name, which kind it is, and the ORDER the watch steps through.
//
// Laid out the way SuuntoLink's own "Create multisport mode" screen is (André's screenshots
// multisport_min2_max6.JPG / triathlonmin2max6withtransition.JPG / adventureracingmin2max6.JPG):
// "Select activity" above, then "Select order of the sport modes" as a numbered list with a
// bin on each row and "Add a sport mode" under it. Same shape, this app's components.
//
// What a combo IS, which is why this dialog has no displays in it: one SPORT_MODES entry
// naming sport modes that already exist, in order. It creates no mode of its own and has no
// screens of its own - the legs bring theirs. See docs/reference/ (custom-modes notes)'s 2026-08-12 section.
//
// The rules enforced here are the same ones tools/sport_mode_manage.py enforces on the way
// to the watch, so the button being disabled and the write being refused can never disagree:
//   * 2 to 6 legs (SuuntoLink's own MAX_MULTISPORT_SPORT_MODES, and its Create screen's min)
//   * repeats allowed - that is how a triathlon gets two transitions
//   * only the three container activities, and a transition must already exist as a mode
ThemedDialog {
    id: root

    // action is "create" or "edit"; `originalName` identifies which combo is being edited.
    signal saveRequested(string action, string originalName, string name,
                          int activityId, var legs)

    property string action: "create"
    property string originalName: ""
    property string modeName: ""
    property int activityId: -1
    // Ordered leg mode names. Plain JS array; a repeat is a real, valid entry.
    property var legs: []
    // Which single-sport modes exist to pick from, as [{name, activityId}].
    property var availableModes: []
    // Combo names already taken (excluding the one being edited).
    property var takenNames: []

    readonly property int dialogWidth: 460
    readonly property int minLegs: CustomModesService.minMultisportLegs
    readonly property int maxLegs: CustomModesService.maxMultisportLegs
    readonly property bool nameTaken:
        modeName.length > 0 && takenNames.indexOf(modeName) !== -1
    readonly property bool enoughLegs: legs.length >= minLegs && legs.length <= maxLegs
    readonly property bool canSave:
        modeName.length > 0 && activityId >= 0 && enoughLegs && !nameTaken

    // Only the three activities that may hold several legs - Multisport, Triathlon and
    // Adventure racing, from activity.js's own getMultisportPhrases(). Never hard-coded
    // here: the flag comes down with the catalogue.
    readonly property var containers: {
        const out = []
        const all = CustomModesService.activities
        for (let i = 0; i < all.length; i++)
            if (all[i].isMultisport)
                out.push(all[i])
        return out
    }

    title: action === "create" ? qsTr("Create multisport mode")
                                : qsTr("Edit multisport mode")
    standardButtons: Dialog.Cancel

    // An explicit width, so the dialog does not derive one from its own contents. Without
    // this Qt warns "Binding loop detected for property implicitWidth": Dialog sizes itself
    // from contentItem while the Column inside sizes its children from the Column's width,
    // and a wrapping Text closes the circle. Fixing it here rather than unpicking the
    // wrapping, which is what makes the long explanatory lines readable.
    implicitWidth: dialogWidth + padding * 2

    function openCreate(modes, taken) {
        action = "create"
        originalName = ""
        availableModes = modes || []
        takenNames = taken || []
        modeName = ""
        nameField.text = ""
        activityId = containers.length > 0 ? containers[0].id : -1
        legs = []
        open()
    }

    function openEdit(combo, modes, taken) {
        action = "edit"
        originalName = combo.name
        availableModes = modes || []
        takenNames = taken || []
        modeName = combo.name
        nameField.text = combo.name
        activityId = combo.activityId
        legs = combo.legNames.slice()
        open()
    }

    function addLeg(name) {
        const next = legs.slice()
        next.push(name)
        legs = next
    }

    function removeLeg(index) {
        const next = legs.slice()
        next.splice(index, 1)
        legs = next
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
                maximumLength: 63
                placeholderText: qsTr("e.g. Triathlon")
                onTextChanged: root.modeName = text
            }
            Text {
                visible: root.nameTaken
                text: qsTr("A mode with this name already exists.")
                color: Theme.error
                font.pixelSize: Theme.fontSizeCaption
            }
        }

        Column {
            width: parent.width
            spacing: 4
            Text {
                text: qsTr("Select activity")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeLabel
            }
            Row {
                spacing: Theme.spacingSmall
                Repeater {
                    model: root.containers
                    delegate: RoundedButton {
                        required property var modelData
                        text: modelData.name
                        checkable: true
                        checked: root.activityId === modelData.id
                        onClicked: root.activityId = modelData.id
                    }
                }
            }
        }

        Column {
            width: parent.width
            spacing: 4
            Text {
                text: qsTr("Select order of the sport modes")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeLabel
            }
            // SuuntoLink's own explanation of what the order means on the watch, kept
            // because it is the only place the button that switches legs is named.
            Text {
                width: parent.width
                wrapMode: Text.WordWrap
                text: qsTr("On the watch you move to the next sport by holding BACK LAP.")
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
            }
        }

        Column {
            width: parent.width
            spacing: Theme.spacingSmall

            Repeater {
                model: root.legs
                delegate: Rectangle {
                    id: legRow
                    required property var modelData
                    required property int index
                    width: parent.width
                    height: 40
                    radius: Theme.radiusSmall
                    color: Theme.background

                    Text {
                        anchors.left: parent.left
                        anchors.leftMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("%1: %2").arg(legRow.index + 1).arg(legRow.modelData)
                        color: Theme.text
                        font.pixelSize: Theme.fontSizeBody
                    }

                    Icon {
                        anchors.right: parent.right
                        anchors.rightMargin: Theme.spacingMedium
                        anchors.verticalCenter: parent.verticalCenter
                        glyph: Icons.delete_
                        size: 20
                        color: Theme.mutedText
                        TapHandler { onTapped: root.removeLeg(legRow.index) }
                    }
                }
            }

            RoundedButton {
                text: qsTr("Add a sport mode")
                // The 6-leg ceiling is the reason this goes flat rather than disappearing:
                // a control that vanishes leaves the user guessing why.
                enabled: root.legs.length < root.maxLegs
                onClicked: legMenu.open()

                Menu {
                    id: legMenu
                    Repeater {
                        model: root.availableModes
                        delegate: MenuItem {
                            required property var modelData
                            text: modelData.name
                            onTriggered: root.addLeg(modelData.name)
                        }
                    }
                }
            }
        }

        // One line that always says where you stand against the two bounds, rather than a
        // disabled Save with no reason attached.
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: root.legs.length < root.minLegs
                  ? qsTr("A multisport mode needs at least %1 sports.").arg(root.minLegs)
                  : (root.legs.length >= root.maxLegs
                     ? qsTr("%1 of %2 sports - this is the maximum.")
                        .arg(root.legs.length).arg(root.maxLegs)
                     : qsTr("%1 of %2 sports.").arg(root.legs.length).arg(root.maxLegs))
            color: root.enoughLegs ? Theme.mutedText : Theme.error
            font.pixelSize: Theme.fontSizeCaption
        }

        Row {
            width: parent.width
            spacing: Theme.spacingSmall
            layoutDirection: Qt.RightToLeft

            RoundedButton {
                text: qsTr("Save")
                enabled: root.canSave
                onClicked: {
                    root.saveRequested(root.action, root.originalName, root.modeName,
                                        root.activityId, root.legs)
                    root.close()
                }
            }
        }
    }
}
