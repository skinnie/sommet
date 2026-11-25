# Bluetooth on Linux

Two things worth knowing when Sommet uses Bluetooth on Linux. To set Bluetooth privacy yourself, see [Keep a Bluetooth watch paired on Linux](bluetooth-pairing-on-linux.md).

## Linux: what Bluetooth needs

Two system packages: `python3-dbus` and `python3-gi`. Sommet talks to your system's Bluetooth
(BlueZ) through them, and they belong to the system, so the download cannot carry its own.
Ubuntu, Mint and Debian desktops install them by default. If they are missing, Sommet says so
when you press **Pair**, before it asks for anything:

- Debian, Ubuntu, Mint: `sudo apt install python3-dbus python3-gi`
- Fedora: `sudo dnf install python3-dbus python3-gobject`

The cable needs neither.

## Linux: pairing a watch over Bluetooth

**What you'll see.** Press **Pair** in Sommet, choose **Pair → Pair with app** on the watch, and
type the code the watch shows into Sommet. Nothing asks for your password, and your other
Bluetooth devices stay connected.

**What Sommet does after that.** Linux gives the watch no identity of the computer while it
pairs: only with Bluetooth *privacy* on, and that is off by default. Without it the watch would
refuse the computer the first time the link drops (it sleeps, or goes out of range). So in the
watch's first session Sommet writes this computer's Bluetooth address and an identity key into
the watch's pairing record itself, and changes nothing on the computer. If that doesn't work
over Bluetooth, Sommet tries again on the next link, and Home asks you to plug the watch in by
cable to finish it. Why, and the fallback: [Keep a Bluetooth watch paired on
Linux](bluetooth-pairing-on-linux.md).

**Sommet 0.2.66 and earlier** asked for your password during a pairing, to turn privacy on for
it. That switched Bluetooth off and on twice, and a Bluetooth mouse or keyboard dropped each
time. If you typed that password once, Sommet also copied a script to
`/usr/local/libexec/sommet/` and described it to the system in
`/usr/share/polkit-1/actions/io.github.skinnie.sommet.policy`. That pairing script is not used
any more: the next time you turn **Settings → Experimental features → Keep Bluetooth privacy
on** on or off, Sommet removes it. To remove everything by hand, delete that folder and that
file as administrator.

**Already paired with privacy off before 0.2.67?** That watch keeps the `00:00:00:00:00:00`
pairing, and Sommet only completes new pairings. Pair it once more: on the watch **Bluetooth →
Unpair → Forget**, in Sommet **Forget Bluetooth device**, then pair again as above.

Android phones and iPhones send their identity when pairing (seen in Bluetooth captures of the
Suunto app), so they aren't affected. macOS and Windows haven't been tested with this yet.

## Linux: a Bluetooth mouse or keyboard stops working while Sommet runs

**What you'll see.** With Sommet open and a Bluetooth scale set up (or a watch connected over
Bluetooth), a Bluetooth mouse or keyboard stops responding or won't reconnect. It comes back
when Sommet is closed.

**Why.** To hear the scale and to find the watch, Sommet scans for Bluetooth devices. On older
Bluetooth chips (seen on a ThinkPad X230 with a Broadcom Bluetooth 4.0 chip, BlueZ 5.72), a
scan that never stops leaves no room for the mouse to reconnect. Sommet 0.2.53 listened for
the scale without a break.

**Fixed in 0.2.54.** Sommet now scans in short bursts: about 3 seconds in every 10 for the
scale, and the same for the watch while it isn't connected (none at all once it is). When the
scale starts talking, Sommet keeps listening until the weigh-in is stored. Two ways a scan
could get stuck on are fixed too: while the watch was connecting, Linux's Bluetooth service
sometimes didn't answer, or refused, Sommet's request to stop scanning, and the scan then ran
until Sommet quit. Closing Sommet's connection to the Bluetooth service is what ends a stuck
scan, so the scale listener and the watch scan each have a connection of their own that
Sommet closes and opens again when the service stops answering it. With both running, the
same X230 mouse kept working.

**Changed in 0.2.67.** From 0.2.54 the scale listener closed and reopened its connection after
every burst, about 360 times an hour. The bursts are unchanged. The listener now keeps one
connection for as long as it listens and replaces it only after a burst the service did not
answer; when that keeps happening, it and the watch scan wait longer each time, up to five
minutes, before connecting again.

## Linux: "Bluetooth on this computer is not answering"

Sommet asks the system's Bluetooth service one short question before it pairs a watch and
before it reads a Magene, a Bryton, a Wahoo or a sensor over Bluetooth. If no answer comes
within three seconds, Sommet stops there and says so.

The message means the service (`bluetoothd`) is running but stuck: no app on the computer can
use Bluetooth, and a Bluetooth mouse or keyboard may have stopped too. It is not about the
watch.

**What to do:** restart Bluetooth (`sudo systemctl restart bluetooth`), or restart the
computer, then try again. The cable works meanwhile.

**The one cause seen so far: Wine.** On a ThinkPad X230 (2026-11-23) a Windows program had
been run under Wine days before, and Wine's background service (`winedevice.exe`) was still
running. It listens to the Bluetooth service's signals and, in that case, never read them.
Once the unread signals reached the system's limit (127 MiB), the system stopped listening to
the Bluetooth service, which then hung. Every Bluetooth scan by any app adds signals, so the
more Sommet scanned, the sooner the limit came. When Sommet sees `winedevice.exe` connected to
the system, its message says so: close Wine (`wineserver -k`). On the X230 that alone brought
Bluetooth, and the mouse, back within seconds; restarting Bluetooth is only for when it does
not. You can look yourself with `busctl --system list | grep -i wine`.

**Fewer signals from Sommet (0.2.67).** Once your scale has been heard, Sommet asks the
Bluetooth service to report that one scale only. A 3-second scan then makes the service send
2 signals instead of about 35 (measured on the X230).

**When Sommet listens for the scale (0.2.67, on every desktop system).** While the **Weight**
page is open, and for 5 minutes after Sommet starts. At other times it does not scan at all.
So step on the scale with the Weight page showing, or just after starting Sommet. In
Settings → Connections the scale then reads "On: listens while the Weight page is open", and
"Listening" while it is scanning. Before 0.2.67 it scanned for as long as Sommet was open.

Sommet checks again by itself: at most once every 15 seconds while a page needs Bluetooth, and
fresh each time you start a pairing.
