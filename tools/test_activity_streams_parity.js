#!/usr/bin/env node
// Parity check: tools/activity_streams.py (desktop backend) vs shared/activity_streams.js (Android)
// must decode the same FIT to the same JSON. Usage: node tools/test_activity_streams_parity.js a.fit b.fit ...
// Floats may differ in the last rounding digit (Python rounds half to even, JavaScript half up),
// so numbers are compared with a small tolerance; everything else must match exactly.
const { execFileSync } = require("child_process");
const fs = require("fs");
const path = require("path");
const { streamsFromFit } = require(path.join(__dirname, "..", "shared", "activity_streams.js"));
const PY = path.join(__dirname, "activity_streams.py");

function cmp(a, b, where, errs) {
    if (errs.length > 20) return;
    if (typeof a === "number" && typeof b === "number") {
        if (Math.abs(a - b) > 1e-3 * Math.max(1, Math.abs(a))) errs.push(`${where}: py ${a} js ${b}`);
        return;
    }
    if (a === null || b === null || typeof a !== "object" || typeof b !== "object") {
        if (a !== b) errs.push(`${where}: py ${JSON.stringify(a)} js ${JSON.stringify(b)}`);
        return;
    }
    if (Array.isArray(a) !== Array.isArray(b)) { errs.push(`${where}: array vs object`); return; }
    if (Array.isArray(a)) {
        if (a.length !== b.length) { errs.push(`${where}: length py ${a.length} js ${b.length}`); return; }
        for (let i = 0; i < a.length; i++) cmp(a[i], b[i], `${where}[${i}]`, errs);
        return;
    }
    const keys = new Set([...Object.keys(a), ...Object.keys(b)]);
    for (const k of keys) cmp(a[k] === undefined ? null : a[k], b[k] === undefined ? null : b[k], `${where}.${k}`, errs);
}

let failed = 0;
for (const f of process.argv.slice(2)) {
    const py = JSON.parse(execFileSync("python3", [PY, f, "--points", "2000"], { maxBuffer: 1 << 28 }));
    const js = JSON.parse(JSON.stringify(streamsFromFit(new Uint8Array(fs.readFileSync(f)), 2000)));
    const errs = [];
    cmp(py, js, "", errs);
    console.log((errs.length ? "FAIL " : "PASS ") + path.basename(f) + (errs.length ? "\n  " + errs.slice(0, 8).join("\n  ") : ""));
    if (errs.length) failed++;
}
process.exit(failed ? 1 : 0);
