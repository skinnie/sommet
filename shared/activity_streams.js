// FIT -> what the activity screen draws. The JavaScript twin of tools/activity_streams.py (the
// desktop decodes in its Python backend; the Android app has no backend, so it runs this).
// tools/test_activity_streams_parity.js checks both give the same JSON on real files - change
// them together. Plain ES2017 + DataView, no platform APIs; copied into the Android app by
// tools/gen_activity_view.py. See the Python file's docstring for the output format.

var FIT_EPOCH = 631065600, SEMI = 180 / Math.pow(2, 31), REST_SPLIT_S = 10;
var BT = { 0: ["u8", 1, 0xFF], 1: ["s8", 1, 0x7F], 2: ["u8", 1, 0xFF], 3: ["s16", 2, 0x7FFF],
           4: ["u16", 2, 0xFFFF], 5: ["s32", 4, 0x7FFFFFFF], 6: ["u32", 4, 0xFFFFFFFF],
           8: ["f32", 4, null], 9: ["f64", 8, null], 10: ["u8", 1, 0x00], 11: ["u16", 2, 0x0000],
           12: ["u32", 4, 0x00000000] };
var WANTED = { 0: 1, 18: 1, 19: 1, 20: 1, 21: 1, 27: 1, 101: 1 };
var LAP_TRIGGERS = { 0: "manual", 1: "time", 2: "distance", 3: "position_start", 4: "position_lap",
                     5: "position_waypoint", 6: "position_marked", 7: "session_end", 8: "fitness_equipment" };
// FIT workout_step: duration_type, intensity, and the scale of duration_value per kind.
var STEP_ENDS = { 0: "time", 1: "distance", 5: "lap" };
var STEP_INTENSITY = { 0: "active", 1: "rest", 2: "warmup", 3: "cooldown" };
var STEP_VALUE_SCALE = { time: 1000, distance: 100 };
var STROKES = { 0: "freestyle", 1: "backstroke", 2: "breaststroke", 3: "butterfly", 4: "drill", 5: "mixed", 6: "im" };
var FOOT_SPORTS = { 1: 1, 11: 1, 17: 1 };

function readVal(dv, o, kind, little) {
    switch (kind) {
    case "u8": return dv.getUint8(o);
    case "s8": return dv.getInt8(o);
    case "u16": return dv.getUint16(o, little);
    case "s16": return dv.getInt16(o, little);
    case "u32": return dv.getUint32(o, little);
    case "s32": return dv.getInt32(o, little);
    case "f32": return dv.getFloat32(o, little);
    case "f64": return dv.getFloat64(o, little);
    }
    return null;
}

// Calls onMsg(globalNum, {field: value}) for the messages in WANTED.
function decodeFit(bytes, onMsg) {
    var dv = new DataView(bytes.buffer, bytes.byteOffset, bytes.byteLength);
    if (bytes.length < 12) throw new Error("not a FIT file (too short)");
    var hs = bytes[0];
    if (String.fromCharCode(bytes[8], bytes[9], bytes[10], bytes[11]) !== ".FIT") throw new Error("not a FIT file (no .FIT signature)");
    var end = Math.min(bytes.length, hs + dv.getUint32(4, true)), i = hs, defs = {}, lastTs = 0;
    while (i < end) {
        var h = bytes[i++], lt, comp = false, off = 0;
        if (h & 0x80) { lt = (h >> 5) & 3; off = h & 0x1F; comp = true; }
        else {
            lt = h & 0x0F;
            if (h & 0x40) {
                var arch = bytes[i + 1], gn = arch === 0 ? dv.getUint16(i + 2, true) : dv.getUint16(i + 2, false), n = bytes[i + 4];
                i += 5;
                var fields = [];
                for (var f = 0; f < n; f++) { fields.push([bytes[i], bytes[i + 1], bytes[i + 2] & 0x1F]); i += 3; }
                var dev = 0;
                if (h & 0x20) { var nd = bytes[i++]; for (var d = 0; d < nd; d++) { dev += bytes[i + 1]; i += 3; } }
                defs[lt] = { little: arch === 0, gn: gn, fields: fields, dev: dev };
                continue;
            }
        }
        var def = defs[lt];
        if (!def) throw new Error("data message for undefined local type " + lt);
        var vals = {}, want = WANTED[def.gn];
        for (var k = 0; k < def.fields.length; k++) {
            var fn = def.fields[k][0], size = def.fields[k][1], bt = def.fields[k][2];
            if (want) {
                var spec = BT[bt];
                if (spec && size === spec[1]) {
                    var v = readVal(dv, i, spec[0], def.little);
                    if (!(spec[2] !== null && v === spec[2])) vals[fn] = v;
                }
            }
            i += size;
        }
        i += def.dev;
        if (vals[253] !== undefined) lastTs = vals[253];
        else if (comp) {
            var t = (lastTs & ~0x1F) + off;
            if (t < lastTs) t += 0x20;
            lastTs = t; vals[253] = t;
        }
        if (want) onMsg(def.gn, vals);
    }
}

