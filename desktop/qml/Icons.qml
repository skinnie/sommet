pragma Singleton
import QtQuick

// AMBITAPP_SPEC.md, "Icons": "Use Material Symbols Rounded. No emoji. No platform-specific
// icons." Codepoints and the subsetting process are documented in
// assets/fonts/NOTICE.md - add a new icon there, not by guessing a codepoint here.
//
// The actual font is registered once in C++ (src/main.cpp, QFontDatabase::addApplicationFont)
// so every Icon.qml instance just references it by family name - not reloaded per instance.
QtObject {
    readonly property string fontFamily: "Material Symbols Rounded"

    readonly property string home: ""
    readonly property string activities: ""       // directions_run
    readonly property string routes: ""            // route
    readonly property string pois: ""              // place
    readonly property string backup: ""
    readonly property string settings: ""
    readonly property string sportModes: ""        // sports
    readonly property string calendar: ""          // calendar_month
    readonly property string trainingProgram: "\ue8df" // today (a dated calendar page)
    readonly property string gear: "\ue52f"            // directions_bike
    readonly property string coach: "\ue0bf"           // forum (chat bubble)
    readonly property string weight: "\uf039"          // monitor_weight (scale)
    readonly property string health: "\ueaa2"          // monitor_heart
    readonly property string ember: "\uef55"           // local_fire_department (Ember fasting app)
    readonly property string appZone: "\ue86f"         // code (App Zone scripting)
    readonly property string apps: "\ue5c3"            // apps (3x3 grid) - the Apps menu

    readonly property string add: ""
    readonly property string arrowBack: ""
    readonly property string checkCircle: ""
    readonly property string chevronRight: ""
    readonly property string close: ""
    readonly property string cloudDownload: ""
    readonly property string cloudUpload: ""
    readonly property string delete_: ""           // "delete" is a QML/JS reserved word
    readonly property string download: ""
    readonly property string edit: ""
    readonly property string error: ""
    readonly property string moreVert: ""
    readonly property string search: ""
    readonly property string sync: ""
    readonly property string upload: ""
    readonly property string warningAmber: ""
    readonly property string watch: ""

    // Weather (Step 5) - mapped from Open-Meteo's WMO weather codes in weatherservice.cpp
    readonly property string weatherSunny: ""
    readonly property string weatherPartlyCloudy: ""
    readonly property string weatherCloudy: ""
    readonly property string weatherFoggy: ""
    readonly property string weatherRainy: ""
    readonly property string weatherSnowy: ""
    readonly property string weatherThunderstorm: ""
    readonly property string wind: ""

    // POI type icons (the icon the watch shows for each of the 18 Ambit POI types),
    // indexed by the type id 0-17 (F.WAYPOINT_TYPES order). Material Symbols glyphs added
    // to the font subset by tools/subset_material_symbols.py - see it for the
    // type->glyph->codepoint table (Cave/Rock use elevation / filter_hdr, no exact glyph).
    readonly property var poiTypeGlyphs: ["\uea40", "\uf6e7", "\uea68", "\ue531", "\uebac", "\ue57b", "\uf06e", "\ue56c", "\uea99", "\ue87a", "\ue53a", "\uf205", "\ue3f7", "\ue3b0", "\ueacd", "\ue3df", "\ue798", "\ue0c8"]
}
