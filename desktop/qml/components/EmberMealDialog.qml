import QtQuick
import QtQuick.Controls
import AmbitApp

// Ember "log a meal" - search Open Food Facts + USDA by name, or type/scan a barcode (a USB
// scanner types the digits here), pick a food, set the grams, log. Recent picks show while the
// box is empty (offline too); "Just kcal" logs a number when nothing matches. Issue #20, twin of
// android/src/components/EmberMealModal.tsx.
ThemedDialog {
    id: root
    width: 520
    title: picked ? picked.name : qsTr("Log a meal")

    property bool fasting: false
    property var picked: null
    signal logMeal(var meal)   // {name, kcal, protein, carbs, fat}

    readonly property real grams: Number(gramsField.text.replace(",", "."))
    readonly property var portionNow: picked && grams > 0 ? EmberFoodService.portion(picked, grams) : null
    readonly property bool _searchingText: searchField.text.trim().length >= 2

    onOpened: {
        picked = null
        searchField.text = ""
        manualField.text = ""
        EmberFoodService.clear()
        searchField.forceActiveFocus()
    }

    Timer {
        id: debounce
        interval: /^\d{8,14}$/.test(searchField.text.trim()) ? 0 : 450
        onTriggered: EmberFoodService.search(searchField.text)
    }

    function pick(food) {
        picked = food
        gramsField.text = "" + Math.round(food.servingG || 100)
        gramsField.forceActiveFocus()
        gramsField.selectAll()
    }

    Column {
        width: 480
        spacing: Theme.spacingSmall

        // --- search ---
        Column {
            width: parent.width
            spacing: Theme.spacingSmall
            visible: root.picked === null

            RoundedTextField {
                id: searchField
                width: parent.width
                horizontalAlignment: TextInput.AlignLeft
                placeholderText: qsTr("Search a food, or type / scan a barcode")
                onTextChanged: debounce.restart()
                onAccepted: { debounce.stop(); EmberFoodService.search(text) }
            }
            Text {
                text: root._searchingText ? qsTr("Open Food Facts · USDA FoodData Central")
                                          : (EmberFoodService.recent.length ? qsTr("Recent") : "")
                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
            }
            BusyIndicator {
                visible: EmberFoodService.searching && EmberFoodService.results.length === 0
                running: visible
                anchors.horizontalCenter: parent.horizontalCenter
            }
            ListView {
                id: list
                width: parent.width
                height: Math.min(contentHeight, 320)
                clip: true
                model: root._searchingText ? EmberFoodService.results : EmberFoodService.recent
                delegate: Rectangle {
                    required property var modelData
                    width: list.width; height: 48; radius: Theme.radiusSmall
                    color: rowHover.containsMouse
                           ? Qt.rgba(Theme.accent.r, Theme.accent.g, Theme.accent.b, 0.10) : "transparent"
                    Column {
                        anchors.left: parent.left; anchors.leftMargin: Theme.spacingSmall
                        anchors.right: kcalText.left; anchors.rightMargin: Theme.spacingSmall
                        anchors.verticalCenter: parent.verticalCenter; spacing: 1
                        Text { width: parent.width; text: modelData.name; elide: Text.ElideRight
                               color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true }
                        Text { width: parent.width; text: modelData.brand || ""; elide: Text.ElideRight
                               visible: text.length > 0; color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption }
                    }
                    Text {
                        id: kcalText
                        anchors.right: parent.right; anchors.rightMargin: Theme.spacingSmall
                        anchors.verticalCenter: parent.verticalCenter
                        text: qsTr("%1 kcal/100 g").arg(Math.round(modelData.kcal100))
                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                    }
                    MouseArea { id: rowHover; anchors.fill: parent; hoverEnabled: true
                                cursorShape: Qt.PointingHandCursor; onClicked: root.pick(modelData) }
                }
            }
            Text {
                visible: root._searchingText && !EmberFoodService.searching && EmberFoodService.results.length === 0
                text: qsTr("Nothing found (or offline).")
                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
            }
            Row {
                spacing: Theme.spacingSmall
                RoundedTextField {
                    id: manualField
                    width: 140
                    placeholderText: qsTr("Just kcal")
                    validator: IntValidator { bottom: 1; top: 10000 }
                    onAccepted: manualBtn.clicked()
                }
                RoundedButton {
                    id: manualBtn
                    text: qsTr("Log")
                    enabled: Number(manualField.text) > 0
                    onClicked: {
                        root.logMeal({ "name": searchField.text.trim() || qsTr("Meal"),
                                       "kcal": Math.round(Number(manualField.text)), "protein": 0, "carbs": 0, "fat": 0 })
                        root.close()
                    }
                }
            }
        }

        // --- portion ---
        Column {
            width: parent.width
            spacing: Theme.spacingSmall
            visible: root.picked !== null

            Text {
                text: root.picked ? (root.picked.brand ? root.picked.brand + " · " : "")
                                    + qsTr("%1 kcal per 100 g").arg(Math.round(root.picked.kcal100)) : ""
                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
            }
            Row {
                spacing: Theme.spacingSmall
                RoundedTextField {
                    id: gramsField
                    width: 100
                    validator: DoubleValidator { bottom: 0; top: 5000 }
                    onAccepted: logBtn.clicked()
                }
                Text { text: qsTr("g"); color: Theme.text; font.pixelSize: Theme.fontSizeBody
                       anchors.verticalCenter: parent.verticalCenter }
            }
            Text {
                visible: root.portionNow !== null
                text: root.portionNow ? qsTr("%1 kcal").arg(root.portionNow.kcal) : ""
                color: Theme.text; font.pixelSize: Theme.fontSizeSubtitle; font.bold: true
            }
            Text {
                visible: root.portionNow !== null
                text: root.portionNow ? qsTr("Protein %1 g · Carbs %2 g · Fat %3 g")
                                            .arg(root.portionNow.protein).arg(root.portionNow.carbs).arg(root.portionNow.fat) : ""
                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
            }
            Text {
                visible: root.fasting
                text: qsTr("Logging this ends your fast.")
                color: Theme.warning; font.pixelSize: Theme.fontSizeCaption
            }
            Row {
                anchors.right: parent.right
                spacing: Theme.spacingSmall
                RoundedButton { text: qsTr("Back"); onClicked: { root.picked = null; searchField.forceActiveFocus(); searchField.selectAll() } }
                RoundedButton {
                    id: logBtn
                    text: qsTr("Log meal")
                    enabled: root.portionNow !== null
                    onClicked: {
                        EmberFoodService.remember(root.picked)
                        root.logMeal(Object.assign({ "name": qsTr("%1 (%2 g)").arg(root.picked.name).arg(Math.round(root.grams)) },
                                                   root.portionNow))
                        root.close()
                    }
                }
            }
        }

        Text {
            width: parent.width; wrapMode: Text.WordWrap
            text: qsTr("Food data © Open Food Facts contributors (ODbL) and USDA FoodData Central.")
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeTiny
        }
    }
}
