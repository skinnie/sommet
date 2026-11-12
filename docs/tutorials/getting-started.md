# Day-One Quickstart — Ambit3 Offline Route Sync

> Historical (August 2026): written on day one of the project and kept as a record. Both
> unknowns below were solved long ago, and the handoff files it names are no longer in the
> repository. For the current state, see the [changelog](../reference/v3-changelog.md) and the
> [compatibility matrix](../reference/compatibility.md).

A one-page orientation for a developer (working with Claude) picking up this project, as it
was written at the start.

---

## What this project is

Put **GPX routes onto a Suunto Ambit3 with no Movescount/Suunto server**, over USB cable and/or
Bluetooth. Three deliverables: **A** = BLE app (iOS-first), **B** = add USB-OTG+BLE to
opensportsync (Android), **C** = openambit desktop full Ambit3 (routes, POIs, sport modes).

---

## What is already PROVEN (build on these, don't re-litigate)

1. **No server, no account is needed to write to the watch.** The Movescount login you may
   remember was for *cloud content fetch only*, and it's dead and bypassable. Confirmed in code
   and by openambit's offline CLI.
2. **Over USB there is no device login at all.** openambit opens the HID device and reads/writes
   directly. (Over BLE there IS a local session login — see "biggest risk" below.)
3. **The route format at the region level is fully mapped** (flash addresses, table sizes,
   record strides) — see full handoff §5.
4. **The transports are fully mapped:** USB HID framing + `0x0b16` writes; NSP protocol; and the
   BLE frame layer is a trivial **20-byte fragmenter** with no delimiter/escape/CRC.
5. **BLE and USB share everything above the frame layer** — one serializer, one NSP layer, two
   thin frame wrappers. Build the serializer once.

## What was NOT yet known then (both since solved)

- ⚠️ **The 12-byte route-point encoding** (relative-coordinate transform). Decodable from
  `libkomposti-ng.so` (`TreeToStruct` + the Ambit3 route path) checked against the **matched
  GPX↔capture fixtures** you have. Full handoff §5.4.
- ⚠️ **The BLE session-login 16-byte token derivation** (BLE only — USB doesn't need it). Best
  recovered by **HCI-sniffing the live Suunto app** connecting to your own watch, not by static
  analysis. Full handoff §2 and §8.3.

Neither blocks the *cable* route write. Both are bounded tasks with a clear recovery path.

---

## The recommended first week

**Do Deliverable C (openambit, cable) first** — it's the easiest to debug and it proves the
route format on real hardware, which then de-risks A and B.

1. **Set up & confirm the offline baseline.** Build openambit from source. Confirm you can run
   the offline route path for an **Ambit1/2** (or read Ambit3 settings via
   `openambit-cli --write-config-json`). This proves your toolchain and the no-server premise.
2. **Wire the empty Ambit3 vtable slot.** In `device_driver_ambit3.c`, `navigation_write` is
   `NULL`. Add a stub `device_driver_ambit3_navigation.c` and confirm the dispatcher reaches it.
3. **Implement the DELETE/RESET path first.** It needs **no** point-format knowledge, is
   byte-identical across two captures, and exercises the whole transport chain. **This is your
   first safe hardware test** — zero the nav headers (`0x005000`, `0x14c080`) + `nav_memory_delete`
   (`0x0b04`). Verify the watch's route list empties.
4. **Finish the 12-byte point encoding (§5.4).** With Claude: decode `TreeToStruct`/EmuDevice in
   `libkomposti-ng.so`, verify against the matched GPX↔capture pairs until you can regenerate the
   captured bytes exactly.
5. **Build the full serializer** (headers + 52-byte descriptors + indexes + 12-byte points) and
   the **simplification pre-pass** (Douglas–Peucker, ≤1000/route — openambit lacks it).
6. **Validate offline, then on hardware.** Regenerate `route12km`/`route128km` from their GPX and
   diff against the captures **before** writing. Then upload to a real Ambit3.

Once the serializer works over cable, **A** (BLE/iOS) and **B** (opensportsync) reuse it — their
only new work is the BLE transport + the session-login token.

---

## Inputs & outputs (canonical formats)

- **Input:** GPX, or the openambit **route-file JSON** (information file + points file — exact
  schema in full handoff §3.2; `gpx2route` produces it for Ambit1/2).
- **Output:** the Ambit3 binary flash layout (full handoff §5), written via `0x0b16`.

---

## Non-negotiable safety rules

- **Dry-run by default.** Log exact bytes, synthesize the ack, touch no BLE/USB until a group is
  explicitly enabled. A wrong body can **reboot or wedge the watch**.
- **Stage in order:** delete/reset → tiny route → large route → multi-route → POIs → sport modes.
- **No oracle exists** (Movescount is dead). The watch accepting and displaying the route is the
  only correctness signal. When a doc disagrees with a fresh capture, **trust the capture.**

---

## Assets you must have (ask for these)

The full handoff (no longer in the repository) · the six pcap captures (`routesmall`, `route12km`,
`route128km`, `sync`, `routedelete`, `poiimport`) · the two **matched source GPX** files ·
`libkomposti-ng.so` (+ its Ghidra C export) · the deobfuscated SuuntoLink JS · openambit &
opensportsync source.

For the BLE login token specifically: the ability to **HCI-sniff your own Ambit3 + the current
Suunto app**.

*(Aside: the modern Suunto app's `libmds.so` is NOT needed for routes — `libkomposti` is the
right source. Keep `libmds.so` only if you later pursue activity-sync / FIT export, future
handoff §16.3.)*

---

## First question to ask Claude on day one

> "Read the handoff and the capture files. Confirm the delete/reset byte sequence
> from `routedelete`, and give me the exact bytes I need to send to empty the Ambit3 route list.
> Do not propose the full serializer yet."

That gets you to a safe, verifiable first hardware write without needing the still-unknown point
encoding.
