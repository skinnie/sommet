# Ambit3 Workout Builder on Mac - notes and workarounds

What went wrong while getting the packaged Workout Builder working on a real Mac (first written
2026-08-06), and how to get past it. The general build notes are in
[Packaging](packaging.md) and `tools/packaging/README.md`.

## Building and installing

```
rm -rf "$HOME/Desktop/sommet/tools/packaging/.build-venv"
cd "$HOME/Desktop/sommet"
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
- Use **absolute paths** for any manual cleanup command (`rm -rf "$HOME/Desktop/sommet/tools/packaging/.build-venv"`,
  not a relative one). Relative paths depend on
  which folder Terminal is currently in, and typing the wrong one silently does nothing
  (`rm -rf` on a nonexistent path is not an error) rather than failing loudly - this caused
  real, repeated confusion (looked like the fix wasn't working, when really the delete
  command was just pointed at the wrong place).

## First launch after installing (Gatekeeper)

Unsigned, unnotarized app (no Apple Developer account involved) - macOS blocks a plain
double-click the first time, and it can look like nothing happened at all rather than showing
an obvious error. Right-click the app -> **Open** -> **Open** (once), or **System Settings ->
Privacy & Security -> scroll down -> "Open Anyway"**.

## Using it

The builder makes native guided workouts: "Create Workout" compiles the workout (this needs an
internet connection), and "Install to Watch" adds it to a sport mode's WORKOUT menu over USB.
It does not use SuuntoLink, so it needs no extra macOS permission. Close SuuntoLink before
installing, because it also takes the watch. Created workouts are saved in
`~/Downloads/AmbitWorkouts`.

The older "Create App" and "Add to SuuntoLink" flow is now in the App Zone builder,
`tools/apps_gui.py`, which is not part of this packaged app. If you use that one on a Mac,
"Add to SuuntoLink" changes a file inside `Suuntolink.app`, so macOS asks for the App
Management permission (System Settings > Privacy & Security > App Management, or Full Disk
Access on older macOS). Never replace SuuntoLink's `index.json` by hand with a compiled file:
it must stay the full list of apps, and `index_old.json` next to it is the way back.

## "First run always crashes/hangs, second run is instant"

Real, reproducible, still unresolved as anything other than a workaround. Confirmed:

- Running the actual binary directly from Terminal (`"/Applications/Ambit3 Workout
  Builder.app/Contents/MacOS/Ambit3 Workout Builder"`) is **instant, every time** - so the
  app itself has no startup bug.
- The app's own crash/error log (`~/Downloads/AmbitWorkouts/app.log`) has **no entry** for these
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
| "couldn't read the watch's sport modes" | Watch on its time screen, cable in, SuuntoLink closed |
