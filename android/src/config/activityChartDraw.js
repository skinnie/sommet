// GENERATED from shared/activity_chart_draw.js by tools/gen_activity_view.py - edit that file.
// How the activity screen DRAWS its charts - ONE file for desktop (QML Canvas) and Android (a
// WebView <canvas>). Both use the standard 2D canvas calls; rounded rectangles go through rrect()
// (arcTo) because Qt's Canvas and the browser disagree on the name. Pointer/touch handling stays
// in each app; everything that ends up as pixels is here. tools/gen_activity_view.py copies it.
// Plain ES2017, no platform APIs. Designed in the mockup artifact 8KKpqaVBCdPUvDUAhzya8h.
//
// o (options) = {
//   L: activity_view_logic module, st: streams, xv: x value per stream point (km or s; NaN = gap),
//   i0, i1: visible stream indexes, channels: [{id,label,unit,key,color,pace?,mul?,min?,min_span?}],
//   focus: the main channel, ribbon: {key, bands, colourOf(b), label(b)} | null,
//   target: {blocks:[{t0,t1,lo,hi}]} | null (planned workout), isDist, dpr?: device pixel ratio,
//   colors: {text, muted, border, borderStrong, card, cardNested, background, primary, hard,
//            warning, secondary, accent}  (all "#rrggbb"), zone: {z1..z7, down, casing},
//   labels: {altitude, fasterUp, plannedTarget, avg, max, best, top, target, colour, all, median}
// }

var PLOT_L = 46, PLOT_R = 12;

