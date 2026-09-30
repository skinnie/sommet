// What the activity screen computes - ONE file for desktop (QML imports it as a JS library) and
// Android (Metro requires it as a CommonJS module). tools/gen_activity_view.py copies it into both
// apps next to activity_view.json; edit it here only. Plain ES2017: no optional chaining, no
// `export`, no platform APIs, so both JS engines (Qt V4, Hermes) run it unchanged.
//
// Inputs are always plain objects:
//   cfg      shared/activity_view.json
//   facts    one activity's summary numbers, normalised by each app's small adapter:
//            {sport_id, dist_m, duration_s, moving_s, elapsed_s, ascent_m, descent_m, avg_hr,
//             max_hr, avg_cad, max_cad, avg_speed, max_speed (m/s), kcal, recovery_s, pte,
//             pool_lengths, max_alt, min_alt, start (ISO)}
//   st       tools/activity_streams.py output (or the Android twin) - may be null until loaded
//   units    {imperialDistance, imperialAltitude, imperialTemperature} (desktop: watch setting)

var M_PER_MI = 1609.344, M_PER_FT = 0.3048;

// ------------------------------------------------------------------ sport
function sportKey(cfg, suuntoId) {
    var sports = cfg.sports;
    for (var k in sports) {
        var ids = sports[k].ids || [];
        for (var i = 0; i < ids.length; i++) if (ids[i] === suuntoId) return k;
    }
    return "other";
}

// When the activity's own type didn't resolve (sportKey -> "other": e.g. an intervals.icu import
// named "Swimming", which is no Suunto activity name), use the sport the FIT itself records.
// A known sport is never overridden. FIT enums: sport 1 run, 2 cycling (sub 6 indoor), 5 swimming
// (sub 17 lap / 18 open water), 11 walking, 17 hiking.
function refineSport(sport, st) {
    if (sport !== "other" || !st || !st.sport) return sport;
    var f = st.sport.sport, sub = st.sport.sub_sport;
    if (f === 5) {
        if (sub === 17 || (st.lengths || []).some(function (l) { return l.active && l.swim_s; })) return "pool_swim";
        return "open_swim";
    }
    if (f === 2) return sub === 6 ? "indoor_ride" : "ride";
    return { 1: "run", 11: "walk", 17: "hike" }[f] || sport;
}

function isFoot(sport) { return sport === "run" || sport === "walk" || sport === "hike"; }

// ------------------------------------------------------------------ formatting
function pad2(n) { return (n < 10 ? "0" : "") + n; }
function fmtClock(sec) {
    sec = Math.round(sec);
    var h = Math.floor(sec / 3600), m = Math.floor((sec % 3600) / 60), s = sec % 60;
    return h > 0 ? h + ":" + pad2(m) + ":" + pad2(s) : m + ":" + pad2(s);
}
function fmtNum(v, dec) {
    var s = Number(v).toFixed(dec || 0), parts = s.split(".");
    parts[0] = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ",");
    return parts.join(".");
}
function paceText(mps, perMeters) {
    if (!mps || mps <= 0.2) return null;
    return fmtClock(perMeters / mps);
}

