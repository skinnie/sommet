pragma Singleton
import QtQuick
import QtCore

// docs/reference/sommet-spec.md, "Maps": "Create a MapService abstraction... The UI must never care
// where tiles come from." MapView.qml (components/) is the only thing that reads this - a
// plain, direct XYZ slippy-tile renderer, no QtLocation/GeoServices plugin involved at all
// (see MapView.qml's own header comment for why that plugin was abandoned).
//
// Provider choice, real not fabricated: only two offered, both because they share the
// same standard {z}/{x}/{y}.png XYZ addressing this override mechanism needs. Esri World
// Topo uses a different scheme (z/y/x order, no file extension) that doesn't fit this
// mechanism - not offered rather than shipped broken. IGN was asked about too: same scheme
// mismatch (WMTS, not XYZ), on top of being France-only coverage - see HANDOFF.md's "Two
// real apps" note on why it isn't a fit for a worldwide default either way.
//
// Default is "cyclosm", not "osm" - real bug, 2026-08-07: tile.openstreetmap.org itself
// enforces a strict usage policy (operations.osmfoundation.org/policies/tiles/) and started
// returning its own "Access blocked" 418 image instead of tiles after this session's own
// heavy automated testing tripped it (compounded by main.cpp previously sending no
// User-Agent at all - now fixed, see main.cpp's TileNetworkAccessManager, but OSMF's block
// is typically IP-based and time-limited, so it may not clear immediately even with that
// fixed). CyclOSM's tile server (OSM France) was the one actually working throughout this
// same testing, so it's the safer default until OSM's own block lapses - "OpenStreetMap
// (standard)" is still one click away in Settings.
//
// Offline: real pre-downloaded MBTiles regions are not built yet - docs/reference/sommet-spec.md lists
// that as "future" explicitly. What DOES exist, since 2026-08-11 (main.cpp's
// TileNetworkAccessManager): every tile fetched is kept in a real on-disk HTTP cache
// (QNetworkDiskCache, ~/.cache/Sommet/Sommet/tiles on Linux), so anywhere already
// viewed once loads instantly and keeps rendering with zero network at all - a real,
// incidental offline capability for already-visited areas, just not a way to pre-fetch a
// region you haven't looked at yet.
QtObject {
    readonly property bool offlineAvailable: false  // pre-fetch-a-region MBTiles - future, per the spec

    // Persisted across launches (André, 2026-08-30) via Qt.labs.settings, the same way
    // Theme.qml persists the theme choice - a plain alias onto a nested Settings property, so
    // every reader of MapService.provider picks up the saved choice on next launch with no other
    // file needing to know persistence is involved. (QtObject has no default property, so the
    // Settings child is an explicit named property with its own id - see Theme.qml's note.)
    property alias provider: mapSettings.provider  // "osm" or "cyclosm"
    property QtObject mapSettingsObj: Settings {
        id: mapSettings
        category: "map"
        property string provider: "cyclosm"
        Component.onCompleted: if (provider !== "osm" && provider !== "cyclosm") provider = "cyclosm"
    }

    // IGN (France) was dropped 2026-11-18 (André: it only covers France, so in Portugal every
    // map was empty). A saved "ign" choice becomes CyclOSM on the next start. Providers still
    // supply a URL function, so any tile URL shape fits.
    readonly property var _providers: ({
        osm: {
            name: "OpenStreetMap",
            url: function(z, x, y) {
                return "https://tile.openstreetmap.org/" + z + "/" + x + "/" + y + ".png"
            },
            attribution: "© OpenStreetMap contributors",
        },
        cyclosm: {
            name: "CyclOSM",
            url: function(z, x, y) {
                return "https://a.tile-cyclosm.openstreetmap.fr/cyclosm/"
                       + z + "/" + x + "/" + y + ".png"
            },
            attribution: "© OpenStreetMap contributors, CyclOSM",
        },
    })

    // The one call every tile makes. Kept as a function rather than a host string so a
    // provider's URL shape is entirely its own business.
    function tileUrl(z, x, y) {
        return _providers[provider].url(z, x, y)
    }

    // The short name, for saying WHICH provider is selected. Deliberately not the
    // attribution: that is a credit line ("© OpenStreetMap contributors, CyclOSM") and using
    // it as a name made the Maps card overflow its own card - real, 2026-08-11, screenshot
    // from André.
    readonly property string providerName: _providers[provider].name
    readonly property string attribution: _providers[provider].attribution
}
