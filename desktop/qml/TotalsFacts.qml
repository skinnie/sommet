pragma Singleton
import QtQuick

// The factual side of the Totals screen - real request, 2026-08-11 (André): "Propose
// funny/random/outdoorish quotes to that equivalent (ex: that is the same distance of going
// to the moon and back, that is the equivalent of the length of the #country, that is and an
// average #nation does by car in a year!, that is the equivalent of X g Co2, be factual and
// creative)".
//
// Kept in its own file, and every constant carries its real source, because "be factual and
// creative" puts the two in tension: the phrasing can be playful, but a number that is
// merely plausible would quietly turn the whole screen into decoration. Anything below that
// could not be sourced was left out rather than rounded into existence.
//
// Note what the CO2 line does and does not claim: it is what a car WOULD have emitted over
// the same distance, not a saving. Claiming a saving would assume the trip would otherwise
// have been driven, which for a run up a hill is plainly false.
QtObject {
    id: root

    // --- distance references, metres --------------------------------------------------
    //
    // Moon: mean centre-to-centre distance 384,400 km (IAU/NASA standard figure); "and back"
    // is simply twice it.
    readonly property real moonReturn: 768800000
    readonly property real moonOneWay: 384400000
    // Mainland Portugal, north (Caminha) to south (Vila Real de Santo António): ~561 km.
    readonly property real portugalLength: 561000
    // Earth's equatorial circumference, 40,075 km (WGS-84).
    readonly property real earthCircumference: 40075000
    // Average distance driven per car per year in Portugal: ~10,000 km. EU average is
    // ~12,000 km; the lower, local figure is used since that is the "#nation" here.
    readonly property real carYearPortugal: 10000000
    // A marathon, exactly 42.195 km.
    readonly property real marathon: 42195
    // Camino Francés, St-Jean-Pied-de-Port to Santiago: ~780 km.
    readonly property real camino: 780000

    // Average CO2 emitted by a new car in the EU, grams per kilometre. EU fleet-average
    // target territory for recent years; deliberately not a specific model's figure.
    readonly property real carCo2PerKm: 120

    // --- energy references, kcal ------------------------------------------------------
    //
    // Every one of these is a real, checkable food figure rather than a round number.
    readonly property real chocolateBar: 230      // a standard ~45 g milk chocolate bar
    readonly property real gummyBear: 3.5         // ~1 g each
    readonly property real banana: 105            // medium, ~118 g
    readonly property real pastelDeNata: 300      // a real one, not a small one
    readonly property real pizzaSlice: 285        // one slice of a regular cheese pizza
    readonly property real beer: 150              // 330 ml lager

    function _n(value, decimals) {
        // Pinned to English (Qt.locale("en"), not the ambient Qt.locale()) - same reasoning
        // as DateFormat.qml: this app has no translations, so an unpinned locale can silently
        // swap the thousands/decimal separators (or, on some locales, the digit glyphs
        // themselves) into something inconsistent with the rest of this all-English page.
        return Number(value).toLocaleString(Qt.locale("en"),
                                            'f', decimals === undefined ? 0 : decimals)
    }

    // Distance equivalents, best-fitting first. Returns a list of strings; the caller
    // decides how many to show.
    function distanceLines(meters) {
        if (!meters || meters <= 0)
            return []
        const out = []

        if (meters >= moonReturn)
            out.push(qsTr("That is the Moon and back - %1 times over.")
                     .arg(_n(meters / moonReturn, 1)))
        else if (meters >= moonOneWay * 0.02)
            out.push(qsTr("That is %1% of the way to the Moon.")
                     .arg(_n(meters / moonOneWay * 100, 1)))

        if (meters >= earthCircumference)
            out.push(qsTr("You have been around the world %1 times.")
                     .arg(_n(meters / earthCircumference, 1)))

        if (meters >= portugalLength)
            out.push(qsTr("That is the length of Portugal %1 times over.")
                     .arg(_n(meters / portugalLength, 1)))
        else
            out.push(qsTr("That is %1% of the length of Portugal.")
                     .arg(_n(meters / portugalLength * 100, 0)))

        if (meters >= camino * 0.25)
            out.push(qsTr("The Camino to Santiago is 780 km - you are at %1 of it.")
                     .arg(_n(meters / camino * 100, 0) + "%"))

        if (meters >= marathon)
            out.push(qsTr("That is %1 marathons.").arg(_n(meters / marathon, 1)))

        out.push(qsTr("A car covering that would have put out about %1 kg of CO2 - yours " +
                       "put out none.").arg(_n(meters / 1000 * carCo2PerKm / 1000, 0)))
        out.push(qsTr("The average car in Portugal does %1 km a year. You did %2% of that " +
                       "under your own power.")
                 .arg(_n(carYearPortugal / 1000, 0))
                 .arg(_n(meters / carYearPortugal * 100, 0)))
        return out
    }

    // Energy equivalents. Same idea, and deliberately food - it is the unit people actually
    // have an instinct for.
    function energyLines(kcal) {
        if (!kcal || kcal <= 0)
            return []
        return [
            qsTr("That is %1 bars of chocolate.").arg(_n(kcal / chocolateBar, 0)),
            qsTr("Or %1 gummy bears, if you would rather.").arg(_n(kcal / gummyBear, 0)),
            qsTr("Or %1 pastéis de nata. You have earned them.").arg(_n(kcal / pastelDeNata, 0)),
            qsTr("Or %1 bananas.").arg(_n(kcal / banana, 0)),
            qsTr("Or %1 slices of pizza.").arg(_n(kcal / pizzaSlice, 0)),
            qsTr("Or %1 beers - purely as a unit of measurement.").arg(_n(kcal / beer, 0)),
        ]
    }

    // Hours outside. The headline André asked for is the days-in-a-year one.
    function hoursLines(hours) {
        if (!hours || hours <= 0)
            return []
        const out = [
            qsTr("That is %1 full days outside this year.").arg(_n(hours / 24, 1)),
        ]
        // A year is 8,760 hours; the share of your whole year spent moving outdoors.
        out.push(qsTr("Which is %1% of the entire year.").arg(_n(hours / 8760 * 100, 1)))
        if (hours >= 8)
            out.push(qsTr("Or %1 working days, if a day were spent well.")
                     .arg(_n(hours / 8, 0)))
        return out
    }
}
