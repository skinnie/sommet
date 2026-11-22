# Keep a Bluetooth watch paired on Linux

**Symptom.** Your Ambit3 pairs with Sommet over Bluetooth and syncs. After the watch sleeps, or the next time you press **Sync now**, it won't connect again, and Sommet says *"The watch no longer accepts this computer's pairing"*.

**Why.** When a phone pairs with the watch, it tells the watch its Bluetooth identity, and the watch saves the pairing under that address. Linux doesn't do this when its Bluetooth privacy setting is off, which is the default. The watch then saves the pairing under the address `00:00:00:00:00:00`. The link works until the watch sleeps. After that, the computer reconnects from its real address, the watch finds no pairing for it, and drops the link.

Sommet handles this for you when you pair from the app: it asks for your password once and turns privacy on just for the pairing (see the README, "Linux: pairing a watch over Bluetooth"). If you'd rather not give Sommet your password, use one of the fixes below.

## Fix it once: turn on Bluetooth privacy

The easy way: in Sommet, **Settings → Experimental features → Pair a watch by Bluetooth without
the password**. It does the steps below for you (your system asks for the password once) and
keeps a copy of the file as `/etc/bluetooth/main.conf.before-sommet`. Turning the switch off
puts the setting back. By hand:


1. Check the current setting:

   ```sh
   grep -n 'Privacy' /etc/bluetooth/main.conf
   ```

   A line `#Privacy = off` (commented out) or `Privacy = off` means privacy is off.

2. Turn it on:

   ```sh
   sudo sed -i 's/^#\?[[:space:]]*Privacy[[:space:]]*=.*/Privacy = device/' /etc/bluetooth/main.conf
   grep -q '^Privacy = device' /etc/bluetooth/main.conf || sudo sed -i '/^\[General\]/a Privacy = device' /etc/bluetooth/main.conf
   grep -n '^Privacy' /etc/bluetooth/main.conf
   ```

   The last command should print `Privacy = device`.

3. Restart Bluetooth. Your Bluetooth mouse, keyboard and headphones reconnect within a few seconds.

   ```sh
   sudo systemctl restart bluetooth
   ```

4. Pair the watch again:
    - On the watch: **Bluetooth → Unpair → Forget**.
    - In Sommet: **Forget Bluetooth device** on this watch.
    - On the watch: **Pair → Pair with app**, then type the code it shows into Sommet.

From now on, the watch reconnects by itself after sleep and on **Sync now**.

!!! note "Other Bluetooth devices"
    With privacy on, the computer uses a changing private address for Bluetooth Low Energy. A mouse or keyboard that was paired before the change may stop reconnecting. If one does, pair it again. Classic Bluetooth devices, such as most headphones, aren't affected.

## Only while pairing

To keep privacy off the rest of the time, turn it on just for the pairing. This needs the `btmgmt` tool, which is in the `bluez` package on Ubuntu and Debian and in `bluez-utils` on Arch.

```sh
sudo btmgmt power off; sudo btmgmt privacy on; sudo btmgmt power on
# pair the watch now (step 4 above)
sudo btmgmt power off; sudo btmgmt privacy off; sudo btmgmt power on
```

The watch keeps the computer's real address, so later reconnects work with privacy off. Tested on an Ambit3 Peak (firmware 2.4.17): paired this way, it reconnected without a code after a drop and a computer restart, with privacy off again.

## Undo

```sh
sudo sed -i 's/^Privacy = device/#Privacy = off/' /etc/bluetooth/main.conf
sudo systemctl restart bluetooth
```

Watches paired while privacy was on may then need pairing again.
