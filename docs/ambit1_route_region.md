# Ambit1/2 route region

Where routes live on the legacy family, how the bytes are laid out, and how much fits.

Sources, in the order they were trusted: **André's own SuuntoLink capture**
(`assets/pcap/2026-08-23-ambit1-suuntolink/zzambit1full.pcap`) and **the watch itself**.
openambit was used only as a cross-check on field names — where the two disagreed, the
hardware won, and it did disagree (see *The trap* below).

Hardware: André's Ambit1, Bluebird, serial `1614984607001600`, fw 2.5.7. Confirmed 2026-08-27.

## Location

| | |
|---|---|
| Region base | `0x041EB0` |
| Points start | `0x041EB0 + 2432` (`0x042830`) |
| Read command | `0x0b17`, `[u32 address][u32 length]`, 512 B at a time |
| Write command | `0x0b16`, `[u32 addr][u16 len][u16 seq]` then payload |

The read has to go through **libambit's transport**, not this project's Python `write_nav.py`.
That speaks the Ambit3 dialect, and on a Bluebird *every* command through it returns empty —
`device_info` and `status` included, not just `0x0b17`. Hence `ambit_legacy_cli flash-read`.

## Layout

```
offset 0      head           32 B
offset 32     route_info     48 B x 50 slots   <- FIXED 50, always
offset 2432   routepoints     8 B x N
```

**head** — `u16 magic = 0x3008`, `u8 0`, `u8 1`, `u16 route_count`, `u16 0`,
`u32 routepoint_count`, `u16 checksum`, then 18 B of zero.
The checksum is CRC16/CCITT-FALSE over the **used** info entries plus the points — the 50-slot
padding is not covered.

**route_info** — `char name[16]` (no terminator when full), `u32 routepoint_start_index`,
`u16 routepoint_count`, `u32 distance` (metres), `i32 latitude`, `i32 longitude`,
`i32 max_x_axis_rel`, `i32 max_y_axis_rel`, `u16 0xFFFF`, `u16 0xFFFF`, `u16 0`.

The `latitude`/`longitude` are the route's **bounding-box centre**, not its first point.

**routepoints** — `i32 x`, `i32 y`. Not coordinates, and not degree offsets: signed **metre**
distances from that centre, x east, y north, via haversine on a sphere of radius 6367 km.

Point blocks are **not** stored in route order. In the capture the start indices are
336 / 0 / 1188 — route 2's points come first.

## The trap

Reassembling the capture's writes by concatenation is wrong and fails silently.

SuuntoLink writes head+info in one 176-byte packet at `0x041EB0`, then jumps to `0x042830`.
That is a real 2,256-byte gap — the rest of the fixed 50-slot table. Closing it shifts every
point, and the result still decodes to coordinates a few hundred metres from the truth, which
looks entirely plausible on a map. Reading the region off the watch is what exposed it.

The same shape of mistake one level down: reading the point units as millimetres instead of
metres collapses a route thousandfold toward its own centre and *also* yields believable
coordinates. The check that catches both is decoding a known route and seeing whether it lands
on the real place — "Gare du Nord to" starts at `48.88097, 2.35613`, which is Gare du Nord.

## Capacity

Probed read-only on the watch, 2026-08-27:

| address | content |
|---|---|
| `0x045D28` (end of route data) → `0x060000` | all `0xFF`, erased |
| `0x0704E0` | GPS SGEE orbit data begins (matches the capture) |
| `0x080000` | real data |
| `0x0A0000` | read fails — past addressable flash |

So the route region runs from `0x041EB0` up to at most `0x0704E0`, i.e. **189,488 bytes**, of
which 2,432 is the table. That is an upper bound of ~23,000 route points, and the table caps
routes at **50**.

For scale: the three routes on André's watch — one of them a 128 km, 852-point tour — take
**15,992 bytes, about 8% of the gap**. Route capacity is not a constraint on this family, and
the concern that a large route would not fit does not arise. Addressable flash reaches past
`0x080000` but not `0x0A0000`.

## Status

Reading is done and wired on **both platforms**, each confirmed against this same watch:

- desktop — `tools/legacy_route.py` (parse/build), `legacy_link.py routes`, `/api/nav`
- Android — `src/services/LegacyRoute.ts` over `readLegacyRegion()`, the same `0x0b17`
  512-byte legacy read the sport-modes page already used

Both replace the A/B waypoint markers that routes used to be inferred from. The difference is
not cosmetic: the reconstruction reported Grand Tour HDF as **9 points / 60.94 km**, because
it only ever saw the named turn-points and measured straight lines between them. The region
gives its real **852 points / 128.72 km**.

Verified on the tablet with the Ambit1 on USB OTG, 2026-08-27 — all three routes, point
counts and distances identical to the desktop read, and the Paris route draws along the
actual street network (Porte Dauphine, Colline de Chaillot, Bois de Boulogne, St-Cloud).
That last check is the one that matters: a wrong scale or anchor still yields plausible
numbers, but the line stops following roads.

Writing is **not** done. openambit's writer produces bytes that match this capture, and the
region can now be read first — which is what makes a safe read-modify-write possible rather
than the whole-region replace that wiped two of André's routes over BLE on 2026-08-11. It
still needs his explicit go-ahead, a region backup, and a dry-run before any real write.
