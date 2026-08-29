pragma Singleton
import QtQuick
import Qt.labs.settings

// Every color used anywhere in the app comes from here - AMBITAPP_SPEC.md's own rule
// ("Never hardcode colors... Future themes should require zero UI changes"). The "future
// Settings -> theme control" this file's own comment anticipated is real now
// (SettingsPage.qml's own Appearance card) - it just sets `Theme.override` to
// "light"/"dark"/"system"; nothing else in the UI needed to change.
//
// Palette notes: teal primary/accent carried over from this project's other recent work
// (the packaging download page, the app icon - tools/packaging/make_icon.py) so AmbitApp has
// one consistent visual identity end to end, not a new color chosen in isolation here.
QtObject {
    id: root

    // Real, 2026-08-10 ("on desktop mode, put the menu on settings for dark mode/system") -
    // Qt.labs.settings persists this the same real way ConnectionsService.h's own QSettings
    // already persists credentials (same underlying mechanism, just declared in QML instead
    // of C++ - no new service class needed for one string). `override` is a plain alias
    // onto the persisted value, so every existing reader of Theme.override (isDark below,
    // and everything derived from it) picks up a saved choice automatically on next launch,
    // with no other file needing to know persistence is involved at all.
    property alias override: settingsId.themeOverride
    // Real, 2026-08-10 (verifying the Appearance card actually compiles, per its own
    // adjacent comment above) - two real issues here, found live: (1) QtObject has no
    // default property, so a bare `Settings { ... }` child (implicitly assigned to a
    // default property the way it would be under Item) failed with "Cannot assign to
    // non-existent default property" - fixed by declaring it as an explicit named
    // property instead of an implicit child. (2) `property alias X: settings.field` needs
    // a real `id:`, not just a property name - fixed by giving the nested object its own
    // id (settingsId) instead of relying on the property name (settingsObj) alone.
    property Settings settingsObj: Settings {
        id: settingsId
        category: "appearance"
        property string themeOverride: "system"
        // André, 2026-08-11 (item 16): "for activities, in settings let's add the option:
        // see as a map, see as a list." Persisted the same way the theme choice is, so the
        // app opens on whichever view he last used.
        property string activitiesView: "map"
        // André, 2026-08-16: the same independent map/list choice for Routes and POIs.
        property string routesView: "map"
        property string poisView: "map"
        // Configurable Activities list-view columns (André, 2026-08-16): a comma-separated
        // ordered list of metric keys (see ActivityMetrics.qml). Default matches the original
        // four fixed columns; the user adds/changes columns via the header dropdowns + "+".
        property string activityColumns: "distance,duration,ascent,calories"
        // André, 2026-08-25: the Ember fasting/calorie companion. `emberEnabled` shows/hides
        // its sidebar entry; `emberInstallUrl` is the phone-install link surfaced in Settings.
        // (Persisted here alongside the other app-view prefs, the same mechanism the theme
        // override uses - no new settings singleton needed for two values.)
        //
        // Ember is an experimental personal companion, so it ships OPT-IN: `emberEnabled`
        // defaults false, which keeps its sidebar entry off until the user turns it on. Its
        // Settings card (the toggle + phone-install link) is now shown openly and labelled
        // experimental (2026-08-28); the old 10-tap `emberUnlocked` easter egg was retired, so
        // that property is no longer read anywhere (kept only so an existing persisted value
        // loads without warning). An existing install keeps whatever these were persisted as.
        property bool emberEnabled: false
        property bool emberUnlocked: false   // retired 2026-08-28, no longer read
        // Empty by DEFAULT on purpose (2026-08-26, release prep): this used to ship André's own
        // personal trycloudflare tunnel URL, which is both ephemeral (dead for anyone else) and
        // personal infrastructure that has no business in a public release. Each user pastes
        // their own Ember URL in Settings; the value is persisted per-install, so an existing
        // setup keeps whatever it already had - only a fresh install starts blank.
        property string emberInstallUrl: ""
        // When on, Ember's daily totals are also pushed to intervals.icu wellness
        // (kcalConsumed / hydrationVolume / macros + the FastingTime & Coffees custom fields)
        // via tools/ember_to_intervals.py, reusing the intervals connection Sommet already has.
        property bool emberSyncIntervals: false
    }

    // "map" (cards with a track thumbnail, the original) or "list" (rows, no maps).
    property alias activitiesView: settingsId.activitiesView
    property alias routesView: settingsId.routesView
    property alias poisView: settingsId.poisView
    property alias activityColumns: settingsId.activityColumns
    property alias emberEnabled: settingsId.emberEnabled
    property alias emberUnlocked: settingsId.emberUnlocked
    property alias emberInstallUrl: settingsId.emberInstallUrl
    property alias emberSyncIntervals: settingsId.emberSyncIntervals

    // Convenience: the column keys as a real array, and a setter that writes them back as CSV.
    function activityColumnList() {
        return activityColumns.length > 0 ? activityColumns.split(",") : []
    }
    function setActivityColumns(keys) {
        activityColumns = keys.join(",")
    }

    readonly property bool isDark: {
        if (override === "light") return false;
        if (override === "dark") return true;
        return Qt.styleHints.colorScheme === Qt.Dark;
    }

    // --- Light palette ---
    // Real, 2026-08-25 (André, the "UI tune-up" pass - calmer + clearer surface hierarchy,
    // agreed off an editable design canvas). Two changes here, both mutualised to Android
    // (android/src/theme/v3.ts) and the Ember PWA (ember/app.css) the same session so all
    // three apps stay one coherent theme:
    //   1. SURFACE STEPPING. The old palette had only background + card, so nothing could
    //      sit "between" and the light theme read washed-out (cards floated on shadow alone).
    //      Added `surface` (the content region a page sits on), `cardNested` (inset groups /
    //      list rows), and a real `border`/`borderStrong` hairline - the piece the light
    //      theme was missing entirely. `background` also nudged a touch darker for separation.
    //   2. CALMER COLOUR. The teal identity (#167E6A) and the semantic ramp were too
    //      saturated for the restrained, technical feel we want; each pulled toward a lower-
    //      chroma tone. Green is still the identity, just quieter. Added `hard` (orange) so
    //      training-load / "hard" states have a semantic between warning-amber and error-red.
    readonly property color _lightBackground: "#E9EDF0"
    readonly property color _lightSurface: "#F2F5F7"
    readonly property color _lightCard: "#FFFFFF"
    readonly property color _lightCardNested: "#EDF1F4"
    readonly property color _lightBorder: "#DCE2E7"
    readonly property color _lightBorderStrong: "#C6CED6"
    readonly property color _lightPrimary: "#2E6A57"
    readonly property color _lightSecondary: "#5B6270"
    readonly property color _lightAccent: "#3C8571"
    readonly property color _lightSuccess: "#3E7D52"
    readonly property color _lightWarning: "#9A7A22"
    readonly property color _lightHard: "#B5652F"
    readonly property color _lightError: "#B0473C"
    readonly property color _lightText: "#1A1D22"
    readonly property color _lightMutedText: "#5B6270"

    // --- Dark palette --- (not a naive invert - contrast and the accent's own legibility
    // are each checked on this ground independently, per this project's own design practice)
    // 2026-08-25 (same tune-up pass): background deepened #14171C -> #0F1216 so the new
    // `surface` (#171B22) and `cardNested` (#232935) each read as a distinct step above it;
    // dark already had stronger hierarchy than light, this just formalises the extra levels.
    readonly property color _darkBackground: "#0F1216"
    readonly property color _darkSurface: "#171B22"
    readonly property color _darkCard: "#1B1F27"
    readonly property color _darkCardNested: "#232935"
    readonly property color _darkBorder: "#2B313C"
    readonly property color _darkBorderStrong: "#3A414E"
    // Orange "hard" semantic, dark. Kept a touch brighter than light so it holds on the
    // dark card, the same way _darkWarning/_darkError already sit brighter than their light
    // counterparts.
    readonly property color _darkHard: "#CE8258"
    // Real, 2026-08-10 ("desktop version in dark mode still has the cyan color like the
    // android version had, can you use the same scheme of colors we did for the android
    // (grey)") - primary/accent were still the original teal (#57C9B3/#7CD6C4) this whole
    // palette's own header comment credits as this project's shared identity; Android's own
    // v3.ts moved off that same teal onto a slate grey deliberately (its own 2026-08-09
    // header comment: "why we have this cyano blue? ... change to a nicer grey" - a real,
    // explicit choice, not a bug). These two values are that same grey, so both platforms'
    // dark mode match again - light mode wasn't part of this request, left as-is.
    // 2026-08-25, second tune-up pass (André, seeing dark live: "grey letters non visible...
    // not smooth or calm at all"). Root cause: EVERY interactive element - links (Open Totals,
    // Synced, View guide) and the active-nav pill - is painted Theme.primary, which was a grey
    // (#9CA3AF). Grey links on grey text on a grey-labelled card read as dead, murky mush, not
    // calm. Primary is now a calm pine green, the same hue family as the light theme's
    // _lightPrimary (#2E6A57) but lifted for legibility on the dark card (~7:1 on _darkCard) -
    // so the dark theme gets the single living green anchor the light one already had. This
    // reverses the earlier all-grey dark choice (which was really about killing a garish CYAN,
    // #57C9B3 - a muted pine is not that). Secondary lifted too, so labels stop disappearing.
    readonly property color _darkPrimary: "#59A88C"
    readonly property color _darkSecondary: "#ADB6C2"
    readonly property color _darkAccent: "#7BC0A6"
    // 2026-08-25 tune-up: semantic ramp desaturated to match the calmer light palette.
    readonly property color _darkSuccess: "#5C9E72"
    readonly property color _darkWarning: "#CB9A45"
    readonly property color _darkError: "#CE6A60"
    readonly property color _darkText: "#E9EBEE"
    // Real, 2026-08-11 (André, S2: "on dark mode, POIs and Routes are grey, we already fight
    // that on android app, we need a more visible color, without hurting the eyes"). Was
    // #9AA3AF. That measures 6.5:1 against the dark card, which passes on paper - but this
    // colour carries the CAPTION text (a route's "128.7 km · 852 points · ascent 530 m"),
    // and at that size antialiasing drags the rendered pixels down to about L=151, well
    // under what the number promises. Measured off a real dark-mode screenshot rather than
    // computed. #B4BDC9 lifts it to 8.7:1 and stays a soft grey-blue rather than white, so
    // nothing glares. The Android theme carries the SAME value (android/src/theme/v3.ts) -
    // changed in both, per his "we need to mutualize on android and here".
    readonly property color _darkMutedText: "#B4BDC9"

    readonly property color background: isDark ? _darkBackground : _lightBackground
    // 2026-08-25 surface-hierarchy tokens (see the light-palette block above for the why).
    // `surface` is the content region every page sits on - so cards rest ON something rather
    // than floating on `background`; `cardNested` is for things that live INSIDE a card
    // (list rows, settings groups, fun-facts); `border`/`borderStrong` are the hairline that
    // separates every level. Card.qml's `variant` property is what actually picks between
    // these per card.
    readonly property color surface: isDark ? _darkSurface : _lightSurface
    readonly property color card: isDark ? _darkCard : _lightCard
    readonly property color cardNested: isDark ? _darkCardNested : _lightCardNested
    readonly property color border: isDark ? _darkBorder : _lightBorder
    readonly property color borderStrong: isDark ? _darkBorderStrong : _lightBorderStrong
    readonly property color hard: isDark ? _darkHard : _lightHard
    readonly property color primary: isDark ? _darkPrimary : _lightPrimary

    // Ink drawn ON TOP OF MAP TILES - the track line and the POI markers. Deliberately NOT
    // theme-dependent: OSM/CyclOSM tiles are light whatever the app is set to, so following
    // the app theme meant the dark palette's primary (#9CA3AF, a grey) was being drawn on a
    // light map. André, 2026-08-11: "for routes, in dark mode track still grey, not visible,
    // use same green as light mode." The green is the light primary, so the two agree.
    readonly property color mapAccent: _lightPrimary
    readonly property color secondary: isDark ? _darkSecondary : _lightSecondary
    readonly property color accent: isDark ? _darkAccent : _lightAccent
    readonly property color success: isDark ? _darkSuccess : _lightSuccess
    readonly property color warning: isDark ? _darkWarning : _lightWarning
    readonly property color error: isDark ? _darkError : _lightError
    readonly property color text: isDark ? _darkText : _lightText
    readonly property color mutedText: isDark ? _darkMutedText : _lightMutedText

    // Shared spacing/radius scale - not in the spec's explicit token list, but "rounded
    // cards / subtle shadows / large whitespace" (Design Language) needs consistent numbers
    // somewhere, and hardcoding 8/12/16 separately in every component is the same mistake
    // as hardcoding colors.
    readonly property int radiusSmall: 8
    readonly property int radiusCard: 16
    readonly property int spacingSmall: 8
    readonly property int spacingMedium: 16
    readonly property int spacingLarge: 24

    // Real, 2026-08-09 ("Introdup proper type scale and migrate pages onto it") - font
    // sizes were hardcoded ad hoc across every page (a real grep found 10 distinct raw
    // pixelSize values, 10-24, with no shared scale anywhere), the same mistake the color/
    // spacing tokens above already exist to avoid. Each token here matches an existing size
    // exactly (not a new visual hierarchy) - this pass is about giving every page a shared
    // name to bind to instead of a bare number, not re-designing type sizes blind without a
    // way to see the result rendered.
    readonly property int fontSizeTiny: 10        // MapView's zoom-control glyphs
    readonly property int fontSizeCaption: 11     // timestamps, secondary annotations
    readonly property int fontSizeLabel: 12       // the most common size - stat labels, body
    readonly property int fontSizeBody: 13        // primary readable body text
    readonly property int fontSizeBodyLarge: 14   // card titles, emphasized body text
    readonly property int fontSizeSubtitle: 15
    readonly property int fontSizeHeading: 16     // section headings
    readonly property int fontSizeTitle: 18
    readonly property int fontSizeLargeTitle: 20
    readonly property int fontSizeDisplay: 24     // hero numbers (e.g. Home's battery %)
}
