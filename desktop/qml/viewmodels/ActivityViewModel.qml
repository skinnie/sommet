pragma Singleton
import QtQuick
import AmbitApp

// Presentation logic for ActivityService's raw parsed fields - formatting, and picking a
// map preview center from a track. No sport-specific icon function here: ActivityService's
// own header comment explains why (sportTypeRaw is a real but never-decoded byte in this
// project's own tooling) - AmbitApp.Icons.activities is used for every entry instead of
// guessing a mapping that was never verified.
QtObject {
    function formatDuration(totalSeconds) {
        const s = Math.max(0, Math.round(totalSeconds));
        const h = Math.floor(s / 3600);
        const m = Math.floor((s % 3600) / 60);
        if (h > 0) return qsTr("%1h %2m").arg(h).arg(m);
        return qsTr("%1 min").arg(m);
    }

    // Distance and elevation follow the WATCH's unit setting, not a hardcoded metric
    // (André, 2026-08-11: "read the units system from the watch and make the app match
    // it"). The stored values stay SI - see WatchUnits.qml's own header for why the
    // conversion belongs at display time and nowhere else.
    function formatDistance(meters) {
        return WatchUnits.distance(meters);
    }

    function formatElevation(meters) {
        return WatchUnits.altitude(meters);
    }

    // kcal, straight off the watch. No unit choice exists for energy on this hardware -
    // see WatchUnits.qml. Returns "" for an activity that never recorded it (an older
    // cached GPX predates the field), so the UI can hide the figure rather than claim the
    // move cost nothing.
    function formatEnergy(kcal) {
        if (!kcal || kcal <= 0) return "";
        return WatchUnits.energy(kcal);
    }

    function formatDate(isoString) {
        if (!isoString) return qsTr("Unknown date");
        const d = new Date(isoString);
        return d.toLocaleDateString(Qt.locale(), Locale.ShortFormat);
    }

    // Track center for a map preview - a plain average, not a real bounding-box fit (good
    // enough for a small preview thumbnail; the detail view's large map may want a real
    // fit-to-bounds later, not needed yet).
    function trackCenter(track) {
        if (!track || track.length === 0) return null;
        let sumLat = 0, sumLon = 0;
        for (const p of track) { sumLat += p.lat; sumLon += p.lon; }
        return {lat: sumLat / track.length, lon: sumLon / track.length};
    }

    // HomePage's "Last Activity" card, real 2026-08-07 (was a placeholder before
    // ActivityService actually worked). Picks by max startTime rather than assuming
    // ActivityService.activities is already sorted - it isn't guaranteed to be, it's just
    // whatever order the watch's own exercise log walk returned. Returns null for an empty
    // list or one where every entry has an unparseable/missing startTime.
    function mostRecent(activities) {
        if (!activities || activities.length === 0) return null;
        let best = null, bestTime = -Infinity;
        for (const a of activities) {
            const t = a.startTime ? new Date(a.startTime).getTime() : NaN;
            if (!isNaN(t) && t > bestTime) { bestTime = t; best = a; }
        }
        return best;
    }

    // ── The one activity feed every list/summary page reads ─────────────────────────────
    //
    // André, 2026-08-26, debugging with a Kailash plugged in: "activities: only show
    // activities on the watch, not full" - and the same for Totals and for Calendar. All
    // three pages picked their source with a device SWITCH (isGarmin ? garmin : isKailash ?
    // kailash : ActivityService.activities), so connecting a Kailash REPLACED the whole
    // local history with the handful of DeviceHistory sessions still on that watch. Each of
    // the three copies carried a comment claiming to be "one source of truth"; this is that
    // place, and it is a UNION rather than a switch.
    //
    // Base is always ActivityService.activities - the local database, which already holds
    // every watch's synced moves plus the intervals.icu imports, and is device-agnostic by
    // construction (dbLoadAll() has no device filter). Added on top are only the sessions
    // that live on the connected device and were never written to that database: Garmin's
    // on-device GPX files and Kailash's ephemeral DeviceHistory logbook.
    readonly property var feed: {
        const base = ActivityService.activities || []
        const extra = HomeViewModel.isGarmin ? (GarminService.activities || [])
                    : HomeViewModel.isKailash ? kailashSessions
                    : []
        if (extra.length === 0)
            return base

        // Deduped by start time to the minute: a Kailash walk that has since been exported
        // to intervals.icu is in the base too, and showing it twice was the other half of
        // this bug. The device copy is the one dropped - the imported row carries the
        // richer fields (real sport type, energy, ascent) that DeviceHistory never had.
        let seen = ({})
        for (const a of base) {
            const k = minuteKey(a.startTime)
            if (k !== "") seen[k] = true
        }
        let out = base.slice()
        for (const a of extra) {
            const k = minuteKey(a.startTime)
            if (k !== "" && seen[k])
                continue
            out.push(a)
        }
        // Newest first, the order dbLoadAll() already returns the base in - a merged-in
        // device session has to land in its real chronological place, not at the end.
        out.sort(function(x, y) {
            return new Date(y.startTime).getTime() - new Date(x.startTime).getTime()
        })
        return out
    }

    // Start time truncated to the minute, as a dedupe key. Seconds drift between the watch's
    // own logbook and what intervals.icu stores back for the same move, so comparing the
    // full-precision timestamps would never match the two copies of one walk.
    function minuteKey(iso) {
        if (!iso) return ""
        const t = new Date(iso).getTime()
        if (isNaN(t)) return ""
        return String(Math.floor(t / 60000))
    }

    // Real, 2026-08-09 ("Activity logs from kailash => treat them as walks => import to
    // the activities") - Kailash has no ExerciseLog PMEM region at all (KailashService's
    // own header comment), so ActivityService has nothing to read for it. Its real
    // per-session data lives in KailashService.sessions instead (the DeviceHistory
    // "activity mode" logbook - when/durationSeconds/distanceMeters/maxSpeed, already
    // fetched on Home) - reshaped here into the exact {name, startTime, distanceMeters,
    // durationSeconds, ascentMeters, track} shape ActivityCard.qml already expects, so no
    // new QML component is needed. Every session becomes a real "Walk" card - Kailash's own
    // DeviceHistory doesn't record which activity type each session was (unlike Ambit3's
    // real ExerciseLog), and "Walk" is the closest honest default for a GPS-adventure watch
    // with no sport-mode concept at all, per this request.
    //
    // Real, 2026-08-09 ("Something is bizarre on the activities, they say no gps, but they
    // have gps") - ascentMeters/track used to be unconditionally empty here, described above
    // as "this logbook has summary stats only, no per-session GPS track" - true of
    // DeviceHistory sessions on their own, but wrong to leave it there: the watch's separate,
    // continuous TrackLog DOES cover these same real time windows. KailashService.
    // trackLogActivities now does that correlation server-side (see kailash_tracklog.py's
    // split_into_activities() docstring) and comes back index-aligned 1:1 with
    // KailashService.sessions - zipped together here so distance/duration keep coming from
    // the watch's own real reported stats (more accurate than a GPS-derived approximation)
    // while track comes from the real correlated GPS points. A session genuinely outside
    // TrackLog's coverage (predates capture start, etc.) still gets a real empty track, not a
    // wrong one - ActivityCard.qml already renders that as "No GPS track", which is correct
    // for that specific case.
    //
    // Kailash's DeviceHistory sessions reshaped into the same {name, startTime, ...} shape
    // every card here expects, zipped with the TrackLog correlation so they carry real GPS
    // wherever the watch's continuous track covers the session. Lifted from
    // ActivitiesPage.qml, whose long comment explains the "Walk" default and the
    // correlation; Totals and Calendar each had a poorer copy with track hardcoded to [].
    readonly property var kailashSessions: (KailashService.sessions || []).map(function(s, i) {
        const t = KailashService.trackLogActivities[i]
        return ({
            name: qsTr("Walk"),
            startTime: s.when,
            distanceMeters: s.distanceMeters,
            durationSeconds: s.durationSeconds,
            ascentMeters: 0,
            energyKcal: 0,
            track: (t && t.track) ? t.track : [],
        })
    })
}
