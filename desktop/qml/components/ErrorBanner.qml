import QtQuick
import QtQuick.Controls
import AmbitApp

// One error banner for the whole app - real request, 2026-08-11 (André, G1). Pages used to
// print a service's `lastError` straight into the UI, which meant raw backend stderr and Qt
// network strings ("Error transferring http://127.0.0.1:8766/api/... - server replied: Bad
// Gateway") in front of the user. That is accurate and useless.
//
// This shows one sentence and a way to send the detail instead. The detail is not lost: it
// goes to the log file, and Report opens a mail to André's GitHub address with the tail of
// the log inline plus the folder holding the full file.
//
//   ErrorBanner { detail: SomeService.lastError; context: "reading POIs" }
//
// `detail` is logged, never displayed.
Rectangle {
    id: root

    // The raw message from whatever failed. Logged, not shown.
    property string detail: ""
    // What the app was doing, in the user's terms - goes into the report mail's subject line
    // area so a report is not just a stack of bytes.
    property string context: ""
    // Opt-in "Try again" - real request, 2026-08-11 (André, after unplugging the watch
    // mid-session left the POIs card stuck on this banner): when the failure is plausibly
    // transient (device unplugged, backend busy), the owner connects onRetry to the same
    // refresh that failed and the user gets a one-click recovery instead of switching
    // pages or restarting the app. Opt-in because not every failure has a sensible retry.
    property bool canRetry: false
    signal retry()

    // #11 (André, 2026-09-02): keep the friendly one-liner up front, but let a curious user
    // expand the raw detail in place (as well as it going to the log). Never shown until asked.
    property bool _showDetail: false

    visible: detail.length > 0
    width: parent ? parent.width : 0
    height: visible ? column.implicitHeight + Theme.spacingMedium * 2 : 0
    radius: Theme.radiusCard
    color: Theme.card
    border.width: 1
    border.color: Theme.error

    onDetailChanged: {
        _showDetail = false;   // collapse the detail view whenever the error changes
        if (detail.length > 0)
            LogService.append((context.length > 0 ? context + ": " : "") + detail)
    }

    Column {
        id: column
        anchors.fill: parent
        anchors.margins: Theme.spacingMedium
        spacing: Theme.spacingSmall

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: LogService.userMessage()
            color: Theme.text
            font.pixelSize: Theme.fontSizeBody
        }

        Row {
            spacing: Theme.spacingSmall
            RoundedButton {
                visible: root.canRetry
                text: qsTr("Try again")
                onClicked: root.retry()
            }
            RoundedButton {
                text: root._showDetail ? qsTr("Hide details") : qsTr("Details")
                onClicked: root._showDetail = !root._showDetail
            }
            RoundedButton {
                text: qsTr("Send logs")
                onClicked: LogService.reportProblem(root.context)
            }
            RoundedButton {
                text: qsTr("Open log folder")
                onClicked: LogService.revealLog()
            }
        }

        // The raw detail, on demand only. Selectable so it can be copied into a bug report.
        Rectangle {
            visible: root._showDetail && root.detail.length > 0
            width: parent.width
            height: visible ? detailText.implicitHeight + Theme.spacingSmall * 2 : 0
            radius: Theme.radiusSmall
            color: Theme.cardNested
            border.width: 1
            border.color: Theme.border
            TextEdit {
                id: detailText
                x: Theme.spacingSmall; y: Theme.spacingSmall
                width: parent.width - Theme.spacingSmall * 2
                text: root.detail
                readOnly: true
                selectByMouse: true
                wrapMode: TextEdit.Wrap
                color: Theme.mutedText
                font.pixelSize: Theme.fontSizeCaption
                font.family: "monospace"
            }
        }
    }
}
