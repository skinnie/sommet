# Bluetooth on Linux

Two things worth knowing when Sommet uses Bluetooth on Linux. To set Bluetooth privacy yourself, see [Keep a Bluetooth watch paired on Linux](bluetooth-pairing-on-linux.md).

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
until Sommet quit. Sommet now drops its connection to the Bluetooth service after every scale
burst, and the watch scan has a connection of its own that Sommet closes and opens again when
the service stops answering it. Closing the connection is what ends a stuck scan. With both
running, the same X230 mouse kept working.

**If it still happens:**

1. Turn the scale off while you don't need it: **Settings → Connections → Bluetooth scale →
   Manage → Stop listening** (Start listening turns it back on; your weigh-ins are kept).
2. Turn Bluetooth off in Sommet: **Settings → Experimental features → Bluetooth**. The cable
   still works for the watch.
3. If the mouse stays dead after closing Sommet, check nothing is still scanning:
   `bluetoothctl show | grep Discovering` should say `no`. If it says `yes` with Sommet closed,
   a Sommet backend may still be running: `pkill -x Sommet` and stop `python3 server.py`, or
   restart Bluetooth (`sudo systemctl restart bluetooth`).
4. A newer USB Bluetooth adapter (Bluetooth 5) handles scanning and connections side by side
   far better than a 4.0 chip.