function sint8(v) { return v !== undefined && v !== null && v > 127 ? v - 256 : v; }
function mean(xs) { var s = 0, n = 0; for (var i = 0; i < xs.length; i++) if (xs[i] !== null) { s += xs[i]; n++; } return n ? s / n : null; }
function roundTo(v, d) { if (v === null) return null; var m = Math.pow(10, d); return Math.round(v * m) / m; }
function has(o, k) { return o[k] !== undefined; }

function normalizedPower(times, watts) {
    var pts = [];
    for (var i = 0; i < times.length; i++) if (watts[i] !== null) pts.push([times[i], watts[i]]);
    if (pts.length < 30) return null;
    var t0 = pts[0][0], span = Math.floor(pts[pts.length - 1][0] - t0), grid = new Array(span + 1), j = 0;
    for (var s = 0; s <= span; s++) {
        while (j + 1 < pts.length && pts[j + 1][0] - t0 <= s) j++;
        grid[s] = (s - (pts[j][0] - t0)) <= 10 ? pts[j][1] : 0;
    }
    if (grid.length < 30) return null;
    var acc = 0;
    for (s = 0; s < 30; s++) acc += grid[s];
    var sum4 = Math.pow(acc / 30, 4), cnt = 1;
    for (s = 30; s < grid.length; s++) { acc += grid[s] - grid[s - 30]; sum4 += Math.pow(acc / 30, 4); cnt++; }
    return Math.pow(sum4 / cnt, 0.25);
}

function interp(ts, vs, maxGap) {
    var known = [];
    for (var k = 0; k < vs.length; k++) if (vs[k] !== null) known.push(k);
    for (var q = 0; q + 1 < known.length; q++) {
        var a = known[q], b = known[q + 1];
        if (b - a < 2) continue;
        var dt = ts[b] - ts[a];
        if (dt <= 0 || (maxGap !== null && dt > maxGap)) continue;
        for (var m = a + 1; m < b; m++) vs[m] = vs[a] + (vs[b] - vs[a]) * ((ts[m] - ts[a]) / dt);
    }
}

function fillGaps(full) {
    var ts = full.t, dist = full.dist, k;
    var anyD = dist.some(function (v) { return v !== null; });
    if (anyD) {
        interp(ts, dist, null);
        var first = -1, last = -1;
        for (k = 0; k < dist.length; k++) if (dist[k] !== null) { if (first < 0) first = k; last = k; }
        for (k = 0; k < first; k++) dist[k] = dist[first] < 0.05 ? 0.0 : null;
        for (k = last + 1; k < dist.length; k++) dist[k] = dist[last];
    }
    ["lat", "lon"].forEach(function (ch) { if (full[ch].some(function (v) { return v !== null; })) interp(ts, full[ch], 30); });
}