// Returns {value, unit} or null. `raw` is always metric (m, s, m/s, C).
function formatValue(fmt, raw, units) {
    units = units || {};
    if (raw === null || raw === undefined || isNaN(raw)) return null;
    switch (fmt) {
    case "distance":
        if (units.swim) return units.imperialDistance ? {value: fmtNum(raw / 0.9144), unit: "yd"} : {value: fmtNum(raw), unit: "m"};
        if (units.imperialDistance) return {value: fmtNum(raw / M_PER_MI, 2), unit: "mi"};
        return raw >= 1000 || raw === 0 ? {value: fmtNum(raw / 1000, raw >= 100000 ? 1 : 2), unit: "km"}
                                        : {value: fmtNum(raw), unit: "m"};
    case "duration": return {value: fmtClock(raw), unit: ""};
    case "pace_km":
        return units.imperialDistance ? {value: paceText(raw, M_PER_MI), unit: "/mi"}
                                      : {value: paceText(raw, 1000), unit: "/km"};
    case "pace_100m": return {value: paceText(raw, 100), unit: "/100 m"};
    case "speed":
        return units.imperialDistance ? {value: fmtNum(raw * 3600 / M_PER_MI, 1), unit: "mph"}
                                      : {value: fmtNum(raw * 3.6, 1), unit: "km/h"};
    case "bpm": return {value: fmtNum(raw), unit: "bpm"};
    case "cadence": return {value: fmtNum(raw), unit: units.foot ? "spm" : "rpm"};
    case "watts": return {value: fmtNum(raw), unit: "W"};
    case "kj": return {value: fmtNum(raw), unit: "kJ"};
    case "int": return {value: fmtNum(raw), unit: ""};
    case "dec1": return {value: Number(raw).toFixed(1), unit: ""};
    case "dec2": return {value: Number(raw).toFixed(2), unit: ""};
    case "kcal": return {value: fmtNum(raw), unit: "kcal"};
    case "meters":
    case "elev_up":
    case "elev_down":
        var sign = fmt === "elev_up" ? "+" : fmt === "elev_down" ? "−" : "";
        if (units.imperialAltitude) return {value: sign + fmtNum(raw / M_PER_FT), unit: "ft"};
        return {value: sign + fmtNum(raw), unit: "m"};
    case "m_per_h":
        return units.imperialAltitude ? {value: fmtNum(raw / M_PER_FT), unit: "ft/h"} : {value: fmtNum(raw), unit: "m/h"};
    case "hours_min":
        var h = Math.floor(raw / 3600), m = Math.round((raw % 3600) / 60);
        return {value: h > 0 ? h + " h " + m : String(m), unit: h > 0 ? "min" : "min"};
    case "temp_range":
        var lo = raw[0], hi = raw[1];
        if (units.imperialTemperature) { lo = lo * 9 / 5 + 32; hi = hi * 9 / 5 + 32; }
        return {value: Math.round(lo) === Math.round(hi) ? String(Math.round(lo)) : Math.round(lo) + "–" + Math.round(hi),
                unit: units.imperialTemperature ? "°F" : "°C"};
    }
    return {value: String(raw), unit: ""};
}

// ------------------------------------------------------------------ metric values
function usable(cfg, v) {
    if (v === null || v === undefined || isNaN(v)) return false;
    var sentinels = cfg.sentinels || [];
    for (var i = 0; i < sentinels.length; i++) if (v === sentinels[i]) return false;
    return true;
}
function pos(cfg, v) { return usable(cfg, v) && v > 0 ? v : null; }

// FTP for the advanced power numbers: from intervals.icu's Ride group.
function ftpFrom(zoneGroups) {
    for (var i = 0; i < (zoneGroups || []).length; i++) {
        var g = zoneGroups[i];
        if (g.ftp && (g.types || []).indexOf("Ride") >= 0) return g.ftp;
    }
    return null;
}

