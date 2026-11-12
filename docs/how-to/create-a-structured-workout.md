# Create a structured workout

Sommet puts interval workouts into your watch's own **WORKOUT** menu, the guided-workout
feature Movescount used to provide. While you run it, the watch shows the target band (heart
rate, pace, speed or power) and each step's text, and beeps at every step change.

It works on **Ambit3** watches, and has been tested on the Ambit3 Peak. It needs:

- the watch plugged in with its USB cable, or connected over Bluetooth, **on its time screen**
  (not in a menu, not recording);
- an internet connection for one step: the workout is compiled by the community Ambit Apps
  compiler. Everything else works offline.

There are two ways in. Use the **Workout Builder** for a workout you'll pick yourself, any day.
Use the **Training Program** to plan workouts on dates.

## Workout Builder (desktop)

1. In Sommet, open **Apps** and press **Open Workout Builder**. It opens in your web browser.
2. Give the workout a name. Keep it short: the watch keeps the first 28 characters.
3. Add the steps with **+ Add step**. For each step, choose:
   - its kind: warm-up, interval, recovery or cool-down;
   - when it ends: after a time, a distance, a lap press, an amount of energy, or when your heart
     rate goes above or below a value;
   - an optional target: heart rate, pace, speed or power, with a minimum and a maximum.

   To repeat a block, put it between **+ Start repeat** (set how many times) and
   **+ End repeat**.
4. Press **Create Workout**. After a second or two the page says the workout is ready, and a
   copy is saved in your Downloads folder, under `AmbitWorkouts`.
5. Press **Install to Watch**, pick the sport mode it belongs to (for example Running), and press
   **Add to WORKOUT menu**.

On the watch: go into that sport mode, **hold [Next] for 3 seconds**, choose **WORKOUT**, pick
the workout, and start recording with **[Start Stop]**.

Every workout you create is kept under **History** at the bottom of the page (in this browser),
so you can load it into the editor again or install it later.

## Training Program (desktop and Android)

1. Open **Training Program** and click a day.
2. Build the workout in the window that opens, the same steps as above, and save it.
3. Press **Sync to watch**. Your next workouts go onto the watch, up to five at a time, named by
   weekday and date (for example "Tue 20/10"). Workouts dated before today are removed.

With **Update the watch when it's plugged in** ticked, this happens by itself each time you
connect the watch.

### On the day

Go into the sport mode (for example Running), **hold [Next] for 3 seconds**, choose **WORKOUT**,
pick the day's workout (for example "Wed 28/10 W9 Workout") and start recording. You get the step
screens, the light and the melody at each step, and "Workout ended" at the finish, on any
Ambit3. A normal start without picking a workout stays a free run, every day.

### "Training day on the watch" (optional, off by default)

With this switch on, starting the sport on a training day runs that day's workout by itself, and
each workout also gets a card under TIME → [Next]. Two trade-offs:

- On stock firmware the workout runs **silently**: step screens and light, but no melody and no
  "Workout ended". The melody on this path needs a firmware patch.
- On a training day the sport is no longer a free run: any start runs the day's workout.

Leave it off unless you want both. See
[Training workouts: stock vs patch](../reference/training-workouts-stock-vs-patch.md) for why.

On Android, each workout is compiled on the community compiler's website in your browser: the
app gives you the text to paste, and you import the result back. The Android app makes no
automatic compile call.

## If something goes wrong

**The page stays on "creating workout...".** Versions before 0.2.50 did this when Sommet couldn't
save its copy of the workout, for example when your Downloads folder has another name (a Linux
system in another language) or lives in OneDrive. Since 0.2.50 the builder saves to your real
Downloads folder and shows an error instead of waiting. If you still see it, update Sommet and
restart your computer: an older copy of the builder can keep running in the background.

**"couldn't read the watch's sport modes - is it connected and on the time screen?"**
- Put the watch on its time screen and check the cable.
- Sommet lends the watch to the builder by itself since 0.2.50. On an older version, close
  Sommet completely (and its watch helper, if you started that from a terminal), then press
  Install to Watch again.
- Close SuuntoLink if it's running; it also takes the watch.

**"The WORKOUT menu is full".** The watch shows five workouts. The page offers to replace one;
nothing is installed until you choose.

**"Testing mode is on, so this page can't use a watch".** Turn Testing mode off in Settings to
use your own watch.

**The workout doesn't show in the WORKOUT menu.** Go into the sport mode you picked when
installing: that's the mode Sommet set up for workouts (it adds the workout screen there).
