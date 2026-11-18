// Cut one activity FIT into two at a long stop - sync/06, "Split here". The JavaScript twin of
// tools/fit_split.py (same rules, byte for byte the same output; tools/test_fit_split.py checks
// it). Read the Python file's docstring for what goes where. Plain ES2017 so Hermes and Qt V4
// both run it; no platform APIs. Input and output are Uint8Array.

var LONG_GAP_S = 6 * 3600, RIDING_DT_S = 60;
var CRC = [0x0000, 0xCC01, 0xD801, 0x1400, 0xF001, 0x3C00, 0x2800, 0xE401,
           0xA001, 0x6C00, 0x7800, 0xB401, 0x5000, 0x9C01, 0x8801, 0x4400];
// base type -> [size, signed, invalid]; 64-bit types are only ever written as invalid
var BT = { 0: [1, false, 0xFF], 1: [1, true, 0x7F], 2: [1, false, 0xFF], 3: [2, true, 0x7FFF],
           4: [2, false, 0xFFFF], 5: [4, true, 0x7FFFFFFF], 6: [4, false, 0xFFFFFFFF],
           8: [4, false, 0xFFFFFFFF], 10: [1, false, 0], 11: [2, false, 0], 12: [4, false, 0],
           13: [1, false, 0xFF] };
var SESSION = 18, LAP = 19, RECORD = 20, ACTIVITY = 34;
var KEEP = { 0: 1, 1: 1, 5: 1, 6: 1, 24: 1, 25: 1, 26: 1, 28: 1, 44: 1, 46: 1, 254: 1 };

function fitCrc(bytes, crc) {
    crc = crc || 0;
    for (var i = 0; i < bytes.length; i++) {
        var b = bytes[i], t = CRC[crc & 0xF];
        crc = ((crc >> 4) & 0x0FFF) ^ t ^ CRC[b & 0xF];
        t = CRC[crc & 0xF];
        crc = ((crc >> 4) & 0x0FFF) ^ t ^ CRC[(b >> 4) & 0xF];
    }
    return crc & 0xFFFF;
}

function readInt(raw, off, size, signed, little) {
    var dv = new DataView(raw.buffer, raw.byteOffset, raw.length);
    if (size === 1) return signed ? dv.getInt8(off) : dv.getUint8(off);
    if (size === 2) return signed ? dv.getInt16(off, little) : dv.getUint16(off, little);
    return signed ? dv.getInt32(off, little) : dv.getUint32(off, little);
}
function writeInt(raw, off, size, signed, little, v) {
    var dv = new DataView(raw.buffer, raw.byteOffset, raw.length);
    if (size === 1) { if (signed) dv.setInt8(off, v); else dv.setUint8(off, v); }
    else if (size === 2) { if (signed) dv.setInt16(off, v, little); else dv.setUint16(off, v, little); }
    else { if (signed) dv.setInt32(off, v, little); else dv.setUint32(off, v, little); }
}

function parse(data) {
    if (data.length < 12 || String.fromCharCode(data[8], data[9], data[10], data[11]) !== ".FIT")
        throw new Error("not a FIT file");
    var hs = data[0];
    var size = data[4] | (data[5] << 8) | (data[6] << 16) | (data[7] * 16777216);
    var end = Math.min(data.length, hs + size);
    var items = [], defs = {}, i = hs;
    while (i < end) {
        var start = i, h = data[i++];
        if (h & 0x80) throw new Error("this file uses compressed timestamps; it can't be split here");
        var lt = h & 0x0F;
        if (h & 0x40) {
            var arch = data[i + 1];
            var gn = arch === 0 ? (data[i + 2] | (data[i + 3] << 8)) : ((data[i + 2] << 8) | data[i + 3]);
            var n = data[i + 4];
            i += 5;
            var layout = [], off = 1;
            for (var k = 0; k < n; k++) {
                layout.push([data[i], off, data[i + 1], data[i + 2] & 0x1F]);
                off += data[i + 1];
                i += 3;
            }
            var dev = 0;
            if (h & 0x20) {
                var nd = data[i++];
                for (var q = 0; q < nd; q++) { dev += data[i + 1]; i += 3; }
            }
            defs[lt] = { arch: arch, gn: gn, layout: layout, dev: dev };
            items.push({ kind: "def", raw: data.slice(start, i) });
            continue;
        }
        var d = defs[lt];
        if (!d) throw new Error("data message for undefined local type " + lt + " at byte " + start);
        var sz = d.dev;
        d.layout.forEach(function (f) { sz += f[2]; });
        var raw = data.slice(start, i + sz);
        i += sz;
        var vals = {};
        d.layout.forEach(function (f) {
            var bt = BT[f[3]];
            if (!bt || bt[0] !== f[2]) return;
            var v = readInt(raw, f[1], f[2], bt[1], d.arch === 0);
            if (v !== bt[2]) vals[f[0]] = v;
        });
        items.push({ kind: "data", raw: raw, gn: d.gn, arch: d.arch, layout: d.layout, vals: vals });
    }
    return { header: data.slice(0, hs), items: items };
}