// Raw metric value (metric units) or null when the move doesn't have it. Streams (the FIT) win
// over the list's summary facts when both exist; zero, sentinels and "flat" never count.
function metricRaw(cfg, key, sport, facts, st, zoneGroups) {
    var sm = st && st.summary ? st.summary : {};
    var f = facts || {};
    function first() { for (var i = 0; i < arguments.length; i++) if (pos(cfg, arguments[i]) !== null) return arguments[i]; return null; }
    var moving = first(sm.timer_s, f.moving_s, f.duration_s);
    var dist = first(sm.dist_m, f.dist_m);
    switch (key) {
    case "distance": return dist;
    case "duration": return first(sm.elapsed_s, f.duration_s, sm.timer_s);
    case "moving_time": return moving;
    case "elapsed_time":
        var el = first(sm.elapsed_s, f.elapsed_s);
        return el && moving && el - moving > 60 ? el : null;       // only when it tells something
    case "avg_pace_100m":
        // a pool swim's pace is over the time actually swimming, not the rests at the wall
        var swum = st && st.lengths && st.lengths.length
            ? st.lengths.reduce(function (a, l) { return a + (l.active ? (l.swim_s || 0) : 0); }, 0) : 0;
        if (dist && swum) return dist / swum;
        if (dist && moving) return dist / moving;
        return first(sm.avg_speed, f.avg_speed);
    case "avg_pace": case "avg_speed":
        if (dist && moving) return dist / moving;
        return first(sm.avg_speed, f.avg_speed);
    case "max_pace": case "max_speed": return first(sm.max_speed, f.max_speed);
    case "avg_hr": return first(sm.avg_hr, f.avg_hr);
    case "max_hr": return first(sm.max_hr, f.max_hr);
    case "avg_cad": return first(sm.avg_cad, f.avg_cad);
    case "max_cad": return first(sm.max_cad, f.max_cad);
    case "avg_power": return first(sm.avg_pw);
    case "max_power": return first(sm.max_pw);
    case "np": return first(sm.np_w);
    case "intensity":
        var ftp = ftpFrom(zoneGroups), np = first(sm.np_w);
        return ftp && np ? np / ftp : null;
    case "tss":
        var ftp2 = ftpFrom(zoneGroups), np2 = first(sm.np_w);
        if (!ftp2 || !np2 || !moving) return null;
        return moving * np2 * (np2 / ftp2) / (ftp2 * 3600) * 100;      // Coggan
    case "work":
        var ap = first(sm.avg_pw);
        return ap && moving ? ap * moving / 1000 : null;
    case "elevation_gain": return first(sm.ascent_m, f.ascent_m);
    case "elevation_loss": return first(sm.descent_m, f.descent_m);
    case "max_altitude": return streamStat(st, "alt", "max", cfg) !== null ? streamStat(st, "alt", "max", cfg) : first(f.max_alt);
    case "min_altitude": return streamStat(st, "alt", "min", cfg);
    case "climb_rate":
        var g = first(sm.ascent_m, f.ascent_m);
        return g && moving ? g / (moving / 3600) : null;
    case "energy": return first(sm.kcal, f.kcal);
    case "pte": return first(f.pte);
    case "recovery_time": return first(f.recovery_s);
    case "pool_lengths":
        var swum = st && st.lengths ? st.lengths.filter(function (l) { return l.active; }).length : 0;
        return first(swum || null, f.pool_lengths);
    case "pool_length": return first(sm.pool_length_m);
    case "strokes_per_length":
        if (!st || !st.lengths || !st.lengths.length) return null;
        return median(st.lengths.filter(function (l) { return l.active; }).map(function (l) { return l.strokes; }));
    case "swolf":
        if (!st || !st.lengths || !st.lengths.length) return null;
        return median(st.lengths.filter(function (l) { return l.active && l.swim_s && l.strokes; })
                                 .map(function (l) { return l.swim_s + l.strokes; }));
    case "rest_time":
        if (!st || !st.lengths || !st.lengths.length) return null;
        return st.lengths.reduce(function (a, l) { return a + (l.active ? (l.rest_s || 0) : 0); }, 0);
    case "time_swimming":
        if (!st || !st.lengths || !st.lengths.length) return null;
        return st.lengths.reduce(function (a, l) { return a + (l.active ? (l.swim_s || 0) : 0); }, 0);
    case "longest_nonstop": return st ? pos(cfg, st.longest_nonstop_m) : null;
    case "water_temp":
        if (sport !== "pool_swim" && sport !== "open_swim") return null;
        var lo = streamStat(st, "temp", "min", cfg), hi = streamStat(st, "temp", "max", cfg);
        return lo !== null ? [lo, hi] : null;
    }
    return null;
}

function streamStat(st, ch, how, cfg) {
    if (!st || !st.streams || !st.streams[ch]) return null;
    var xs = st.streams[ch].filter(function (v) { return v !== null && v !== undefined; });
    if (!xs.length) return null;
    var lo = Math.min.apply(null, xs), hi = Math.max.apply(null, xs);
    if (ch === "alt" && hi - lo < 1) return null;                 // flat indoors = not recorded
    return how === "min" ? lo : how === "max" ? hi : xs.reduce(function (a, b) { return a + b; }, 0) / xs.length;
}

function median(xs) {
    xs = xs.filter(function (v) { return v !== null && v !== undefined; }).sort(function (a, b) { return a - b; });
    if (!xs.length) return null;
    var m = Math.floor(xs.length / 2);
    return xs.length % 2 ? xs[m] : (xs[m - 1] + xs[m]) / 2;
}

// The three levels of the Overview for one move: {headline, secondary, more}, each a list of
// {key, label, value, unit, info}. Metrics the move doesn't have are simply left out.
function overview(cfg, sport, facts, st, opts) {
    opts = opts || {};
    var sc = cfg.sports[sport] || cfg.sports.other;
    var units = {imperialDistance: opts.imperialDistance, imperialAltitude: opts.imperialAltitude,
                 imperialTemperature: opts.imperialTemperature, foot: isFoot(sport),
                 swim: sport === "pool_swim" || sport === "open_swim"};
    function build(keys) {
        var out = [];
        for (var i = 0; i < keys.length; i++) {
            var key = keys[i], md = cfg.metrics[key];
            if (!md) continue;
            if (md.advanced && !opts.advanced) continue;
            var raw = metricRaw(cfg, key, sport, facts, st, opts.zoneGroups);
            var f = formatValue(md.fmt, raw, units);
            if (!f || f.value === null) continue;
            out.push({key: key, label: md.label, value: f.value, unit: f.unit,
                      info: md.info ? cfg.info[md.info] || "" : ""});
        }
        return out;
    }
    var headline = build(sc.headline || []);
    var secondary = build(opts.advanced && sc.advanced ? sc.advanced : (sc.secondary || []));
    var shown = {};
    headline.concat(secondary).forEach(function (m) { shown[m.key] = true; });
    var more = build((sc.more || []).filter(function (k) { return !shown[k]; }));
    return {headline: headline, secondary: secondary, more: more};
}