function rgba(c, a) {
    if (typeof c === "string" && /^#[0-9a-fA-F]{6}$/.test(c))
        return "rgba(" + parseInt(c.substr(1, 2), 16) + "," + parseInt(c.substr(3, 2), 16) + "," + parseInt(c.substr(5, 2), 16) + "," + a + ")";
    if (typeof c === "string" && /^#[0-9a-fA-F]{8}$/.test(c))           // Qt's #aarrggbb
        return "rgba(" + parseInt(c.substr(3, 2), 16) + "," + parseInt(c.substr(5, 2), 16) + "," + parseInt(c.substr(7, 2), 16) + "," + a + ")";
    return c;
}
function rrect(ctx, x, y, w, h, r) {
    r = Math.max(0, Math.min(r, w / 2, h / 2));
    ctx.beginPath();
    ctx.moveTo(x + r, y);
    ctx.arcTo(x + w, y, x + w, y + h, r);
    ctx.arcTo(x + w, y + h, x, y + h, r);
    ctx.arcTo(x, y + h, x, y, r);
    ctx.arcTo(x, y, x + w, y, r);
    ctx.closePath();
}
function clamp(v, lo, hi) { return Math.max(lo, Math.min(hi, v)); }
function chanColour(o, c) { return o.colors[c.color] || o.colors.primary; }
function zoneColour(o, token) { return o.zone[token] || o.colors.muted; }

function buckets(i0, i1, maxPts) {
    var n = i1 - i0 + 1, out = [], k;
    if (n <= maxPts) { for (k = i0; k <= i1; k++) out.push([k, k]); return out; }
    var step = n / maxPts;
    for (var b = 0; b < maxPts; b++) {
        var a = i0 + Math.floor(b * step), e = Math.min(i1, i0 + Math.floor((b + 1) * step) - 1);
        if (e >= a) out.push([a, e]);
    }
    return out;
}
function meanOf(arr, a, e) {
    var s = 0, c = 0;
    for (var k = a; k <= e; k++) { var v = arr[k]; if (v !== null && v !== undefined && !isNaN(v)) { s += v; c++; } }
    return c ? s / c : null;
}
function modeOf(arr, a, e) {
    var cnt = {}, best = null, bn = 0;
    for (var k = a; k <= e; k++) {
        var v = arr[k];
        if (v === null || v === undefined) continue;
        cnt[v] = (cnt[v] || 0) + 1;
        if (cnt[v] > bn) { bn = cnt[v]; best = v; }
    }
    return best;
}
function dispValue(c, rawv, sc) {
    if (rawv === null || rawv === undefined) return null;
    var v = rawv * (c.mul || 1);
    if (c.min && v < c.min) return null;
    return clamp(v, sc.lo, sc.hi);
}
function xTicks(L, lo, hi, isDist) {
    if (isDist) return L.niceTicks(lo, hi, 5).map(function (v) { return [v, (Math.round(v * 100) / 100) + " km"]; });
    var hrs = hi > 9000, u = hrs ? 3600 : 60;
    return L.niceTicks(lo / u, hi / u, 5).map(function (v) { return [v * u, (Math.round(v * 100) / 100) + (hrs ? " h" : " min")]; });
}

// ---------------------------------------------------------------- plot (lines, repainted on data/zoom change)
function drawPlot(ctx, W, H, o) {
    var Lg = o.L, s = o.st && o.st.streams, i0 = o.i0, i1 = o.i1, col = o.colors;
    if (!s || !o.channels.length || !o.focus || i1 - i0 < 1) return null;
    var L = PLOT_L, R = PLOT_R, BB = o.ribbon ? 38 : 22;
    var B = buckets(i0, i1, Math.max(60, Math.floor((W - 58) / 3)));
    var xv = B.map(function (r) { var m = meanOf(o.xv, r[0], r[1]); return m === null ? NaN : m; });
    var finite = xv.filter(function (v) { return !isNaN(v); });
    if (finite.length < 2) return null;
    var x0 = finite[0], x1 = finite[finite.length - 1];
    ctx.font = "11px sans-serif"; ctx.textAlign = "left";
    // legend (tap/click a name = main line)
    var lx = L, ly = 12, hits = [];
    o.channels.forEach(function (c) {
        var main = c.id === o.focus.id, name = c.label + (c.pace ? " " + o.labels.fasterUp : "");
        ctx.font = (main ? "bold " : "") + "11px sans-serif";
        var w = 30 + ctx.measureText(name).width;
        if (lx + w > W - R && lx > L) { lx = L; ly += 20; }
        if (main) { ctx.fillStyle = col.cardNested; rrect(ctx, lx - 4, ly - 12, w, 18, 9); ctx.fill(); }
        ctx.strokeStyle = chanColour(o, c); ctx.lineWidth = main ? 4 : 2.5; ctx.lineCap = "round";
        ctx.beginPath(); ctx.moveTo(lx + 2, ly - 3); ctx.lineTo(lx + 14, ly - 3); ctx.stroke();
        ctx.fillStyle = main ? col.text : col.muted;
        ctx.fillText(name, lx + 19, ly + 1);
        hits.push({ x: lx - 4, y: ly - 12, w: w, h: 18, id: c.id });
        lx += w + 6;
    });
    ctx.font = "11px sans-serif";
    var altOn = o.channels.some(function (c) { return c.key === "alt"; });
    var sil = !altOn && s.alt ? Lg.channelScale({ key: "alt", min_span: 60 }, B.map(function (r) { return meanOf(s.alt, r[0], r[1]); })) : null;
    if (sil) {
        if (lx + 80 > W - R) { lx = L; ly += 20; }
        ctx.fillStyle = rgba(col.borderStrong, 0.6); ctx.fillRect(lx, ly - 8, 14, 10);
        ctx.fillStyle = col.muted; ctx.fillText(o.labels.altitude, lx + 19, ly + 1);
        lx += 30 + ctx.measureText(o.labels.altitude).width;
    }
    var showPlan = o.target && !o.isDist && o.channels.some(function (c) { return c.key === "pw"; });
    if (showPlan) {
        if (lx + 140 > W - R) { lx = L; ly += 20; }
        ctx.fillStyle = rgba(col.primary, 0.18); ctx.fillRect(lx, ly - 8, 14, 10);
        ctx.strokeStyle = rgba(col.primary, 0.7); ctx.setLineDash([3, 2]); ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(lx, ly - 8); ctx.lineTo(lx + 14, ly - 8); ctx.stroke(); ctx.setLineDash([]);
        ctx.fillStyle = col.muted; ctx.fillText(o.labels.plannedTarget, lx + 19, ly + 1);
    }
    var TB = ly + 16, PB = H - BB;
    var X = function (v) { return L + (v - x0) / ((x1 - x0) || 1) * (W - L - R); };
    var geo = { L: L, R: R, W: W, TB: TB, PB: PB, x0: x0, x1: x1, i0: i0, i1: i1, scales: {}, legendHits: hits, X: X };
    ctx.strokeStyle = col.border; ctx.lineWidth = 1;
    rrect(ctx, L + 0.5, TB + 0.5, W - L - R - 1, PB - TB - 1, 6); ctx.stroke();
    function pathOf(vals, Y) {
        ctx.beginPath(); var pen = false;
        for (var k = 0; k < vals.length; k++) {
            var v = vals[k];
            if (v === null || isNaN(xv[k])) { pen = false; continue; }
            if (pen) ctx.lineTo(X(xv[k]), Y(v)); else { ctx.moveTo(X(xv[k]), Y(v)); pen = true; }
        }
    }
    function areaOf(vals, Y) {
        ctx.beginPath(); var started = false, lastX = 0;
        for (var k = 0; k < vals.length; k++) {
            var v = vals[k];
            if (v === null || isNaN(xv[k])) continue;
            var px = X(xv[k]), py = Y(v);
            if (!started) { ctx.moveTo(px, PB); ctx.lineTo(px, py); started = true; } else ctx.lineTo(px, py);
            lastX = px;
        }
        if (started) { ctx.lineTo(lastX, PB); ctx.closePath(); }
        return started;
    }
    if (sil) {
        var Ys = function (v) { return TB + (1 - (v - sil.lo) / (sil.hi - sil.lo)) * (PB - TB); };
        if (areaOf(sil.vals, Ys)) { ctx.fillStyle = rgba(col.borderStrong, 0.28); ctx.fill(); }
    }
    var rb = o.ribbon ? B.map(function (r) { return modeOf(o.ribbon.bands, r[0], r[1]); }) : null;
    var ordered = o.channels.filter(function (c) { return c.id !== o.focus.id; }).concat([o.focus]);
    ordered.forEach(function (c) {
        if (!s[c.key]) return;
        var raw = B.map(function (r) { return meanOf(s[c.key], r[0], r[1]); });
        var rawFull = s[c.key].slice(i0, i1 + 1);
        var sc = Lg.channelScale(c, raw);
        if (!sc) return;
        var planHere = showPlan && c.key === "pw";
        if (planHere) {
            var top = Math.max.apply(null, o.target.blocks.map(function (bk) { return Math.max(bk.lo, bk.hi); })) * 1.05;
            if (top > sc.hi) { sc.hi = top; sc.ticks = Lg.niceTicks(sc.lo, sc.hi, 3).map(function (v) { return [v, String(v)]; }); }
        }
        var Y = function (v) { return TB + (1 - (v - sc.lo) / (sc.hi - sc.lo)) * (PB - TB); };
        geo.scales[c.id] = { lo: sc.lo, hi: sc.hi, top: TB, bot: PB };
        if (planHere) {
            o.target.blocks.forEach(function (bk) {
                var xa = X(bk.t0), xb = X(bk.t1);
                if (xb < L || xa > W - R) return;
                var ca = Math.max(L, xa), cb = Math.min(W - R, xb);
                var ya = Y(bk.lo + (bk.hi - bk.lo) * ((ca - xa) / ((xb - xa) || 1)));
                var yb = Y(bk.lo + (bk.hi - bk.lo) * ((cb - xa) / ((xb - xa) || 1)));
                ctx.fillStyle = rgba(col.primary, 0.13);
                ctx.beginPath(); ctx.moveTo(ca, PB); ctx.lineTo(ca, ya); ctx.lineTo(cb, yb); ctx.lineTo(cb, PB); ctx.closePath(); ctx.fill();
                ctx.strokeStyle = rgba(col.primary, 0.6); ctx.lineWidth = 1.2; ctx.setLineDash([4, 3]);
                ctx.beginPath(); ctx.moveTo(ca, ya); ctx.lineTo(cb, yb); ctx.stroke(); ctx.setLineDash([]);
            });
        }
        var main = c.id === o.focus.id, zoneLine = main && rb && o.ribbon.key === c.key, cc = chanColour(o, c);
        if (main && !zoneLine && areaOf(sc.vals, Y)) { ctx.fillStyle = rgba(cc, 0.16); ctx.fill(); }
        if (zoneLine) {
            // runs of consecutive points in one zone: one tinted area and one line per run
            var runs = [], cur = null;
            for (var k = 0; k < sc.vals.length; k++) {
                if (sc.vals[k] === null || isNaN(xv[k])) { cur = null; continue; }
                if (cur && cur.band === rb[k]) { cur.ks.push(k); continue; }
                var prev = cur ? cur.ks[cur.ks.length - 1] : -1;
                cur = { band: rb[k], ks: prev >= 0 ? [prev, k] : [k] };
                runs.push(cur);
            }
            runs.forEach(function (r) {
                if (r.ks.length < 2 || r.band === null || r.band === undefined) return;
                ctx.beginPath(); ctx.moveTo(X(xv[r.ks[0]]), PB);
                r.ks.forEach(function (kk) { ctx.lineTo(X(xv[kk]), Y(sc.vals[kk])); });
                ctx.lineTo(X(xv[r.ks[r.ks.length - 1]]), PB); ctx.closePath();
                ctx.fillStyle = rgba(zoneColour(o, o.ribbon.colourOf(r.band)), 0.28); ctx.fill();
            });
            pathOf(sc.vals, Y); ctx.strokeStyle = o.zone.casing; ctx.lineWidth = 5; ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.stroke();
            runs.forEach(function (r) {
                if (r.ks.length < 2) return;
                ctx.beginPath(); ctx.moveTo(X(xv[r.ks[0]]), Y(sc.vals[r.ks[0]]));
                for (var j = 1; j < r.ks.length; j++) ctx.lineTo(X(xv[r.ks[j]]), Y(sc.vals[r.ks[j]]));
                ctx.strokeStyle = r.band === null || r.band === undefined ? cc : zoneColour(o, o.ribbon.colourOf(r.band));
                ctx.lineWidth = 2.8; ctx.lineJoin = "round"; ctx.lineCap = "butt"; ctx.stroke();
            });
        } else {
            pathOf(sc.vals, Y);
            ctx.strokeStyle = main ? cc : rgba(cc, rb ? 0.45 : 0.7);
            ctx.lineWidth = main ? 2.4 : 1.4; ctx.lineJoin = "round"; ctx.lineCap = "round"; ctx.stroke();
        }
        if (!main) return;
        // left scale with the unit on top
        ctx.fillStyle = col.muted; ctx.textAlign = "right"; ctx.font = "10px monospace";
        sc.ticks.forEach(function (tk) {
            var y = Y(tk[0]);
            ctx.strokeStyle = col.border; ctx.setLineDash([2, 4]); ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(L, y); ctx.lineTo(W - R, y); ctx.stroke(); ctx.setLineDash([]);
            ctx.fillText(tk[1], L - 6, y + 3);
        });
        ctx.fillStyle = cc; ctx.font = "bold 10px monospace"; ctx.fillText(c.unit, L - 6, TB - 4);
        ctx.textAlign = "left"; ctx.font = "11px sans-serif";
        // average: pace/speed as distance over time, others a plain mean
        var avgV = null;
        if (c.key === "v" && s.dist && s.dist[i0] !== null && s.dist[i1] !== null) {
            var dt = s.t[i1] - s.t[i0];
            if (dt > 0) avgV = (s.dist[i1] - s.dist[i0]) * 1000 / dt * (c.mul || 1);
        } else {
            var vv = raw.filter(function (v) { return v !== null; }).map(function (v) { return v * (c.mul || 1); })
                        .filter(function (v) { return !(c.min && v < c.min); });
            if (vv.length) avgV = vv.reduce(function (a, b) { return a + b; }, 0) / vv.length;
        }
        if (avgV !== null && c.key !== "alt") {
            var txt = o.labels.avg + " " + Lg.channelValueText(c, avgV / (c.mul || 1)) + " " + c.unit;
            var ay = Y(clamp(avgV, sc.lo, sc.hi));
            ctx.strokeStyle = rgba(col.text, 0.7); ctx.setLineDash([5, 4]); ctx.lineWidth = 1;
            ctx.beginPath(); ctx.moveTo(L, ay); ctx.lineTo(W - R, ay); ctx.stroke(); ctx.setLineDash([]);
            ctx.font = "10.5px monospace";
            var tw = ctx.measureText(txt).width + 12;
            ctx.fillStyle = col.text; rrect(ctx, L + 4, ay - 19, tw, 16, 4); ctx.fill();
            ctx.fillStyle = col.background; ctx.fillText(txt, L + 10, ay - 7);
            ctx.font = "11px sans-serif";
        }
        // max from the real samples (not the drawn averages), placed on the drawn scale
        var mi = -1, mv = -Infinity;
        for (var q = 0; q < rawFull.length; q++) {
            var v2 = rawFull[q] === null ? null : rawFull[q] * (c.mul || 1);
            if (v2 !== null && !(c.min && v2 < c.min) && v2 > mv) { mv = v2; mi = q; }
        }
        var mX = mi >= 0 ? o.xv[i0 + mi] : NaN;
        if (mi >= 0 && !isNaN(mX)) {
            mv = clamp(mv, sc.lo, sc.hi);
            var rv = rawFull[mi];
            var mtxt = c.key === "alt" ? Math.round(rv) + " m " + o.labels.top
                     : Lg.channelValueText(c, rv) + " " + c.unit + " " + (c.pace ? o.labels.best : o.labels.max);
            ctx.font = "bold 11px monospace";
            var mw = ctx.measureText(mtxt).width + 14;
            var px = Math.min(W - R - mw, Math.max(L, X(mX) - mw / 2)), py = Math.max(TB + 2, Y(mv) - 24);
            ctx.fillStyle = cc; ctx.beginPath(); ctx.arc(X(mX), Y(mv), 4, 0, 2 * Math.PI); ctx.fill();
            rrect(ctx, px, py, mw, 17, 4); ctx.fill();
            ctx.fillStyle = col.background; ctx.fillText(mtxt, px + 7, py + 12);
            ctx.font = "11px sans-serif";
        }
    });
    if (rb) {
        for (var k2 = 0; k2 < rb.length - 1; k2++) {
            if (rb[k2] === null || isNaN(xv[k2]) || isNaN(xv[k2 + 1])) continue;
            ctx.fillStyle = zoneColour(o, o.ribbon.colourOf(rb[k2]));
            ctx.fillRect(X(xv[k2]), PB + 4, Math.max(0.6, X(xv[k2 + 1]) - X(xv[k2]) + 0.4), 11);
        }
    }
    ctx.fillStyle = col.muted; ctx.font = "10px monospace"; ctx.textAlign = "center";
    xTicks(Lg, x0, x1, o.isDist).forEach(function (tk) { ctx.fillText(tk[1], X(tk[0]), H - 6); });
    ctx.textAlign = "left";
    return geo;
}

// ---------------------------------------------------------------- cursor layer (repainted on every move)
function drawCursor(ctx, W, H, o, geo, k, sel) {
    if (!geo || !o.st) return;
    var s = o.st.streams, X = geo.X, xv = o.xv, col = o.colors, Lg = o.L;
    if (sel) {
        var a = Math.min(sel[0], sel[1]), b = Math.max(sel[0], sel[1]);
        ctx.fillStyle = rgba(col.primary, 0.14);
        ctx.fillRect(X(xv[a]), geo.TB, Math.max(1, X(xv[b]) - X(xv[a])), geo.PB - geo.TB);
        return;
    }
    if (k < geo.i0 || k > geo.i1 || isNaN(xv[k])) return;
    var cx = X(xv[k]);
    ctx.strokeStyle = col.text; ctx.lineWidth = 1;
    ctx.beginPath(); ctx.moveTo(cx, geo.TB); ctx.lineTo(cx, geo.PB); ctx.stroke();
    var rows = [];
    o.channels.forEach(function (c) {
        var sc = geo.scales[c.id];
        if (!sc || !s[c.key] || s[c.key][k] === null || s[c.key][k] === undefined) return;
        var v = dispValue(c, s[c.key][k], sc);
        if (v === null) return;
        var y = sc.top + (1 - (v - sc.lo) / (sc.hi - sc.lo)) * (sc.bot - sc.top);
        ctx.fillStyle = chanColour(o, c); ctx.strokeStyle = col.card; ctx.lineWidth = 2;
        ctx.beginPath(); ctx.arc(cx, y, 4.5, 0, 2 * Math.PI); ctx.fill(); ctx.stroke();
        rows.push([chanColour(o, c), c.label, Lg.channelValueText(c, s[c.key][k]) + (c.unit ? " " + c.unit : "")]);
    });
    if (s.alt && !o.channels.some(function (c) { return c.key === "alt"; }) && s.alt[k] !== null)
        rows.push([col.borderStrong, o.labels.altitude, Math.round(s.alt[k]) + " m"]);
    if (o.target && !o.isDist) {
        var ts = s.t[k] - s.t[0];
        var bk = o.target.blocks.filter(function (b2) { return ts >= b2.t0 && ts < b2.t1; })[0];
        if (bk) rows.push([col.primary, o.labels.target, Math.round(bk.lo + (bk.hi - bk.lo) * ((ts - bk.t0) / ((bk.t1 - bk.t0) || 1))) + " W"]);
    }
    if (o.ribbon && o.ribbon.bands[k] !== null && o.ribbon.bands[k] !== undefined)
        rows.push([zoneColour(o, o.ribbon.colourOf(o.ribbon.bands[k])), o.labels.colour, o.ribbon.label(o.ribbon.bands[k])]);
    var head = o.isDist && s.dist && s.dist[k] !== null ? s.dist[k].toFixed(2) + " km · " + Lg.fmtClock(s.t[k] - s.t[0]) : Lg.fmtClock(s.t[k] - s.t[0]);
    ctx.font = "12px sans-serif";
    var cw = 176;
    // Measure each part in the font it is drawn in (label 11px sans, value bold 12px monospace):
    // measuring both in 12px sans let a long value ("Fast fifth of this activity") run over its label.
    rows.forEach(function (r) {
        ctx.font = "11px sans-serif"; var lw = ctx.measureText(r[1]).width;
        ctx.font = "bold 12px monospace"; var vw = ctx.measureText(r[2]).width;
        cw = Math.max(cw, 50 + lw + vw);
    });
    ctx.font = "12px sans-serif";
    var chh = 26 + rows.length * 19, left = cx + 14 + cw > geo.W - geo.R;
    var bx = left ? cx - 14 - cw : cx + 14, by = geo.TB + 6;
    if (bx < 2) bx = 2;
    ctx.fillStyle = col.card; ctx.strokeStyle = col.borderStrong; ctx.lineWidth = 1;
    rrect(ctx, bx, by, cw, chh, 10); ctx.fill(); ctx.stroke();
    ctx.fillStyle = col.muted; ctx.font = "11px monospace"; ctx.fillText(head, bx + 12, by + 18);
    rows.forEach(function (r, j) {
        var ry = by + 34 + j * 19;
        ctx.fillStyle = r[0]; ctx.beginPath(); ctx.arc(bx + 16, ry, 4, 0, 2 * Math.PI); ctx.fill();
        ctx.fillStyle = col.muted; ctx.font = "11px sans-serif"; ctx.fillText(r[1], bx + 26, ry + 4);
        ctx.fillStyle = col.text; ctx.font = "bold 12px monospace"; ctx.textAlign = "right";
        ctx.fillText(r[2], bx + cw - 12, ry + 4); ctx.textAlign = "left";
    });
}

// ---------------------------------------------------------------- overview strip
function drawStrip(ctx, W, H, o) {
    var s = o.st && o.st.streams, c = o.focus;
    if (!s || !c || !s[c.key]) return;
    // T leaves room for the caption above the strip (it replaced a cryptic "all" in the margin).
    var L = PLOT_L, R = PLOT_R, T = 18, B = 4, col = o.colors;
    var n = o.xv.length, Bk = buckets(0, n - 1, Math.max(60, Math.floor((W - 58) / 2)));
    var xv = Bk.map(function (r) { var m = meanOf(o.xv, r[0], r[1]); return m === null ? NaN : m; });
    var sc = o.L.channelScale(c, Bk.map(function (r) { return meanOf(s[c.key], r[0], r[1]); }));
    if (!sc) return;
    var finite = o.xv.filter(function (v) { return !isNaN(v); });
    if (finite.length < 2) return;
    var x0 = finite[0], x1 = finite[finite.length - 1];
    var X = function (v) { return L + (v - x0) / ((x1 - x0) || 1) * (W - L - R); };
    var Y = function (v) { return T + (1 - (v - sc.lo) / (sc.hi - sc.lo)) * (H - T - B); };
    ctx.fillStyle = col.cardNested; rrect(ctx, L, T, W - L - R, H - T - B, 6); ctx.fill();
    ctx.beginPath(); var started = false, lastX = L;
    for (var k = 0; k < sc.vals.length; k++) {
        if (sc.vals[k] === null || isNaN(xv[k])) continue;
        var px = X(xv[k]), py = Y(sc.vals[k]);
        if (!started) { ctx.moveTo(px, H - B); ctx.lineTo(px, py); started = true; } else ctx.lineTo(px, py);
        lastX = px;
    }
    if (started) { ctx.lineTo(lastX, H - B); ctx.closePath(); ctx.fillStyle = rgba(chanColour(o, c), 0.35); ctx.fill(); }
    var zoomed = o.i0 > 0 || o.i1 < n - 1;
    var a = zoomed ? X(o.xv[o.i0]) : L, b = zoomed ? X(o.xv[o.i1]) : W - R;
    ctx.fillStyle = rgba(col.card, 0.65);
    ctx.fillRect(L, T, Math.max(0, a - L), H - T - B); ctx.fillRect(b, T, Math.max(0, W - R - b), H - T - B);
    ctx.strokeStyle = col.primary; ctx.lineWidth = 2;
    rrect(ctx, a, T, Math.max(2, b - a), H - T - B, 5); ctx.stroke();
    ctx.fillStyle = col.muted; ctx.font = "11px sans-serif"; ctx.textAlign = "left";
    ctx.fillText(o.labels.stripHint, L, 12);
}

// ---------------------------------------------------------------- geometry helpers for the pointer code
function idxAt(geo, xv, px) {
    if (!geo) return -1;
    var v = geo.x0 + (px - geo.L) / (geo.W - geo.L - geo.R) * (geo.x1 - geo.x0), best = geo.i0, bd = Infinity;
    for (var k = geo.i0; k <= geo.i1; k++) { var d = Math.abs(xv[k] - v); if (d < bd) { bd = d; best = k; } }
    return best;
}
function stripIdxAt(W, xv, px) {
    var finite = xv.filter(function (v) { return !isNaN(v); });
    if (finite.length < 2) return 0;
    var x0 = finite[0], x1 = finite[finite.length - 1], v = x0 + (px - PLOT_L) / (W - PLOT_L - PLOT_R) * (x1 - x0), best = 0, bd = Infinity;
    for (var k = 0; k < xv.length; k++) { var d = Math.abs(xv[k] - v); if (d < bd) { bd = d; best = k; } }
    return best;
}
// Normalise a requested zoom window (fractional indexes allowed) -> [a, b] or null (= everything).
function zoomRange(n, a, b) {
    var last = n - 1, minW = 8;
    if (b - a < minW) { var c = (a + b) / 2; a = c - minW / 2; b = c + minW / 2; }
    if (a < 0) { b -= a; a = 0; }
    if (b > last) { a -= b - last; b = last; }
    a = Math.max(0, a);
    if (b - a >= last - 0.5) return null;
    return [Math.round(a), Math.round(b)];
}

// ---------------------------------------------------------------- pool swim: one bar per length
// o = {L, st, yMode: "len"|"pace", colourMode: "stroke"|"speed", showHr, hover, colors, zone, labels}
// Pool-swim stroke colours: one per stroke, so a backstroke length (Ambit swims record it) no
// longer passes for breaststroke. Keys name a Theme colour in o.colors, or "zone:<k>" in o.zone.
var SWIM_STROKES = [["breaststroke", "primary", "Breaststroke"], ["freestyle", "zone:down", "Freestyle"],
                    ["backstroke", "secondary", "Backstroke"], ["butterfly", "zone:z7", "Butterfly"],
                    ["drill", "warning", "Drill"]];
function legendColour(o, key) {
    return key.indexOf("zone:") === 0 ? o.zone[key.slice(5)] : (o.colors[key] || o.colors.muted);
}
function strokeColour(o, stroke) {
    for (var i = 0; i < SWIM_STROKES.length; i++) if (SWIM_STROKES[i][0] === stroke) return legendColour(o, SWIM_STROKES[i][1]);
    return o.colors.muted;
}
// The legend under the bars: the strokes this swim actually has (or the speed scale), as
// [{key, label}] - both apps draw it with legendColour().
function swimLegend(st, colourMode) {
    if (colourMode === "speed")
        return [{ key: "zone:z1", label: "Slowest lengths" }, { key: "zone:z3", label: "Middle" }, { key: "zone:z5", label: "Fastest lengths" }];
    var act = ((st && st.lengths) || []).filter(function (l) { return l.active && l.swim_s; });
    var out = SWIM_STROKES.filter(function (sk) { return act.some(function (l) { return l.stroke === sk[0]; }); })
                          .map(function (sk) { return { key: sk[1], label: sk[2] }; });
    if (act.some(function (l) { return !l.stroke || !SWIM_STROKES.some(function (sk) { return sk[0] === l.stroke; }); }))
        out.push({ key: "muted", label: "Stroke not recorded" });
    return out;
}

function drawSwim(ctx, W, H, o) {
    var Lg = o.L, col = o.colors;
    var Ls = (o.st && o.st.lengths ? o.st.lengths : []).filter(function (l) { return l.active && l.swim_s; });
    var n = Ls.length;
    if (!n) return null;
    var pool = o.st.summary && o.st.summary.pool_length_m ? o.st.summary.pool_length_m : 25;
    var L = PLOT_L, R = 10, T = 18, B = 22, pace = o.yMode === "pace", k100 = 100 / pool;
    var val = function (l) { return pace ? l.swim_s * k100 : l.swim_s; };
    var maxV = pace ? 300 : 90;
    var Y = function (v) { return T + (1 - Math.min(v, maxV) / maxV) * (H - T - B); };
    var bw = (W - L - R) / n;
    var ticks = pace ? [[0, "0:00"], [60, "1:00"], [120, "2:00"], [180, "3:00"], [240, "4:00"], [300, "5:00"]]
                     : [[0, "0"], [30, "30"], [60, "60"], [90, "90"]];
    ctx.font = "10px monospace"; ctx.textAlign = "right";
    ticks.forEach(function (tk) {
        ctx.strokeStyle = col.border; ctx.lineWidth = 1;
        ctx.beginPath(); ctx.moveTo(L, Y(tk[0])); ctx.lineTo(W - R, Y(tk[0])); ctx.stroke();
        ctx.fillStyle = col.muted; ctx.fillText(tk[1], L - 6, Y(tk[0]) + 3);
    });
    ctx.fillStyle = col.text; ctx.font = "bold 10px monospace"; ctx.fillText(pace ? "/100 m" : "s", L - 6, T - 6);
    ctx.textAlign = "left";
    var sorted = Ls.map(function (l) { return l.swim_s; }).sort(function (a, b) { return a - b; });
    var q = [0.2, 0.4, 0.6, 0.8].map(function (p) { return sorted[Math.floor(p * (n - 1))]; });
    Ls.forEach(function (l, i) {
        var x0 = L + i * bw + 1, w = Math.max(2, bw - 2);
        var c = o.colourMode === "speed" ? o.zone["z" + (5 - q.filter(function (t) { return l.swim_s > t; }).length)]
                                         : strokeColour(o, l.stroke);
        ctx.fillStyle = i === o.hover ? rgba(c, 0.75) : c;
        rrect(ctx, x0, Y(val(l)), w, Y(0) - Y(val(l)), 2); ctx.fill();
        var rest = pace ? 0 : Math.min(l.rest_s || 0, maxV - l.swim_s);
        if (rest >= 1) { ctx.fillStyle = col.borderStrong; rrect(ctx, x0, Y(l.swim_s + rest), w, Math.max(1, Y(l.swim_s) - Y(l.swim_s + rest) - 1), 2); ctx.fill(); }
    });
    var med = Lg.median(Ls.map(val));
    ctx.strokeStyle = col.text; ctx.setLineDash([4, 4]); ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.moveTo(L, Y(med)); ctx.lineTo(W - R, Y(med)); ctx.stroke(); ctx.setLineDash([]);
    var mtxt = o.labels.median + " " + (pace ? Lg.fmtClock(med) + " /100 m" : med.toFixed(1) + " s");
    ctx.font = "10.5px monospace";
    var tw = ctx.measureText(mtxt).width + 12;
    ctx.fillStyle = col.text; rrect(ctx, W - R - tw, Y(med) - 19, tw, 16, 4); ctx.fill();
    ctx.fillStyle = col.background; ctx.fillText(mtxt, W - R - tw + 6, Y(med) - 7);
    if (o.showHr) {
        var hrs = Ls.map(function (l) { return l.avg_hr; }).filter(function (v) { return v; });
        if (hrs.length > 1) {
            var lo = Math.min.apply(null, hrs) - 5, hi = Math.max.apply(null, hrs) + 5;
            var YH = function (v) { return T + (1 - (v - lo) / (hi - lo)) * (H - T - B); };
            ctx.beginPath(); var pen = false;
            Ls.forEach(function (l, i) {
                if (!l.avg_hr) { pen = false; return; }
                var px = L + (i + 0.5) * bw, py = YH(l.avg_hr);
                if (pen) ctx.lineTo(px, py); else { ctx.moveTo(px, py); pen = true; }
            });
            ctx.strokeStyle = o.zone.casing; ctx.lineWidth = 4.5; ctx.stroke();
            ctx.strokeStyle = col.hard; ctx.lineWidth = 2.2; ctx.stroke();
        }
    }
    ctx.fillStyle = col.muted; ctx.font = "10px monospace"; ctx.textAlign = "center";
    [1, 10, 20, 30, 40, 50, 60, 70, 80, 90, 100].filter(function (k) { return k <= n; }).concat(n > 1 ? [n] : [])
        .forEach(function (k) { ctx.fillText(String(k), L + (k - 0.5) * bw, H - 6); });
    ctx.textAlign = "left";
    return { L: L, bw: bw, n: n, pool: pool, lengths: Ls };
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = { drawPlot: drawPlot, drawCursor: drawCursor, drawStrip: drawStrip, drawSwim: drawSwim,
                       idxAt: idxAt, stripIdxAt: stripIdxAt, zoomRange: zoomRange, rgba: rgba, PLOT_L: PLOT_L, PLOT_R: PLOT_R,
                       swimLegend: swimLegend, legendColour: legendColour };
}
