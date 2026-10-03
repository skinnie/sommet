import QtQuick
import QtQuick.Controls
import AmbitApp

// Fresh OpenStreetMap maps for the Wahoo ELEMNT (André, 2026-10-03: "osm based tiles interest me,
// if we can integrated it is nice!"). tools/wahoo_maps.py builds zoom-8 tiles here with
// wahooMapsCreator and installs them over the cable, keeping Wahoo's original on the ELEMNT for
// "Restore". The usual way in is the Routes page (Send to… → "Wahoo ELEMNT maps for this route");
// this card shows the job, the ELEMNT's tiles, and takes tiles by number too.
Column {
    id: root
    spacing: Theme.spacingSmall

    property bool loading: true
    property bool ready: false
    property bool usb: false
    property var tiles: []
    property string error: ""
    property string msg: ""
    property var job: ({ running: false })
    readonly property var sommetTiles: root.tiles.filter(t => t.sommet)

    function api(method, path, body, done) {
        const xhr = new XMLHttpRequest()
        xhr.onreadystatechange = function () {
            if (xhr.readyState !== XMLHttpRequest.DONE) return
            let r = {}
            try { r = JSON.parse(xhr.responseText) } catch (e) { r = { ok: false } }
            done(r)
        }
        xhr.open(method, "http://127.0.0.1:8766" + path)
        xhr.setRequestHeader("Content-Type", "application/json")
        xhr.send(body ? JSON.stringify(body) : null)
    }
    function load() {
        root.loading = true; root.error = ""
        root.api("GET", "/api/wahoo/maps", null, function (r) {
            root.loading = false
            if (!r.ok) { root.error = r.error || qsTr("Could not check the map tools."); return }
            root.ready = !!r.ready
            root.usb = !!r.usb
            root.tiles = r.tiles || []
        })
    }
    function pollJob() {
        root.api("GET", "/api/wahoo/maps/job", null, function (r) {
            const was = root.job.running
            root.job = r.ok ? r : { running: false }
            if (was && !root.job.running) root.load()
        })
    }
    function start(path, body) {
        root.msg = ""
        root.api("POST", path, body, function (r) {
            if (!r.ok) root.msg = r.error || qsTr("Couldn't start.")
            root.pollJob()
        })
    }
    function update(list) { root.start("/api/wahoo/maps/update", { tiles: list }) }
    function restore(tile) {
        root.msg = ""
        root.api("POST", "/api/wahoo/maps/restore", { tiles: [tile] }, function (r) {
            root.msg = r.ok ? qsTr("Wahoo's map is back on tile %1 ✓").arg(tile) : (r.error || qsTr("Restore failed."))
            root.load()
        })
    }
    function stageText(j) {
        if (j.kind === "setup")
            return j.running ? qsTr("Installing the map tools…") : j.stage === "done" ? qsTr("Map tools installed ✓")
                 : qsTr("Installing the map tools failed: %1").arg(j.error || "")
        if (j.stage === "building") return qsTr("Building %1 — %2").arg(j.tiles.join(", ")).arg(j.step || "")
        if (j.stage === "installing") return qsTr("Copying %1 to the ELEMNT…").arg(j.tiles.join(", "))
        if (j.stage === "done") return qsTr("New maps on the ELEMNT ✓ (%1)").arg(j.tiles.join(", "))
        if (j.stage === "error") return qsTr("Map update failed: %1").arg(j.error || "")
        return j.running ? qsTr("Starting…") : ""
    }

    Component.onCompleted: { root.load(); root.pollJob() }
    Timer { interval: 3000; repeat: true; running: root.job.running === true; onTriggered: root.pollJob() }

    Text {
        text: qsTr("Maps")
        color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
    }
    Text {
        width: parent.width; wrapMode: Text.WordWrap
        text: qsTr("Up-to-date OpenStreetMap maps, built on this computer and copied over the USB cable — no Wahoo servers. "
                   + "Pick a route on the Routes page and use Send to… → “Wahoo ELEMNT maps for this route”, or enter tiles below. "
                   + "The first build downloads the region's map data, so it can take a while.")
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
    }
    Text {
        visible: root.loading
        text: qsTr("Checking…")
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
    }
    Text {
        visible: root.error.length > 0
        width: parent.width; wrapMode: Text.WordWrap
        text: root.error; color: Theme.error; font.pixelSize: Theme.fontSizeBody
    }
    // Job line: shown while running and after it ends.
    Text {
        visible: text.length > 0
        width: parent.width; wrapMode: Text.WordWrap
        text: root.stageText(root.job)
        color: root.job.stage === "error" || (root.job.kind === "setup" && !root.job.running && root.job.stage !== "done")
               ? Theme.error : root.job.running ? Theme.text : Theme.success
        font.pixelSize: Theme.fontSizeBody
    }
    // Toolchain not installed yet.
    Row {
        visible: !root.loading && !root.ready && !root.job.running
        spacing: Theme.spacingSmall
        Text {
            anchors.verticalCenter: parent.verticalCenter
            text: qsTr("The map tools aren't installed yet.")
            color: Theme.text; font.pixelSize: Theme.fontSizeBody
        }
        RoundedButton {
            text: qsTr("Install map tools")
            onClicked: root.start("/api/wahoo/maps/setup", {})
        }
    }
    Text {
        visible: !root.loading && !root.usb
        width: parent.width; wrapMode: Text.WordWrap
        text: qsTr("Plug the ELEMNT in with the USB cable to see and change its maps.")
        color: Theme.mutedText; font.pixelSize: Theme.fontSizeCaption
    }
    Text {
        visible: !root.loading && root.usb
        width: parent.width; wrapMode: Text.WordWrap
        text: qsTr("%n map tile(s) on the ELEMNT", "", root.tiles.length)
              + (root.sommetTiles.length > 0 ? qsTr(", %n from Sommet", "", root.sommetTiles.length) : "") + "."
        color: Theme.text; font.pixelSize: Theme.fontSizeBody
    }
    Repeater {
        model: root.sommetTiles
        delegate: Item {
            id: row
            required property var modelData
            width: root.width
            height: 36
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: qsTr("Tile %1  ·  %2 MB  ·  %3").arg(row.modelData.tile)
                          .arg((row.modelData.bytes / 1e6).toFixed(1)).arg(row.modelData.date)
                color: Theme.text; font.pixelSize: Theme.fontSizeBody
            }
            Row {
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: Theme.spacingSmall
                RoundedButton {
                    text: qsTr("Rebuild"); enabled: root.ready && !root.job.running
                    onClicked: root.update([row.modelData.tile])
                }
                RoundedButton {
                    text: qsTr("Restore Wahoo's"); enabled: !root.job.running
                    onClicked: root.restore(row.modelData.tile)
                }
            }
        }
    }
    Row {
        visible: !root.loading && root.ready && root.usb
        spacing: Theme.spacingSmall
        RoundedTextField {
            id: tileField
            width: 220
            placeholderText: qsTr("Tiles, e.g. 130/86, 131/86")
        }
        RoundedButton {
            text: qsTr("Build and install")
            enabled: !root.job.running && tileField.text.trim().length > 0
            onClicked: root.update(tileField.text.split(/[\s,;]+/).filter(t => /^\d+\/\d+$/.test(t)))
        }
    }
    Text {
        visible: root.msg.length > 0
        width: parent.width; wrapMode: Text.WordWrap
        text: root.msg
        color: root.msg.indexOf("✓") >= 0 ? Theme.success : Theme.error
        font.pixelSize: Theme.fontSizeCaption
    }
    RoundedButton {
        text: qsTr("Re-read"); enabled: !root.loading
        onClicked: root.load()
    }
}