// ------------------------------------------------------------------ zones
function zoneGroupFor(zoneGroups, sport) {
    var want = sport === "ride" || sport === "indoor_ride" ? "Ride"
             : sport === "run" ? "Run" : sport === "pool_swim" || sport === "open_swim" ? "Swim" : "Other";
    var other = null;
    for (var i = 0; i < (zoneGroups || []).length; i++) {
        var g = zoneGroups[i], types = g.types || [];
        if (types.indexOf(want) >= 0) return g;
        if (types.indexOf("Other") >= 0) other = g;
    }
    return other;
}

// {hr: {bounds:[bpm upper bounds], names:[...]}, power: {bounds:[W], names:[...]}} - either may be null.
function zonesFor(zoneGroups, sport) {
    var g = zoneGroupFor(zoneGroups, sport), ride = zoneGroupFor(zoneGroups, "ride");
    var hr = g && g.hr_zones ? {bounds: g.hr_zones.slice(0, g.hr_zones.length - 1), names: g.hr_zone_names || []} : null;
    var power = null;
    if (ride && ride.ftp && ride.power_zones)
        power = {bounds: ride.power_zones.map(function (p) { return ride.ftp * p / 100; }),
                 names: ride.power_zone_names || [], ftp: ride.ftp};
    return {hr: hr, power: power};
}

function zoneOf(v, bounds) { var z = 0; for (var i = 0; i < bounds.length; i++) if (v > bounds[i]) z++; return z; }

// Seconds in each zone from a stream; gaps longer than 10 s don't count (pauses).
function zoneTimes(st, ch, bounds) {
    var out = [], i;
    for (i = 0; i <= bounds.length; i++) out.push(0);
    if (st && st.hist && st.hist[ch] && st.hist[ch].length) {      // exact, full resolution
        st.hist[ch].forEach(function (p) { out[zoneOf(p[0], bounds)] += p[1]; });
        return out;
    }
    if (!st || !st.streams || !st.streams[ch]) return out;
    var t = st.streams.t, v = st.streams[ch];
    // a gap longer than 3 sample spacings (at least 10 s) is a pause and doesn't count
    var spacing = t.length > 1 ? (t[t.length - 1] - t[0]) / (t.length - 1) : 1, cap = Math.max(10, 3 * spacing);
    for (i = 0; i < t.length - 1; i++) {
        if (v[i] === null || v[i] === undefined) continue;
        var dt = t[i + 1] - t[i];
        if (dt > 0 && dt <= cap) out[zoneOf(v[i], bounds)] += dt;
    }
    return out;
}

function zoneBoundsText(z, unit) {
    var b = z.bounds.map(function (x) { return Math.round(x); }), parts = [];
    for (var i = 0; i <= b.length; i++) {
        if (i === 0) parts.push("Z1 ≤" + b[0]);
        else if (i === b.length) parts.push("Z" + (i + 1) + " " + (b[i - 1] + 1) + "+");
        else parts.push("Z" + (i + 1) + " " + (b[i - 1] + 1) + "–" + b[i]);
    }
    return parts.join(" · ") + (unit ? " " + unit : "");
}

