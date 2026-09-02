pragma Singleton
import QtQuick

// AMBITAPP_SPEC.md, "Future Features": Sport Modes ships hidden until it's real. Any nav
// item/page gated on a flag here just needs `visible: FeatureFlags.sportModes` (or similar) -
// flipping the flag reveals it with no other change, "no redesign required later."
//
// Flipped to true 2026-08-08: CustomModes read/write is now real and hardware-confirmed
// (renaming a mode, Autolap/HR limits/pod search, display field content - see
// custom_modes_andre.md), same day the real SportModesPage.qml replaced the placeholder.
// Still Ambit3-only - not tested against Kailash's own CustomModes region at all (its
// memory map reports no CustomModes region, confirmed empty, per custom_modes_andre.md's
// own Kailash section), so this page assumes the Ambit3/Traverse family throughout.
QtObject {
    property bool sportModes: true

    // Training Program (date-gated scheduled workouts). ON HOLD 2026-08-13: the feature
    // works as a scheduled App-Zone workout on a data row, but the native "Training program /
    // planned moves" (§3.39) and the browsable WORKOUT menu (§3.18) it should really drive are
    // blocked on one firmware-locked value (the PID_RUNNER_GPS_TEMPLATE_GUIDANCE display-template
    // ID inside the AES-encrypted firmware) - see docs/training_program_andre.md Findings 58-61.
    // Kept behind this flag (code intact, page unreachable) until that's resolved; flip to true
    // to reveal it with no other change.
    property bool trainingProgram: true
}
