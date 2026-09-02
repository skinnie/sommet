# UX Fixes Plan

Numbered issues from the average-user UX audit (2026-09-02) and André's decisions.
Plan-mode style: number · description · scope · status. **Desktop + Android parity applies to
every item unless noted** (per project rule).

Status legend: 🔲 open · 🚧 in progress · ✅ done

---

## 1. Feature defaults + a graceful no-watch state 🚧 (desktop done · Android pending)
Clarified by André (2026-09-02) into two SEPARATE things:

### 1a. Default "no watch connected" state — real data, not fake
When nothing is plugged in, the app still shows every card/page that does NOT need a watch
(weather, maps, already-downloaded activities, calendar, totals, coach, health, weight, gear)
with the user's **real** data. Only the **watch-linked** cards are hidden while disconnected,
reappearing when a watch is plugged in: Watch Settings, Sport Modes, Routes, POIs, Firmware,
watch Backup, Smart Sensor, Training Program, Kailash travel, orbital/GPS-orbit.
- Home: watch cards simply hidden when disconnected.
- Dedicated watch nav pages: **HIDE the nav entry** when no watch is connected (André, 2026-09-02).
- This is NOT demo mode and shows NO fake data.

### 1b. Demo mode — stays an explicit, manual toggle
Demo mode (global fake sample-watch data, for exploring/screenshots) stays exactly as today:
**opt-in only**, never auto-triggered. This avoids the conflict where a global fake-data flag
would flip on during real connect/disconnect flows (e.g. the #3 Sync cable-swap).

### Feature toggles (unchanged from original #1)
Turn the experimental toggles ON by default so normal owners find the features they bought the
watch for: Smart Sensor (HR belt), Intervals (workout builder), App Zone, Coach.
- **Keep OFF by default:** T6 / X6HR and GPS Track Pod (niche, no test hardware).
- Existing users' explicit toggle choices are respected; this changes the default only.

## 2. Activities detail — remove/repair dead tabs 🚧 (desktop done · Android pending)
Approach (André approved data-aware, 2026-09-02): **never show developer text to the user.**
- Tabs are **data-aware per activity**: a tab appears only when THAT activity has that data
  (e.g. Laps tab only if it has laps; Charts only if it has HR/elevation/etc. streams).
- Features with **no working data pipeline yet** are **hidden entirely** (Notes needs local
  storage that doesn't exist) — no empty shell, no dev text.
- Map + Summary always show (every activity has them).
- **Fix the contradictory Upload tab:** show only the working "Export to intervals.icu / Garmin"
  action; remove the "not built yet" fallback.
- **Follow-up issue (deferred):** actually *build* Charts / Laps / Notes. This pass only
  gates/hides them cleanly.

## 3. Sync page — redesign to the simple flow 🚧 (desktop done · Android pending)
Replace the snapshot/freefly/mirror/merge model with André's flow:
1. Plug in the **main watch** → app **backs it up silently**.
2. Plug in **watch B**.
3. App shows **"here's what will be written — confirm"**.
4. Write on confirm.
- Drop the vocabulary: "snapshot", "freefly", "mirror", "two-way merge", "slot A/B".
- **Remove the greyed placeholder chips** (Routes / Sport modes are shown but unfinished) until
  their write paths exist — don't show a button that does nothing.

## 4. Routes — remove "Rehearse (no write)" 🚧 (desktop done · Android pending)
Delete the separate dry-run button. Fold its validation into the normal **"Send to watch"**:
validate first, show the preview, then write. One button.

## 5. Sport Modes — make all edits instant (debounced) 🚧 (desktop done · Android pending)
Remove the visible "unsaved changes" / "Save to watch" model. Autolap/HR/pods already write
immediately. Display/field edits rewrite the whole ~7.5 KB region, so (André, 2026-09-02):
**auto-save debounced** — write once ~1s after the user stops editing, coalescing rapid edits
into a single write. No Save button; show a small "Saving…/Saved" indicator instead.

## 6. Training Program — single "Sync" button 🚧 (desktop done · Android pending)
Collapse "Preview sync" + "Sync to watch" into one **"Sync"** action (drop the preview step and
the rotation-diff internals).

## 7. Totals — reword "More to come!" 🚧 (desktop done · Android pending)
It means more activity types will be added, not that the screen is unfinished. Reword to
**"More activity types coming"** (or similar) so it doesn't read as broken.

## 8. Coach — fix the API-key wording 🚧 (desktop done · Android pending)
Make clear the chat needs an **API key from console.anthropic.com**, which is **separate from a
claude.ai subscription login** (that login does not work here) and bills separately. Reword the
Settings copy and the Coach empty state accordingly.

## 9. Connections — mutualize per service 🚧 (desktop done: data layer already shared via ConnectionsService; added one-tap 'Open Settings → Connections' banners on Gear/Weight/Health · Android pending)
Consolidate so the user signs in **once per service**, and every dependent screen just works:
- **One Garmin connection** → powers Health, Weight (body composition), Garmin activities.
- **One intervals.icu connection** → powers Gear, wellness/Health, planned workouts (Calendar /
  Training Program), Weight.
- Each account-gated screen shows a short **"Connect X in Settings"** banner instead of looking
  broken/empty.

## 10. Silent no-ops — show validation messages 🚧 (desktop: POI coords+name, Weight date done · Training-Program import date + Android pending)
Replace silent reverts with a short message:
- Invalid coordinates (POI add) → "Coordinates aren't valid."
- POI name over limit → "Name can be up to 15 characters."
- Bad date (Weight, Training Program import) → "Enter a date as YYYY-MM-DD."
- Any field that currently just reverts on invalid input.

## 11. Raw backend errors — friendly message + Details + log 🚧 (desktop: shared ErrorBanner improved with expandable Details, applies everywhere it's used · follow-up: convert remaining direct raw-error Texts on Firmware/Home/Gear/Health/Settings/Weight · Android pending)
Never show raw backend text (e.g. `502`, `compile failed`, `lastError`, `CustomModes region`)
front-and-center. For every backend failure:
- Show a **plain sentence** ("Can't reach the app's helper — try reconnecting your watch.",
  "That workout couldn't be built.").
- Offer an expandable **"Details"** with the technical text.
- Still **write the raw error to a log file** for bug reports.

---

### Not changing (decided)
- **General watch jargon** (POI, orbital, pods, autolap, etc.) stays — the audience is Ambit
  owners familiar with it. Only genuinely wrong-word labels are fixed (e.g. #4 "Rehearse").
- **Right-click actions** (delete activity, plan workout, Ember options) stay as-is.
