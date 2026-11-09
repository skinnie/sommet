# Ambit3 Workout Builder on Mac - notes and workarounds

Everything real found while getting the packaged app working on André's actual Mac
(2026-08-06). Companion to `tools/packaging/README.md` (the general build doc) - this one is
specifically the "here's what actually goes wrong on a real Mac and how to get past it" notes.

## Building and installing

```
rm -rf "/Users/andre/Desktop/sommet/tools/packaging/.build-venv"
cd "/Users/andre/Desktop/sommet"
./tools/packaging/build_mac.sh
```

- Produces `dist/mac/Ambit3 Workout Builder.app` and **auto-copies it into `/Applications`**,
  replacing any previous copy there.
- **Always delete `.build-venv` before rebuilding if it came from anywhere other than this
  exact Mac** (copied via pen drive, NAS, another machine, etc.). It's a Python virtual
  environment, not a portable file - it contains binaries built for one specific OS and
  symlinks to one specific machine's Python install. Copying it anywhere else produces
  `bad interpreter: No such file or directory`. The build script recreates it fresh and
  correctly in seconds every time, so there's nothing lost by always deleting it first.
- If you ever manually copy/paste `tools/packaging/build_mac.sh` itself (rather than getting
  it via a normal file copy), run `chmod +x tools/packaging/build_mac.sh` first - pasted/
  downloaded files lose the executable bit.
- Use **absolute paths** for any manual cleanup command (`rm -rf "/Users/andre/Desktop/
  sommet/tools/packaging/.build-venv"`, not a relative one). Relative paths depend on
  which folder Terminal is currently in, and typing the wrong one silently does nothing
  (`rm -rf` on a nonexistent path is not an error) rather than failing loudly - this caused
  real, repeated confusion (looked like the fix wasn't working, when really the delete
  command was just pointed at the wrong place).

## First launch after installing (Gatekeeper)

Unsigned, unnotarized app (no Apple Developer account involved) - macOS blocks a plain
double-click the first time, and it can look like nothing happened at all rather than showing
an obvious error. Right-click the app -> **Open** -> **Open** (once), or **System Settings ->
Privacy & Security -> scroll down -> "Open Anyway"**.

## "Add to SuuntoLink" needs a real macOS permission

The app modifies a file inside another app's bundle (`Suuntolink.app`), which macOS gates
behind a permission separate from Gatekeeper:

1. **System Settings -> Privacy & Security -> App Management** (use **Full Disk Access**
   instead if your macOS version doesn't have an App Management section).
2. Find **"Ambit3 Workout Builder"** in that list and enable it. If it's not listed yet, click
   **+** and add it manually from `/Applications/Ambit3 Workout Builder.app`.
3. **Fully quit the app and reopen it** - a running process doesn't pick up a permission
   grant retroactively. `killall "Ambit3 Workout Builder"` (closing the browser tab alone
   does *not* quit it - it keeps running quietly as a background server), then relaunch.

**This permission can need re-granting after every rebuild.** The app is unsigned, and every
`build_mac.sh` run produces a brand-new binary - macOS sometimes ties the permission to that
specific binary, so a grant from before a rebuild can silently stop applying. If "Add to
SuuntoLink" that used to work suddenly fails again right after a rebuild, remove "Ambit3
Workout Builder" from the permissions list (select it, click **-**), re-add it fresh, quit/
reopen, and try again.

## After a successful "Add to SuuntoLink"

- The app tries to auto-launch SuuntoLink for you. **If SuuntoLink was already open, this
  most likely just brings its window to the front rather than making it re-read the catalog
  file it already loaded at startup.** If a newly added app doesn't show up, fully quit
  SuuntoLink (Cmd+Q) and reopen it.
- **Every click of "Add to SuuntoLink" appends a brand-new catalog entry - it never checks
  whether one already exists.** Retrying after an error (permission issue, etc.) leaves the
  earlier failed-looking attempts behind as real duplicate entries once the retry succeeds.
  Harmless clutter, not a sign of corruption - and safely identifiable/cleanable later if
  wanted, since every entry this tool adds gets a `ruleId` of 90,000,000+
  (`suuntolink_catalog.py`'s `CUSTOM_RULE_ID_BASE`), deliberately far above anything in the
  real official catalog (~13.7 million max) - a cleanup would only ever touch entries this
  tool added, never a real Suunto app.
- The app name shown in SuuntoLink's picker is taken from the workout's own name, not the
  compiler's generic default ("Ambit App") - fixed in `workout_gui.py`'s `/api/compile`
  handler. If you see "Ambit App" for something you just added, check you're not looking at
  an old entry added before this fix (same appends-only behavior as above - old entries never
  get renamed, only new ones use the fixed name).

## Never replace SuuntoLink's `index.json` by hand

Confirmed on real hardware, 2026-08-06: manually swapping in a downloaded/saved compiled-app
JSON as the whole `index.json` breaks SuuntoLink outright ("unknown error" / "apps not
iterable", blank sport-mode screens on the watch) - the file has to stay the full array of
every app SuuntoLink knows about; a compiled app is meant to be *appended*, never used to
replace it. Always use the app's own "Add to SuuntoLink" button, which does this correctly.
If it ever does go wrong, `index_old.json` (written right next to the real file on every
successful use) is the way back - copy it back over `index.json`.

## "First run always crashes/hangs, second run is instant"

Real, reproducible, still unresolved as anything other than a workaround. Confirmed:

- Running the actual binary directly from Terminal (`"/Applications/Ambit3 Workout
  Builder.app/Contents/MacOS/Ambit3 Workout Builder"`) is **instant, every time** - so the
  app itself has no startup bug.
- The app's own crash/error log (`~/AmbitWorkouts/app.log`) has **no entry** for these
  failures - confirms whatever's happening stops the process before the Python code even
  starts running, i.e. it's not something this project's own code could catch or fix.
- Tried `xattr -cr "/Applications/Ambit3 Workout Builder.app"` (strips any quarantine flag)
  as a real fix attempt - **did not help**, behavior unchanged.

Points at something in how Finder/LaunchServices launches an unsigned `.app` specifically
(vs. running the raw binary directly, which always works) - most likely Gatekeeper doing a
silent verification pass on that first Finder-launched attempt. Not confirmed further, and
real code-signing (which would very likely eliminate this) needs a paid Apple Developer
account - out of scope here. **Accepted workaround: just try opening it a second time** if
the first double-click after installing/rebuilding doesn't seem to do anything.

## Quick reference

| Problem | Fix |
|---|---|
| `bad interpreter: No such file or directory` | Delete `.build-venv`, rebuild |
| Double-click does nothing (first time ever) | Right-click -> Open -> Open |
| Double-click does nothing (every first launch) | Just try again - known unresolved quirk |
| "Add to SuuntoLink" - permission/not allowed | System Settings -> Privacy & Security -> App Management -> enable the app -> quit/reopen |
| "Add to SuuntoLink" - `doesn't look like a JSON array` | Don't guess-fix by hand - check `index_old.json`'s real shape first |
| Added app still shows "Ambit App" | Probably an old entry from before the name fix - add a new one and check that instead |
| New app not visible in SuuntoLink | Fully quit (Cmd+Q) and reopen SuuntoLink |
