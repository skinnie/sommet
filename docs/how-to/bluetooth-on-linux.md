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

## Linux: pairing a watch over Bluetooth (the password prompt)

**What you'll see.** The first time you pair a Suunto watch with Sommet over Bluetooth on
Linux (**Pair another device by Bluetooth → Suunto watch**), your system asks for your
password once, with the usual administrator prompt. A notification says why just before it,
and the prompt names `sommet-bluetooth-privacy`. Your Bluetooth mouse, keyboard or
headphones may disconnect for a second, then come back.

**Order matters:** type the password **first**, and only then choose **Pair → Pair with app**
on the watch, and type the code it shows into Sommet. The watch only offers to pair for a
short time; if you start it on the watch before the password, it can run out, and you
choose Pair with app again.

**Why Sommet asks.** When a phone pairs with the watch, it tells the watch its Bluetooth
identity, and the watch saves the pairing under that address. Linux only does this when its
Bluetooth *privacy* setting is on, and it is off by default. With privacy off, the watch saves
the pairing under the address `00:00:00:00:00:00`. Everything works until the link drops
(the watch sleeps, or goes out of range). From then on the watch refuses the computer, and
you'd have to pair again every time. Sommet says so with *"The watch no longer accepts this
computer's pairing"*.

**What Sommet does with your password.** Changing privacy needs administrator rights, so
Sommet runs one short script as root through `pkexec` (`tools/bt_privacy.py`). It:

1. turns Bluetooth privacy on, unless it already is. This switches Bluetooth off for about
   a second.
2. waits while the watch pairs and syncs.
3. turns privacy off again, the same way, once the watch's link drops by itself. It also
   turns it off if you stop Bluetooth in Sommet, if Sommet quits, or after 30 minutes at
   most. If privacy was already on, Sommet leaves it on.

The watch keeps the address it saved while privacy was on, so it reconnects later with
privacy off: tested on an Ambit3 Peak, through a link drop and a computer restart. Nothing
in `/etc` is changed, and a restart of the computer or of Bluetooth also resets privacy to
your setting.

**When Sommet doesn't ask.**
- A watch is already paired with this computer: that's a reconnect, not a pairing.
- `pkexec` or `btmgmt` isn't installed (`btmgmt` is in `bluez` on Ubuntu and Debian, and in
  `bluez-utils` on Arch).
- You cancel the prompt. The pairing still works, but the watch won't reconnect after the
  link drops.

**Doing it yourself instead.** If you'd rather not give Sommet your password, turn privacy on
once in `/etc/bluetooth/main.conf`, or only for the pairing with `btmgmt`. The commands, and
how to undo them, are in
[Keep a Bluetooth watch paired on Linux](bluetooth-pairing-on-linux.md).

**Already paired before this?** A watch paired with privacy off has the `00:00:00:00:00:00`
pairing. Pair it once more: on the watch **Bluetooth → Unpair → Forget**, in Sommet
**Forget Bluetooth device**, then pair again as above.

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

Sommet asks the system's Bluetooth service one short question before it pairs a watch, before
it asks for your password, and before it reads a Magene, a Bryton, a Wahoo or a sensor over
Bluetooth. If no answer comes within three seconds, Sommet stops there and says so. It does
not ask for the password and does not switch the adapter off and on.

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
the system, its message says so: close Wine first (`wineserver -k`), then restart Bluetooth.
You can look yourself with `busctl --system list | grep -i wine`.

Sommet checks again by itself: at most once every 15 seconds while a page needs Bluetooth, and
fresh each time you start a pairing.