// ------------------------------------------------------------------ colour by
// Returns {bands:[band index or null per stream point], legend:[{i, name, colour}], label(b)}.
// Colours are palette indexes: "zN" (zone N, 1-based) or "down"; each app maps them to its theme.
function colourBands(cfg, mode, st, zones, sport) {
    if (!st || !st.streams) return null;
    var s = st.streams, cm = cfg.colour_modes[mode];
    if (!cm) return null;
    if (mode === "hrz" || mode === "pwz") {
        var z = mode === "hrz" ? zones.hr : zones.power, v = s[cm.key];
        if (!z || !v) return null;
        var names = z.names || [];
        return {bands: v.map(function (x) { return x === null || x === undefined ? null : zoneOf(x, z.bounds); }),
                legend: z.bounds.concat([0]).map(function (_, i) { return {i: i, name: "Z" + (i + 1) + (names[i] ? " " + names[i] : ""), colour: "z" + (i + 1)}; }),
                label: function (b) { return "Z" + (b + 1) + (names[b] ? " " + names[b] : ""); }};
    }
    if (mode === "pace" || mode === "speed") {
        if (!s.v) return null;
        var mv = s.v.filter(function (x) { return x !== null && x > 0.5; }).sort(function (a, b) { return a - b; });
        if (mv.length < 5) return null;
        var q = [0.2, 0.4, 0.6, 0.8].map(function (p) { return mv[Math.floor(p * (mv.length - 1))]; });
        var fmt = function (x) { return mode === "pace" ? paceText(x, 1000) : (x * 3.6).toFixed(1); };
        var u = mode === "pace" ? "/km" : "km/h";
        return {bands: s.v.map(function (x) { return x === null || x <= 0.5 ? null : q.filter(function (t) { return x > t; }).length; }),
                legend: [0, 1, 2, 3, 4].map(function (i) {
                    var name = i === 0 ? (mode === "pace" ? "slower than " : "under ") + fmt(q[0]) + " " + u
                             : i === 4 ? (mode === "pace" ? "faster than " : "over ") + fmt(q[3]) + " " + u
                             : fmt(q[i - 1]) + "–" + fmt(q[i]);
                    return {i: i, name: name, colour: "z" + (i + 1)}; }),
                label: function (b) { return ["Slowest fifth", "Slow fifth", "Middle fifth", "Fast fifth", "Fastest fifth"][b] + " of this activity"; }};
    }
    if (mode === "slope") {
        if (!s.alt || !s.dist) return null;
        var n = s.alt.length, sl = [], spacing = s.dist[n - 1] / n || 0.02;
        var w = Math.max(1, Math.min(5, Math.round((cm.window_m / 1000) / spacing)));
        for (var i = 0; i < n; i++) {
            var a = Math.max(0, i - w), b = Math.min(n - 1, i + w);
            if (s.alt[a] === null || s.alt[b] === null || s.dist[a] === null || s.dist[b] === null) { sl.push(null); continue; }
            var dd = (s.dist[b] - s.dist[a]) * 1000;
            sl.push(dd < 5 ? null : (s.alt[b] - s.alt[a]) / dd * 100);
        }
        var cuts = cm.cuts_pct, labels = ["Downhill", "Flat, under " + cuts[0] + "%", cuts[0] + "–" + cuts[1] + "%",
                                          cuts[1] + "–" + cuts[2] + "%", cuts[2] + "–" + cuts[3] + "%", "Over " + cuts[3] + "%"];
        var cols = ["down", "z1", "z2", "z3", "z4", "z5"];
        return {bands: sl.map(function (x) { return x === null ? null : x < cm.downhill_pct ? 0 : 1 + cuts.filter(function (c) { return x > c; }).length; }),
                legend: labels.map(function (nm, i) { return {i: i, name: nm, colour: cols[i]}; }),
                label: function (b) { return "Slope " + labels[b].toLowerCase(); },
                colourOf: function (b) { return cols[b]; }};
    }
    return null;
}
function bandColour(cb, b) { return cb.colourOf ? cb.colourOf(b) : "z" + (b + 1); }

// Share of points per band (for the legend's %), only bands that occur.
function bandShares(cb) {
    var cnt = {}, tot = 0;
    cb.bands.forEach(function (b) { if (b !== null) { cnt[b] = (cnt[b] || 0) + 1; tot++; } });
    return cb.legend.filter(function (l) { return cnt[l.i]; })
                    .map(function (l) { var p = 100 * cnt[l.i] / tot; return {i: l.i, name: l.name, colour: l.colour, pct: p < 1 ? "<1" : String(Math.round(p))}; });
}

// ------------------------------------------------------------------ chart helpers
function niceTicks(lo, hi, n) {
    var span = hi - lo || 1, step0 = span / (n || 3), mag = Math.pow(10, Math.floor(Math.log(step0) / Math.LN10));
    var steps = [1, 2, 2.5, 5, 10].map(function (x) { return x * mag; }), step = steps[steps.length - 1];
    for (var i = 0; i < steps.length; i++) if (steps[i] >= step0) { step = steps[i]; break; }
    var out = [];
    for (var v = Math.ceil(lo / step) * step; v <= hi + 1e-9; v += step) out.push(Math.round(v * 1e6) / 1e6);
    return out;
}

function pct(sorted, q) { return sorted[Math.min(sorted.length - 1, Math.floor(q * (sorted.length - 1)))]; }

