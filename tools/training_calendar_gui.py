#!/usr/bin/env python3
"""A small local web GUI for the Calendar feature (André's locked design, 2026-08-21): pick a
date, pick a sport mode/activity, build a workout (same step builder as workout_gui.py), add it
to a Plan, then Sync — which erases whatever's dated before today and installs whatever's next,
straight onto the watch's WORKOUT menu, named "dd/mm_<workout name>". The compile+install+
rotation logic is entirely `training_calendar.py`, run as a subprocess exactly like a person at
a terminal would; this file only adds a browser-facing front end on top, stdlib-only (no
framework, no build step — this project's existing style, see workout_gui.py/apps_gui.py).

    ./tools/training_calendar_gui.py               # serves http://127.0.0.1:8766, opens browser
    ./tools/training_calendar_gui.py --port 9001 --no-browser

The Plan itself lives in the browser (localStorage) — this tool has no server-side database.
"""
import argparse
import json
import subprocess
import sys
import tempfile
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOOLS_DIR = Path(__file__).resolve().parent


def run_tool(script, args, timeout=180):
    """Same shape as workout_gui.py's own run_tool() / desktop/backend/server.py's — runs one
    of tools/*.py exactly as a person at a terminal would. Returns (returncode, stdout, stderr);
    never raises for a nonzero exit, the caller decides what that means."""
    proc = subprocess.run([sys.executable, str(TOOLS_DIR / script), *args],
                          cwd=TOOLS_DIR, capture_output=True, text=True, timeout=timeout)
    return proc.returncode, proc.stdout, proc.stderr


def parse_last_json_line(out):
    """Same reasoning as workout_gui.py's own version: tools print human-readable progress
    *and* one machine-readable JSON summary line, not always in the same position — try every
    line, keep the last one that parses."""
    parsed = None
    for line in out.strip().splitlines():
        try:
            parsed = json.loads(line)
        except ValueError:
            continue
    return parsed


