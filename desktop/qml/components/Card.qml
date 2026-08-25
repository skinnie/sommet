import QtQuick
import AmbitApp

// The base surface every content card in the app builds on (Home's device card, activity
// cards, route/POI cards, etc.) - AMBITAPP_SPEC.md's Design Language: "Rounded cards, Subtle
// shadows." One implementation, so a future design tweak (radius, shadow strength) changes
// every card in the app at once, the same reasoning as Theme.qml for colors.
Rectangle {
    id: root

    // Children declared inside `Card { ... }` land here automatically, so this reads and
    // behaves like a normal container (Column, Row, etc.), not a special content property
    // callers have to remember to use.
    default property alias content: contentItem.data
    property int padding: Theme.spacingMedium

    // Real, 2026-08-25 (the UI tune-up, then André: "no shadows, all app, desktop android").
    // One Card, three weights, so pages stop reading at a single uniform emphasis (André's own
    // words: "a bit noisy... every element the same") - but flat, no shadow anywhere now.
    //   "primary" - the one thing that matters on a page (device status, readiness): card
    //               fill + hairline border. This is the default, so a bare `Card { }` keeps
    //               behaving like every existing card.
    //   "flat"    - everything else that is still a card (weather, totals, health metrics,
    //               and containers for lists/settings): card fill + border.
    //   "nested"  - things that live INSIDE a card (list rows, settings groups, fun-facts):
    //               the recessed `cardNested` fill + border.
    // The three variants now differ ONLY in fill colour (border/radius/no-shadow are shared) -
    // border + fill come from the Theme surface tokens.
    property string variant: "primary"

    radius: Theme.radiusCard
    color: root.variant === "nested" ? Theme.cardNested : Theme.card
    border.width: 1
    border.color: Theme.border
    // Real, 2026-08-09 ("general desktop polish pass") - every card in the app builds on
    // this one Rectangle, so this one Behavior covers every card's light/dark theme
    // transition at once, matching Main.qml's own window-background fix for the same gap.
    Behavior on color { ColorAnimation { duration: 150; easing.type: Easing.OutCubic } }

    // contentItem is a plain Item, and a plain Item's implicitWidth/implicitHeight are always
    // 0 regardless of its children - unlike Column/Row, it doesn't compute implicit size from
    // content. Every card was collapsing to just `padding * 2` because of this, causing every
    // page's stacked cards to overlap (found via real screenshots, 2026-08-07 - see
    // V3_CHANGELOG.md). childrenRect does track the actual bounding rect of contentItem's
    // children (the Column/Row callers put inside), which is what was needed here.
    implicitWidth: contentItem.childrenRect.width + padding * 2
    implicitHeight: contentItem.childrenRect.height + padding * 2

    Item {
        id: contentItem
        anchors.fill: parent
        anchors.margins: root.padding
        // General rule for every card in the app (André, 2026-08-13): content can never spill
        // past the card's own padded edge. A child wider than the card (a Row that does not
        // wrap) used to draw outside the rounded rectangle - "the bike pod gets out of its
        // card". Clipping here guards it everywhere at once; the proper per-case fix is still to
        // make such content wrap/fit. Popups (ComboBox lists, dialogs) render in the Overlay
        // layer, not as children here, so this never clips them.
        clip: true
    }
}