// Scale for one channel over the visible points: {vals (display units, clamped), lo, hi,
// ticks:[[value, label]]}. Pace is drawn as speed (faster = up) with pace labels.
function channelScale(ch, raw) {
    var mul = ch.mul || 1, vals = raw.map(function (v) { return v === null || v === undefined ? null : v * mul; });
    if (ch.min) vals = vals.map(function (v) { return v !== null && v < ch.min ? null : v; });
    var f = vals.filter(function (v) { return v !== null; }).sort(function (a, b) { return a - b; });
    if (!f.length) return null;
    var lo, hi, ticks;
    if (ch.pace) {
        var moving = f.filter(function (v) { return v > 0.3; });
        if (!moving.length) return null;
        lo = Math.max(0.3, pct(moving, 0.03) * 0.9); hi = pct(moving, 0.995) * 1.05;
        var per = ch.pace, cands = per === 100 ? [60, 90, 120, 150, 180, 210, 240, 300] : [180, 240, 300, 360, 420, 480, 540, 600, 720, 840, 900, 1080, 1200, 1500, 1800];
        ticks = cands.map(function (s) { return [per / s, fmtClock(s)]; }).filter(function (t) { return t[0] >= lo && t[0] <= hi; });
        if (ticks.length > 4) ticks = ticks.filter(function (_, i) { return i % 2 === 0; });
        vals = vals.map(function (v) { return v === null ? null : Math.max(lo, Math.min(hi, v)); });
    } else {
        lo = f[0]; hi = f[f.length - 1];
        if (ch.min_span && hi - lo < ch.min_span) { var mid = (hi + lo) / 2; lo = mid - ch.min_span / 2; hi = mid + ch.min_span / 2; }
        var padv = (hi - lo) * 0.08 || 1; lo -= padv; hi += padv;
        if (lo < 0 && f[0] >= 0) lo = 0;
        ticks = niceTicks(lo, hi, 3).map(function (v) { return [v, String(v)]; });
    }
    return {vals: vals, lo: lo, hi: hi, ticks: ticks};
}

function channelValueText(ch, raw) {
    if (raw === null || raw === undefined) return null;
    if (ch.pace) return paceText(raw, ch.pace);
    var v = raw * (ch.mul || 1);
    return ch.mul ? v.toFixed(1) : String(Math.round(v));
}

// Summary of a selected stretch [a, b] (stream indexes): "0.79 km · 5:00 · heart rate 157 bpm ..."
function sectionSummary(st, chans, a, b) {
    var s = st.streams, dt = s.t[b] - s.t[a], parts = [];
    var dist = s.dist && s.dist[a] !== null && s.dist[b] !== null ? s.dist[b] - s.dist[a] : null;
    if (dist !== null) parts.push(dist.toFixed(2) + " km");
    parts.push(fmtClock(dt));
    chans.forEach(function (c) {
        var seg = (s[c.key] || []).slice(a, b + 1);
        if (c.pace) {
            var v = dist && dt ? dist * 1000 / dt : null;
            if (v) parts.push("pace " + paceText(v, c.pace) + " " + c.unit);
        } else if (c.key === "alt") {
            parts.push("climb +" + Math.round(gain(seg)) + " m");
        } else {
            var vals = seg.map(function (r) { return r === null ? null : r * (c.mul || 1); })
                          .filter(function (v) { return v !== null && !(c.min && v < c.min); });
            if (vals.length) {
                var m = vals.reduce(function (x, y) { return x + y; }, 0) / vals.length;
                parts.push(c.label.toLowerCase() + " " + (c.mul ? m.toFixed(1) : Math.round(m)) + (c.unit ? " " + c.unit : ""));
            }
        }
    });
    if (s.alt && !chans.some(function (c) { return c.key === "alt"; })) parts.push("climb +" + Math.round(gain(s.alt.slice(a, b + 1))) + " m");
    return parts.join(" · ");
}
function gain(xs) { var g = 0; for (var i = 1; i < xs.length; i++) if (xs[i] !== null && xs[i - 1] !== null && xs[i] > xs[i - 1]) g += xs[i] - xs[i - 1]; return g; }

// The longest continuous run of indexes whose flag is true (gap tolerance: `gap` points).
function longestRun(flags, gap) {
    var best = [-1, -1], ra = -1, last = -1, miss = 0;
    for (var i = 0; i < flags.length; i++) {
        if (flags[i]) { if (ra < 0) ra = i; last = i; miss = 0; }
        else if (ra >= 0 && ++miss > gap) { if (last - ra > best[1] - best[0]) best = [ra, last]; ra = -1; miss = 0; }
    }
    if (ra >= 0 && last - ra > best[1] - best[0]) best = [ra, last];
    return best[0] < 0 ? null : best;
}

