import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import AmbitApp

// Journal — the "Personal Connection MVP" (2026-09-21). One screen for the whole loop:
// write freely → see what Sommet extracted → it's enriched with activity/weather →
// notice tentative patterns → ask a free-form reflection → optionally try a small
// experiment. Backed entirely by JournalService (local journal.db + the SAME Anthropic
// key Coach already uses). No scores, no streaks — awareness, not optimisation.
Item {
    id: root

    readonly property string today: Qt.formatDate(new Date(), "yyyy-MM-dd")

    Component.onCompleted: JournalService.refresh()

    function kindColor(kind, status) {
        if (kind === "quiet") return Theme.mutedText
        if (status === "supported") return Theme.success
        if (status === "contradicted") return Theme.mutedText
        return Theme.primary
    }

    PageFlickable {
        anchors.fill: parent
        contentWidth: width
        contentHeight: col.implicitHeight + 2 * Theme.spacingLarge

        ColumnLayout {
            id: col
            x: Theme.spacingLarge
            y: Theme.spacingLarge
            width: root.width - 2 * Theme.spacingLarge
            spacing: Theme.spacingMedium

            // ---- header ----
            ColumnLayout {
                Layout.fillWidth: true
                spacing: 2
                Text { text: qsTr("Journal"); color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeTitle }
                Text {
                    text: qsTr("Write about your day. Sommet quietly notices connections — it won't score you.")
                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                }
            }

            // ---- 1) How was your day? ----
            Card {
                Layout.fillWidth: true
                padding: Theme.spacingMedium
                ColumnLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Text { text: qsTr("How was your day?"); color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeSubtitle }

                    Rectangle {
                        Layout.fillWidth: true
                        Layout.preferredHeight: 160
                        radius: Theme.radiusSmall
                        color: Theme.cardNested
                        border.width: journalArea.activeFocus ? 2 : 1
                        border.color: journalArea.activeFocus ? Theme.primary : Theme.border
                        ScrollView {
                            anchors.fill: parent
                            anchors.margins: Theme.spacingSmall
                            clip: true
                            TextArea {
                                id: journalArea
                                wrapMode: TextArea.Wrap
                                color: Theme.text
                                placeholderTextColor: Theme.mutedText
                                selectionColor: Theme.primary
                                selectedTextColor: Theme.card
                                font.pixelSize: Theme.fontSizeBody
                                background: null
                                placeholderText: qsTr("Woke at 6:46… rode at midday… felt dead by 18h… wrote naturally, no format needed.")
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spacingSmall
                        Text { text: qsTr("Date"); color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        RoundedTextField {
                            id: dateField
                            text: root.today
                            Layout.preferredWidth: 120
                            horizontalAlignment: TextInput.AlignHCenter
                        }
                        Item { Layout.fillWidth: true }
                        Text {
                            visible: !JournalService.anthropicKeySet
                            text: qsTr("No API key — saved raw, extraction skipped")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                        RoundedButton {
                            text: JournalService.interpreting ? qsTr("Reading…") : qsTr("Save")
                            enabled: journalArea.text.trim().length > 0 && !JournalService.interpreting
                            onClicked: {
                                var w = ({})
                                if (WeatherService.available) {
                                    w = {
                                        temperature: WeatherService.currentTemperature,
                                        weatherCode: WeatherService.currentWeatherCode,
                                        windSpeed: WeatherService.windSpeed,
                                        high: WeatherService.todayHigh,
                                        low: WeatherService.todayLow,
                                        place: WeatherService.placeName
                                    }
                                }
                                JournalService.saveEntry(dateField.text, journalArea.text, w)
                                journalArea.text = ""
                            }
                        }
                    }
                    Text {
                        visible: JournalService.lastError.length > 0
                        text: JournalService.lastError
                        color: Theme.error; font.pixelSize: Theme.fontSizeCaption
                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                    }
                }
            }

            // ---- 2) What Sommet has noticed (plain stats) ----
            Card {
                Layout.fillWidth: true
                padding: Theme.spacingMedium
                ColumnLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Text { text: qsTr("What Sommet has noticed"); color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeSubtitle }
                    Text {
                        text: qsTr("Tentative patterns from your own days — never certainties, never advice.")
                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        Layout.fillWidth: true; wrapMode: Text.WordWrap
                    }
                    Repeater {
                        model: JournalService.insights
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            radius: Theme.radiusSmall
                            color: Theme.cardNested
                            implicitHeight: insCol.implicitHeight + 2 * Theme.spacingSmall
                            ColumnLayout {
                                id: insCol
                                x: Theme.spacingSmall; y: Theme.spacingSmall
                                width: parent.width - 2 * Theme.spacingSmall
                                spacing: 3
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.spacingSmall
                                    Rectangle {
                                        visible: (modelData.kindLabel || "").length > 0
                                        radius: 4
                                        color: Qt.rgba(root.kindColor(modelData.kind, modelData.status).r,
                                                       root.kindColor(modelData.kind, modelData.status).g,
                                                       root.kindColor(modelData.kind, modelData.status).b, 0.18)
                                        implicitWidth: kindTxt.implicitWidth + 12
                                        implicitHeight: kindTxt.implicitHeight + 6
                                        Text {
                                            id: kindTxt
                                            anchors.centerIn: parent
                                            text: (modelData.kindLabel || "").toUpperCase()
                                            color: root.kindColor(modelData.kind, modelData.status)
                                            font.pixelSize: Theme.fontSizeTiny; font.bold: true
                                        }
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.title || ""
                                        color: Theme.text; font.pixelSize: Theme.fontSizeLabel; font.bold: true
                                        wrapMode: Text.WordWrap
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.detail || ""
                                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                    wrapMode: Text.WordWrap
                                }
                                RoundedButton {
                                    visible: modelData.kind === "hypothesis" && modelData.status !== "contradicted"
                                    text: qsTr("Try a 7-day experiment")
                                    onClicked: {
                                        JournalService.createExperiment(
                                            qsTr("Test: %1").arg(modelData.title || ""),
                                            7, modelData.hypothesisId || -1)
                                    }
                                }
                            }
                        }
                    }
                }
            }

            // ---- 3) The knowledge base (1 + 2 -> 3, grows over time) ----
            Card {
                id: knowledgeCard
                Layout.fillWidth: true
                padding: Theme.spacingMedium
                function originColor(o) {
                    return o === "pubmed" ? Theme.success : o === "model" ? Theme.primary : Theme.mutedText
                }
                ColumnLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("Science Sommet has gathered"); color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeSubtitle }
                        Item { Layout.fillWidth: true }
                        Text {
                            text: qsTr("%1 in your base").arg(JournalService.knowledge.length)
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Claude's own knowledge + real Europe PMC studies, fused into a local base and cited. "
                                 + "It fills up as you deepen topics, so over time Sommet leans on this and needs the internet less.")
                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
                    }

                    // Deepen: the ONLY control that goes online — topic phrases only, never your data.
                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spacingSmall
                        RoundedButton {
                            text: JournalService.enriching ? qsTr("Deepening…") : qsTr("Deepen the science on your habits")
                            enabled: !JournalService.enriching && JournalService.anthropicKeySet
                            onClicked: JournalService.deepenScience()
                        }
                        Text {
                            Layout.fillWidth: true
                            text: JournalService.enrichStatus
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        visible: !JournalService.anthropicKeySet
                        text: qsTr("Needs an Anthropic key (Settings → Coach) to ground and cite sources.")
                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                    }
                    Text {
                        Layout.fillWidth: true
                        text: qsTr("↗ Goes online to Europe PMC (open research). Only the general topic is sent — never your journal or habits.")
                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeTiny; wrapMode: Text.WordWrap
                    }

                    // Per-topic chips drawn from YOUR habits: tap one to deepen just that topic.
                    Flow {
                        Layout.fillWidth: true
                        spacing: Theme.spacingSmall
                        visible: JournalService.knowledgeTopics.length > 0
                        Repeater {
                            model: JournalService.knowledgeTopics
                            delegate: RoundedButton {
                                text: (modelData.have ? "✓ " : "+ ") + modelData.topic
                                enabled: !modelData.have && !JournalService.enriching && JournalService.anthropicKeySet
                                onClicked: JournalService.enrichTopic(modelData.topic, modelData.query)
                            }
                        }
                    }

                    Rectangle { Layout.fillWidth: true; height: 1; color: Theme.border; visible: JournalService.knowledge.length > 0 }

                    // The base itself — cited items first.
                    Repeater {
                        model: JournalService.knowledge
                        delegate: Rectangle {
                            Layout.fillWidth: true
                            radius: Theme.radiusSmall
                            color: Theme.cardNested
                            implicitHeight: kCol.implicitHeight + 2 * Theme.spacingSmall
                            ColumnLayout {
                                id: kCol
                                x: Theme.spacingSmall; y: Theme.spacingSmall
                                width: parent.width - 2 * Theme.spacingSmall
                                spacing: 3
                                RowLayout {
                                    Layout.fillWidth: true
                                    spacing: Theme.spacingSmall
                                    Rectangle {
                                        radius: 4
                                        color: Qt.rgba(knowledgeCard.originColor(modelData.origin).r,
                                                       knowledgeCard.originColor(modelData.origin).g,
                                                       knowledgeCard.originColor(modelData.origin).b, 0.18)
                                        implicitWidth: oTxt.implicitWidth + 12
                                        implicitHeight: oTxt.implicitHeight + 6
                                        Text {
                                            id: oTxt
                                            anchors.centerIn: parent
                                            text: (modelData.origin === "pubmed" ? qsTr("CITED")
                                                 : modelData.origin === "model" ? qsTr("AI")
                                                 : qsTr("SEED")) + " · " + (modelData.evidenceLevel || "")
                                            color: knowledgeCard.originColor(modelData.origin)
                                            font.pixelSize: Theme.fontSizeTiny; font.bold: true
                                        }
                                    }
                                    Text {
                                        Layout.fillWidth: true
                                        text: modelData.topic || ""
                                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                        horizontalAlignment: Text.AlignRight
                                    }
                                    RoundedButton {
                                        text: "×"
                                        onClicked: JournalService.deleteKnowledge(modelData.id)
                                    }
                                }
                                Text {
                                    Layout.fillWidth: true
                                    text: modelData.statement || ""
                                    color: Theme.text; font.pixelSize: Theme.fontSizeLabel; wrapMode: Text.WordWrap
                                }
                                Text {
                                    visible: (modelData.caveats || "").length > 0
                                    Layout.fillWidth: true
                                    text: qsTr("Caveat: %1").arg(modelData.caveats)
                                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption; wrapMode: Text.WordWrap
                                }
                                Text {
                                    visible: (modelData.source || "").length > 0 || (modelData.sourceUrl || "").length > 0
                                    Layout.fillWidth: true
                                    text: (modelData.sourceUrl && modelData.sourceUrl.length
                                           ? '<a href="' + modelData.sourceUrl + '">' + (modelData.source || modelData.sourceUrl) + '</a>'
                                           : modelData.source || "")
                                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                    textFormat: Text.RichText; wrapMode: Text.WordWrap
                                    onLinkActivated: (url) => Qt.openUrlExternally(url)
                                    linkColor: Theme.primary
                                }
                            }
                        }
                    }
                }
            }

            // ---- 4) Habit experiments ----
            Card {
                Layout.fillWidth: true
                visible: JournalService.experiments.length > 0
                padding: Theme.spacingMedium
                ColumnLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall
                    Text { text: qsTr("Small experiments"); color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeSubtitle }
                    Repeater {
                        model: JournalService.experiments
                        delegate: RowLayout {
                            Layout.fillWidth: true
                            spacing: Theme.spacingSmall
                            ColumnLayout {
                                Layout.fillWidth: true
                                spacing: 1
                                Text {
                                    text: modelData.description || ""
                                    color: Theme.text; font.pixelSize: Theme.fontSizeLabel
                                    Layout.fillWidth: true; wrapMode: Text.WordWrap
                                }
                                Text {
                                    text: qsTr("%1 → %2 · %3").arg(modelData.startDate).arg(modelData.endDate).arg(modelData.status)
                                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                }
                            }
                            RoundedButton {
                                text: qsTr("Done")
                                visible: modelData.status === "active"
                                onClicked: JournalService.updateExperimentStatus(modelData.id, "completed")
                            }
                            RoundedButton {
                                text: qsTr("Drop")
                                onClicked: JournalService.updateExperimentStatus(modelData.id, "dismissed")
                            }
                        }
                    }
                }
            }

            // ---- 4) Ask Sommet (reflection) ----
            Card {
                Layout.fillWidth: true
                padding: Theme.spacingMedium
                ColumnLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    RowLayout {
                        Layout.fillWidth: true
                        Text { text: qsTr("Ask Sommet"); color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeSubtitle }
                        Item { Layout.fillWidth: true }
                        RoundedButton {
                            text: qsTr("Reset")
                            visible: JournalService.messages.length > 0
                            onClicked: JournalService.resetConversation()
                        }
                    }

                    Repeater {
                        model: JournalService.messages
                        delegate: Item {
                            Layout.fillWidth: true
                            implicitHeight: bubble.height
                            readonly property bool mine: modelData.role === "me"
                            Rectangle {
                                id: bubble
                                width: Math.min(bubbleText.implicitWidth + 24, parent.width * 0.85)
                                height: bubbleText.implicitHeight + 18
                                radius: 14
                                color: parent.mine ? Theme.primary : Theme.cardNested
                                anchors.right: parent.mine ? parent.right : undefined
                                anchors.left: parent.mine ? undefined : parent.left
                                Text {
                                    id: bubbleText
                                    anchors.fill: parent
                                    anchors.margins: 9
                                    text: modelData.text || ""
                                    color: parent.parent.mine ? "white" : Theme.text
                                    font.pixelSize: Theme.fontSizeBody
                                    wrapMode: Text.WordWrap
                                    textFormat: Text.PlainText
                                }
                            }
                        }
                    }

                    Text {
                        visible: JournalService.asking
                        text: qsTr("Sommet is thinking…")
                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                    }

                    // starter prompts
                    Flow {
                        Layout.fillWidth: true
                        spacing: Theme.spacingSmall
                        visible: JournalService.messages.length === 0
                        Repeater {
                            model: [
                                qsTr("Noticed anything about my energy lately?"),
                                qsTr("How do my rides relate to my mood?"),
                                qsTr("Anything worth trying this week?")
                            ]
                            delegate: RoundedButton {
                                text: modelData
                                onClicked: JournalService.ask(modelData)
                            }
                        }
                    }

                    RowLayout {
                        Layout.fillWidth: true
                        spacing: Theme.spacingSmall
                        RoundedTextField {
                            id: askInput
                            Layout.fillWidth: true
                            horizontalAlignment: TextInput.AlignLeft
                            placeholderText: qsTr("Ask about your days…")
                            onAccepted: askBtn.clicked()
                        }
                        RoundedButton {
                            id: askBtn
                            text: qsTr("Ask")
                            enabled: askInput.text.trim().length > 0 && !JournalService.asking
                            onClicked: {
                                JournalService.ask(askInput.text)
                                askInput.text = ""
                            }
                        }
                    }
                    Text {
                        Layout.fillWidth: true
                        text: qsTr("Answers separate what you said, what the data shows, what science suggests, and what Sommet only guesses.")
                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeTiny; wrapMode: Text.WordWrap
                    }
                }
            }

            // ---- 5) Past entries ----
            Text {
                text: qsTr("Your entries")
                color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeSubtitle
                Layout.topMargin: Theme.spacingSmall
                visible: JournalService.entries.length > 0
            }

            Repeater {
                model: JournalService.entries
                delegate: Card {
                    Layout.fillWidth: true
                    padding: Theme.spacingMedium
                    ColumnLayout {
                        width: parent.width
                        spacing: Theme.spacingSmall

                        RowLayout {
                            Layout.fillWidth: true
                            Text { text: modelData.date || ""; color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeLabel }
                            Item { Layout.fillWidth: true }
                            RoundedButton {
                                text: qsTr("Re-read")
                                visible: JournalService.anthropicKeySet
                                onClicked: JournalService.interpretEntry(modelData.id)
                            }
                            RoundedButton {
                                text: qsTr("Delete")
                                onClicked: JournalService.deleteEntry(modelData.id)
                            }
                        }

                        Text {
                            Layout.fillWidth: true
                            text: modelData.rawText || ""
                            color: Theme.text; font.pixelSize: Theme.fontSizeBody
                            wrapMode: Text.WordWrap
                        }

                        // enrichment strip: weather + activities
                        Flow {
                            Layout.fillWidth: true
                            spacing: Theme.spacingSmall
                            visible: (modelData.weather && modelData.weather.place !== undefined)
                                     || (modelData.activities && modelData.activities.length > 0)
                            Text {
                                visible: modelData.weather && modelData.weather.temperature !== undefined
                                text: qsTr("🌡 %1°C  %2").arg(Math.round(modelData.weather ? modelData.weather.temperature : 0))
                                          .arg(modelData.weather ? (modelData.weather.place || "") : "")
                                color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                            }
                            Repeater {
                                model: modelData.activities || []
                                delegate: Text {
                                    text: qsTr("🚴 %1 (%2 min)").arg(modelData.name || "").arg(modelData.durationMin || 0)
                                    color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                                }
                            }
                        }

                        Rectangle {
                            Layout.fillWidth: true; height: 1; color: Theme.border
                            visible: (modelData.events && modelData.events.length > 0)
                                     || (modelData.observations && modelData.observations.length > 0)
                        }

                        // extracted events
                        Flow {
                            Layout.fillWidth: true
                            spacing: 6
                            Repeater {
                                model: modelData.events || []
                                delegate: Rectangle {
                                    radius: 4
                                    color: Theme.cardNested
                                    border.width: 1; border.color: Theme.border
                                    implicitWidth: evTxt.implicitWidth + 12
                                    implicitHeight: evTxt.implicitHeight + 6
                                    Text {
                                        id: evTxt
                                        anchors.centerIn: parent
                                        text: (modelData.time && modelData.time.length ? modelData.time + " " : "")
                                              + (modelData.category || "") + (modelData.value ? ": " + modelData.value : "")
                                        color: Theme.text; font.pixelSize: Theme.fontSizeCaption
                                    }
                                }
                            }
                        }
                        // subjective observations
                        Flow {
                            Layout.fillWidth: true
                            spacing: 6
                            Repeater {
                                model: modelData.observations || []
                                delegate: Rectangle {
                                    radius: 4
                                    color: modelData.valence === "positive" ? Qt.rgba(Theme.success.r, Theme.success.g, Theme.success.b, 0.15)
                                         : modelData.valence === "negative" ? Qt.rgba(Theme.warning.r, Theme.warning.g, Theme.warning.b, 0.15)
                                         : Theme.cardNested
                                    implicitWidth: obTxt.implicitWidth + 12
                                    implicitHeight: obTxt.implicitHeight + 6
                                    Text {
                                        id: obTxt
                                        anchors.centerIn: parent
                                        text: modelData.text || modelData.category || ""
                                        color: Theme.text; font.pixelSize: Theme.fontSizeCaption
                                    }
                                }
                            }
                        }

                        Text {
                            visible: !modelData.interpreted
                            text: JournalService.anthropicKeySet
                                  ? qsTr("Not read yet — tap Re-read to extract events.")
                                  : qsTr("Saved raw. Add an Anthropic key in Settings → Coach to extract events.")
                            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
                        }
                    }
                }
            }
        }
    }
}
