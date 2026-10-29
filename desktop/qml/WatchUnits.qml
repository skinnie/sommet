pragma Singleton
import QtQuick
import AmbitApp

// The watch's own unit choices, applied to everything the app displays - real request,
// 2026-08-11 (André): "read the units system from the watch and make the app match it".
//
// WHY THIS IS A CONVERSION LAYER AND NOT A PARSING ONE. The watch records canonically and
// only converts for its own screen: Suunto's own ServiceAdapter.xml annotates the log fields
// `unit="meters"`, `unit="m/s"`, `unit="celsius"`, `unit="kcal"` regardless of what the user
// set. So nothing upstream of here changes when the units do - the GPX, the decoder and the
// cache all stay in SI, and only this file's formatters differ. That also means switching
// units never invalidates a cached activity.
//
// WHERE THE CHOICE COMES FROM. SettingsWriteService's live read of the watch, keyed by the
// same field names the Settings page edits, so the app follows the watch rather than keeping
// a second preference the user would have to set twice. Units.Mode picks the family
// (0 metric / 1 imperial / 2 advanced); in Advanced the seven per-quantity fields are the
// real answer, and in Metric/Imperial the watch forces them to match the family anyway - so
// reading the per-quantity field is correct in ALL three cases, and Mode is only a fallback
// for a watch that did not report one.
//
// Energy is deliberately absent: the watch has no energy unit to read. Suunto's schema has no
// Units.Energy node at all, and its own comment next to the unit list says an energy-unit
// setting was planned and "IMPLEMENTATION MISSING". kcal is the only unit this data has ever
// had - checked 2026-08-11 rather than inherited from libambit's annotation (PROJECT_RULES
// rule 16).
QtObject {
    id: root

    // --- the raw choices, straight off the watch -------------------------------------

    // Look a setting up by key in SettingsWriteService's live list. Returns undefined when
    // the watch has not been read yet, which every caller below treats as "assume metric" -
    // the app must not sit blank waiting for a settings read that may never come (no watch
    // connected, a Garmin instead, Testing mode with a device whose table lacks the field).
    function _value(key) {
        const list = SettingsWriteService.settings
        for (let i = 0; i < list.length; i++) {
            if (list[i].key === key)
                return list[i].value
        }
        return undefined
    }

    readonly property var settingsRevision: SettingsWriteService.settings

    // --- numbers the user types -------------------------------------------------------

    // A typed number with "," or "." as the decimal separator. The app shows "1.00", but on a
    // French or Portuguese system (LC_NUMERIC) a DoubleValidator drops a typed "." and people
    // type "1,5", which parseFloat reads as 1: Autolap "2.50" reached the watch as 250 km
    // (desktop check 2026-10-29, on André's own locale). NaN when it isn't a plain number.
    function parseDecimal(text) {
        const s = String(text === undefined || text === null ? "" : text).trim().replace(",", ".")
        return /^[-+]?(\d+\.?\d*|\.\d+)$/.test(s) ? Number(s) : NaN
    }

    // For a RegularExpressionValidator on a decimal box: digits, then "," or "." and up to two
    // decimals. Use decimalSignedPattern where a value can be negative (coordinates).
    readonly property var decimalPattern: /^\d{0,6}([.,]\d{0,2})?$/
    readonly property var decimalSignedPattern: /^-?\d{0,3}([.,]\d{0,7})?$/

    // 0 = metric, 1 = imperial, 2 = advanced (per-quantity). Only consulted when a specific
    // unit field is missing.
    readonly property int mode: {
        settingsRevision                       // re-evaluate when the watch is re-read
        const v = _value("units_mode")
        return v === undefined ? 0 : v
    }

    // Each of these is 0 = metric-ish, 1 = imperial-ish, matching the watch's own enums.
    readonly property bool imperialDistance: {
        settingsRevision
        const v = _value("distance_unit")
        return v === undefined ? (mode === 1) : (v === 1)
    }
    readonly property bool imperialAltitude: {
        settingsRevision
        const v = _value("altitude_unit")
        return v === undefined ? (mode === 1) : (v === 1)
    }
    readonly property bool imperialTemperature: {
        settingsRevision
        const v = _value("temperature_unit")
        return v === undefined ? (mode === 1) : (v === 1)
    }
    readonly property bool imperialWeight: {
        settingsRevision
        const v = _value("weight_unit")
        return v === undefined ? (mode === 1) : (v === 1)
    }

    // --- formatters ------------------------------------------------------------------
    //
    // Every one takes SI, because that is what the watch stored.

    function distance(meters) {
        if (imperialDistance)
            return qsTr("%1 mi").arg((meters / 1609.344).toFixed(1))
        return qsTr("%1 km").arg((meters / 1000).toFixed(1))
    }

    // The unit label on its own, for a column header or a total where the number is
    // formatted separately.
    function distanceUnit() {
        return imperialDistance ? qsTr("mi") : qsTr("km")
    }

    function distanceValue(meters) {
        return imperialDistance ? meters / 1609.344 : meters / 1000
    }

    function altitude(meters) {
        if (imperialAltitude)
            return qsTr("%1 ft").arg(Math.round(meters / 0.3048))
        return qsTr("%1 m").arg(Math.round(meters))
    }

    function temperature(celsius) {
        if (imperialTemperature)
            return qsTr("%1 °F").arg(Math.round(celsius * 9 / 5 + 32))
        return qsTr("%1 °C").arg(Math.round(celsius))
    }

    function weight(kg) {
        if (imperialWeight)
            return qsTr("%1 lb").arg((kg / 0.45359237).toFixed(1))
        return qsTr("%1 kg").arg(kg.toFixed(1))
    }

    // Energy has no unit choice on this watch - see the header comment.
    function energy(kcal) {
        return qsTr("%1 kcal").arg(Math.round(kcal))
    }

    // Speed and pace follow the DISTANCE unit choice (km<->mi), like every other distance-
    // based figure. Input is metres/hour (the watch log header's own avg/max-speed unit) for
    // speed, and seconds/km for pace. Added 2026-08-16 for the configurable Activities columns.
    function speed(metersPerHour) {
        if (imperialDistance)
            return qsTr("%1 mph").arg((metersPerHour / 1609.344).toFixed(1))
        return qsTr("%1 km/h").arg((metersPerHour / 1000).toFixed(1))
    }

    function pace(secondsPerKm) {
        if (secondsPerKm <= 0)
            return ""
        // Convert to per-mile when the watch shows miles.
        const perUnit = imperialDistance ? secondsPerKm * 1.609344 : secondsPerKm
        const m = Math.floor(perUnit / 60)
        const s = Math.round(perUnit % 60)
        const mm = (s === 60) ? m + 1 : m
        const ss = (s === 60) ? 0 : s
        return qsTr("%1:%2 /%3").arg(mm).arg(String(ss).padStart(2, "0"))
                                .arg(imperialDistance ? qsTr("mi") : qsTr("km"))
    }
}