// ------------------------------------------------------------------ compared with your usual
// current + candidates are facts objects ({sport_id, dist_m, duration_s, avg_speed, avg_hr,
// avg_power, ascent_m, start}); candidates are every other activity. Returns {text, tone} or null.
function compareUsual(cfg, sport, cur, candidates) {
    var cu = cfg.compare_usual, t = Date.parse(cur.start);
    if (!t) return null;
    var byLen = sport === "indoor_ride" || sport === "gym" ? "duration_s" : "dist_m";
    var ref = cur[byLen];
    var sameSport = candidates.filter(function (c) {
        if (c === cur || sportKey(cfg, c.sport_id) !== sport) return false;
        var tc = Date.parse(c.start);
        return tc && tc < t && t - tc <= cu.window_days * 86400000;
    });
    if (ref > 0) {
        // a record for the calendar year beats a comparison
        var year = String(cur.start).slice(0, 4), inYear = candidates.filter(function (c) {
            return c !== cur && sportKey(cfg, c.sport_id) === sport && String(c.start).slice(0, 4) === year && Date.parse(c.start) < t; });
        if (inYear.length >= cu.min_similar && sport !== "gym") {
            var longest = inYear.every(function (c) { return (c.dist_m || 0) < cur.dist_m; });
            var climb = cur.ascent_m > 0 && inYear.every(function (c) { return (c.ascent_m || 0) < cur.ascent_m; });
            var noun = sport === "ride" || sport === "indoor_ride" ? "ride" : sport === "run" ? "run" : sport === "hike" ? "hike" : sport === "walk" ? "walk" : "activity";
            if (longest && climb && byLen === "dist_m") return {text: "Your longest " + noun + " and biggest climb of " + year + " so far (" + (inYear.length + 1) + " " + noun + "s).", tone: "best"};
            if (longest && byLen === "dist_m") return {text: "Your longest " + noun + " of " + year + " so far (" + (inYear.length + 1) + " " + noun + "s).", tone: "best"};
        }
    }
    if (!(ref > 0)) return null;
    var similar = sameSport.filter(function (c) { return c[byLen] >= ref * cu.similar_ratio[0] && c[byLen] <= ref * cu.similar_ratio[1]; });
    if (similar.length < cu.min_similar) return null;
    var what = (sport === "indoor_ride" || sport === "gym") ? "of similar length" : "of " + Math.round(ref * cu.similar_ratio[0] / 1000) + "–" + Math.round(ref * cu.similar_ratio[1] / 1000) + " km";
    var noun2 = sport === "run" ? "runs" : sport === "walk" ? "walks" : sport === "hike" ? "hikes" : sport === "indoor_ride" ? "indoor rides" : sport === "ride" ? "rides" : "sessions";
    var base = " on your " + similar.length + " " + noun2 + " " + what + " in the last year.";
    var medPw = median(similar.map(function (c) { return c.avg_power; }));
    if (cur.avg_power && medPw) {
        var dp = (cur.avg_power - medPw) / medPw * 100;
        if (Math.abs(dp) >= cu.min_pct)
            return {text: (dp > 0 ? "Stronger than usual: " : "Easier than usual: ") + Math.round(cur.avg_power) + " W, against " + Math.round(medPw) + " W" + base, tone: dp > 0 ? "up" : "down"};
    }
    var medV = median(similar.map(function (c) { return c.avg_speed; }));
    if (cur.avg_speed && medV) {
        var dv = (cur.avg_speed - medV) / medV * 100;
        if (Math.abs(dv) >= cu.min_pct) {
            var show = function (v) { return isFoot(sport) ? paceText(v, 1000) + " /km" : (v * 3.6).toFixed(1) + " km/h"; };
            return {text: (dv > 0 ? "Faster than usual: " : "Slower than usual: ") + show(cur.avg_speed) + ", against " + show(medV) + base, tone: dv > 0 ? "up" : "down"};
        }
    }
    var medH = median(similar.map(function (c) { return c.avg_hr; }));
    if (cur.avg_hr && medH && Math.abs(cur.avg_hr - medH) >= cu.min_bpm)
        return {text: (cur.avg_hr < medH ? "Easier than usual: heart rate " : "Harder than usual: heart rate ") + Math.round(cur.avg_hr) + " bpm, against " + Math.round(medH) + base, tone: cur.avg_hr < medH ? "up" : "down"};
    return null;
}

