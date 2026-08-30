Desktop/Android Parity means:
- Design wise
- Feature wise: except if a feature is not compatible (Example: Suunto T6/X6 and GPS Pod Support)


Testing features:
- Not only launch the app and inspect visually but compare the data vs the data on the watch
- Regarding testing, visually the function should render as the Ambit 3 (example: sports modes with icons, acitvities with icons, etc)
- If a function is not 100% functional, or identical to ambit 3, do the necessary to be, don't wait for my ok. 
- If I say I am away, and you are testing, you have my ok to compile as much times it is needed to arrive to a correct debug
- When a feature is complete and tested (or have my ok) get up a 0.0.1 on the version on the tested platform and note the feature on the changelog
- If a feature depends on a suunto watch, ask to plug each of the models to test: ambit 3, kailash, ambit 3, unless I tell you to no do it.
- Whatever tasks you can do without human interaction, do it, automatize it.If a protocol worked write it down here.

Design:
- Design should be replicated from desktop: colors, buttons, types of letters, capitalization of letters, etc
Spacing should also be respect and uniform across pages
- Design should be adaptable to desktop 720p, 1080p and higher, tablet landscape mode, tablet vertical mode, smartphone vertical mode, smartphone landscape mode
- If we change some design on desktop replicate it for android. they should have parity

Reverse Engineering:
- ALWAYS STICK TO THE SNIFFINGS. The packet captures (.pklg / decoded sessions) are the source of
  truth for any protocol question — byte order, who speaks first, chunk sizes, MTU, encryption.
  Decode and read the real capture before changing protocol code; never infer the wire behaviour
  from comments or guesses. (Proven 2026-08-30: the iOS BLE handshake was fixed in minutes once
  the sniff showed the phone must send the 0x0000 opener first.) Tools: tools/ble_pklg.py +
  tshark (Wireshark). To inspect live device state, pull the app's DB/files off the phone with
  `xcrun devicectl device copy from` and read them directly rather than guessing.
- Unless I tell you so, don't search online. Everything needed is on our assets folder
- If I tell you to challenge, find another angle, don't take conclusions from written .md files, get back to assets folder and go deeper on materials.
- If I ask to revive a certain feature, don't give me workarounds, unless I tell you so
- Suunto watches for this project  don't reboot
- Suunto watches for this project  don't need "refreshing"
- Suunto ambit 3 family watches + kailash + Traverse + Traverse alpha for this project use the following hardware/have the following specs: Atmel ATSAM4E16E-family candidate
ARM Cortex-M4 / M4F, up to 120 MHz 128 KiB Main sram 1 MiB internal flash + Nordic nRF51822, Cortex-M0, typically 16 KiB RAM / 256 KiB Flash for BLE Operations. Reverse engineer according and engineer according. 
- Suunto Ambit 1 and 2 family use even older/lower end hardware, most likely ARM Cortex M3, no bluetooth. ANT+ only.
- Originally sunto kailash only connected to ios 7r. Now to suunto link with very limited features (no import of activity, no travel log etc). And to linux, and android with sommet app)
- Movescount was the original cloud/app that managed suunto ambit, which is now dead. Replaced by suunto app for android and ios (lost route sync, poi sync, training plans, scheduled activities, complex workouts, settings, sports modes) and suunto link for pc (to manage settings,sports modes, and apps).
- Suunto Ambit 2 vs 2s vs 2r: 2 is the full feature version, 2s looses barometer, 2r looses different pod pairing feature and is from stock stripped down toward running only. The same for Ambit 3 Peak vs 3s vs 3r
- Suunto Traverse vs Traverse alpha
- Suunto Kailash doesn't have routes, pois, apps, custom sports modes.
 


General
- Never be negative, saying it is too much work, either do it, or tell me an estimate ammount of time, credits and best model effort for this job
- Never ask to stop, that is too much etc, continue working
- Don't resume achievements unless I tell you so
- For every missing task, open issue, please make it like plan mode: number, description, and keep it in memory so we don't loose track
- For everything we use from other's github, please add it in credits and readme
- Always verify the legality of what we are doing
- Always check if we are acting according the licenses of the projects we are based/inspired on



