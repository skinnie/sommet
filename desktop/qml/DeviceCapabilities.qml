pragma Singleton
import QtQuick
import SommetUi

// docs/reference/sommet-spec.md, "Device Capabilities": "Never hardcode watch models. Instead expose
// capabilities... The UI should automatically adapt." A page/button just binds to
// `DeviceCapabilities.supportsRoutes` etc., never to `model === "Ambit3"` anywhere.
//
// WIRED TO THE REAL CONNECTED DEVICE, 2026-08-22 - this had been a static placeholder
// since it was first written (its own comment said so), and nothing in the app ever
// actually read it: real, live proof was a French Ambit1 connected while Watch
// Settings/Sport Modes/Routes were all open - each page called its Ambit3-only SBEM
// endpoint regardless, got a real "ok: false" back (the honest, correct answer - Ambit1
// predates SBEM, see [[sommet_hardware_fleet_check]] project history), and the shared
// ErrorBanner showed "An error has occurred. Please send the logs." for what is really
// just "this feature doesn't apply to this watch." That is the actual reason this keeps
// happening on every new device family: this file existed for exactly this, and was never
// connected to anything.
//
// Bluebird/Duck/Colibri/Greentit (Ambit1/2) speak the older, pre-SBEM PMEM 2.0 protocol -
// confirmed live (2026-08-22) that the SBEM object queries this app's Routes/POIs/Sport
// Modes/Apps pages depend on all come back empty on real Ambit1 hardware, not an error.
// Device identity/battery/settings-write (the one field-level SBEM path that predates the
// object model, see [[sommet_ambit12_settings_write]]) still work. No BLE on this
// family either ([[sommet_device_lineup]] - Ambit1/2 are USB-cable-only).
QtObject {
    readonly property var _legacyModels: (["Bluebird", "Duck", "Colibri", "Greentit"])
    readonly property bool _isLegacy: _legacyModels.indexOf(DeviceService.model) !== -1

    // Kailash (Hoopoe) is a GPS travel/adventure watch with no route-following and no POI
    // feature at all - see KAILASH-SCOPING-NOTE.md's own "What this project is (and isn't)".
    // That is not a gap in the Routes/POIs pages, it is the hardware.
    //
    // Keyed off DeviceService.model, NOT HomeViewModel.isKailash, which is where NavRail read
    // it before: isKailash is `connected && model === "Hoopoe"`, so every blip in the 10s
    // connection heartbeat flashed Routes and POIs back into the rail on a watch that has
    // never supported either (André, 2026-08-26: "if kailash, routes and pois should not
    // show, since it is not supported" - seen while the link had dropped). deviceservice.cpp
    // only ever ASSIGNS m_model on a successful read and never clears it, so the last-known
    // model rides out a blip and the rail stays still.
    readonly property bool _isKailash: DeviceService.model === "Hoopoe"

    // Ambit1/2 DO have routes and POIs - André, 2026-08-27: "the ambit 1 has routes... it is
    // just legacy not sbem.. same for ambit 2". They live in the legacy PMEM waypoint region,
    // not the SBEM object model, and this file's header above (written before that path
    // existed) concluded "no routes/POIs" from the SBEM queries coming back empty. That was a
    // statement about the protocol, not about the watch. /api/nav and /api/pois now dispatch
    // to tools/legacy_link.py for this family - a route being simply the waypoints that share
    // a route_name - so both are real here and must be reachable.
    property bool supportsRoutes: !_isKailash
    property bool supportsPOIs: !_isKailash

    // Route WRITING: Ambit3 family / Traverse through write_nav.py; Ambit1/2 (2026-10-19) through
    // legacy_link.py route-send - the route region as SuuntoLink writes it (existing routes kept
    // byte for byte) plus the route's waypoints in the waypoint list. Kailash has no routes.
    property bool supportsRouteWrite: !_isKailash

    // Removing one POI or one route from the watch (2026-10-25): Ambit3 family / Traverse only,
    // through write_nav.py delpoi/delroute (the Android app's own removal, ported). Ambit1/2
    // keep theirs in the legacy waypoint region, which this does not write.
    property bool supportsNavRemove: !_isKailash && !_isLegacy

    // ---- POI types: the list the connected watch knows -------------------------------------
    // The type byte of a POI is the icon the watch draws. The Ambit3 family has 18 (0-17, Lars's
    // "Types of Poi.md"; tools/ambit_format.py WAYPOINT_TYPES). The Traverse and Traverse Alpha
    // have their own 48 (0-47, read from the 2.0.22 watch program and seen in its own picker in
    // the PC emulator; ambit_format.TRAVERSE_WAYPOINT_TYPES), numbered differently: 2 is "Camp"
    // on an Ambit3 and "Car" on a Traverse. `set` is the watch's own grouping: the "hunting" and
    // "fishing" types are the ones it offers during a Hunting or Fishing recording. Ten Traverse
    // types have an icon of their own (tools/subset_material_symbols.py TRAVERSE_POI_GLYPHS); the
    // others draw the Ambit icon of their kind, as on the watch. Listed as the picker shows them:
    // the plain type first, then by name. tools/test_poi_types_parity.py compares both lists
    // with ambit_format.py and with the Android copy (android/src/services/PoiTypes.ts).
    readonly property bool traversePoiTypes: DeviceService.model === "Jabiru" || DeviceService.model === "Loon"
    readonly property var ambitPoiTypes: [
        { id: 0, name: qsTr("Building"), set: "general", glyph: "\uea40" },
        { id: 1, name: qsTr("Cave"), set: "general", glyph: "\uf6e7" },
        { id: 2, name: qsTr("Camp"), set: "general", glyph: "\uea68" },
        { id: 3, name: qsTr("Car"), set: "general", glyph: "\ue531" },
        { id: 4, name: qsTr("Crossroads"), set: "general", glyph: "\uebac" },
        { id: 5, name: qsTr("Beginning"), set: "general", glyph: "\ue57b" },
        { id: 6, name: qsTr("End"), set: "general", glyph: "\uf06e" },
        { id: 7, name: qsTr("Food"), set: "general", glyph: "\ue56c" },
        { id: 8, name: qsTr("Forest"), set: "general", glyph: "\uea99" },
        { id: 9, name: qsTr("Geocache"), set: "general", glyph: "\ue87a" },
        { id: 10, name: qsTr("Lodging"), set: "general", glyph: "\ue53a" },
        { id: 11, name: qsTr("Meadow"), set: "general", glyph: "\uf205" },
        { id: 12, name: qsTr("Mountain"), set: "general", glyph: "\ue3f7" },
        { id: 13, name: qsTr("Sight"), set: "general", glyph: "\ue3b0" },
        { id: 14, name: qsTr("Road"), set: "general", glyph: "\ueacd" },
        { id: 15, name: qsTr("Rock"), set: "general", glyph: "\ue3df" },
        { id: 16, name: qsTr("Water"), set: "general", glyph: "\ue798" },
        { id: 17, name: qsTr("Waypoint"), set: "general", glyph: "\ue0c8" },
    ]
    readonly property var traversePoiTypeList: [
        { id: 26, name: qsTr("POI"), set: "general", glyph: "\ue0c8" },
        { id: 37, name: qsTr("Bedding"), set: "hunting", glyph: "\ue0c8" },
        { id: 23, name: qsTr("Begin"), set: "general", glyph: "\ue57b" },
        { id: 43, name: qsTr("Big game"), set: "hunting", glyph: "\ue71a" },
        { id: 45, name: qsTr("Bird"), set: "hunting", glyph: "\uf555" },
        { id: 0, name: qsTr("Building"), set: "general", glyph: "\uea40" },
        { id: 8, name: qsTr("Cafe"), set: "general", glyph: "\ue56c" },
        { id: 4, name: qsTr("Camp"), set: "general", glyph: "\uea68" },
        { id: 5, name: qsTr("Camping"), set: "general", glyph: "\uea68" },
        { id: 2, name: qsTr("Car"), set: "general", glyph: "\ue531" },
        { id: 31, name: qsTr("Cave"), set: "general", glyph: "\uf6e7" },
        { id: 19, name: qsTr("Cliff"), set: "general", glyph: "\ue3f7" },
        { id: 15, name: qsTr("Coast"), set: "general", glyph: "\ue798" },
        { id: 21, name: qsTr("Crossroad"), set: "general", glyph: "\uebac" },
        { id: 32, name: qsTr("Emergency"), set: "general", glyph: "\uf2c3" },
        { id: 24, name: qsTr("End"), set: "general", glyph: "\uf06e" },
        { id: 47, name: qsTr("Fish"), set: "fishing", glyph: "\uf1ea" },
        { id: 36, name: qsTr("Fishing spot"), set: "fishing", glyph: "\uead7" },
        { id: 6, name: qsTr("Food"), set: "general", glyph: "\ue56c" },
        { id: 20, name: qsTr("Forest"), set: "general", glyph: "\uea99" },
        { id: 25, name: qsTr("Geocache"), set: "general", glyph: "\ue87a" },
        { id: 17, name: qsTr("Hill"), set: "general", glyph: "\ue3f7" },
        { id: 1, name: qsTr("Home"), set: "general", glyph: "\uea40" },
        { id: 10, name: qsTr("Hostel"), set: "general", glyph: "\ue53a" },
        { id: 11, name: qsTr("Hotel"), set: "general", glyph: "\ue53a" },
        { id: 33, name: qsTr("Info"), set: "general", glyph: "\ue88e" },
        { id: 14, name: qsTr("Lake"), set: "general", glyph: "\ue798" },
        { id: 9, name: qsTr("Lodging"), set: "general", glyph: "\ue53a" },
        { id: 30, name: qsTr("Meadow"), set: "general", glyph: "\uf205" },
        { id: 16, name: qsTr("Mountain"), set: "general", glyph: "\ue3f7" },
        { id: 3, name: qsTr("Parking"), set: "general", glyph: "\ue531" },
        { id: 34, name: qsTr("Peak"), set: "general", glyph: "\ue3f7" },
        { id: 38, name: qsTr("Prints"), set: "hunting", glyph: "\ue91d" },
        { id: 7, name: qsTr("Restaurant"), set: "general", glyph: "\ue56c" },
        { id: 13, name: qsTr("River"), set: "fishing", glyph: "\ue798" },
        { id: 27, name: qsTr("Road"), set: "general", glyph: "\ueacd" },
        { id: 29, name: qsTr("Rock"), set: "fishing", glyph: "\ue3df" },
        { id: 39, name: qsTr("Rub"), set: "hunting", glyph: "\uea99" },
        { id: 40, name: qsTr("Scrape"), set: "hunting", glyph: "\uea99" },
        { id: 46, name: qsTr("Shot"), set: "hunting", glyph: "\ue719" },
        { id: 22, name: qsTr("Sight"), set: "general", glyph: "\ue3b0" },
        { id: 44, name: qsTr("Small game"), set: "hunting", glyph: "\ue799" },
        { id: 41, name: qsTr("Stand"), set: "hunting", glyph: "\ue2cb" },
        { id: 28, name: qsTr("Trail"), set: "general", glyph: "\ueacd" },
        { id: 42, name: qsTr("Trail cam"), set: "hunting", glyph: "\ue3b0" },
        { id: 18, name: qsTr("Valley"), set: "general", glyph: "\ue3f7" },
        { id: 12, name: qsTr("Water"), set: "fishing", glyph: "\ue798" },
        { id: 35, name: qsTr("Waterfall"), set: "fishing", glyph: "\ue798" },
    ]
    // What the Add-a-POI picker offers, and the type a new POI gets when none is picked
    // ("Waypoint" 17; on a Traverse its plain "POI" 26).
    readonly property var poiTypes: traversePoiTypes ? traversePoiTypeList : ambitPoiTypes
    readonly property int poiTypeDefault: traversePoiTypes ? 26 : 17
    // The Ambit1/2 store the older Movescount numbers, which are the Traverse's 0-31 (openambit's
    // movescount_waypoint_type list; an Ambit2 was seen with 26, "POI"): a POI READ from one is
    // named from that list too.
    readonly property var _readPoiTypes: (traversePoiTypes || _isLegacy) ? traversePoiTypeList : ambitPoiTypes
    function poiTypeIndex(id) {
        for (let i = 0; i < poiTypes.length; i++)
            if (poiTypes[i].id === id) return i
        return poiTypeIndex(poiTypeDefault)
    }
    // The entry for a type byte read off the watch; an unknown number reads as the plain type.
    function poiTypeOf(id) {
        const list = _readPoiTypes
        let plain = list[0]
        const plainId = list === traversePoiTypeList ? 26 : 17
        for (let i = 0; i < list.length; i++) {
            if (list[i].id === id) return list[i]
            if (list[i].id === plainId) plain = list[i]
        }
        return plain
    }
    function poiTypeGlyph(id) { return poiTypeOf(id).glyph }
    function poiTypeName(id) { return poiTypeOf(id).name }

    // Backup & Restore is write_nav.py's nav --save/restore over the SBEM flash regions.
    // BackupPage used to read supportsRoutes for this, which was fine while the two were the
    // same predicate; they are separated because excluding Kailash from Routes would
    // otherwise have taken Backup away from it as a silent side effect.
    //
    // Kailash IS excluded, but on its own evidence - a real `nav --save` run against the
    // connected watch, 2026-08-27:
    //   - routes.bin  came back 129,968 of 130,000 bytes 0xFF, header magic 0x3008 vs the
    //     0x340c expected -> "routes 0  points 0  waypoints 0", and the API's own hasRoutes
    //     was false. waypoints.bin the same: 16,380 of 16,384 bytes 0xFF. Both regions are
    //     blank, which is consistent - this watch has no routes/POIs feature to fill them.
    //   - TrainingProgram "this watch does not declare it"; CustomModes and Apps both
    //     "read failed (short reply)". The 0x0b21 memory map answered with 4 bytes.
    //   - The one region with real bytes was GlonassSGEE, which is the GPS ephemeris: it is
    //     re-downloaded and expires on its own, so capturing it is pointless.
    // So the card promised a watch backup that captures nothing, and - the part that matters
    // - `nav --save` never touches TrackLog or DeviceHistory, which IS the irreplaceable data
    // on this watch and IS what a firmware flash wipes ([[sommet_kailash_desktop_home_fix]]).
    // Backing up a Kailash properly means those two regions and is a real feature, not a flag.
    // With this false, BackupPage falls through to its dedicated Ember card, so Ember backup
    // is still offered.
    property bool supportsWatchBackup: !_isLegacy && !_isKailash

    // What a Kailash CAN have backed up, which is not nothing - it is just not the nav
    // regions above. Its DeviceHistory (visited cities/countries, travel stats, the
    // activity-mode logbook) and TrackLog (passive GPS track) are the irreplaceable data on
    // this watch, and are exactly what a firmware flash wipes. The backend writes them as
    // JSON plus one .gpx per correlated segment.
    //
    // One-way on purpose: there is no proven write path back for either region, so this is
    // an archive, not a restore point, and the UI must not offer to restore it.
    property bool supportsTravelArchive: _isKailash

    // The Ambit1/2 equivalent. Their waypoints ARE their navigation database (a route is the
    // waypoints sharing a route_name), and legacy_link.py reads them plus the watch settings
    // in one go - real data that `nav --save` could never reach, so Backup used to offer this
    // family nothing at all. Archive only: POIs can be written back one at a time, but there
    // is no whole-region legacy restore.
    property bool supportsLegacyArchive: _isLegacy
    // The Watch Settings page's SettingsWriteService calls /api/settings, the Ambit3 SBEM
    // path - real device-level settings ARE readable/writable on Ambit1/2 too, but only
    // through the separate /api/legacy/settings this page doesn't call yet (see
    // [[sommet_ambit12_settings_write]]) - that's a real follow-up, not done here.
    // Watch settings work on the legacy family too now: GET /api/settings device-dispatches
    // and serves the Ambit1's own fields in the same schema (35 of them, grouped
    // General/Units/Personal). They come back writable:false - this project has never
    // captured the settings WRITE format for this family - and the shared field renderer
    // already draws a non-writable row read-only, so one page serves both.
    property bool supportsWatchSettings: true
    // Display-slot assignment within a sport mode is proven on real Ambit3 hardware
    // (custom_modes_andre.md); full sport-mode *settings* writing (autolap thresholds,
    // sensor pods, intervals, etc.) is not - see unresolved_questions_for_devs.md #1.
    // Sport modes now work on the legacy family too: the Ambit1's region is decoded
    // (docs/ambit1_sport_mode_format.md) and GET /api/customodes serves it in the SAME
    // shape as the Ambit3's, so one page renders both. André, 2026-08-23: "all watches
    // should look like ambit 3, but for sure with adapted features".
    property bool supportsSportModes: true
    // ...the "adapted features" part. Displays ARE decoded on the Ambit1 now
    // (ambit1_sport_mode.c, built-in system screens stripped), so the Displays card shows
    // real screens for it too. Multisport it genuinely does not have at all.
    property bool supportsSportModeDisplays: true
    property bool supportsMultisport: !_isLegacy
    property bool supportsApps: !_isLegacy
    property bool supportsNavigation: !_isLegacy
    property bool supportsBluetooth: !_isLegacy
    // Firmware update: real and hardware-proven for Ambit3 (Peak) and, as of 2026-08-22,
    // Ambit1 too (see [[sommet_hardware_fleet_check]]) - but exposing a live Firmware
    // page to end users is a real product decision beyond fixing this file's own wiring,
    // not made here. Stays false; revisit deliberately, don't flip as a side effect.
    property bool supportsFirmware: false
}
