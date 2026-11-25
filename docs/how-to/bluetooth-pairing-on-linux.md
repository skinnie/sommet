# Keep a Bluetooth watch paired on Linux

**Symptom.** Your Ambit3 pairs with Sommet over Bluetooth and syncs. After the watch sleeps, or the next time you press **Sync now**, it won't connect again, and Sommet says *"The watch no longer accepts this computer's pairing"*.

**Why.** When a phone pairs with the watch, it tells the watch its Bluetooth identity. Linux only does this when its Bluetooth privacy setting is on, and it is off by default. Without that identity the watch saves the pairing under the address `00:00:00:00:00:00`, with an empty identity key. The link works until it drops: the watch then hands the pairing to its Bluetooth chip, the chip reads the empty key as "no pairing" and throws the pairing's key away, and the next connection is refused. (Read from the watch's Bluetooth chip firmware and seen on an Ambit3 Peak.)

**What Sommet does, from 0.2.67.** Right after a pairing, in the watch's first session, Sommet writes this computer's Bluetooth address and a new identity key into the watch's pairing record. The watch's own key stays as it was. Nothing on the computer changes: no password, no setting, and a Bluetooth mouse or keyboard stays connected. Tested on an Ambit3 Peak (firmware 2.4.17): after a real link drop the watch came back within a second, with no code.

If that write doesn't work over Bluetooth, Sommet tries again the next time the watch links, and Home says *"The watch may not reconnect to this computer by itself. Plug it in by cable and Sommet will finish setting it up."* Plug the watch in and Sommet does it over the cable.

Sommet 0.2.66 asked for your password during a pairing instead, to turn privacy on for it. That switched Bluetooth off and on, so a Bluetooth mouse or keyboard dropped for a moment each time.

**The Kailash isn't covered yet.** Its settings are laid out differently, so Sommet writes nothing to it, and Home says *"Sommet can't finish pairing this watch model by itself yet..."*. Turn on the fallback below before you pair a Kailash on Linux. Not yet tested on the Traverse or the Traverse Alpha either. If a watch still forgets this computer after pairing, use the fallback below. If Home says *"The watch's pairing with this computer is incomplete"*, press **Forget**, then pair the watch again. A watch paired with privacy off before 0.2.67 keeps its broken pairing. Pair it once more (on the watch **Bluetooth → Unpair → Forget**, in Sommet **Forget Bluetooth device**, then **Pair → Pair with app** on the watch), and Sommet completes the new one.

## Fallback: turn on Bluetooth privacy

The easy way: in Sommet, **Settings → Experimental features → Keep Bluetooth privacy on**. It does
the steps below for you (your system asks for the password once) and keeps a copy of the file as
`/etc/bluetooth/main.conf.before-sommet`. Turning the switch off puts the setting back. By hand:


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