function lapsKind(laps, hasLengths) {
    if (laps.length < 2) return null;
    if (hasLengths) return "auto";          // a pool swim's laps are its rest intervals, shown as sets
    var trig = laps.map(function (l) { return l.trigger; }).filter(function (x) { return x; });
    if (trig.length) return trig.some(function (x) { return x === "manual" || x === "position_lap"; }) ? "pressed" : "auto";
    var dists = laps.slice(0, -1).map(function (l) { return l.dist_m; });
    if (dists.every(function (d) { return d; })) {
        var ref = dists[0], splits = [1000, 1609.344, 5000, 500, 400];
        for (var s = 0; s < splits.length; s++)
            if (dists.every(function (d) { return Math.abs(d - splits[s]) <= splits[s] * 0.02; })) return "auto";
        if (Math.max.apply(null, dists) - Math.min.apply(null, dists) <= ref * 0.01) return "auto";
    }
    return "pressed";
}

function streamsFromFit(bytes, points) {
    points = points || 2000;
    var fileId = null, session = null, lapsRaw = [], recs = [], lensRaw = [];
    var stepDefs = {}, stepEvents = [];
    decodeFit(bytes, function (gn, v) {
        if (gn === 27) stepDefs[v[254]] = v;
        else if (gn === 21 && (v[0] === 3 || v[0] === 4)) stepEvents.push(v);
        if (gn === 0 && !fileId) fileId = v;
        else if (gn === 18 && !session) session = v;
        else if (gn === 19) lapsRaw.push(v);
        else if (gn === 20) recs.push(v);
        else if (gn === 101) lensRaw.push(v);
    });
    fileId = fileId || {}; session = session || {};
    var sport = has(session, 5) ? session[5] : null, foot = !!FOOT_SPORTS[sport];
    var t0 = session[2] || (recs.length && recs[0][253]) || 0;

    var keys = ["t", "dist", "hr", "pw", "cad", "v", "alt", "lat", "lon", "temp"], full = {};
    keys.forEach(function (k) { full[k] = []; });
    recs.forEach(function (r) {
        if (!has(r, 253)) return;
        full.t.push(r[253] - t0);
        full.dist.push(has(r, 5) ? r[5] / 100000 : null);
        full.hr.push(r[3] ? r[3] : null);
        full.pw.push(has(r, 7) ? r[7] : null);
        var cad = has(r, 4) ? r[4] : null;
        if (cad !== null && foot) cad = (cad + (r[53] || 0) / 128) * 2;
        full.cad.push(cad);
        var spd = has(r, 73) ? r[73] : has(r, 6) ? r[6] : null;
        full.v.push(spd !== null ? spd / 1000 : null);
        var alt = has(r, 78) ? r[78] : has(r, 2) ? r[2] : null;
        full.alt.push(alt !== null ? alt / 5 - 500 : null);
        full.lat.push(has(r, 0) ? r[0] * SEMI : null);
        full.lon.push(has(r, 1) ? r[1] * SEMI : null);
        full.temp.push(has(r, 13) ? sint8(r[13]) : null);
    });
    var n = full.t.length;
    fillGaps(full);
    var present = keys.filter(function (k) { return k !== "t" && full[k].some(function (x) { return x !== null; }); });

    var step = Math.max(1, Math.ceil(n / Math.max(1, points))), streams = { t: [] };
    present.forEach(function (k) { streams[k] = []; });
    for (var s = 0; s < n; s += step) {
        var e = Math.min(n, s + step);
        streams.t.push(full.t[s]);
        present.forEach(function (k) {
            var seg = full[k].slice(s, e);
            if (k === "lat" || k === "lon") {
                var val = null;
                for (var q = 0; q < seg.length; q++) if (seg[q] !== null) { val = seg[q]; break; }
                streams[k].push(val !== null ? roundTo(val, 6) : null);
            } else streams[k].push(roundTo(mean(seg), 3));
        });
    }

    function hist(ch) {
        var acc = {};
        for (var k = 0; k < n - 1; k++) {
            var v = full[ch][k], dt = full.t[k + 1] - full.t[k];
            if (v === null || dt <= 0 || dt > 10) continue;
            var key = Math.floor(v);
            acc[key] = (acc[key] || 0) + dt;
        }
        return Object.keys(acc).map(Number).sort(function (a, b) { return a - b; })
                     .map(function (key) { return [key, roundTo(acc[key], 1)]; });
    }
    var histograms = {};
    if (present.indexOf("hr") >= 0) histograms.hr = hist("hr");
    if (present.indexOf("pw") >= 0) histograms.pw = hist("pw");

    function sv(f, scale) { return has(session, f) ? session[f] / (scale || 1) : null; }
    var summary = {
        elapsed_s: sv(7, 1000), timer_s: sv(8, 1000), dist_m: sv(9, 100),
        kcal: has(session, 11) ? session[11] : null, avg_hr: has(session, 16) ? session[16] : null, max_hr: has(session, 17) ? session[17] : null,
        avg_cad: has(session, 18) ? session[18] : null, max_cad: has(session, 19) ? session[19] : null,
        avg_pw: has(session, 20) ? session[20] : null, max_pw: has(session, 21) ? session[21] : null,
        ascent_m: has(session, 22) ? session[22] : null, descent_m: has(session, 23) ? session[23] : null,
        avg_speed: has(session, 124) ? session[124] / 1000 : has(session, 14) ? session[14] / 1000 : null,
        max_speed: has(session, 125) ? session[125] / 1000 : has(session, 15) ? session[15] / 1000 : null,
        pool_length_m: sv(44, 100), avg_temp: has(session, 57) ? sint8(session[57]) : null, max_temp: has(session, 58) ? sint8(session[58]) : null
    };
    if (foot) ["avg_cad", "max_cad"].forEach(function (f) { if (summary[f] !== null) summary[f] = summary[f] * 2; });
    if (present.indexOf("pw") >= 0) { var np = normalizedPower(full.t, full.pw); summary.np_w = np ? Math.round(np) : null; }
    var temps = full.temp.filter(function (x) { return x !== null; });
    if (temps.length) {
        summary.min_temp = Math.min.apply(null, temps);
        if (summary.max_temp === null) summary.max_temp = Math.max.apply(null, temps);
    }

    var laps = lapsRaw.map(function (lp) {
        var spd = has(lp, 110) ? lp[110] : has(lp, 13) ? lp[13] : null, cad = has(lp, 17) ? lp[17] : null;
        if (cad !== null && foot) cad = cad * 2;
        return { start_s: has(lp, 2) ? lp[2] - t0 : null, timer_s: has(lp, 8) ? lp[8] / 1000 : null,
                 elapsed_s: has(lp, 7) ? lp[7] / 1000 : null, dist_m: has(lp, 9) ? lp[9] / 100 : null,
                 avg_hr: has(lp, 15) ? lp[15] : null, max_hr: has(lp, 16) ? lp[16] : null,
                 avg_speed: spd !== null ? spd / 1000 : null, avg_cad: cad, avg_pw: has(lp, 19) ? lp[19] : null,
                 trigger: has(lp, 24) ? (LAP_TRIGGERS[lp[24]] || null) : null };
    });

    var recHr = recs.filter(function (r) { return has(r, 253) && r[3]; }).map(function (r) { return [r[253], r[3]]; });
    var lengths = lensRaw.map(function (ln, idx) {
        var st = has(ln, 2) ? ln[2] : null, swim = has(ln, 4) ? ln[4] / 1000 : null, el = has(ln, 3) ? ln[3] / 1000 : null, hr = null;
        if (st !== null && el) {
            var hs = recHr.filter(function (p) { return p[0] >= st && p[0] < st + el; }).map(function (p) { return p[1]; });
            hr = hs.length ? Math.round(hs.reduce(function (a, b) { return a + b; }, 0) / hs.length) : null;
        }
        return { n: idx + 1, start_s: st !== null ? st - t0 : null, swim_s: swim,
                 rest_s: el !== null && swim !== null ? el - swim : 0, strokes: has(ln, 5) ? ln[5] : null,
                 stroke: has(ln, 7) ? (STROKES[ln[7]] || null) : null, active: (has(ln, 12) ? ln[12] : 1) === 1, avg_hr: hr };
    });
    // Garmin writes a rest at the wall as its own IDLE length (length_type 0): fold each idle
    // length into the rest after the length before it (same as the Python twin).
    var lastActive = null;
    lengths.forEach(function (ln, k) {
        if (ln.active) { lastActive = ln; return; }
        if (lastActive) {
            var raw = lensRaw[k], idle = has(raw, 3) ? raw[3] / 1000 : (has(raw, 4) ? raw[4] / 1000 : 0);
            lastActive.rest_s = (lastActive.rest_s || 0) + idle;
        }
    });
    var pool = summary.pool_length_m || 25, sets = [], cur = [];
    lengths.filter(function (l) { return l.active && l.swim_s; }).forEach(function (l) {
        cur.push(l);
        if ((l.rest_s || 0) >= REST_SPLIT_S) { sets.push(cur); cur = []; }
    });
    if (cur.length) sets.push(cur);
    var setRows = sets.map(function (st, k) {
        var strokes = st.map(function (x) { return x.strokes; }).filter(function (x) { return x !== null; });
        var kinds = st.map(function (x) { return x.stroke; }).filter(function (x) { return x; });
        var hrs = st.map(function (x) { return x.avg_hr; }).filter(function (x) { return x; });
        var best = null, bestN = -1, counts = {};
        kinds.forEach(function (x) { counts[x] = (counts[x] || 0) + 1; if (counts[x] > bestN) { bestN = counts[x]; best = x; } });
        return { n: k + 1, lengths: st.length, dist_m: Math.round(st.length * pool),
                 swim_s: roundTo(st.reduce(function (a, x) { return a + x.swim_s; }, 0), 1),
                 rest_after_s: roundTo(st[st.length - 1].rest_s || 0, 1), stroke: best,
                 strokes_per_length: strokes.length ? Math.round(strokes.reduce(function (a, b) { return a + b; }, 0) / strokes.length) : null,
                 avg_hr: hrs.length ? Math.round(hrs.reduce(function (a, b) { return a + b; }, 0) / hrs.length) : null };
    });

    // guided-workout steps: each workout_step start event, described by the step it names
    var workoutSteps = [], workoutN = 1;
    stepEvents.forEach(function (ev) {
        if (ev[0] === 3) {                    // workout stopped: the next step opens a new one
            if (ev[1] === 1 && workoutSteps.length) workoutN = workoutSteps[workoutSteps.length - 1].workout + 1;
            return;
        }
        if (ev[1] !== 0 || !has(ev, 253)) return;
        var d = stepDefs[ev[3]] || {};
        var endsOn = has(d, 1) && STEP_ENDS[d[1]] ? STEP_ENDS[d[1]] : null;
        var intensity = has(d, 7) && STEP_INTENSITY[d[7]] ? STEP_INTENSITY[d[7]] : null;
        workoutSteps.push({ workout: workoutN, start_s: ev[253] - t0, intensity: intensity, ends_on: endsOn,
                            value: has(d, 2) && endsOn && STEP_VALUE_SCALE[endsOn] ? d[2] / STEP_VALUE_SCALE[endsOn] : null });
    });

    return {
        ok: true, workout_steps: workoutSteps, laps_kind: lapsKind(laps, lengths.length > 0),
        sport: { sport: sport, sub_sport: has(session, 6) ? session[6] : null },
        start_utc: t0 ? t0 + FIT_EPOCH : null, manufacturer: has(fileId, 1) ? fileId[1] : null, product: has(fileId, 2) ? fileId[2] : null,
        records: n, channels: present, summary: summary, streams: streams, hist: histograms,
        laps: laps, lengths: lengths, sets: setRows,
        longest_nonstop_m: setRows.length ? Math.max.apply(null, setRows.map(function (r) { return r.dist_m; })) : null
    };
}

if (typeof module !== "undefined" && module.exports) module.exports = { streamsFromFit: streamsFromFit, decodeFit: decodeFit, lapsKind: lapsKind };
