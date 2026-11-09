pragma Singleton
import QtQuick
import QtCore
import SommetUi

// The training plan's home outside the Training Program page: the saved draft, the sport ->
// watch-mode matching, and the AUTOMATIC sync when a watch is plugged in.
//
// Why automatic (André, 2026-10-09): with "Training day on the watch" on, every mode of a sport
// runs the plan's NEXT workout as an active rule - and that rule is re-pointed only by a sync.
// He synced on the 5th (W6.1), ran on the 7th: the card said W6.2 but Start -> Running ran W6.1
// again ("I believe the watch didn't match intervals.icu timings... like 8min run"), then went
// quiet after W6.1's 29 minutes. The watch cannot pick a native stepped workout by date on its
// own (the stepped binary is the compiler's; the only date-aware mechanism is the old display-
// field app family, tools/training_plan.py, with no step screen), so the fix is to make the
// sync happen without him: once per connection, as soon as the watch is recognised. The page
// still has its button for an on-demand sync.
//
// The page's own helpers (sport words, default mode, entriesWithMode) moved here so the sync
// gives the backend exactly what the button does.
QtObject {
    id: store

    // The plan draft and the switches, shared with TrainingProgramPage (one Settings object,
    // so an edit on the page is what the plug-in sync sends).
    property Settings settings: Settings {
        category: "trainingProgram"
        property string draft: ""
        // Route 2, opt-in (f6dbb10, 2026-10-21): every mode of a plan sport runs the next
        // upcoming workout by itself - silent on stock firmware, and no free run on a training
        // day. Saved under a NEW name since 2026-10-27: the old "autoStart" key was still true
        // from the testing before 21/10 and quietly sent André's week as route 2. Old values are
        // ignored; only ticking the box again turns it on.
        property bool autoStartOptIn: false
        // Sync the plan whenever a watch is plugged in (2026-10-09).
        property bool autoSync: true
    }

    // ---- sport -> watch mode (moved from TrainingProgramPage, 2026-10-09) --------------------
    // The watch sport mode for an intervals.icu sport (André, 2026-09-25: rides go to the cycling
    // mode, runs to running, ... automatically). Matched by name against the watch's own modes; ""
    // when the watch has none for that sport (the entry then keeps its mode / the picked one).
    readonly property var sportWords: ({
        "Ride": ["cycl", "bike", "ride", "vélo", "velo"], "VirtualRide": ["indoor", "cycl", "bike"],
        "GravelRide": ["gravel", "cycl", "bike"], "MountainBikeRide": ["mountain", "mtb", "cycl", "bike"],
        "Run": ["run"], "VirtualRun": ["treadmill", "run"], "TrailRun": ["trail", "run"],
        "Walk": ["walk"], "Hike": ["hik", "trek", "walk"], "Rowing": ["row"],
        // A pool swim goes to the pool mode, an open-water one to the open-water mode - not to
        // whichever mode with "swim" in its name the watch lists first.
        "Swim": ["pool", "swim"], "OpenWaterSwim": ["open", "swim"],
        "NordicSki": ["ski"]
    })
    // Watch sport modes to offer for a workout: the first one per sport (ActivityID). The watch
    // lists a native workout under EVERY mode of its sport (hardware-confirmed 2026-09-26), so a
    // second mode of the same sport adds nothing as a choice.
    readonly property var workoutModes: {
        const seen = {}, out = []
        for (const m of CustomModesService.modes) {
            if (seen[m.activityId]) continue
            seen[m.activityId] = true
            out.push(m.name)
        }
        return out
    }
    function modeForSport(sport) {
        const words = store.sportWords[sport] || []
        for (const w of words)
            for (const n of store.workoutModes)
                if (n.toLowerCase().indexOf(w) >= 0) return n
        return ""
    }
    // Where a workout with no sport of its own goes: the watch's running mode (Suunto ActivityID
    // 3), else its first mode.
    readonly property string defaultMode: {
        const run = CustomModesService.modes.find(m => m.activityId === 3)
        return run ? run.name : (store.workoutModes[0] || "")
    }
    // Sport mode of a planned workout: the one picked in the editor, else the mode matching its
    // intervals.icu sport, else its own mode, else `fallbackMode`.
    function modeOf(e, fallbackMode) {
        if (e.modePicked && e.mode) return e.mode
        return (e.sport && store.modeForSport(e.sport)) || e.mode || fallbackMode
    }
    // What a sync installs: every entry except those made for a bike computer, each in the sport
    // mode matching its sport when it has one, else its own mode, else the fallback.
    function entriesWithMode(entries, fallbackMode) {
        return entries
            .filter(e => e.device !== "bryton" && e.device !== "magene")
            .map(function (e) {
                return { date: e.date, mode: store.modeOf(e, fallbackMode), workout: e.workout }
            })
    }
    // The saved draft's entries (what the page shows), [] when there is no plan.
    function draftEntries() {
        try {
            const d = store.settings.draft ? JSON.parse(store.settings.draft) : null
            return d && Array.isArray(d.entries) ? d.entries : []
        } catch (e) { return [] }
    }

    // ---- the plug-in sync -------------------------------------------------------------------
    // One sync per connection: the watch's serial, so a re-plug syncs again and the device
    // service's periodic refreshes do not.
    property string syncedFor: ""
    property bool pending: false
    // Last automatic sync, for the page: "" or "HH:MM Monday ..." + what happened.
    property string lastAutoSync: ""
    property bool lastAutoSyncOk: true

    function watchConnected() {
        console.info("[plan-sync] watch connected: autoSync", store.settings.autoSync,
                    "demo", DeviceService.demoMode, "serial", DeviceService.serial,
                    "entries", store.draftEntries().length, "syncedFor", store.syncedFor)
        if (!store.settings.autoSync || DeviceService.demoMode) return
        const key = DeviceService.serial || DeviceService.model
        if (!key || key === store.syncedFor) return
        if (store.draftEntries().length === 0) return
        store.syncedFor = key
        // The sport -> mode matching needs this watch's own modes; read them fresh (a different
        // watch than last time has different names), then sync when they land.
        store.pending = true
        CustomModesService.refresh()
    }
    function watchDisconnected() { store.syncedFor = ""; store.pending = false }

    function runPendingSync() {
        if (!store.pending) return
        store.pending = false
        if (TrainingProgramService.installing || !DeviceService.deviceInfoOk) return
        const entries = store.entriesWithMode(store.draftEntries(), store.defaultMode)
        console.info("[plan-sync] syncing", entries.length, "workouts, default mode", store.defaultMode,
                    "autoStart", store.settings.autoStartOptIn)
        if (entries.length === 0) return
        store.lastAutoSync = ""
        TrainingProgramService.syncCalendar(entries, true, store.settings.autoStartOptIn)
    }

    property Connections modesWatch: Connections {
        target: CustomModesService
        function onModesChanged() { store.runPendingSync() }
    }
    property Connections resultWatch: Connections {
        target: TrainingProgramService
        function onLastInstallResultChanged() {
            const r = TrainingProgramService.lastInstallResult
            console.info("[plan-sync] result", JSON.stringify(r))
            if (r.dryRun !== false || r.rotation !== true) return
            const when = Qt.formatDateTime(new Date(), "ddd HH:mm")
            store.lastAutoSyncOk = r.ok === true
            if (r.ok === true) {
                const auto = (r.autoStart || []).map(x => x.mode + " → " + (x.workouts || []).join(", "))
                store.lastAutoSync = qsTr("Watch updated %1.").arg(when)
                    + (auto.length ? " " + auto.join("; ") + "." : "")
            } else {
                store.lastAutoSync = qsTr("Watch update failed %1: %2").arg(when).arg(r.error || "")
            }
        }
    }
}
