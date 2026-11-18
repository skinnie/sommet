// GENERATED from shared/sport_picture.js by tools/gen_activity_view.py - edit that file.
// The picture a move without a GPS track shows where its map would be (André, 2026-11-18: "an
// image that kinda tells what type of sport, but that doesn't look bad near the maps"). ONE file
// for desktop (QML imports it as a JS library) and Android (Metro requires it); tools/
// gen_activity_view.py copies it into both apps, edit it here only.
//
// Faint contour lines in a map tile's own colours, so the card reads like a quiet map among the
// real ones, with the sport's Ambit badge (assets/activity_types.json) in the middle. The lines
// come from a seed (the move's start), so neighbouring cards don't all look the same. The sport
// name is drawn by each app under the badge, in its own font.
//
// Map tiles are light in both themes, so these colours are fixed, not theme colours (the same
// reason the track colour is fixed, see android/src/services/MapTile.ts).

var LAND = "#EFECE4";      // CyclOSM / OSM land
var LINE = "#DDD6C8";      // contour lines
var LINE_MAJOR = "#D2C9B6"; // every third line, a touch darker, like a map's index contours

// A small deterministic random (mulberry32), so the same move always draws the same picture.
function _rand(seed) {
    var a = seed >>> 0;
    return function () {
        a = (a + 0x6D2B79F5) >>> 0;
        var t = a;
        t = Math.imul(t ^ (t >>> 15), t | 1);
        t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
        return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
    };
}

function seedOf(text) {
    var h = 2166136261;
    var s = String(text || "");
    for (var i = 0; i < s.length; i++) { h ^= s.charCodeAt(i); h = Math.imul(h, 16777619); }
    return h >>> 0;
}

function _f(v) { return Math.round(v * 10) / 10; }

// A closed, smooth ring through n points (Catmull-Rom turned into cubic Béziers).
function _ring(cx, cy, rx, ry, wobble, n) {
    var pts = [];
    for (var i = 0; i < n; i++) {
        var a = (i / n) * Math.PI * 2;
        var k = 1 + wobble[i % wobble.length];
        pts.push([cx + Math.cos(a) * rx * k, cy + Math.sin(a) * ry * k]);
    }
    var d = "M" + _f(pts[0][0]) + " " + _f(pts[0][1]);
    for (var j = 0; j < n; j++) {
        var p0 = pts[(j - 1 + n) % n], p1 = pts[j], p2 = pts[(j + 1) % n], p3 = pts[(j + 2) % n];
        d += "C" + _f(p1[0] + (p2[0] - p0[0]) / 6) + " " + _f(p1[1] + (p2[1] - p0[1]) / 6) + " "
             + _f(p2[0] - (p3[0] - p1[0]) / 6) + " " + _f(p2[1] - (p3[1] - p1[1]) / 6) + " "
             + _f(p2[0]) + " " + _f(p2[1]);
    }
    return d + "Z";
}

// The contour lines alone, for a w x h box.
function contours(w, h, seed) {
    var r = _rand(seed);
    var out = "";
    var hills = 3;
    for (var k = 0; k < hills; k++) {
        // Hills sit towards the sides and corners, leaving the middle calm for the badge.
        var side = k % 2 === 0 ? 0.12 + r() * 0.2 : 0.68 + r() * 0.2;
        var cx = w * side;
        var cy = h * (0.1 + r() * 0.8);
        var size = Math.max(w, h) * (0.28 + r() * 0.22);
        var wob = [];
        for (var q = 0; q < 7; q++) wob.push((r() - 0.5) * 0.36);
        var rings = 5 + Math.floor(r() * 3);
        for (var i = rings; i >= 1; i--) {
            var f = i / rings;
            var colour = i % 3 === 0 ? LINE_MAJOR : LINE;
            out += '<path d="' + _ring(cx, cy, size * f, size * f * (0.55 + r() * 0.1), wob, 7)
                 + '" fill="none" stroke="' + colour + '" stroke-width="' + (i % 3 === 0 ? 1.4 : 1)
                 + '"/>';
        }
    }
    return out;
}

// The badge, as ActivityBadge.qml draws it: a "glyph" entry is a white symbol on a disc of the
// sport's colour; a "badge" entry is its own disc, painted in the sport's colour on white.
function badge(entry, cx, cy, size) {
    if (!entry) return "";
    var isBadge = entry.style === "badge";
    var colour = entry.color || "#9d9d9d";
    var body = String(entry.svg || "").split("#fff").join(isBadge ? colour : "#ffffff");
    var inner = isBadge ? size : size * 0.611;
    var s = inner / 24;
    return '<circle cx="' + _f(cx) + '" cy="' + _f(cy) + '" r="' + _f(size / 2 + 5)
         + '" fill="' + LAND + '" opacity="0.9"/>'
         + '<circle cx="' + _f(cx) + '" cy="' + _f(cy) + '" r="' + _f(size / 2) + '" fill="'
         + (isBadge ? "#ffffff" : colour) + '"/>'
         + '<g transform="translate(' + _f(cx - inner / 2) + ' ' + _f(cy - inner / 2) + ') scale('
         + s.toFixed(4) + ')">' + body + '</g>';
}

// The whole picture as an SVG document. The badge sits a little above the middle, leaving room
// for the sport name each app draws under it.
function sportPictureSvg(entry, w, h, seed) {
    var size = Math.max(28, Math.min(56, h * 0.32));
    return '<svg xmlns="http://www.w3.org/2000/svg" width="' + w + '" height="' + h
         + '" viewBox="0 0 ' + w + ' ' + h + '"><rect width="' + w + '" height="' + h + '" fill="'
         + LAND + '"/>' + contours(w, h, seed) + badge(entry, w / 2, h * 0.44, size) + '</svg>';
}

// Where the name goes (its top, from the top of the box), and its colour.
function nameTop(h) { return h * 0.44 + Math.max(28, Math.min(56, h * 0.32)) / 2 + 8; }
var NAME_COLOUR = "#6B6353";

if (typeof module !== "undefined" && module.exports) {
    module.exports = { sportPictureSvg: sportPictureSvg, seedOf: seedOf, nameTop: nameTop,
                       NAME_COLOUR: NAME_COLOUR, LAND: LAND };
}