// Swim benchmark: this swim's longest non-stop vs the best of earlier swims that have lengths.
function swimBenchmark(longestNow, earlier) {
    if (!longestNow) return null;
    var prev = earlier.filter(function (e) { return e.longest_nonstop_m; });
    if (!prev.length) return {text: "Longest non-stop swim: " + longestNow + " m.", tone: "up"};
    var best = Math.max.apply(null, prev.map(function (e) { return e.longest_nonstop_m; }));
    if (longestNow > best) return {text: "New longest non-stop swim: " + longestNow + " m (before: " + best + " m).", tone: "best"};
    return {text: "Longest non-stop swim: " + longestNow + " m. Your best so far is " + best + " m.", tone: "up"};
}

// ------------------------------------------------------------------ planned workout (indoor rides)
// intervals.icu workout_doc -> power target blocks in watts: [{t0, t1, lo, hi}] (seconds from the
// start; lo/hi differ on a ramp). Handles nested repeats ({reps, steps}) and the power units
// intervals.icu uses: "%ftp" (value or start/end), "w", and ranges ({start, end} or {value}).
function workoutBlocks(doc, ftp) {
    var out = [], t = 0;
    function watts(p, which) {
        if (!p) return null;
        var v = p[which] !== undefined ? p[which] : (p.value !== undefined ? p.value : null);
        if (v === null || v === undefined) return null;
        var u = p.units || "%ftp";
        if (u === "w" || u === "W" || u === "watts") return v;
        if (u === "%ftp") return ftp ? ftp * v / 100 : null;
        return null;
    }
    function walk(steps) {
        (steps || []).forEach(function (s) {
            if (s.reps && s.steps) { for (var r = 0; r < s.reps; r++) walk(s.steps); return; }
            var d = s.duration || 0;
            if (d <= 0) return;
            var lo = watts(s.power, "start"), hi = watts(s.power, "end");
            if (lo === null) lo = hi;
            if (hi === null) hi = lo;
            if (lo !== null) out.push({ t0: t, t1: t + d, lo: lo, hi: hi });
            t += d;
        });
    }
    walk(doc && doc.steps);
    return out;
}

// Which chart lines start switched on: the sport's defaults that this file actually recorded;
// when it has none of them (a ride with no power or heart rate), the first two it does have -
// never an empty chart. Returns {channel id: bool} over the sport's channel list.
// A chart line worth drawing: the stream exists and actually varies. A flat line (a watch left
// on a table: altitude 27 m for 11 minutes) says nothing, so it is hidden like a missing one.
function channelUseful(st, key) {
    var v = st && st.streams && st.streams[key];
    if (!v) return false;
    var lo = null, hi = null;
    for (var i = 0; i < v.length; i++) {
        var x = v[i];
        if (x === null || x === undefined || !isFinite(x)) continue;
        if (lo === null || x < lo) lo = x;
        if (hi === null || x > hi) hi = x;
        if (hi > lo) return true;
    }
    return false;
}

function defaultChannelsOn(cfg, sportCfg, st) {
    var c = sportCfg && sportCfg.chart, on = {}, avail = [];
    if (!c || !c.channels) return on;
    c.channels.forEach(function (id) {
        var def = cfg.channels[id], has = !!(def && channelUseful(st, def.key));
        on[id] = has && (c.on || []).indexOf(id) >= 0;
        if (has) avail.push(id);
    });
    if (!avail.some(function (id) { return on[id]; }))
        avail.slice(0, 2).forEach(function (id) { on[id] = true; });
    return on;
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        sportKey: sportKey, isFoot: isFoot, fmtClock: fmtClock, formatValue: formatValue, metricRaw: metricRaw,
        overview: overview, zonesFor: zonesFor, zoneTimes: zoneTimes, zoneOf: zoneOf, zoneBoundsText: zoneBoundsText,
        colourBands: colourBands, bandColour: bandColour, bandShares: bandShares, niceTicks: niceTicks,
        channelScale: channelScale, channelValueText: channelValueText, sectionSummary: sectionSummary,
        longestRun: longestRun, compareUsual: compareUsual, swimBenchmark: swimBenchmark, median: median,
        paceText: paceText, ftpFrom: ftpFrom, workoutBlocks: workoutBlocks, defaultChannelsOn: defaultChannelsOn, refineSport: refineSport, channelUseful: channelUseful
    };
}
