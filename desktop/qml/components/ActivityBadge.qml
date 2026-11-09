import QtQuick

import SommetUi

// The coloured circular badge Sport Modes shows for each mode, with a per-activity symbol.
//
// Real, 2026-08-10 (André: "I see different colors for different sports but all have the
// same icon... suunto link had specific icons for activity type, which was later linked to
// sport mode"). Both the colour and the symbol are keyed on the mode's own `activityId`
// (custom_modes.py already decodes and exposes it, entry 0x213) - never on the English
// mode name, which is free text the owner can rename and which no other language matches.
//
// The activity table itself lives in assets/activity_types.json: 84 activities with their
// real ids, names, Suunto category colours and an SVG body each. Two rendering styles are
// carried in that file:
//   "glyph" (77) - our own drawing, a white symbol laid on the coloured disc
//   "badge"  (7) - a disc-with-symbol-knocked-out taken from Suunto's own icon font, for a
//                  handful whose equipment shapes we could not draw legibly at 22px (see
//                  CREDITS.md). Drawn as a white disc with the glyph filled in the activity
//                  colour on top, so the symbol reads white in either theme.
Item {
    id: root

    property int activityId: -1
    property int size: 36
    // Falls back to the generic "Unspecified sport" entry (id 1) for an activity we have no
    // row for, rather than showing nothing.
    readonly property var entry: ActivityTypes.forId(activityId)

    implicitWidth: size
    implicitHeight: size

    readonly property bool isBadge: entry && entry.style === "badge"
    readonly property color badgeColor: entry ? entry.color : Theme.primary

    Rectangle {
        anchors.fill: parent
        radius: width / 2
        color: root.isBadge ? "white" : root.badgeColor
    }

    // The symbol. `svg` is a fragment in a 24x24 box; scaled to the badge for a "badge"
    // entry (its own disc IS the badge) and inset to ~61% for a "glyph" entry.
    Image {
        anchors.centerIn: parent
        width: root.isBadge ? root.size : root.size * 0.611
        height: width
        smooth: true
        sourceSize.width: width * 2
        sourceSize.height: height * 2
        source: root.entry ? ActivityTypes.iconUri(root.entry, root.isBadge ? root.badgeColor
                                                                             : "#ffffff")
                           : ""
    }
}
