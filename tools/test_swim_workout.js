#!/usr/bin/env node
// shared/swim_workout.js: what a swim step can ask for, the stroke words, pool lengths.
//   node tools/test_swim_workout.js
const assert = require("assert");
const path = require("path");
const SW = require(path.join(__dirname, "..", "shared", "swim_workout.js"));

const row = (o) => Object.assign({ stepType: "interval", durationKind: "distance_m", durationValue: 50,
                                   targetKind: "none", targetMin: 0, targetMax: 0, stepText: "" }, o);

// the watch's swim sports
assert.strictEqual(SW.swimKind(6), "pool");
assert.strictEqual(SW.swimKind(83), "open");
assert.strictEqual(SW.swimKind(3), "");
assert.deepStrictEqual(SW.swimCaps("pool").targets, ["none"]);
assert.ok(SW.swimCaps("pool").durations.includes("lengths"));
assert.ok(!SW.swimCaps("open").durations.includes("lengths"));
assert.strictEqual(SW.swimCaps(""), null);

// the words on the watch
assert.strictEqual(SW.stepText("free", "pt", row({}), 25), "Crawl 50m");
assert.strictEqual(SW.stepText("breast", "pt", row({ durationKind: "lengths", durationValue: 4 }), 25), "Brucos 100m");
assert.strictEqual(SW.stepText("rest", "pt", row({ durationKind: "time_s", durationValue: 45 }), 25), "Descanso 45s");
assert.strictEqual(SW.stepText("fly", "en", row({ durationKind: "lap" }), 25), "Butterfly");
assert.deepStrictEqual(SW.wordOf("Mariposa 50m"), { id: "fly", lang: "pt" });
assert.strictEqual(SW.wordOf("Sprint"), null);
SW.WORDS.forEach(w => SW.LANGUAGES.forEach(l => assert.ok(/^[\x20-\x7e]+$/.test(w[l.id]) && w[l.id].length <= 12, w.id + " " + l.id)));

// pool lengths
assert.strictEqual(SW.lengthsFor(100, 25), 4);
assert.strictEqual(SW.lengthsFor(60, 25), null);
assert.strictEqual(SW.metresOf(row({ durationKind: "lengths", durationValue: 2 }), 50), 100);

// a text the picker wrote follows its step; a typed one stays
const auto = row({ stepText: "Crawl 50m" });
assert.strictEqual(SW.retext(auto, row({ stepText: "Crawl 50m", durationValue: 100 }), 25, 25), "Crawl 100m");
const typed = row({ stepText: "Crawl forte" });
assert.strictEqual(SW.retext(typed, row({ stepText: "Crawl forte", durationValue: 100 }), 25, 25), "Crawl forte");
const laps = row({ durationKind: "lengths", durationValue: 2, stepText: "Costas 50m" });
assert.strictEqual(SW.retext(laps, laps, 25, 50), "Costas 100m");

// moving a workout into the pool, and out of it
const run = row({ durationKind: "hr_above", durationValue: 150, targetKind: "hr", targetMin: 120, targetMax: 140, lightLimits: true });
const inPool = SW.fitRow(run, SW.swimCaps("pool"), 25);
assert.deepStrictEqual([inPool.durationKind, inPool.targetKind, inPool.lightLimits], ["lap", "none", false]);
const km = SW.fitRow(row({ durationKind: "distance_km", durationValue: 1 }), SW.swimCaps("pool"), 25);
assert.deepStrictEqual([km.durationKind, km.durationValue], ["distance_m", 1000]);
const out = SW.fitRow(laps, { durations: ["time_min", "distance_m", "lap"], targets: ["none", "hr"] }, 25);
assert.deepStrictEqual([out.durationKind, out.durationValue, out.stepText], ["distance_m", 50, "Costas 50m"]);
const rep = { stepType: "repeatStart", repeatCount: 4 };
assert.strictEqual(SW.fitRow(rep, SW.swimCaps("pool"), 25), rep);

console.log("swim_workout: all checks passed");
