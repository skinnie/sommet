import QtQuick
import QtQuick.Controls
import AmbitApp
import "../ActivityViewLogic.js" as AVL

// The activity screen's Overview (André, 2026-09-27; mockup artifact 8KKpqaVBCdPUvDUAhzya8h):
//   level 1  up to four big numbers, chosen per sport in shared/activity_view.json
//            + one "compared with your usual" line when there's something worth saying
//   level 2  up to four smaller numbers + time in zones (power or heart rate)
//   level 3  "More details", folded
// A number the move doesn't have is left out, never replaced. Everything is computed by the
// shared ActivityViewLogic.js, the same file the Android app runs.
Column {
    id: root
    property var cfg: ActivityService.viewConfig
    property string sport: "other"
    property var facts: ({})
    property var st: null                  // streams, once decoded (null until then)
    property var zoneGroups: ActivityService.zoneGroups
    property bool advanced: false
    property var zonePalette: ({})
    property var usual: null               // {text, tone} or null
    spacing: Theme.spacingMedium

    readonly property var _ov: cfg && cfg.sports
        ? AVL.overview(cfg, sport, facts, st, {
              advanced: advanced, zoneGroups: zoneGroups,
              imperialDistance: WatchUnits.imperialDistance, imperialAltitude: WatchUnits.imperialAltitude,
              imperialTemperature: WatchUnits.imperialTemperature })
        : ({ headline: [], secondary: [], more: [] })
    readonly property var _zones: AVL.zonesFor(zoneGroups, sport)
    readonly property var _zoneKinds: {
        const out = []
        const want = cfg && cfg.sports && cfg.sports[sport] ? (cfg.sports[sport].zones || []) : []
        for (const k of want) {
            const z = k === "power" ? _zones.power : _zones.hr
            const ch = k === "power" ? "pw" : "hr"
            if (!z || !st || !st.hist || !st.hist[ch] || !st.hist[ch].length) continue
            const times = AVL.zoneTimes(st, ch, z.bounds)
            if (times.reduce((a, b) => a + b, 0) < 60) continue
            out.push({ kind: k, times: times, names: z.names, text: AVL.zoneBoundsText(z, k === "power" ? "W" : "bpm") })
        }
        return out
    }
    property string zoneKind: ""
    readonly property var _zoneShown: {
        for (const z of _zoneKinds) if (z.kind === zoneKind) return z
        return _zoneKinds.length ? _zoneKinds[0] : null
    }

    component InfoDot: Rectangle {
        property string tip: ""
        visible: tip.length > 0
        width: 15; height: 15; radius: 8
        color: "transparent"
        border.color: Theme.borderStrong
        Text { anchors.centerIn: parent; text: "i"; font.pixelSize: 10; color: Theme.mutedText }
        HoverHandler { id: infoHover }
        ToolTip.visible: infoHover.hovered
        ToolTip.text: tip
        ToolTip.delay: 200
    }

    // ---------------------------------------------------------------- level 1
    Grid {
        id: headline
        width: parent.width
        columns: width < 560 ? 2 : 4
        spacing: 10
        Repeater {
            model: root._ov.headline
            delegate: Rectangle {
                width: (headline.width - headline.spacing * (headline.columns - 1)) / headline.columns
                height: 66
                radius: 12
                color: Theme.card
                border.color: Theme.border
                Column {
                    anchors.fill: parent
                    anchors.margins: 12
                    spacing: 2
                    Row {
                        spacing: 4
                        Text { text: modelData.label; color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        InfoDot { tip: modelData.info; anchors.verticalCenter: parent.verticalCenter }
                    }
                    Row {
                        spacing: 3
                        Text { text: modelData.value; color: Theme.text; font.pixelSize: 24; font.bold: true }
                        Text { text: modelData.unit; color: Theme.mutedText; font.pixelSize: Theme.fontSizeBody
                               anchors.bottom: parent.bottom; anchors.bottomMargin: 4; visible: text.length > 0 }
                    }
                }
            }
        }
    }

    Rectangle {
        visible: root.usual !== null
        width: parent.width
        height: usualText.implicitHeight + 16
        radius: 10
        color: Theme.card
        border.color: Theme.border
        Row {
            x: 12; y: 8
            width: parent.width - 24
            spacing: 10
            Text { text: root.usual && root.usual.tone === "best" ? "★" : "↗"; color: Theme.primary; font.bold: true }
            Text {
                id: usualText
                width: parent.width - 24
                wrapMode: Text.WordWrap
                text: root.usual ? root.usual.text : ""
                color: Theme.text
                font.pixelSize: Theme.fontSizeBody
            }
        }
    }

    // ---------------------------------------------------------------- level 2
    Grid {
        id: secondary
        visible: root._ov.secondary.length > 0
        width: parent.width
        columns: width < 560 ? 2 : 4
        spacing: 10
        Repeater {
            model: root._ov.secondary
            delegate: Column {
                width: (secondary.width - secondary.spacing * (secondary.columns - 1)) / secondary.columns
                spacing: 0
                Row {
                    spacing: 4
                    Text { text: modelData.label; color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                    InfoDot { tip: modelData.info; anchors.verticalCenter: parent.verticalCenter }
                }
                Row {
                    spacing: 2
                    Text { text: modelData.value; color: Theme.text; font.pixelSize: Theme.fontSizeHeading; font.bold: true }
                    Text { text: modelData.unit; color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                           anchors.bottom: parent.bottom; anchors.bottomMargin: 2; visible: text.length > 0 }
                }
            }
        }
    }

    // time in zones
    Column {
        visible: root._zoneShown !== null
        width: parent.width
        spacing: 6
        Item {
            width: parent.width
            height: 24
            Text {
                anchors.verticalCenter: parent.verticalCenter
                text: root._zoneShown && root._zoneShown.kind === "power" ? qsTr("Time in power zones") : qsTr("Time in heart-rate zones")
                color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true
            }
            Row {
                visible: root._zoneKinds.length > 1
                anchors.right: parent.right
                anchors.verticalCenter: parent.verticalCenter
                spacing: 2
                Repeater {
                    model: root._zoneKinds
                    delegate: Rectangle {
                        readonly property bool on: root._zoneShown && root._zoneShown.kind === modelData.kind
                        width: zkText.implicitWidth + 20; height: 22; radius: 11
                        color: on ? Theme.cardNested : "transparent"
                        border.color: Theme.border
                        Text { id: zkText; anchors.centerIn: parent; text: modelData.kind === "power" ? qsTr("Power") : qsTr("Heart rate")
                               color: parent.on ? Theme.text : Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        TapHandler { onTapped: root.zoneKind = modelData.kind }
                    }
                }
            }
        }
        Row {
            id: zoneBar
            width: parent.width
            height: 12
            spacing: 2
            readonly property real _tot: root._zoneShown ? root._zoneShown.times.reduce((a, b) => a + b, 0) : 1
            Repeater {
                model: root._zoneShown ? root._zoneShown.times : []
                delegate: Rectangle {
                    visible: modelData / zoneBar._tot > 0.002
                    width: visible ? Math.max(2, (zoneBar.width - 12) * modelData / zoneBar._tot) : 0
                    height: 12
                    radius: 4
                    color: root.zonePalette["z" + (index + 1)] || Theme.primary
                }
            }
        }
        Flow {
            width: parent.width
            spacing: 14
            Repeater {
                model: root._zoneShown ? root._zoneShown.times : []
                delegate: Row {
                    visible: modelData > 0
                    spacing: 6
                    Rectangle { width: 10; height: 10; radius: 3; anchors.verticalCenter: parent.verticalCenter
                                color: root.zonePalette["z" + (index + 1)] || Theme.primary }
                    Text {
                        text: "Z" + (index + 1) + " " + AVL.fmtClock(modelData) + " · "
                              + Math.round(100 * modelData / zoneBar._tot) + "%"
                        color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
                    }
                }
            }
        }
        Text {
            width: parent.width
            wrapMode: Text.WordWrap
            text: root._zoneShown ? root._zoneShown.text : ""
            color: Theme.mutedText
            font.pixelSize: Theme.fontSizeCaption
        }
    }

    // ---------------------------------------------------------------- level 3
    Rectangle {
        id: more
        visible: root._ov.more.length > 0
        property bool open: false
        width: parent.width
        height: moreHead.height + (open ? moreGrid.height : 0)
        radius: 12
        color: Theme.card
        border.color: Theme.border
        clip: true
        Item {
            id: moreHead
            width: parent.width
            height: 40
            Text { x: 14; anchors.verticalCenter: parent.verticalCenter; text: qsTr("More details")
                   color: Theme.text; font.pixelSize: Theme.fontSizeBody; font.bold: true }
            Text {
                anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
                text: (root._ov.more.length === 1 ? qsTr("1 value") : qsTr("%1 values").arg(root._ov.more.length))
                      + "  " + (more.open ? "▴" : "▾")
                color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel
            }
            TapHandler { onTapped: more.open = !more.open }
            HoverHandler { cursorShape: Qt.PointingHandCursor }
        }
        Grid {
            id: moreGrid
            y: moreHead.height
            width: parent.width
            columns: width < 560 ? 1 : 3
            Repeater {
                model: root._ov.more
                delegate: Rectangle {
                    width: moreGrid.width / moreGrid.columns
                    height: 34
                    color: "transparent"
                    Rectangle { anchors.top: parent.top; width: parent.width; height: 1; color: Theme.border }
                    Row {
                        x: 14; anchors.verticalCenter: parent.verticalCenter; spacing: 4
                        Text { text: modelData.label; color: Theme.mutedText; font.pixelSize: Theme.fontSizeLabel }
                        InfoDot { tip: modelData.info; anchors.verticalCenter: parent.verticalCenter }
                    }
                    Text {
                        anchors.right: parent.right; anchors.rightMargin: 14; anchors.verticalCenter: parent.verticalCenter
                        text: modelData.value + (modelData.unit ? " " + modelData.unit : "")
                        color: Theme.text; font.pixelSize: Theme.fontSizeLabel; font.bold: true
                    }
                }
            }
        }
    }
}
