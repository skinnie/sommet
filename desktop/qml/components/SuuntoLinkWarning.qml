import QtQuick
import AmbitApp

// SuuntoLink warning (André, 2026-09-28: "suunto link warning should be present in every
// firmware flash or even when opening our app"). Suunto's own desktop app grabs any Ambit on
// USB, and on a Mac its launch agents restart it whenever the watch re-enumerates - that is what
// killed a real firmware flash at 23.7% (old GitHub #14). Status comes from the backend
// (tools/suuntolink_guard.py via /api/suuntolink/status); "Quit SuuntoLink" closes it.
//
//   context: "app"   - banner on app open: shown only when SuuntoLink is installed; closable
//                      for this session.
//   context: "flash" - in every firmware-flash confirmation: always shown, with the live status.
Rectangle {
    id: root
    property string context: "app"
    readonly property string backend: "http://127.0.0.1:8766"
    property var st: ({})
    property bool dismissed: false
    property bool quitting: false
    readonly property bool _running: st.running === true
    readonly property bool _installed: st.installed === true

    visible: context === "flash" || (!dismissed && _installed)
    implicitHeight: col.implicitHeight + Theme.spacingMedium * 2
    radius: Theme.radiusSmall
    color: Qt.rgba(Theme.warning.r, Theme.warning.g, Theme.warning.b, _running ? 0.22 : 0.12)
    border.color: Theme.warning
    border.width: _running ? 2 : 1

    function refresh() {
        const xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE || xhr.status !== 200) return;
            try { root.st = JSON.parse(xhr.responseText); } catch (e) { }
        };
        xhr.open("GET", backend + "/api/suuntolink/status");
        xhr.send();
    }
    function quit() {
        quitting = true;
        const xhr = new XMLHttpRequest();
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return;
            quitting = false;
            try { root.st = JSON.parse(xhr.responseText); } catch (e) { refresh(); }
        };
        xhr.open("POST", backend + "/api/suuntolink/quit");
        xhr.setRequestHeader("Content-Type", "application/json");
        xhr.send("{}");
    }
    Component.onCompleted: refresh()
    // It can be started (or auto-launched by plugging a watch in) at any time.
    Timer { interval: 15000; repeat: true; running: root.visible || root.context === "app"; onTriggered: root.refresh() }

    Column {
        id: col
        anchors.left: parent.left; anchors.right: parent.right; anchors.top: parent.top
        anchors.margins: Theme.spacingMedium
        spacing: Theme.spacingSmall

        Row {
            width: parent.width
            spacing: Theme.spacingSmall
            Text {
                width: parent.width - (closeX.visible ? closeX.width + parent.spacing : 0)
                wrapMode: Text.WordWrap
                color: Theme.text; font.bold: true; font.pixelSize: Theme.fontSizeBody
                text: root._running ? qsTr("SuuntoLink is running")
                    : root._installed ? qsTr("SuuntoLink is installed on this computer")
                    : qsTr("Before updating: nothing else may use the watch")
            }
            Text {
                id: closeX
                visible: root.context === "app"
                text: "✕"; color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
                MouseArea { anchors.fill: parent; anchors.margins: -6; cursorShape: Qt.PointingHandCursor
                            onClicked: root.dismissed = true }
            }
        }
        Text {
            width: parent.width; wrapMode: Text.WordWrap
            color: Theme.text; font.pixelSize: Theme.fontSizeLabel
            text: root.context === "flash"
                ? (root._installed
                   ? qsTr("SuuntoLink takes over the watch's USB, and during an update it restarts by itself when the watch reconnects - that can stop the update halfway. Sommet closes it before the update and keeps closing it until the update is done.")
                   : qsTr("Close SuuntoLink, Moveslink or any other app that talks to the watch. Sommet closes SuuntoLink automatically during the update when it finds it."))
                : qsTr("It takes over the watch's USB connection. Quit it while you use Sommet - it can block syncing, and during a firmware update it can stop the update halfway.")
        }
        Text {
            visible: (root.st.autoLaunch || []).length > 0
            width: parent.width; wrapMode: Text.WordWrap
            color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
            text: qsTr("It is set to start by itself when a watch is plugged in (%1 launcher entries).")
                      .arg((root.st.autoLaunch || []).length)
        }
        RoundedButton {
            visible: root._running
            text: root.quitting ? qsTr("Closing…") : qsTr("Quit SuuntoLink")
            enabled: !root.quitting
            onClicked: root.quit()
        }
    }
}
