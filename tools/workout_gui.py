#!/usr/bin/env python3
"""A small local web GUI for building native Suunto guided workouts (step builder -> the
community/Komposti compiler's JSON->guidance path -> the watch's WORKOUT menu). The compile +
install logic is `guided_workout.py`, imported here unchanged; this file only adds a
browser-facing front end on top, stdlib-only (no framework, no build step, matching this
project's existing style of small standalone tools).

"Create Workout" compiles the workout JSON into the genuine native guidance binary (target band
+ step text); "Install to Watch" adds it to a chosen sport mode's WORKOUT menu straight over USB
(`guided_workout.py`, Apps entry byte0=1 + guidance display, no rule). Works identically on
Linux, Mac and Windows - none of it touches SuuntoLink (a guided workout lives in the watch's
own WORKOUT menu, not in SuuntoLink's app catalog).

    ./tools/workout_gui.py               # serves http://127.0.0.1:8765, opens your browser
    ./tools/workout_gui.py --port 9000 --no-browser
"""

import argparse
import html as _html_mod
from urllib.parse import quote as _url_quote
import json
import re
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import guided_workout as GW

TOOLS_DIR = Path(__file__).resolve().parent


def run_tool(script, args, timeout=180):
    """Runs one of tools/*.py exactly as a person at a terminal would - same shape as
    desktop/backend/server.py's own run_tool(), reimplemented here rather than imported so
    this stays the standalone, dependency-free tool it already is. Returns (returncode,
    stdout, stderr); never raises for a nonzero exit, the caller decides what that means."""
    proc = subprocess.run([sys.executable, str(TOOLS_DIR / script), *args],
                          cwd=TOOLS_DIR, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def parse_last_json_line(out):
    """Same reasoning as server.py's own _parse_last_json_line(): tools print human-readable
    progress *and* one machine-readable JSON summary line, not always in the same position -
    try every line, keep the last one that parses."""
    parsed = None
    for line in out.strip().splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
    return parsed

# Real, 2026-08-08 ("app is installed in some strange directory, please install it in
# Downloads directory"): was Path.home() / "AmbitWorkouts" (e.g. C:\Users\<user>\AmbitWorkouts
# on Windows) - moved under Downloads so saved workouts land somewhere the user actually
# expects to look, keeping the same named subfolder for organization.
SAVE_DIR = Path.home() / "Downloads" / "AmbitWorkouts"


def save_compiled(name, compiled):
    """Every successful compile also lands here as a JSON, independent of the History list, so a
    workout can be re-downloaded later."""
    SAVE_DIR.mkdir(exist_ok=True)
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", name).strip("_") or "workout"
    path = SAVE_DIR / f"{safe_name}_{int(time.time())}.json"
    path.write_text(json.dumps(compiled, indent=2))
    return path


# Default workout name shown in the builder. Overridable per-launch by the ?name= query (the
# desktop Calendar's pre-filled scheduled workout) or the --name CLI flag.
INITIAL_WORKOUT_NAME = "My workout"
INITIAL_WORKOUT_JSON = None  # base64 JSON, set by --workout-b64


def _html_attr(value):
    """Escape a string for safe use inside an HTML double-quoted attribute."""
    return _html_mod.escape(str(value), quote=True)


HTML_PAGE = r"""<!doctype html>
<html data-theme="system">
<head>
<meta charset="utf-8">
<title>Ambit3 Workout Builder</title>
<style>
  /* Explicit light/dark palettes (2026-08-08 request: "have a light mode... switch from
     light mode, to dark to system") rather than the old bare `color-scheme: light dark`,
     which only ever nudged native form-control colors and left everything else to
     whatever the browser's own default page background happened to be - no real "light
     mode" to switch to on a browser whose default is already dark. `data-theme` on <html>
     picks the palette: "light"/"dark" force one, "system" (the default) follows the OS via
     prefers-color-scheme, same three-state idea as AmbitApp's own Theme.qml `override`. */
  :root {
    --bg: #F6F8F9; --card: #FFFFFF; --text: #1A1D22; --muted: #5B6270;
    --border: #00000022; --code-bg: #00000010; --primary: #167E6A;
    --primary-text: #FFFFFF; --ok: #1A7F37; --err: #C0392B;
  }
  :root[data-theme="dark"] {
    --bg: #14171C; --card: #1B1F27; --text: #E9EBEE; --muted: #B4BDC9;
    --border: #FFFFFF2A; --code-bg: #FFFFFF14; --primary: #9CA3AF;
    --primary-text: #14171C; --ok: #4CAF6D; --err: #E0655A;
  }
  @media (prefers-color-scheme: dark) {
    :root[data-theme="system"] {
      --bg: #14171C; --card: #1B1F27; --text: #E9EBEE; --muted: #B4BDC9;
      --border: #FFFFFF2A; --code-bg: #FFFFFF14; --primary: #9CA3AF;
      --primary-text: #14171C; --ok: #4CAF6D; --err: #E0655A;
    }
  }
  html { background: var(--bg); }
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 780px; margin: 2rem auto;
         padding: 0 1rem; line-height: 1.4; background: var(--bg); color: var(--text); }
  a { color: var(--primary); }
  h1 { font-size: 1.3rem; display: flex; align-items: center; justify-content: space-between;
       gap: 1rem; }
  #themeToggle { font-size: .8rem; padding: .35rem .7rem; border-radius: 999px;
                 border: 1px solid var(--border); background: var(--card); color: var(--text); }
  .meta input { width: 100%; box-sizing: border-box; padding: .4rem; margin-bottom: .5rem;
                background: var(--card); color: var(--text); border: 1px solid var(--border); }
  .step { border: 1px solid var(--border); border-radius: 8px; padding: .6rem .8rem;
          margin-bottom: .5rem; display: flex; flex-wrap: wrap; gap: .5rem; align-items: center; }
  .step.marker { background: var(--code-bg); font-weight: 600; }
  .step select, .step input[type=number] { padding: .3rem; background: var(--card);
                                            color: var(--text); border: 1px solid var(--border); }
  .step input[type=number] { width: 5.5rem; }
  .step .grow { flex: 1 1 auto; }
  .step button.remove { margin-left: auto; }
  .row-buttons { display: flex; gap: .5rem; margin: .8rem 0; flex-wrap: wrap; }
  button { cursor: pointer; padding: .4rem .8rem; background: var(--card); color: var(--text);
           border: 1px solid var(--border); border-radius: 6px; }
  code { background: var(--code-bg); border-radius: 4px; padding: .1rem .3rem; }
  pre { background: var(--code-bg); padding: .8rem; border-radius: 8px; overflow-x: auto;
        white-space: pre-wrap; word-break: break-word; }
  .result-ok { color: var(--ok); }
  .result-err { color: var(--err); }
  label { font-size: .8rem; opacity: .8; }
  .field { display: flex; flex-direction: column; gap: .1rem; }
  .primary { font-size: 1rem; padding: .6rem 1.2rem; font-weight: 600;
             background: var(--primary); color: var(--primary-text); border-color: var(--primary); }
  .secondary { font-size: .85rem; opacity: .85; }
  details { margin: .5rem 0; }
  .history-entry { display: flex; gap: .6rem; align-items: center; border-top: 1px solid var(--border);
                    padding: .4rem 0; }
  .history-entry .grow { flex: 1 1 auto; }
  .hint { font-size: .8rem; opacity: .7; }
  .install-picker { border: 1px solid var(--border); border-radius: 8px; padding: .6rem .8rem;
                     margin: .5rem 0; max-width: 420px; }
  .install-picker select { width: 100%; box-sizing: border-box; padding: .35rem;
                            background: var(--card); color: var(--text);
                            border: 1px solid var(--border); }
  #notes { margin-top: 2rem; padding-top: 1rem; border-top: 1px solid var(--border); }
  #notes h2 { font-size: 1rem; }
</style>
</head>
<body>
<h1>
  Ambit3 Workout Builder
  <button id="themeToggle" onclick="cycleTheme()"></button>
</h1>

<p>Builds a structured workout and compiles it into a real native Suunto <strong>guided
workout</strong> - the Movescount interval screen with the target band and step text.
"Create Workout" compiles it; "Install to Watch" adds it to a sport mode's WORKOUT menu (hold
[Next] &rarr; WORKOUT). Each creation is saved below so you can come back for it later.</p>

<p class="hint">This page authors <strong>your own</strong> guided workouts and installs them
straight to the watch over USB - it works the same on Linux, Mac and Windows and doesn't touch
SuuntoLink at all (a guided workout lives in the watch's own WORKOUT menu). Every workout you
create is also saved to <code>~/Downloads/AmbitWorkouts</code>.</p>

<div class="meta">
  <label>Workout name</label>
  <input id="wname" value="My workout">
  <label>Description</label>
  <input id="wdesc" value="">
</div>

<div id="steps"></div>

<div class="row-buttons">
  <button onclick="addStep()">+ Add step</button>
  <button onclick="addRepeatStart()">+ Start repeat</button>
  <button onclick="addRepeatEnd()">+ End repeat</button>
</div>

<div class="row-buttons">
  <button class="primary" onclick="doCompile()">Create Workout</button>
</div>

<details>
  <summary class="secondary">Advanced (source preview, save/load files)</summary>
  <div class="row-buttons">
    <button class="secondary" onclick="doGenerate()">Show generated source</button>
    <button class="secondary" onclick="exportJson()">Export workout JSON</button>
    <button class="secondary" onclick="document.getElementById('importFile').click()">Import workout JSON</button>
    <input type="file" id="importFile" style="display:none" onchange="importJson(event)">
  </div>
</details>

<div id="output"></div>

<h2>History</h2>
<p class="hint">Every workout you create is kept here (in this browser only) so you can revisit
or re-download it later.</p>
<div id="history"></div>

<div id="notes">
  <h2>Important notes</h2>
  <p class="hint"><strong>"Install to Watch" needs the watch plugged in and on its time
  screen</strong> - it only talks over USB from there, so a menu or an active recording will
  look disconnected. If it can't reach the watch, check that first.</p>
  <p class="hint"><strong>Where it lands:</strong> the workout goes into the sport mode's own
  <strong>WORKOUT menu</strong> (on the watch: the mode &rarr; hold [Next] &rarr; WORKOUT &rarr;
  pick it). It stays dormant until you select it there, then guides you through the steps with
  the native target band, step text and step beeps.</p>
  <p class="hint">This is an independent, unofficial tool, not affiliated with, endorsed by,
  or supported by Suunto. "Suunto", "Ambit", "Traverse" and the watch names shown above are
  trademarks of their respective owner, used here only to describe compatibility. Provided
  as-is, with no warranty of any kind - test carefully before relying on it, and the people
  who built it aren't responsible for any malfunction, data loss, or damage to your watch
  from using it.</p>
</div>

<script>
// Theme (2026-08-08 request: "have a light mode... a button to switch from light mode, to
// dark to system") - three-state cycle, persisted so it survives a reload, same idea as
// AmbitApp's own Theme.qml `override` property.
const THEME_KEY = "ambit_workout_theme";
const THEME_ORDER = ["light", "dark", "system"];
const THEME_LABELS = {light: "Theme: Light", dark: "Theme: Dark", system: "Theme: System"};
function applyTheme(theme) {
  document.documentElement.setAttribute("data-theme", theme);
  document.getElementById("themeToggle").textContent = THEME_LABELS[theme];
}
function cycleTheme() {
  const current = localStorage.getItem(THEME_KEY) || "system";
  const next = THEME_ORDER[(THEME_ORDER.indexOf(current) + 1) % THEME_ORDER.length];
  localStorage.setItem(THEME_KEY, next);
  applyTheme(next);
}
applyTheme(localStorage.getItem(THEME_KEY) || "system");

let steps = [];

// The compiler reports compatibility using Suunto's internal engineering codenames
// (docs/history.md) - translated here to the names people actually buy so "compatible with"
// is readable. Left untranslated (falls through to the raw name) for anything not in
// this project's own confirmed codename table.
// Spacing between "Ambit" and its generation number matches ambitapp-v2's own
// HomeViewModel.qml _modelNames table (2026-08-08 request, applied in both places for the
// same reason: "Ambit3" -> "Ambit 3").

const TYPE_NAMES = ["warmup", "interval", "recovery", "cooldown"];
const DURATION_NAMES = ["time", "distance", "ascent", "lap"];
const TARGET_NAMES = ["none", "hr", "pace", "speed", "vertical_speed", "power"];

// value/unit -> base units (seconds for time, meters for distance/ascent) this project's
// generator expects (SUUNTO_DURATION is seconds, SUUNTO_DISTANCE/SUUNTO_ASCENT are meters).
const TIME_UNITS = {seconds: 1, minutes: 60, hours: 3600};
const DISTANCE_UNITS = {meters: 1, kilometers: 1000};

function unitsFor(durationName) {
  if (durationName === "time") return TIME_UNITS;
  if (durationName === "distance" || durationName === "ascent") return DISTANCE_UNITS;
  return null;
}
function defaultUnit(durationName) {
  return durationName === "time" ? "seconds" : "meters";
}

// SUUNTO_PACE's native unit is decimal minutes/km (SuuntoAppZoneDeveloperManual.pdf) - "6:30"
// is friendlier to type than "6.5", so pace fields are mm:ss text, converted at the edges.
function parsePace(text) {
  const m = /^(\d+):([0-5]?\d)$/.exec((text || "").trim());
  if (!m) return 0;
  return +m[1] + (+m[2]) / 60;
}
function formatPace(decimalMinPerKm) {
  const v = +decimalMinPerKm || 0;
  const min = Math.floor(v);
  const sec = Math.round((v - min) * 60);
  return `${min}:${String(sec).padStart(2, "0")}`;
}

function addStep() {
  steps.push({type: {typeName: "interval"}, duration: {durationName: "time", value: 60, unit: "seconds"},
              target: {targetName: "none", valueRange: {min: 0, max: 0}},
              notify: {beep: true, light: true}});
  render();
}
function addRepeatStart() { steps.push({type: {typeName: "repeatStart", value: 3}}); render(); }
function addRepeatEnd() { steps.push({type: {typeName: "repeatEnd"}}); render(); }
function removeStep(i) { steps.splice(i, 1); render(); }
function moveStep(i, dir) {
  const j = i + dir;
  if (j < 0 || j >= steps.length) return;
  [steps[i], steps[j]] = [steps[j], steps[i]];
  render();
}
function setDurationName(i, name) {
  steps[i].duration.durationName = name;
  steps[i].duration.unit = defaultUnit(name);
  render();
}
function setDurationValue(i, displayValue) {
  const units = unitsFor(steps[i].duration.durationName);
  const factor = units ? units[steps[i].duration.unit] : 1;
  steps[i].duration.value = Math.round((+displayValue || 0) * factor);
}
function setDurationUnit(i, unit) {
  steps[i].duration.unit = unit;
  render();
}

function optionList(names, current) {
  return names.map(n => `<option value="${n}" ${n === current ? "selected" : ""}>${n}</option>`).join("");
}

function render() {
  const el = document.getElementById("steps");
  el.innerHTML = steps.map((s, i) => {
    const t = s.type.typeName;
    if (t === "repeatStart") {
      return `<div class="step marker">
        Start repeat
        <input type="number" min="1" value="${s.type.value}" onchange="steps[${i}].type.value=+this.value">
        times
        ${stepButtons(i)}
      </div>`;
    }
    if (t === "repeatEnd") {
      return `<div class="step marker">End repeat ${stepButtons(i)}</div>`;
    }
    const dur = s.duration, tgt = s.target;
    const notify = s.notify || (s.notify = {beep: true, light: true});
    const units = unitsFor(dur.durationName);
    const unit = dur.unit || defaultUnit(dur.durationName);
    const factor = units ? units[unit] : 1;
    const displayValue = units ? (dur.value || 0) / factor : "";
    const showRange = tgt.targetName !== "none";
    const isPace = tgt.targetName === "pace";
    return `<div class="step">
      <div class="field"><label>Phase</label>
        <select onchange="steps[${i}].type.typeName=this.value">${optionList(TYPE_NAMES, t)}</select>
      </div>
      <div class="field"><label>Text on watch</label>
        <input type="text" size="7" maxlength="6" placeholder="e.g. Fast" value="${s.text || ""}"
               oninput="steps[${i}].text=this.value"
               title="Short label the watch shows when this step starts. Digits are stripped and it's trimmed to about 6 characters.">
      </div>
      <div class="field"><label>Duration</label>
        <select onchange="setDurationName(${i}, this.value)">${optionList(DURATION_NAMES, dur.durationName)}</select>
      </div>
      ${units ? `
      <div class="field"><label>Value</label>
        <input type="number" step="any" value="${displayValue}" onchange="setDurationValue(${i}, this.value)">
      </div>
      <div class="field"><label>Unit</label>
        <select onchange="setDurationUnit(${i}, this.value)">${optionList(Object.keys(units), unit)}</select>
      </div>` : ""}
      <div class="field"><label>Target</label>
        <select onchange="steps[${i}].target.targetName=this.value; render()">${optionList(TARGET_NAMES, tgt.targetName)}</select>
      </div>
      ${showRange && isPace ? `
      <div class="field"><label>Min pace (min/km)</label>
        <input type="text" size="5" placeholder="6:30" value="${formatPace(tgt.valueRange.min)}"
               onchange="steps[${i}].target.valueRange.min=parsePace(this.value); this.value=formatPace(steps[${i}].target.valueRange.min)">
      </div>
      <div class="field"><label>Max pace (min/km)</label>
        <input type="text" size="5" placeholder="6:00" value="${formatPace(tgt.valueRange.max)}"
               onchange="steps[${i}].target.valueRange.max=parsePace(this.value); this.value=formatPace(steps[${i}].target.valueRange.max)">
      </div>` : ""}
      ${showRange && !isPace ? `
      <div class="field"><label>Min</label>
        <input type="number" value="${tgt.valueRange.min}" onchange="steps[${i}].target.valueRange.min=+this.value">
      </div>
      <div class="field"><label>Max</label>
        <input type="number" value="${tgt.valueRange.max}" onchange="steps[${i}].target.valueRange.max=+this.value">
      </div>` : ""}
      <div class="field"><label>On entering this step</label>
        <label><input type="checkbox" ${notify.beep ? "checked" : ""}
          onchange="steps[${i}].notify.beep=this.checked">Beep</label>
        <label><input type="checkbox" ${notify.light ? "checked" : ""}
          onchange="steps[${i}].notify.light=this.checked">Light</label>
      </div>
      ${stepButtons(i)}
    </div>`;
  }).join("");
}

function stepButtons(i) {
  return `<span class="grow"></span>
    <button onclick="moveStep(${i},-1)" title="move up">^</button>
    <button onclick="moveStep(${i},1)" title="move down">v</button>
    <button class="remove" onclick="removeStep(${i})">remove</button>`;
}

function currentWorkout() {
  return {name: document.getElementById("wname").value,
          workoutDescription: document.getElementById("wdesc").value,
          steps: steps};
}

async function doGenerate() {
  const out = document.getElementById("output");
  out.innerHTML = "generating...";
  const resp = await fetch("/api/generate", {method: "POST", body: JSON.stringify(currentWorkout())});
  const data = await resp.json();
  if (!resp.ok) { out.innerHTML = `<p class="result-err">${data.error}</p>`; return; }
  out.innerHTML = `<h3>Generated source</h3><pre>${data.source.replace(/</g, "&lt;")}</pre>`;
}

let lastCompiled = null;
let lastWorkout = null;   // the workout JSON behind lastCompiled - what the guided install needs

// "Install to Watch" writes the guided workout straight to a connected watch over USB
// (guided_workout.py, no SuuntoLink) - works the same on Linux, Mac and Windows. Each button
// gets its own picker <div>, since the same installButtonHtml() call renders both after a fresh
// compile and once per History row.
let installPickerSeq = 0;
const pickerWorkout = {};    // pickerId -> the workout JSON it installs as a guided workout
const pickerModes = {};      // pickerId -> /api/modes result, fetched once per picker open

function installButtonHtml(historyIndex) {
  const wk = historyIndex === undefined ? "lastWorkout" : `loadHistory()[${historyIndex}].workout`;
  const cls = historyIndex === undefined ? "" : ' class="secondary"';
  const pickerId = `installPicker${installPickerSeq++}`;
  // Small helper text next to the button (only on a fresh compile, not in every history row).
  const help = historyIndex !== undefined ? "" : `
    <p class="hint" style="margin:.4rem 0 0"><strong>Install to Watch</strong> writes the workout
    straight to a connected watch over USB &ndash; no SuuntoLink, no account (Linux/Mac/Windows).
    Pick a sport mode; it lands in that mode's WORKOUT menu (hold [Next] &rarr; WORKOUT, then pick
    it). Watch plugged in on its time screen.</p>`;
  return `<button${cls} onclick="toggleInstallPicker('${pickerId}', ${wk})">Install to Watch</button>
    <div id="${pickerId}" class="install-picker" style="display:none"></div>${help}`;
}

async function toggleInstallPicker(pickerId, workout) {
  pickerWorkout[pickerId] = workout;
  const el = document.getElementById(pickerId);
  if (el.style.display !== "none") { el.style.display = "none"; return; }
  el.style.display = "block";
  el.innerHTML = '<p class="hint">reading sport modes from the watch...</p>';
  try {
    const resp = await fetch("/api/modes");
    const data = await resp.json();
    if (!resp.ok || !data.ok) throw new Error(data.error || "couldn't read the watch");
    pickerModes[pickerId] = data.modes;
  } catch (e) {
    el.innerHTML = `<p class="result-err">${e.message}</p>`;
    return;
  }
  renderPickerForm(pickerId);
}

function renderPickerForm(pickerId) {
  const modes = pickerModes[pickerId];
  const el = document.getElementById(pickerId);
  // A guided workout goes into the mode's WORKOUT menu - it is NOT placed on a display row, so
  // there is no screen/field to choose; just pick the sport mode.
  const modeOpts = modes.map((m) => `<option value="${m.name}">${m.name}</option>`).join("");
  el.innerHTML = `
    <div class="field"><label>Sport mode</label>
      <select id="${pickerId}_mode">${modeOpts}</select>
    </div>
    <div class="row-buttons">
      <button class="primary" onclick="doInstallToWatch('${pickerId}')">Add to WORKOUT menu</button>
    </div>
    <div id="${pickerId}_result"></div>`;
}

async function doInstallToWatch(pickerId) {
  const mode = document.getElementById(`${pickerId}_mode`).value;
  const resultEl = document.getElementById(`${pickerId}_result`);
  resultEl.innerHTML = '<p class="hint">installing...</p>';
  const resp = await fetch("/api/install-to-watch", {method: "POST", body: JSON.stringify({
    workout: pickerWorkout[pickerId], mode,
  })});
  const data = await resp.json();
  if (resp.ok && data.ok) {
    resultEl.innerHTML = `<p class="result-ok">Installed &ndash; now in ${mode}'s WORKOUT menu (hold [Next] &rarr; WORKOUT).</p>`;
    return;
  }
  // Show what the watch tool actually said - "no parseable JSON" usually means the watch
  // wasn't on its time screen, but a real error hides in stderr, so make it reachable.
  const detail = (data.stderr || data.raw_output || "").trim();
  resultEl.innerHTML = `<p class="result-err">${data.error || "install failed"}</p>`
    + (detail ? `<details><summary class="secondary">What the watch tool reported</summary>`
       + `<pre>${detail.replace(/</g, "&lt;").slice(-2000)}</pre></details>` : "");
}

async function doCompile() {
  const out = document.getElementById("output");
  out.innerHTML = "creating workout...";
  const workout = currentWorkout();
  const resp = await fetch("/api/compile", {method: "POST", body: JSON.stringify(workout)});
  const data = await resp.json();
  if (!resp.ok) { out.innerHTML = `<p class="result-err">${data.error}</p>`; return; }
  renderCompiledResult(workout.name, data, data.savedTo ? `Saved to ${data.savedTo}.` : "", workout);
  saveHistory(workout, data);
}

function renderCompiledResult(name, data, extraNote, workout) {
  lastCompiled = data;
  lastWorkout = workout || null;
  // A guidance binary comes back as a plain byte array with no compatibleVariants field (unlike
  // the old app-zone response) - so report its size and let the install button carry the workout.
  document.getElementById("output").innerHTML = `<p class="result-ok">"${name}" - native guided
    workout ready (${data.binary.length}-byte guidance binary with target band + step text).
    ${extraNote}</p>
    ${installButtonHtml()}`;
}

function download(filename, text) {
  const a = document.createElement("a");
  a.href = URL.createObjectURL(new Blob([text], {type: "application/json"}));
  a.download = filename;
  a.click();
}

function exportJson() { download("workout.json", JSON.stringify(currentWorkout(), null, 2)); }

function importJson(event) {
  const file = event.target.files[0];
  const reader = new FileReader();
  reader.onload = () => {
    const w = JSON.parse(reader.result);
    document.getElementById("wname").value = w.name || "";
    document.getElementById("wdesc").value = w.workoutDescription || "";
    steps = (w.steps || []).map(s => {
      if (s.duration && s.duration.durationName && !s.duration.unit) {
        s.duration.unit = defaultUnit(s.duration.durationName);
      }
      return s;
    });
    render();
  };
  reader.readAsText(file);
}

// --- history (localStorage only - nothing server-side to keep this tool stateless) ---
const HISTORY_KEY = "ambit_workout_history";

function loadHistory() {
  try { return JSON.parse(localStorage.getItem(HISTORY_KEY)) || []; }
  catch (e) { return []; }
}
function saveHistory(workout, compiled) {
  const history = loadHistory();
  history.unshift({
    at: new Date().toISOString(), workout: workout, compiled: compiled,
  });
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history.slice(0, 50)));
  renderHistory();
}
function renderHistory() {
  const history = loadHistory();
  const el = document.getElementById("history");
  if (history.length === 0) { el.innerHTML = '<p class="hint">Nothing created yet.</p>'; return; }
  el.innerHTML = history.map((h, i) => `
    <div class="history-entry">
      <div class="grow">
        <strong>${h.workout.name}</strong>
        <span class="hint">${new Date(h.at).toLocaleString()} - ${h.workout.steps.length} step(s),
        ${h.compiled.binary.length} byte binary</span>
      </div>
      <button class="secondary" onclick="loadFromHistory(${i})">Load into editor</button>
      <button class="secondary" onclick="downloadFromHistory(${i})">Download compiled</button>
      ${installButtonHtml(i)}
      <button class="secondary" onclick="deleteFromHistory(${i})">Delete</button>
    </div>`).join("");
}
function loadFromHistory(i) {
  const h = loadHistory()[i];
  document.getElementById("wname").value = h.workout.name || "";
  document.getElementById("wdesc").value = h.workout.workoutDescription || "";
  steps = h.workout.steps;
  render();
  window.scrollTo({top: 0, behavior: "smooth"});
}
function downloadFromHistory(i) {
  const h = loadHistory()[i];
  download(`${h.workout.name.replace(/[^a-z0-9]+/gi, "_")}_compiled.json`, JSON.stringify(h.compiled, null, 2));
}
function deleteFromHistory(i) {
  const history = loadHistory();
  history.splice(i, 1);
  localStorage.setItem(HISTORY_KEY, JSON.stringify(history));
  renderHistory();
}

// A full workout handed in by the desktop Calendar planner ("Create workout"): name,
// description and steps, all pre-built. Injected server-side as __INITIAL_WORKOUT_JSON__.
// Falls back to just the name field the server already set when no workout is passed.
(function () {
  try {
    var initial = __INITIAL_WORKOUT_JSON__;
    if (initial && typeof initial === "object") {
      if (initial.name) document.getElementById("wname").value = initial.name;
      if (initial.workoutDescription)
        document.getElementById("wdesc").value = initial.workoutDescription;
      if (Array.isArray(initial.steps) && initial.steps.length) {
        steps = initial.steps.map(function (st) {
          if (st.duration && st.duration.durationName && !st.duration.unit)
            st.duration.unit = defaultUnit(st.duration.durationName);
          return st;
        });
      }
    }
  } catch (e) { /* no/blank initial workout - leave the empty builder */ }
})();

render();
renderHistory();
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass  # keep stdout clean; errors still surface via response bodies

    def _send_json(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/modes":
            self._handle_list_modes()
            return
        from urllib.parse import urlparse, parse_qs, unquote  # noqa: PLC0415
        parsed = urlparse(self.path)
        if parsed.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        # Pre-filled workout name, 2026-08-23: the desktop Calendar can open this builder for a
        # chosen day+sport with the title already set (e.g. "Running_24_08"), the "workaround"
        # for scheduled workouts. Passed as ?name=... ; falls back to the page's own default.
        # A CLI --name (see main) becomes the process-wide default the query still overrides.
        q = parse_qs(parsed.query)
        initial = q.get("name", [INITIAL_WORKOUT_NAME])[0][:64]
        page = HTML_PAGE.replace('id="wname" value="My workout"',
                                 'id="wname" value="%s"' % _html_attr(initial))
        # A full pre-built workout from the Calendar planner - ?workout=<base64 of JSON>.
        # Decoded and validated here so a malformed value just yields the empty builder rather
        # than injecting anything unsafe; only json.dumps output reaches the page.
        initial_workout = "null"
        raw_w = q.get("workout", [None])[0] or INITIAL_WORKOUT_JSON
        if raw_w:
            try:
                import base64                                  # noqa: PLC0415
                obj = json.loads(base64.urlsafe_b64decode(raw_w.encode()).decode("utf-8"))
                if isinstance(obj, dict):
                    initial_workout = json.dumps(obj)
            except (ValueError, TypeError, json.JSONDecodeError):
                initial_workout = "null"
        page = page.replace("__INITIAL_WORKOUT_JSON__", initial_workout)
        body = page.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/api/generate", "/api/compile", "/api/install-to-watch"):
            self.send_response(404)
            self.end_headers()
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except json.JSONDecodeError as e:
            self._send_json(400, {"error": f"invalid JSON body: {e}"})
            return

        if self.path == "/api/install-to-watch":
            self._handle_install_to_watch(body)
            return

        workout = body

        if self.path == "/api/generate":
            # For a guided workout, the "source" is the workout JSON itself - that's what gets
            # POSTed to the compiler (no hand-written app-zone script anymore).
            self._send_json(200, {"source": json.dumps(workout, indent=2)})
            return

        # /api/compile: compile the workout JSON into the GENUINE native guidance binary (the
        # real Movescount interval screen - target band + step text), via the compiler's
        # JSON->guidance path. This is a WORKOUT for the WORKOUT menu, not an app-zone app.
        try:
            result = GW.compile_workout(workout)
        except (RuntimeError, SystemExit) as e:
            self._send_json(502, {"error": str(e)})
            return
        result["name"] = workout.get("name") or result.get("name", "Workout")
        saved_to = save_compiled(workout.get("name", "Workout"), result)
        self._send_json(200, {**result, "savedTo": str(saved_to)})

    def _handle_list_modes(self):
        """GET /api/modes - the connected watch's own sport modes/displays/fields, read-only
        (0x0b17), for the "where does this go" picker before an install. Trims
        custom_modes.py --json's own output down to what a placement picker needs; a mode
        already at the 5-app ceiling (check_mode_app_limit's own SPORT_MODE_APP_LIMIT) is kept
        in the list but flagged rather than dropped, so the UI can explain why it's disabled
        instead of just not offering it."""
        code, out, err = run_tool("custom_modes.py", ["--json"], timeout=60)
        info = parse_last_json_line(out)
        if info is None or not info.get("ok"):
            self._send_json(502, {"ok": False,
                                   "error": "couldn't read the watch's sport modes - is it "
                                   "connected and on the time screen?",
                                   "raw_output": out, "stderr": err})
            return
        modes = [{
            "index": i, "name": m.get("name"), "appCount": m.get("appCount", 0),
            "atLimit": m.get("appCount", 0) >= 5,
            "displays": [{
                "index": d["index"], "template": d.get("templateLabel") or d.get("template"),
                "isBuiltIn": d.get("isBuiltIn"), "screenNumber": d.get("screenNumber"),
                "fields": [{"index": i, "row": f.get("rowLabel") or f"row {i}",
                            "shows": f.get("typeLabel")}
                           for i, f in enumerate(d.get("fields", []))],
            } for d in m.get("displays", [])],
        } for i, m in enumerate(info.get("exerciseModes", []))]
        self._send_json(200, {"ok": True, "modes": modes})

    def _handle_install_to_watch(self, body):
        """POST /api/install-to-watch. Body: {"workout": {...}, "mode": "<mode name>"}. Installs
        the workout as a NATIVE GUIDED WORKOUT into the named sport mode's WORKOUT menu via
        tools/guided_workout.py --append: the compiled guidance binary goes into the Apps region
        with entry byte0=1 (guidance) and the mode gets a guidance display (Template 295), NO
        rule - so it's dormant until picked from [Next]-3s -> WORKOUT and renders the native
        target-band + step-text screen. No display/field to choose (that was the old app-zone
        data-field shortcut); a workout is not slotted onto a screen. No SuuntoLink, all
        platforms."""
        workout = body.get("workout")
        mode = body.get("mode")
        if not workout or not mode:
            self._send_json(400, {"ok": False, "error": 'need "workout" and "mode"'})
            return
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(workout, f)
            workout_path = f.name
        try:
            args = [workout_path, "--mode", str(mode), "--append", "--json", "--write"]
            code, out, err = run_tool("guided_workout.py", args, timeout=180)
        finally:
            Path(workout_path).unlink(missing_ok=True)
        info = parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False,
                                   "error": "guided_workout.py produced no parseable JSON - "
                                   "is the watch connected and on the time screen?",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)


def _log_startup_failure(exc):
    """The packaged app runs with console=False (no terminal window, so print() goes
    nowhere) - without this, a startup failure when double-clicked from Finder is
    completely silent, just "nothing happens". Logged instead of just swallowed."""
    SAVE_DIR.mkdir(exist_ok=True)
    with (SAVE_DIR / "app.log").open("a") as f:
        f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} - failed to start: {exc!r}\n")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-browser", action="store_true",
                     help="don't open a browser automatically (default: open one)")
    ap.add_argument("--name", default=None,
                     help="pre-fill the workout name (the Calendar passes e.g. Running_24_08)")
    ap.add_argument("--workout-b64", default=None,
                     help="pre-fill the WHOLE workout: base64 of its JSON (Calendar planner)")
    args = ap.parse_args()
    global INITIAL_WORKOUT_NAME, INITIAL_WORKOUT_JSON
    if args.name:
        INITIAL_WORKOUT_NAME = args.name
    if args.workout_b64:
        INITIAL_WORKOUT_JSON = args.workout_b64
    # A --name launch opens the browser straight at that name, so the query and the default
    # agree even before any typing.
    name_q = ("?name=" + _url_quote(args.name)) if args.name else ""
    url = f"http://{args.host}:{args.port}/{name_q}"

    try:
        server = ThreadingHTTPServer((args.host, args.port), Handler)
    except OSError as e:
        # Most likely cause: an earlier launch is still running in the background (e.g.
        # the icon was double-clicked more than once). Open the browser at the existing
        # instance rather than dying invisibly - and log it either way, since that
        # guess could be wrong.
        _log_startup_failure(e)
        if not args.no_browser:
            webbrowser.open(url)
        return 0

    print(f"Workout builder running at {url} (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    except Exception as e:
        _log_startup_failure(e)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
