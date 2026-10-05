// GENERATED from shared/swim_workout.js by tools/gen_activity_view.py - edit that file.
// Swimming in the workout builders - ONE file for desktop (QML imports it as a JS library) and
// Android (Metro requires it as a CommonJS module). tools/gen_activity_view.py copies it into both
// apps; edit it here only.
//
// What a guided workout can ask for in the water, the words to put on the watch for each stroke,
// and pool lengths as a way to say a distance. Rows are the editors' own step rows:
// {stepType, durationKind, durationValue, targetKind, targetMin, targetMax, stepText, ...}.

// The watch's swim sports, by the sport mode's ActivityID (assets/activity_types.json).
var SWIM_ACTIVITY = { 6: "pool", 83: "open" };
function swimKind(activityId) { return SWIM_ACTIVITY[activityId] || ""; }

// No targets in the water: the belt's radio does not reach the watch there, so there is no live
// heart rate to hold a step to (André's pool swim, 2026-10-04: the whole swim's heart rate came
// from the belt's memory afterwards), and no power or cadence. Steps end on a distance, a time
// or the Lap button - a pool also on a number of lengths.
var SWIM_CAPS = {
    pool: { durations: ["distance_m", "lengths", "time_s", "time_min", "lap"], targets: ["none"] },
    open: { durations: ["distance_m", "distance_km", "time_min", "time_s", "lap"], targets: ["none"] }
};
function swimCaps(kind) { return SWIM_CAPS[kind] || null; }

// Step words for the watch, per language. Plain ASCII: whether the watch draws accented letters
// in step text is not known, so "Brucos", not "Bruços".
var LANGUAGES = [{ id: "en", label: "English" }, { id: "pt", label: "Português" }, { id: "fr", label: "Français" }];
var WORDS = [
    { id: "free",   label: "Freestyle",    en: "Freestyle",    pt: "Crawl",    fr: "Crawl" },
    { id: "breast", label: "Breaststroke", en: "Breaststroke", pt: "Brucos",   fr: "Brasse" },
    { id: "back",   label: "Backstroke",   en: "Backstroke",   pt: "Costas",   fr: "Dos" },
    { id: "fly",    label: "Butterfly",    en: "Butterfly",    pt: "Mariposa", fr: "Papillon" },
    { id: "kick",   label: "Kick",         en: "Kick",         pt: "Pernas",   fr: "Jambes" },
    { id: "drill",  label: "Drill",        en: "Drill",        pt: "Tecnica",  fr: "Educatif" },
    { id: "rest",   label: "Rest",         en: "Rest",         pt: "Descanso", fr: "Repos" }
];
function wordById(id) { for (var i = 0; i < WORDS.length; i++) if (WORDS[i].id === id) return WORDS[i]; return null; }

function num(v) { var n = Number(v); return isFinite(n) ? n : 0; }
function trim(v) { return String(Math.round(v * 100) / 100); }

// The metres a row's step covers, or null when it does not end on a distance.
function metresOf(row, pool) {
    var v = num(row.durationValue);
    if (row.durationKind === "distance_m") return v;
    if (row.durationKind === "distance_km") return v * 1000;
    if (row.durationKind === "lengths") return v * num(pool);
    return null;
}

// A distance as whole pool lengths, or null when it is not one.
function lengthsFor(metres, pool) {
    pool = num(pool);
    if (!(pool > 0) || !(metres > 0)) return null;
    var n = metres / pool;
    return Math.abs(n - Math.round(n)) < 1e-6 ? Math.round(n) : null;
}

// What the watch shows for a step: the word and what ends it - "Crawl 50m", "Descanso 45s",
// just "Crawl" for a step that ends on the Lap button.
function stepText(wordId, lang, row, pool) {
    var w = wordById(wordId);
    if (!w) return "";
    var word = w[lang] || w.en, m = metresOf(row, pool), v = num(row.durationValue);
    if (m !== null && m > 0) return word + " " + trim(m) + "m";
    if (row.durationKind === "time_s" && v > 0) return word + " " + trim(v) + "s";
    if (row.durationKind === "time_min" && v > 0) return word + " " + trim(v) + "min";
    return word;
}

// Which word a step text starts with, in any language: {id, lang} or null.
function wordOf(text, preferLang) {
    var first = String(text || "").trim().split(/\s+/)[0].toLowerCase();
    if (!first) return null;
    var langs = [preferLang || "en"].concat(LANGUAGES.map(function (l) { return l.id; }));
    for (var k = 0; k < langs.length; k++)
        for (var i = 0; i < WORDS.length; i++)
            if (String(WORDS[i][langs[k]] || "").toLowerCase() === first) return { id: WORDS[i].id, lang: langs[k] };
    return null;
}

// The step text to keep after a row (or the pool length) changed: a text this file wrote follows
// the change ("Crawl 50m" -> "Crawl 100m"); one the user typed is left alone.
function retext(oldRow, newRow, oldPool, newPool) {
    var text = oldRow.stepText || "", w = wordOf(text);
    if (!w || text !== stepText(w.id, w.lang, oldRow, oldPool)) return newRow.stepText !== undefined ? newRow.stepText : text;
    return stepText(w.id, w.lang, newRow, newPool === undefined ? oldPool : newPool);
}

// A row made to fit what a sport mode takes (`caps`), when the workout moves to another mode: a
// target the mode has no use for is dropped, and a step ending the mode cannot do becomes the
// nearest one it can - kilometres and lengths become metres, anything else the Lap button.
function fitRow(row, caps, pool) {
    if (row.stepType === "repeatStart" || row.stepType === "repeatEnd") return row;
    var out = {}, k;
    for (k in row) out[k] = row[k];
    if (caps.targets.indexOf(out.targetKind) < 0) { out.targetKind = "none"; out.targetMin = 0; out.targetMax = 0; out.lightLimits = false; }
    if (caps.durations.indexOf(out.durationKind) < 0) {
        var m = metresOf(out, pool);
        if (m !== null && caps.durations.indexOf("distance_m") >= 0) { out.durationKind = "distance_m"; out.durationValue = m; }
        else { out.durationKind = "lap"; out.durationValue = 0; }
    }
    out.stepText = retext(row, out, pool, pool);
    return out;
}

if (typeof module !== "undefined" && module.exports) {
    module.exports = {
        swimKind: swimKind, swimCaps: swimCaps, LANGUAGES: LANGUAGES, WORDS: WORDS, metresOf: metresOf,
        lengthsFor: lengthsFor, stepText: stepText, wordOf: wordOf, retext: retext, fitRow: fitRow
    };
}
