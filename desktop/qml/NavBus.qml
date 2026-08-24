pragma Singleton
import QtQuick

// One-signal navigation bus - born with the 2026-08-11 Home redesign ("act like a designer,
// audit this home page"). Home's cards want to lead somewhere (Last Activity -> Activities,
// This year -> Totals), but pages live behind Main.qml's Loader and know nothing about the
// NavRail that picks them. Rather than threading a callback through every page, any page
// emits navigate("activities") and Main.qml - the one place that owns the current page -
// listens. Deliberately just a signal: no state here, the NavRail's own currentPage stays
// the single source of truth.
QtObject {
    signal navigate(string pageId)

    // Optional payload for navigate("activities"): the activity to open straight into its
    // detail (André, 2026-08-24: click a Calendar day's activity to go to it). ActivitiesPage
    // reads and clears this when it loads. null = just open the list.
    property var pendingActivity: null
}
