import QtQuick
import QtQuick.Controls
import SommetUi

// GPS Track Pod - EXPERIMENTAL, built blind (André, 2026-08-12: "just blind, as
// experimental"). A different, older Suunto product from everything else this app talks to:
// a standalone, hip-mounted GPS logger from the pre-GPS-watch Ambit1 era, not the
// Ambit3/Traverse/Kailash watches the rest of this app targets - its own USB product id
// (0x1493:0x0020), its own protocol, its own on-device filesystem. Wraps Ivor Wanders'
// gps_track_pod (tools/vendor/gpspod/, MIT) via tools/gps_track_pod.py - see that file's own
// module docstring for the full reasoning. Only reachable at all behind the Settings ->
// Experimental features toggle (off by default), and this page's own banner repeats the
// warning inline since a feature this unverified should never look like a finished one.
//
// Talks to the local backend with plain XMLHttpRequest, same shape as FirmwarePage.qml.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    readonly property string api: "http://127.0.0.1:8766"

    property bool busy: false
    property var deviceInfo: null
    property string statusError: ""
    property var tracks: []
    property string logBundlePath: ""
    property string actionText: ""
    property bool actionOk: true

    Component.onCompleted: refreshStatus()

    // ---- backend calls -------------------------------------------------------

    function refreshStatus() {
        root.busy = true
        root.statusError = ""
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            root.busy = false
            let d = null
            try { d = JSON.parse(xhr.responseText) } catch (e) {}
            if (!d || !d.ok) {
                root.deviceInfo = null
                root.tracks = []
                root.statusError = (d && d.error) ? d.error : qsTr("Couldn't reach the app backend.")
                return
            }
            root.deviceInfo = d
            refreshTracks()
        }
        xhr.open("GET", api + "/api/gpstrackpod/status")
        xhr.send()
    }

    function refreshTracks() {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            let d = null
            try { d = JSON.parse(xhr.responseText) } catch (e) {}
            root.tracks = (d && d.ok && d.tracks) ? d.tracks : []
        }
        xhr.open("GET", api + "/api/gpstrackpod/tracks")
        xhr.send()
    }

    function retrieveTrack(index) {
        root.busy = true
        root.actionText = ""
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            root.busy = false
            let d = null
            try { d = JSON.parse(xhr.responseText) } catch (e) {}
            root.actionOk = !!(d && d.ok)
            if (d && d.ok) {
                const paths = (d.written || []).map(w => w.path).join(", ")
                root.actionText = qsTr("Saved: %1").arg(paths)
            } else {
                root.actionText = (d && d.error) ? d.error : qsTr("Retrieval failed.")
            }
        }
        xhr.open("POST", api + "/api/gpstrackpod/retrieve")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({ index: index }))
    }

    // "Send logs" - real request, 2026-08-12 (André). Runs a real diagnostic capture (raw
    // USB packets + device status/track list) through the backend and writes a bundle file;
    // this only ever writes locally and reveals it, same "no accounts, no automatic upload"
    // shape as LogService.reportProblem()/revealLog() elsewhere in this app - see
    // GPSTRACKPOD_DIR's own comment in server.py. LogService.append() puts the bundle's path
    // in the app's own log too, so reportProblem()'s mail (opened alongside) mentions it in
    // the log tail it quotes, even though the bundle itself lives in a different folder that
    // gets opened directly right after.
    function sendLogs() {
        root.busy = true
        root.actionText = ""
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            root.busy = false
            let d = null
            try { d = JSON.parse(xhr.responseText) } catch (e) {}
            root.actionOk = !!(d && d.ok)
            if (d && d.log_path) {
                root.logBundlePath = d.log_path
                LogService.append("GPS Track Pod diagnostic bundle saved: " + d.log_path)
                root.actionText = qsTr("Diagnostic bundle saved: %1").arg(d.log_path)
                Qt.openUrlExternally("file://" + d.log_path.substring(0, d.log_path.lastIndexOf("/")))
                LogService.reportProblem(qsTr("GPS Track Pod diagnostic"))
            } else {
                root.actionText = (d && d.error) ? d.error : qsTr("Couldn't capture a diagnostic bundle.")
            }
        }
        xhr.open("POST", api + "/api/gpstrackpod/logs")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({}))
    }

    Column {
        id: column
        width: parent.width
        spacing: Theme.spacingMedium
        anchors.margins: Theme.spacingLarge
        x: Theme.spacingLarge
        y: Theme.spacingLarge

        Text {
            text: qsTr("GPS Track Pod")
            color: Theme.text
            font.pixelSize: Theme.fontSizeTitle
            font.bold: true
        }

        // The banner this whole feature exists to justify - see this file's own header
        // comment. Never hidden, never dismissible: every session using this page should
        // see it, not just the first one.
        Rectangle {
            width: parent.width
            height: warningColumn.implicitHeight + Theme.spacingMedium * 2
            radius: Theme.radiusCard
            color: Theme.card
            border.width: 1
            border.color: Theme.warning
            Column {
                id: warningColumn
                anchors.fill: parent
                anchors.margins: Theme.spacingMedium
                spacing: Theme.spacingSmall
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.warning
                    font.bold: true
                    font.pixelSize: Theme.fontSizeBody
                    text: qsTr("Experimental - built without a real device to test against")
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("The GPS Track Pod is a different, older Suunto product (a " +
                                "standalone GPS logger, not a watch). This page wraps a " +
                                "third-party client nobody on this project has been able to " +
                                "test against real hardware. If you have one and something " +
                                "looks wrong, use \"Send logs\" below rather than trusting " +
                                "the result.")
                }
            }
        }

        // --- Status ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Row {
                    spacing: Theme.spacingSmall
                    Text { text: qsTr("Device"); font.bold: true; color: Theme.text
                           font.pixelSize: Theme.fontSizeBodyLarge
                           anchors.verticalCenter: parent.verticalCenter }
                    LoadingPill { visible: root.busy }
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    visible: root.statusError.length > 0
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                    text: root.statusError
                }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    visible: root.deviceInfo !== null
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeBody
                    text: root.deviceInfo
                          ? (root.deviceInfo.info || "") + "\n" + (root.deviceInfo.status || "")
                          : ""
                }
                RoundedButton {
                    text: qsTr("Refresh")
                    enabled: !root.busy
                    onClicked: root.refreshStatus()
                }
            }
        }

        // --- Tracks ---
        Card {
            width: parent.width
            visible: root.deviceInfo !== null
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text { text: qsTr("Tracks on the device"); font.bold: true; color: Theme.text
                       font.pixelSize: Theme.fontSizeBodyLarge }
                Text {
                    width: parent.width
                    visible: root.tracks.length === 0
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                    text: qsTr("No tracks found.")
                }
                Repeater {
                    model: root.tracks
                    delegate: Row {
                        id: trackRow
                        required property var modelData
                        width: parent.width
                        spacing: Theme.spacingSmall
                        Text {
                            width: parent.width - retrieveBtn.width - Theme.spacingSmall
                            anchors.verticalCenter: parent.verticalCenter
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBody
                            elide: Text.ElideRight
                            text: qsTr("#%1 - %2 samples, %3 m")
                                  .arg(modelData.index)
                                  .arg(modelData.samples !== undefined ? modelData.samples : "?")
                                  .arg(modelData.distance !== undefined ? modelData.distance : "?")
                        }
                        RoundedButton {
                            id: retrieveBtn
                            text: qsTr("Retrieve as GPX")
                            enabled: !root.busy
                            onClicked: root.retrieveTrack(modelData.index)
                        }
                    }
                }
                RoundedButton {
                    text: qsTr("Retrieve all")
                    visible: root.tracks.length > 0
                    enabled: !root.busy
                    onClicked: root.retrieveTrack(-1)
                }
            }
        }

        // --- Send logs ---
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text { text: qsTr("Send logs"); font.bold: true; color: Theme.text
                       font.pixelSize: Theme.fontSizeBodyLarge }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                    text: qsTr("Captures a real diagnostic session (device status, track " +
                                "list, the raw USB traffic) into a file on this computer and " +
                                "opens the folder holding it, plus a report email with the " +
                                "app's own log attached. Nothing is sent automatically - " +
                                "attach the file by hand.")
                }
                RoundedButton {
                    text: qsTr("Send logs")
                    enabled: !root.busy
                    onClicked: root.sendLogs()
                }
            }
        }

        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            visible: root.actionText.length > 0
            color: root.actionOk ? Theme.mutedText : Theme.error
            font.pixelSize: Theme.fontSizeBody
            text: root.actionText
        }
    }
}