HTML_PAGE = r"""<!doctype html>
<html data-theme="system">
<head>
<meta charset="utf-8">
<title>Ambit3 Training Calendar</title>
<style>
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
  body { font-family: -apple-system, system-ui, sans-serif; max-width: 820px; margin: 2rem auto;
         padding: 0 1rem; line-height: 1.4; background: var(--bg); color: var(--text); }
  h1 { font-size: 1.3rem; display: flex; align-items: center; justify-content: space-between;
       gap: 1rem; }
  h2 { font-size: 1.05rem; margin-top: 2rem; }
  #themeToggle { font-size: .8rem; padding: .35rem .7rem; border-radius: 999px;
                 border: 1px solid var(--border); background: var(--card); color: var(--text); }
  .meta { display: flex; flex-wrap: wrap; gap: .6rem; }
  .meta .field { flex: 1 1 160px; }
  .meta input, .meta select { width: 100%; box-sizing: border-box; padding: .4rem;
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
  .plan-entry { display: flex; gap: .6rem; align-items: center; border-top: 1px solid var(--border);
                    padding: .5rem 0; }
  .plan-entry .grow { flex: 1 1 auto; }
  .plan-entry .date-badge { font-variant-numeric: tabular-nums; font-weight: 600;
                             min-width: 4.2rem; }
  .plan-entry.past .date-badge { color: var(--err); }
  .hint { font-size: .8rem; opacity: .7; }
  #syncResult { margin-top: .8rem; }
</style>
</head>
<body>
<h1>
  Ambit3 Training Calendar
  <button id="themeToggle" onclick="cycleTheme()"></button>
</h1>

<p>Build a workout for a specific date, add it to the plan, then Sync. On sync the watch's
WORKOUT menu gets whatever's still upcoming, named <code>dd/mm_name</code> so you can tell
entries apart — anything dated before today gets erased first. No on-watch date logic is
involved; you pick the right one by hand.</p>

<h2>Import from intervals.icu</h2>
<p class="hint">Pull your planned workouts straight from intervals.icu. Your athlete id and API
key are kept in this browser only (never sent anywhere but intervals.icu). Find them in
intervals.icu → Settings → Developer.</p>
<div class="meta">
  <div class="field"><label>Athlete id</label><input id="iAthlete" placeholder="i12345 or 12345"></div>
  <div class="field" style="flex:2 1 240px"><label>API key</label>
    <input id="iKey" type="password" placeholder="intervals.icu API key"></div>
</div>
<div class="meta">
  <div class="field"><label>From</label><input type="date" id="iStart"></div>
  <div class="field"><label>To</label><input type="date" id="iEnd"></div>
  <div class="field"><label>Install into mode</label>
    <select id="iMode"><option value="">(loading modes...)</option></select></div>
</div>
<div class="row-buttons">
  <button class="primary" onclick="importFromIntervals()">Fetch planned workouts</button>
</div>
<div id="importResult"></div>

<h2>New calendar entry</h2>

<div class="meta">
  <div class="field"><label>Date</label><input type="date" id="edate"></div>
  <div class="field"><label>Sport mode</label>
    <select id="emode"><option value="">(loading modes...)</option></select>
  </div>
  <div class="field" style="flex:2 1 260px"><label>Workout name</label>
    <input id="wname" value="My workout"></div>
</div>

<div id="steps"></div>

<div class="row-buttons">
  <button onclick="addStep()">+ Add step</button>
  <button onclick="addRepeatStart()">+ Start repeat</button>
  <button onclick="addRepeatEnd()">+ End repeat</button>
</div>

<div class="row-buttons">
  <button class="primary" onclick="addToPlan()">Add to Calendar</button>
</div>

<div id="addResult"></div>

<h2>Plan</h2>
<p class="hint">Kept in this browser only. Entries dated before today are marked — Sync erases
them from the watch and installs what's next.</p>
<div id="plan"></div>

<div class="row-buttons">
  <button onclick="doSync(false)">Preview sync</button>
  <button class="primary" onclick="doSync(true)">Sync to Watch</button>
</div>
<div id="syncResult"></div>

<div id="notes" style="margin-top:2rem; padding-top:1rem; border-top:1px solid var(--border)">
  <p class="hint"><strong>Sync needs the watch plugged in and on its time screen</strong> — it
  only talks over USB from there. Each real workout compiles through the live community
  compiler, so it also needs an internet connection.</p>
  <p class="hint">This is an independent, unofficial tool, not affiliated with, endorsed by,
  or supported by Suunto. Provided as-is, with no warranty of any kind.</p>
</div>

<script>
const THEME_KEY = "ambit_calendar_theme";
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

// today's date as the date input's default, local time (not UTC — avoids an off-by-one near
// midnight in negative-UTC-offset zones from toISOString()).
function todayIso() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth()+1).padStart(2,"0")}-${String(d.getDate()).padStart(2,"0")}`;
}
document.getElementById("edate").value = todayIso();

let steps = [];
const TYPE_NAMES = ["warmup", "interval", "recovery", "cooldown"];
const DURATION_NAMES = ["time", "distance", "ascent", "lap"];
const TARGET_NAMES = ["none", "hr", "pace", "speed", "vertical_speed", "power"];
const TIME_UNITS = {seconds: 1, minutes: 60};
const DISTANCE_UNITS = {meters: 1, kilometers: 1000};

function unitsFor(durationName) {
  if (durationName === "time") return TIME_UNITS;
  if (durationName === "distance" || durationName === "ascent") return DISTANCE_UNITS;
  return null;
}
function defaultUnit(durationName) {
  return durationName === "time" ? "seconds" : "meters";
}
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
               oninput="steps[${i}].text=this.value">
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
  return {name: document.getElementById("wname").value, steps: steps};
}

// --- sport modes, fetched once from the watch ---
async function loadModes() {
  const sels = ["emode", "iMode"].map(id => document.getElementById(id)).filter(Boolean);
  try {
    const resp = await fetch("/api/modes");
    const data = await resp.json();
    if (!resp.ok || !data.ok) throw new Error(data.error || "couldn't read the watch");
    const opts = data.modes.map(m => `<option value="${m.name}">${m.name}</option>`).join("");
    sels.forEach(sel => { sel.innerHTML = opts; });
    const savedMode = localStorage.getItem(IMPORT_MODE_KEY);
    if (savedMode) { const im = document.getElementById("iMode"); if (im) im.value = savedMode; }
  } catch (e) {
    sels.forEach(sel => { sel.innerHTML = '<option value="">(watch unreachable)</option>'; });
  }
}
loadModes();

// --- intervals.icu import (credentials kept in this browser only) ---
const IMPORT_ATH_KEY = "ambit_intervals_athlete";
const IMPORT_KEY_KEY = "ambit_intervals_apikey";
const IMPORT_MODE_KEY = "ambit_intervals_mode";
(function restoreImportForm() {
  const a = localStorage.getItem(IMPORT_ATH_KEY); if (a) document.getElementById("iAthlete").value = a;
  const k = localStorage.getItem(IMPORT_KEY_KEY); if (k) document.getElementById("iKey").value = k;
})();
async function importFromIntervals() {
  const athlete = document.getElementById("iAthlete").value.trim();
  const key = document.getElementById("iKey").value.trim();
  const start = document.getElementById("iStart").value;
  const end = document.getElementById("iEnd").value;
  const mode = document.getElementById("iMode").value;
  const out = document.getElementById("importResult");
  if (!athlete || !key) { out.innerHTML = '<p class="result-err">enter your athlete id and API key</p>'; return; }
  if (!start || !end) { out.innerHTML = '<p class="result-err">pick a From and To date</p>'; return; }
  if (!mode) { out.innerHTML = '<p class="result-err">pick a sport mode to install into</p>'; return; }
  localStorage.setItem(IMPORT_ATH_KEY, athlete);
  localStorage.setItem(IMPORT_KEY_KEY, key);
  localStorage.setItem(IMPORT_MODE_KEY, mode);
  out.innerHTML = '<p class="hint">fetching from intervals.icu...</p>';
  try {
    const resp = await fetch("/api/intervals-fetch", {method: "POST", body: JSON.stringify({
      athlete_id: athlete, api_key: key, start, end, mode,
    })});
    const data = await resp.json();
    if (!resp.ok || !data.ok) { out.innerHTML = `<p class="result-err">${data.error || "fetch failed"}</p>`; return; }
    const plan = loadPlan();
    const seen = new Set(plan.map(e => e.date + "|" + e.workout.name));
    let added = 0;
    for (const e of data.entries) {
      if (seen.has(e.date + "|" + e.workout.name)) continue;   // skip duplicates already in the plan
      plan.push(e); added++;
    }
    savePlan(plan);
    let html = `<p class="result-ok">Added ${added} of ${data.entries.length} workout(s) to the plan`
      + `${data.resolvedToWatch ? " (HR bands resolved to the watch)" : " (HR bands from intervals.icu zones)"}.</p>`;
    if (data.skipped && data.skipped.length) {
      html += `<details><summary class="secondary">Skipped ${data.skipped.length}</summary><pre>`
        + data.skipped.map(s => `${s.date} ${s.name}: ${s.reason}`).join("\n").replace(/</g, "&lt;")
        + `</pre></details>`;
    }
    out.innerHTML = html;
  } catch (e) {
    out.innerHTML = `<p class="result-err">${e.message || e}</p>`;
  }
}

// --- plan (localStorage) ---
const PLAN_KEY = "ambit_calendar_plan";
function loadPlan() {
  try { return JSON.parse(localStorage.getItem(PLAN_KEY)) || []; }
  catch (e) { return []; }
}
function savePlan(plan) { localStorage.setItem(PLAN_KEY, JSON.stringify(plan)); renderPlan(); }

function addToPlan() {
  const date = document.getElementById("edate").value;
  const mode = document.getElementById("emode").value;
  const workout = currentWorkout();
  const out = document.getElementById("addResult");
  if (!date) { out.innerHTML = '<p class="result-err">pick a date</p>'; return; }
  if (!mode) { out.innerHTML = '<p class="result-err">pick a sport mode</p>'; return; }
  if (!workout.name || workout.steps.length === 0) {
    out.innerHTML = '<p class="result-err">name the workout and add at least one step</p>';
    return;
  }
  const plan = loadPlan();
  plan.push({date, mode, workout});
  savePlan(plan);
  out.innerHTML = `<p class="result-ok">added "${workout.name}" on ${date}</p>`;
  steps = [];
  document.getElementById("wname").value = "My workout";
  render();
}
function removeFromPlan(i) {
  const plan = loadPlan();
  plan.splice(i, 1);
  savePlan(plan);
}
function renderPlan() {
  const plan = loadPlan().slice().sort((a, b) => a.date.localeCompare(b.date));
  const el = document.getElementById("plan");
  if (plan.length === 0) { el.innerHTML = '<p class="hint">Nothing planned yet.</p>'; return; }
  const today = todayIso();
  el.innerHTML = plan.map((e) => {
    const i = loadPlan().indexOf(e);
    const isPast = e.date < today;
    return `<div class="plan-entry ${isPast ? "past" : ""}">
      <span class="date-badge">${e.date}${isPast ? " (past)" : ""}</span>
      <div class="grow"><strong>${e.workout.name}</strong>
        <span class="hint"> — ${e.mode}, ${e.workout.steps.length} step(s)</span></div>
      <button class="secondary" onclick="removeFromPlan(${i})">Remove</button>
    </div>`;
  }).join("");
}
renderPlan();

// --- sync ---
async function doSync(write) {
  const plan = loadPlan();
  const resultEl = document.getElementById("syncResult");
  if (plan.length === 0) {
    resultEl.innerHTML = '<p class="result-err">plan is empty</p>';
    return;
  }
  resultEl.innerHTML = `<p class="hint">${write ? "syncing" : "checking"}...</p>`;
  const resp = await fetch("/api/sync-calendar", {method: "POST", body: JSON.stringify({
    entries: plan, write,
  })});
  const data = await resp.json();
  if (!resp.ok || !data.ok) {
    const detail = (data.stderr || data.raw_output || "").trim();
    resultEl.innerHTML = `<p class="result-err">${data.error || "sync failed"}</p>`
      + (detail ? `<details><summary class="secondary">What the watch tool reported</summary>`
         + `<pre>${detail.replace(/</g, "&lt;").slice(-2000)}</pre></details>` : "");
    return;
  }
  let html = `<p class="result-ok">${write ? "Synced" : "Preview"} as of ${data.today}.</p>
    <p>Erase: ${data.removed.length ? data.removed.join(", ") : "(none)"}</p>
    <p>Install: ${data.added.length ? data.added.join(", ") : "(none)"}</p>`;
  if (data.displaysAdded && data.displaysAdded.length) {
    html += `<p>Guidance display added to: ${data.displaysAdded.join(", ")}</p>`;
  }
  if (data.failed && data.failed.length) {
    html += `<p class="result-err">Failed to compile (skipped): ${
      data.failed.map(f => `${f.name} (${f.error})`).join("; ")}</p>`;
  }
  resultEl.innerHTML = html;
}
</script>
</body>
</html>
"""


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

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
        if self.path != "/":
            self.send_response(404)
            self.end_headers()
            return
        body = HTML_PAGE.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self):
        if self.path not in ("/api/sync-calendar", "/api/intervals-fetch"):
            self.send_response(404)
            self.end_headers()
            return
        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length)) if length else {}
        except json.JSONDecodeError as e:
            self._send_json(400, {"error": f"invalid JSON body: {e}"})
            return
        if self.path == "/api/intervals-fetch":
            self._handle_intervals_fetch(body)
        else:
            self._handle_sync_calendar(body)

    def _handle_list_modes(self):
        """GET /api/modes — same idea as workout_gui.py's own: the connected watch's own sport
        modes, read-only, for the "which mode is this for" picker."""
        code, out, err = run_tool("custom_modes.py", ["--json"], timeout=60)
        info = parse_last_json_line(out)
        if info is None or not info.get("ok"):
            self._send_json(502, {"ok": False,
                                   "error": "couldn't read the watch's sport modes - is it "
                                   "connected and on the time screen?",
                                   "raw_output": out, "stderr": err})
            return
        modes = [{"name": m.get("name")} for m in info.get("exerciseModes", [])]
        self._send_json(200, {"ok": True, "modes": modes})

    def _handle_sync_calendar(self, body):
        """POST /api/sync-calendar. Body: {"entries": [{date, mode, workout}, ...], "write": bool}.
        Writes the plan to a temp file and runs training_calendar.py --sync (optionally
        --write) on it — same compile+install+erase-past-today rotation a terminal user gets."""
        entries = body.get("entries")
        if not entries:
            self._send_json(400, {"ok": False, "error": 'need a non-empty "entries" list'})
            return
        plan = {"name": "Calendar", "entries": entries}
        with tempfile.NamedTemporaryFile("w", suffix=".json", delete=False) as f:
            json.dump(plan, f)
            plan_path = f.name
        try:
            args = [plan_path, "--sync", "--json"]
            if body.get("write"):
                args.append("--write")
            code, out, err = run_tool("training_calendar.py", args, timeout=300)
        finally:
            Path(plan_path).unlink(missing_ok=True)
        info = parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False,
                                   "error": "training_calendar.py produced no parseable JSON - "
                                   "is the watch connected and on the time screen?",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)

    def _handle_intervals_fetch(self, body):
        """POST /api/intervals-fetch. Body: {athlete_id, api_key, start, end, mode}. Shells
        intervals_workout.py --from-intervals (same as a terminal user), which pulls the planned
        workouts from intervals.icu, reconstructs HR bands from the athlete's zones, resolves them
        to the watch when it's reachable, and prints the dated plan entries as JSON."""
        need = ("athlete_id", "api_key", "start", "end", "mode")
        missing = [k for k in need if not body.get(k)]
        if missing:
            self._send_json(400, {"ok": False, "error": "missing: " + ", ".join(missing)})
            return
        args = ["--from-intervals", "--json",
                "--athlete-id", str(body["athlete_id"]), "--api-key", str(body["api_key"]),
                "--start", str(body["start"]), "--end", str(body["end"]),
                "--mode", str(body["mode"])]
        code, out, err = run_tool("intervals_workout.py", args, timeout=120)
        info = parse_last_json_line(out)
        if info is None:
            self._send_json(502, {"ok": False,
                                   "error": "intervals_workout.py produced no parseable JSON",
                                   "raw_output": out, "stderr": err})
            return
        self._send_json(200 if info.get("ok") else 502, info)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=8766)
    ap.add_argument("--no-browser", action="store_true")
    args = ap.parse_args()
    url = f"http://{args.host}:{args.port}/"

    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Training calendar running at {url} (Ctrl+C to stop)")
    if not args.no_browser:
        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