function setField(item, fn, value) {
    for (var k = 0; k < item.layout.length; k++) {
        var f = item.layout[k];
        if (f[0] !== fn) continue;
        var bt = BT[f[3]], off = f[1], sz = f[2];
        if (!bt || bt[0] !== sz) {
            if (value === null) for (var j = 0; j < sz; j++) item.raw[off + j] = f[3] === 7 ? 0 : 0xFF;
            return;
        }
        var v;
        if (value === null) v = bt[2];
        else {
            v = Math.round(value);                       // half up, like tools/fit_split.py
            var lo = bt[1] ? -Math.pow(2, 8 * sz - 1) : 0;
            var hi = bt[1] ? Math.pow(2, 8 * sz - 1) - 1 : Math.pow(2, 8 * sz) - 1;
            v = Math.min(Math.max(v, lo), hi);
            if (v === bt[2]) v = v > 0 ? v - 1 : v + 1;
        }
        writeInt(item.raw, off, sz, bt[1], item.arch === 0, v);
        return;
    }
}

function ascent(alts) {
    var up = 0, down = 0, ref = null;
    for (var i = 0; i < alts.length; i++) {
        var a = alts[i];
        if (ref === null) ref = a;
        else if (a - ref >= 3) { up += a - ref; ref = a; }
        else if (ref - a >= 3) { down += ref - a; ref = a; }
    }
    return [up, down];
}

function has(o, k) { return o[k] !== undefined; }

function stats(recs) {
    var ts = recs.map(function (r) { return r[253]; }), ride = 0;
    for (var k = 1; k < ts.length; k++) { var dt = ts[k] - ts[k - 1]; if (dt > 0 && dt <= RIDING_DT_S) ride += dt; }
    function pick(f) { return recs.filter(function (r) { return f(r) !== undefined; }).map(f); }
    var dist = pick(function (r) { return r[5]; });
    var hr = recs.filter(function (r) { return r[3]; }).map(function (r) { return r[3]; });
    var spd = pick(function (r) { return has(r, 73) ? r[73] : r[6]; });
    var pw = pick(function (r) { return r[7]; }), cad = pick(function (r) { return r[4]; });
    var alts = recs.filter(function (r) { return has(r, 78) || has(r, 2); })
                   .map(function (r) { return (has(r, 78) ? r[78] : r[2]) / 5.0 - 500; });
    var ud = ascent(alts);
    function avg(xs) { return xs.length ? xs.reduce(function (a, b) { return a + b; }, 0) / xs.length : null; }
    function max(xs) { return xs.length ? Math.max.apply(null, xs) : null; }
    var d0 = dist.length ? dist[0] : 0;
    return { start: ts[0], end: ts[ts.length - 1], ride_s: ride, d0_cm: d0,
             dist_cm: dist.length ? dist[dist.length - 1] - d0 : 0,
             avg_hr: avg(hr), max_hr: max(hr), max_mms: max(spd), avg_pw: avg(pw), max_pw: max(pw),
             avg_cad: avg(cad), max_cad: max(cad),
             ascent: alts.length ? ud[0] : null, descent: alts.length ? ud[1] : null };
}

function patchSummary(item, st, share) {
    item.layout.forEach(function (f) { if (!KEEP[f[0]]) setField(item, f[0], null); });
    var v = item.vals;
    if (item.gn === ACTIVITY) {
        setField(item, 253, st.end);
        setField(item, 0, st.ride_s * 1000);
        [1, 2, 3, 4, 5, 6].forEach(function (fn) {
            if (v[fn] === undefined) return;
            setField(item, fn, fn !== 5 ? v[fn] : v[fn] + (st.end - (has(v, 253) ? v[253] : st.end)));
        });
        return;
    }
    var avgMms = st.ride_s ? st.dist_cm * 10.0 / st.ride_s : null;
    [[253, st.end], [2, st.start], [7, (st.end - st.start) * 1000], [8, st.ride_s * 1000], [9, st.dist_cm],
     [16, st.avg_hr], [17, st.max_hr], [14, avgMms], [124, avgMms], [15, st.max_mms], [125, st.max_mms],
     [20, st.avg_pw], [21, st.max_pw], [18, st.avg_cad], [19, st.max_cad], [22, st.ascent], [23, st.descent]]
        .forEach(function (p) { if (p[1] !== null && p[1] !== undefined) setField(item, p[0], p[1]); });
    if (has(v, 11)) setField(item, 11, v[11] * share);
}

