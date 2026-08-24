import QtQuick
import QtQuick.Controls
import AmbitApp

// Suunto T6 - EXPERIMENTAL, built blind (André, 2026-08-14: "implement Suunto t6 ... only as
// experimental"). A different, older Suunto product from the Ambit3/Traverse/Kailash watches
// the rest of this app targets: a 2004-2010 heart-rate training computer with NO built-in GPS
// (the map track came from a separate GPS Track Pod, already supported here). Its own FTDI
// USB-serial protocol (0403:f680). Wraps evelbulgroz/suunto-t6-sync (tools/vendor/
// suunto_t6_sync/, MIT) via tools/suunto_t6.py; the merge card drives tools/legacy_merge.py to
// combine a T6 heart-rate log with a GPS Track Pod track of the same session. See those files'
// own docstrings for the full reasoning. Only reachable behind Settings -> Experimental
// features, off by default; the banner repeats the warning inline.
//
// Talks to the local backend with plain XMLHttpRequest, same shape as GpsTrackPodPage.qml.
PageFlickable {
    id: root
    contentWidth: width
    contentHeight: column.height + Theme.spacingLarge * 2
    clip: true

    readonly property string api: "http://127.0.0.1:8766"

    // Which legacy wristop the backend detected: "t6" (FTDI cradle) or "x6hr" (serial/IR). One
    // page auto-detects and routes to that device's own endpoints; the merge below is shared.
    property string device: ""
    property string modelName: qsTr("T6 / X6HR")
    // The T6 exports FIT; the X6HR (no vendored FIT writer) exports GPX. Both merge with a Pod.
    property string exportFormat: "fit"

    property bool busy: false
    property var deviceInfo: null
    property string statusError: ""
    property var logs: []
    // Device-first merge: what's plugged in right now (both devices at once).
    property var devT6Logs: []
    property var devPodTracks: []
    property bool devT6Present: false
    property bool devPodPresent: false
    property int liveT6Choice: 0
    property int livePodChoice: 0
    // File fallback: previously-retrieved tracks/exports already on disk.
    property var podSources: []
    property var t6Sources: []
    property int podChoice: 0
    property int t6Choice: 0
    property string actionText: ""
    property bool actionOk: true

    Component.onCompleted: { refreshStatus(); fetchDevices(); refreshSources() }

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
            if (!d) {
                root.device = ""; root.deviceInfo = null; root.logs = []
                root.statusError = qsTr("Couldn't reach the app backend.")
                return
            }
            if (!d.ok || !d.device) {
                root.device = ""; root.deviceInfo = null; root.logs = []
                root.statusError = (d && d.error) ? d.error
                    : qsTr("No Suunto T6 or X6HR detected. Connect it with its PC-interface cable.")
                return
            }
            root.device = d.device
            root.modelName = d.device === "x6hr" ? qsTr("Suunto X6HR") : qsTr("Suunto T6")
            root.exportFormat = d.device === "x6hr" ? "gpx" : "fit"
            root.deviceInfo = d.status || null
            refreshLogs()
        }
        xhr.open("GET", api + "/api/legacywatch/status")
        xhr.send()
    }

    function _deviceApi() { return root.device === "x6hr" ? "suuntox6hr" : "suuntot6" }

    function refreshLogs() {
        if (!root.device) { root.logs = []; return }
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            let d = null
            try { d = JSON.parse(xhr.responseText) } catch (e) {}
            root.logs = (d && d.ok && d.logs) ? d.logs : []
        }
        xhr.open("GET", api + "/api/" + _deviceApi() + "/logs")
        xhr.send()
    }

    function fetchDevices() {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            let d = null
            try { d = JSON.parse(xhr.responseText) } catch (e) {}
            if (!d || !d.ok) { root.devT6Present = false; root.devPodPresent = false; return }
            root.devT6Present = !!(d.t6 && d.t6.present)
            root.devPodPresent = !!(d.pod && d.pod.present)
            root.devT6Logs = (d.t6 && d.t6.logs) ? d.t6.logs : []
            root.devPodTracks = (d.pod && d.pod.tracks) ? d.pod.tracks : []
        }
        xhr.open("GET", api + "/api/legacymerge/devices")
        xhr.send()
    }

    // Device-first merge: read the chosen T6 log and Pod track live off both plugged-in
    // devices and combine them in one action (André, 2026-08-15).
    function runLiveMerge(fmt) {
        if (!root.devT6Present || !root.devPodPresent)
            return
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
                root.actionText = qsTr("Merged %1 points (%2% with heart rate): %3")
                                  .arg(d.points).arg(d.hr_percent).arg(d.path)
            } else {
                root.actionText = (d && d.error) ? d.error : qsTr("Merge failed.")
            }
        }
        xhr.open("POST", api + "/api/legacymerge/live")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({
            t6_index: root.devT6Logs[root.liveT6Choice].index,
            pod_index: root.devPodTracks[root.livePodChoice].index,
            format: fmt
        }))
    }

    function refreshSources() {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function() {
            if (xhr.readyState !== XMLHttpRequest.DONE)
                return
            let d = null
            try { d = JSON.parse(xhr.responseText) } catch (e) {}
            root.podSources = (d && d.ok && d.pod) ? d.pod : []
            root.t6Sources = (d && d.ok && d.t6) ? d.t6 : []
        }
        xhr.open("GET", api + "/api/legacymerge/sources")
        xhr.send()
    }

    function exportLog(index, fmt) {
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
                refreshSources()
            } else {
                root.actionText = (d && d.error) ? d.error : qsTr("Export failed.")
            }
        }
        xhr.open("POST", api + "/api/" + _deviceApi() + "/retrieve")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({ index: index, format: fmt }))
    }

    function runMerge(fmt) {
        if (root.podSources.length === 0 || root.t6Sources.length === 0)
            return
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
                root.actionText = qsTr("Merged %1 points (%2% with heart rate): %3")
                                  .arg(d.points).arg(d.hr_percent).arg(d.path)
            } else {
                root.actionText = (d && d.error) ? d.error : qsTr("Merge failed.")
            }
        }
        xhr.open("POST", api + "/api/legacymerge/run")
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(JSON.stringify({
            pod_gpx: root.podSources[root.podChoice].path,
            t6_json: root.t6Sources[root.t6Choice].path,
            format: fmt
        }))
    }

    Column {
        id: column
        width: parent.width
        spacing: Theme.spacingMedium
        x: Theme.spacingLarge
        y: Theme.spacingLarge

        Text {
            text: root.modelName
            color: Theme.text
            font.pixelSize: Theme.fontSizeTitle
            font.bold: true
        }

        // A plain one-liner on what the T6 is - no experimental warning banner (the user
        // turned this on themselves in Settings -> Experimental, so they already know;
        // André, 2026-08-15).
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeBody
            text: qsTr("An older Suunto wristop with no GPS - a T6 heart-rate computer or an " +
                        "X6HR (whichever is plugged in). Its export is a heart-rate + barometric-" +
                        "altitude series; to get a map track, merge it with a GPS Track Pod " +
                        "recording of the same session below.")
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
                          ? (root.modelName + "   serial " + (root.deviceInfo.serial || "?"))
                          : ""
                }
                RoundedButton {
                    text: qsTr("Refresh")
                    enabled: !root.busy
                    onClicked: { root.refreshStatus(); root.fetchDevices() }
                }
            }
        }

        // --- Logs ---
        Card {
            width: parent.width
            visible: root.deviceInfo !== null
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text { text: qsTr("Training logs on the device"); font.bold: true
                       color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge }
                Text {
                    width: parent.width
                    visible: root.logs.length === 0
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                    text: qsTr("No logs found.")
                }
                Repeater {
                    model: root.logs
                    delegate: Row {
                        required property var modelData
                        width: parent.width
                        spacing: Theme.spacingSmall
                        Text {
                            width: parent.width - fitBtn.width - Theme.spacingSmall
                            anchors.verticalCenter: parent.verticalCenter
                            color: Theme.text
                            font.pixelSize: Theme.fontSizeBody
                            elide: Text.ElideRight
                            text: qsTr("#%1 - %2 - %3 samples, %4 laps")
                                  .arg(modelData.index)
                                  .arg(modelData.start !== undefined ? modelData.start : "?")
                                  .arg(modelData.samples !== undefined ? modelData.samples : "?")
                                  .arg(modelData.laps !== undefined ? modelData.laps : "?")
                        }
                        RoundedButton {
                            id: fitBtn
                            text: qsTr("Export %1").arg(root.exportFormat.toUpperCase())
                            enabled: !root.busy
                            onClicked: root.exportLog(modelData.index, root.exportFormat)
                        }
                    }
                }
            }
        }

        // --- Merge with a GPS Track Pod track ---
        // Device-first (André, 2026-08-15: "if the gps pod is connected, just read and select
        // directly the activity we want to merge"). Both devices use different USB transports,
        // so they plug in at the same time; when a Pod is connected we list its tracks live
        // right next to the T6 logs and merge the selected pair in one action. The
        // previously-saved-files path stays below as a fallback.
        Card {
            width: parent.width
            Column {
                width: parent.width
                spacing: Theme.spacingSmall
                Text { text: qsTr("Merge with a GPS Track Pod track"); font.bold: true
                       color: Theme.text; font.pixelSize: Theme.fontSizeBodyLarge }
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeBody
                    text: qsTr("Plug in the GPS Track Pod alongside the T6, pick the T6 log and " +
                                "the Pod track from the same workout, and merge them into one " +
                                "GPS activity with heart rate.")
                }

                // No Pod connected -> tell them to plug it in.
                Text {
                    width: parent.width
                    wrapMode: Text.WordWrap
                    visible: !root.devPodPresent
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Connect a GPS Track Pod to merge. (No Pod detected right now.)")
                }

                // Both connected -> pick a log and a track live.
                Text {
                    width: parent.width
                    visible: root.devPodPresent
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Suunto T6 log")
                }
                RoundedComboBox {
                    width: parent.width
                    visible: root.devPodPresent && root.devT6Logs.length > 0
                    model: root.devT6Logs.map(l => qsTr("#%1 - %2 - %3 samples")
                                              .arg(l.index).arg(l.start).arg(l.samples))
                    currentIndex: root.liveT6Choice
                    onActivated: root.liveT6Choice = currentIndex
                }
                Text {
                    width: parent.width
                    visible: root.devPodPresent && root.devT6Logs.length === 0
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("No T6 logs found - is the T6 connected too?")
                }
                Text {
                    width: parent.width
                    visible: root.devPodPresent
                    color: Theme.text
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("GPS Track Pod track")
                }
                RoundedComboBox {
                    width: parent.width
                    visible: root.devPodPresent && root.devPodTracks.length > 0
                    model: root.devPodTracks.map(t => qsTr("#%1 - %2 samples")
                                                 .arg(t.index)
                                                 .arg(t.samples !== undefined ? t.samples : "?"))
                    currentIndex: root.livePodChoice
                    onActivated: root.livePodChoice = currentIndex
                }
                Row {
                    spacing: Theme.spacingSmall
                    visible: root.devPodPresent && root.devT6Logs.length > 0
                             && root.devPodTracks.length > 0
                    RoundedButton {
                        text: qsTr("Merge to GPX")
                        enabled: !root.busy
                        onClicked: root.runLiveMerge("gpx")
                    }
                    RoundedButton {
                        text: qsTr("Merge to FIT")
                        enabled: !root.busy
                        onClicked: root.runLiveMerge("fit")
                    }
                }

                // Fallback: merge files retrieved earlier (either device unplugged now).
                Text {
                    width: parent.width
                    topPadding: Theme.spacingMedium
                    visible: root.podSources.length > 0 && root.t6Sources.length > 0
                    color: Theme.mutedText
                    font.pixelSize: Theme.fontSizeLabel
                    text: qsTr("Or merge previously saved files")
                }
                RoundedComboBox {
                    width: parent.width
                    visible: root.podSources.length > 0 && root.t6Sources.length > 0
                    model: root.podSources.map(s => s.name)
                    currentIndex: root.podChoice
                    onActivated: root.podChoice = currentIndex
                }
                RoundedComboBox {
                    width: parent.width
                    visible: root.podSources.length > 0 && root.t6Sources.length > 0
                    model: root.t6Sources.map(s => s.name)
                    currentIndex: root.t6Choice
                    onActivated: root.t6Choice = currentIndex
                }
                Row {
                    spacing: Theme.spacingSmall
                    visible: root.podSources.length > 0 && root.t6Sources.length > 0
                    RoundedButton {
                        text: qsTr("Merge files to GPX")
                        enabled: !root.busy
                        onClicked: root.runMerge("gpx")
                    }
                    RoundedButton {
                        text: qsTr("Merge files to FIT")
                        enabled: !root.busy
                        onClicked: root.runMerge("fit")
                    }
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