function copyItem(it) { return { kind: it.kind, raw: it.raw.slice(), gn: it.gn, arch: it.arch, layout: it.layout, vals: it.vals }; }

function recordsAndStart(items) {
    var recs = items.filter(function (it) { return it.kind === "data" && it.gn === RECORD && has(it.vals, 253); });
    var sess = null;
    for (var i = 0; i < items.length; i++) if (items[i].kind === "data" && items[i].gn === SESSION) { sess = items[i].vals; break; }
    var t0 = (sess && sess[2]) || (recs.length ? recs[0].vals[253] : 0);
    return { recs: recs, t0: t0 };
}

// [{at_s, len_s}] for the record gaps of LONG_GAP_S or more (see tools/fit_split.py long_stops).
function longStops(data) {
    var rs = recordsAndStart(parse(data).items), out = [];
    for (var k = 1; k < rs.recs.length; k++) {
        var a = rs.recs[k - 1].vals[253], b = rs.recs[k].vals[253];
        if (b - a >= LONG_GAP_S) out.push({ at_s: a - rs.t0, len_s: b - a });
    }
    return out;
}

// -> [first FIT, second FIT] (Uint8Array), cut at the long stop starting at_s seconds in (the
// first long stop when at_s is null/undefined). Throws when there is no such stop.
function splitFit(data, atS) {
    var p = parse(data), items = p.items, rs = recordsAndStart(items), recs = rs.recs, cut = null;
    for (var k = 1; k < recs.length; k++) {
        var a = recs[k - 1].vals[253], b = recs[k].vals[253];
        if (b - a >= LONG_GAP_S && (atS === null || atS === undefined || Math.abs((a - rs.t0) - atS) <= 1)) { cut = a; break; }
    }
    if (cut === null) throw new Error("no stop of 6 hours or more at that point in this file");
    var sides = [recs.filter(function (r) { return r.vals[253] <= cut; }).map(function (r) { return r.vals; }),
                 recs.filter(function (r) { return r.vals[253] > cut; }).map(function (r) { return r.vals; })];
    var st = [stats(sides[0]), stats(sides[1])];
    var total = (st[0].ride_s + st[1].ride_s) || 1, shares = [st[0].ride_s / total, st[1].ride_s / total];
    var shift = [sides[0].length ? st[0].d0_cm : 0, st[1].d0_cm];
    var out = [[], []], side = null;
    items.forEach(function (it) {
        if (it.kind === "def") { out[0].push(it.raw); out[1].push(it.raw); return; }
        var v = it.vals, c;
        if (it.gn === SESSION || it.gn === LAP) {
            var s0 = has(v, 2) ? v[2] : (has(v, 253) ? v[253] : 0), e0 = has(v, 253) ? v[253] : s0;
            if (s0 <= cut && cut < e0) {
                [0, 1].forEach(function (q) { c = copyItem(it); patchSummary(c, st[q], shares[q]); out[q].push(c.raw); });
            } else out[e0 <= cut ? 0 : 1].push(it.raw);
            return;
        }
        if (it.gn === ACTIVITY) {
            [0, 1].forEach(function (q) { c = copyItem(it); patchSummary(c, st[q], shares[q]); out[q].push(c.raw); });
            return;
        }
        if (has(v, 253)) side = v[253] <= cut ? 0 : 1;
        if (side === null) { out[0].push(it.raw); out[1].push(it.raw); return; }
        if (it.gn === RECORD && has(v, 5) && shift[side]) {
            c = copyItem(it); setField(c, 5, v[5] - shift[side]); out[side].push(c.raw); return;
        }
        out[side].push(it.raw);
    });
    return out.map(function (chunks) { return wrap(p.header, chunks); });
}

function wrap(header, chunks) {
    var bodyLen = 0;
    chunks.forEach(function (c) { bodyLen += c.length; });
    var outb = new Uint8Array(header.length + bodyLen + 2);
    outb.set(header, 0);
    outb[4] = bodyLen & 0xFF; outb[5] = (bodyLen >> 8) & 0xFF; outb[6] = (bodyLen >> 16) & 0xFF; outb[7] = (bodyLen >>> 24) & 0xFF;
    if (header.length >= 14) {
        var hc = fitCrc(outb.subarray(0, 12));
        outb[12] = hc & 0xFF; outb[13] = hc >> 8;
    }
    var pos = header.length;
    chunks.forEach(function (c) { outb.set(c, pos); pos += c.length; });
    var crc = fitCrc(outb.subarray(0, pos));
    outb[pos] = crc & 0xFF; outb[pos + 1] = crc >> 8;
    return outb;
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = { splitFit: splitFit, longStops: longStops, fitCrc: fitCrc };
}
