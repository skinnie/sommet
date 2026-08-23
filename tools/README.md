# Ambit3 navigation database analysis tooling

Decodes the USBPcap captures of SuuntoLink writing routes to a Suunto Ambit3, and
verifies the binary format byte by byte against the source GPX files.

```
./tools/selftest.py                        # non-regression, the whole corpus
./tools/decode_route.py CAPTURE            # dump + self-checks
./tools/decode_route.py --sequence CAPTURE # chronological command sequence
./tools/regen_route.py CAPTURE --from-gpx GPX [--route N]
./tools/build_route.py --compare CAPTURE GPX [GPX...]
```

`build_route.py` builds the entire navigation database from the GPX files alone and diffs
it byte for byte against a capture, packet sequence included. Four values are not derivable
from a GPX and are taken from the capture: `distance`, `ascent`, `descent` and the route
timestamp. Everything else is computed.

The captures are not versioned (see `.gitignore`): they are recordings of personal
hardware, and they carry the watch serial number.

## Modules

| File | Role |
|---|---|
| `ambit_pcap.py` | pcap -> reassembled HID messages -> sparse flash image |
| `ble_pklg.py` | Apple PacketLogger (.pklg) BLE capture -> reassembled NSP messages, via `tshark` |
| `ambit_format.py` | structures, CRC, region hash, relative geometry |
| `decode_route.py` | dump of a capture with self-checks |
| `regen_route.py` | reserialization, then comparison against the source GPX |
| `ambit_simplify.py` | SuuntoLink's Douglas-Peucker |
| `build_route.py` | GPX to navigation database, and comparison against a capture |
| `c_reference.py` | confronts the C serializer in `csrc/` with this reference |
| `hid_roundtrip.py` | re-encodes the outgoing messages and compares them to the captures |
| `write_nav.py` | writes to the watch, or simulates; dry-run by default |
| `sbem_schema.py` | SuuntoLink schema dictionary, names the SBEM payloads |
| `custom_modes.py` | decodes CustomModes (sport modes): settings, displays, multisport, app rules |
| `sport_mode_manage.py` | creates/deletes sport modes and builds, edits and deletes multisport combos; enforces the real limits (10 modes, 2 combos, 2-6 legs, transitions cost a slot) and replays `removeandaddsportsmodeandmultisport` byte-exact with `--selftest` |
| `custom_modes_write.py` | encodes CustomModes' BXml body (inverse of `custom_modes.py`), verified byte-for-byte against real live watch data - see `V3_CHANGELOG.md` |
| `custom_modes_writeback_test.py` | real-hardware-confirmed write of the CustomModes region (chunk writes + padded hash + commit) - see `V3_CHANGELOG.md` |
| `apps.py` | decodes the Apps flash region (installed Suunto App bytecode), IDs against SuuntoLink's catalog |
| `exercise_log.py` | decodes ExerciseLog (recorded moves) to GPX and FIT; reports each move's logged Suunto App outputs (ruleoutput1..5) |
| `app_logging.py` | turns a Suunto App's logging on/off (EXERCISE_MODES_RULE `LogRule`) - revives the Movescount feature of recording an app's output into the Move; a single-byte CustomModes edit, dry-run by default. Firmware emission of ruleoutput still needs a real recorded-move confirmation |
| `sgee.py` | writes GPS/GLONASS orbital data (AGPS) to GpsSGEE |
| `device_info.py` | model/serial/firmware/hardware version + live battery charge, verified on real hardware - see `V3_CHANGELOG.md` |
| `firmware_check.py` | asks Suunto's real device-info service for the latest firmware + a real download link, given a model/hw version (or read live) - see `V3_CHANGELOG.md` |
| `firmware_flash.py` | decodes/replays the firmware-update wire protocol against a downloaded firmware container; READ-ONLY, no `--write` - two real unknowns (bootloader-entry trigger, the 0x0e00 header's 8-byte prefix) still block a real install, see the module's own docstring |
| `training_program.py` | EXPERIMENTAL - writes a TrainingProgram item; no real capture to verify against |
| `workout.py` | generates App Zone source for a structured workout, compiles it via the live community compiler; portable, no OS/GUI dependency |
| `workout_gui.py` | local browser GUI (wanarun.net-style step builder) on top of `workout.py`; packaged as a standalone desktop app, see `packaging/README.md` |
| `suuntolink_catalog.py` | finds and safely appends to SuuntoLink's own `suunto-apps/index.json`, the documented real install path (`forum.suunto.com/topic/7592`) |
| `workout_install.py` | EXPERIMENTAL, CLI-only - this project's own Apps/CustomModes flash writer; byte-exact encoding proven, but installed apps show an unresolved real "app error" on hardware, paused (`training_program_andre.md` Finding 19) |
| `selftest.py` | chains everything over the whole corpus |

## Writing to the watch

```
./tools/write_nav.py reset                       # simulates, nothing is emitted
./tools/write_nav.py reset --compare CAPTURE     # checks against a capture
./tools/write_nav.py route GPX --meta CAPTURE
./tools/write_nav.py reset --write               # ACTUALLY EMITS
```

A navigation write **erases the watch's POI store**, confirmed on hardware 2026-08-04. So the
sequence follows SuuntoLink's: `0x0b24` reads the complete POI list before the writes, and
`0x0b25` puts it back after the `0x0b04` commit. The watch reports one SBEM entry per POI and
the write concatenates them into one, in the reverse of the order read, which on `routedelete`
is also SuuntoLink's order, most recently modified first. Reversing rather than sorting needs
neither the schema nor any decoding of a POI's insides, and reproduces the capture byte for
byte. `poiimport` puts a newly added POI first and the rest in that order, which is how to add
one rather than only preserve them.

Without `--write`, not a byte goes out. `--compare` mode proves the simulated payloads are
identical to SuuntoLink's: they are for `routedelete`, 5 payloads, and `route12km`, 13, down to the 4-byte
word at offset 4 of the `0x0b18`, which is supplied by the application and remains
unidentified.

`--write` needs the `hid` Python module. Two different packages import under that name and
their APIs differ: PyPI `hid` exposes `Device(path=...)`, while cython-hidapi, which Debian
and Mint ship as `python3-hid`, exposes `device()` plus `open_path()`. `open_hid()` accepts
either, so either packaging works and the reader is not told to prefer one. Consequences for
anyone touching that code: keep the `read()` timeout positional, the keyword differs between
the two, and keep wrapping reply slices in `bytes()`, since one returns bytes and the other a
list of ints.

The `settings` action is the exception, and it is read-only: it sends the `0x1100`
query, four zero bytes, which is what SuuntoLink sends on every connection, and
decodes the reply through the schema dictionary. It therefore needs the cable but
takes no `--write`, and refuses one. Its point is `WhitelistedBleDevices`, the
watch's BLE pairing bond, which is step 1 of the BLE milestone in `HANDOFF.md`.

```
./tools/write_nav.py settings                    # reads the watch, read-only
./tools/write_nav.py settings --from CAPTURE      # decodes a capture, no watch
./tools/write_nav.py settings --all               # every entry, not just bonds and pods
./tools/write_nav.py settings --redact            # mask keys and MAC, safe to send
```

`pois` and `logbook` are the same shape and equally read-only. `pois` sends the `0x0b24` and
lists the watch's POIs with all ten fields; `logbook` sends the `0x1200` and lists the
activities, 47 named fields each including `MemArea.StartAddress1/EndAddress1`, the flash
range of every move. Both take `--from CAPTURE` and `--all`.

`logbook` returns one page. The watch pages a long list, newest first, and the continuation
cursor sits in the reply prefix - `poiimport`'s second request carries `0218 0000`. Paging is
not implemented, because a run made to look at the newest activity does not need it; the count
is in the reply, so a truncated list is visible rather than silent.

```
./tools/write_nav.py pois                        # the watch's POIs
./tools/write_nav.py logbook                     # the activities, first page
./tools/write_nav.py nav                         # the navigation database, off the watch
./tools/write_nav.py nav --save backup           # and keep the raw regions
```

`nav` is the read path `HANDOFF.md` asked for and never had. `0x0b17` takes
`[u32 address][u32 length]` and answers with the same eight bytes then the data, 1024 at a
time as SuuntoLink does in `ambit3full`; `nav` walks the `Waypoints` and `Routes` regions that
way and decodes them with the same structures the serializer writes. It skips `GpsSGEE`, which
is ephemeris and nothing to do with navigation.

Two things follow. The read is **self-validating**: the regions carry their own CRC over the
descriptors and the points, so a matching CRC proves the bytes came back intact - it holds on
all six captures that contain a database, and an empty one is the documented special case where
the field is a literal zero rather than the CRC of nothing. And `--save` makes a real backup
possible, which matters before any write.

Without the descriptor the command cannot name the entries, and therefore cannot tell a
paired watch from an unpaired one: it says so and exits non-zero rather than reporting an
absence of bonds it cannot actually see. That false negative was hit for real, against a
capture that does carry a bond.

`--redact` replaces `MAC`, `IdentityResolvingKey`, `EncodingKey` and `EncodingRnd` with a
length and an eight-character digest, which still tells two reads apart or matches them.
Use it for anything that leaves the machine.

The reassembly of a long reply is exercised by `--from`, which replays the 589-byte
`0x1100` of `ambit3full`. The live read path did run against the watch on 2026-08-03; the
write path never has.

`tools/sgee.py` writes GPS/GLONASS extended ephemeris (AGPS orbital data) to the `GpsSGEE`
region `nav` skips. A separate file, not a `write_nav.py` action - it only imports `Link`/
`send_plan`/the memory-map check from there for transport, matching `custom_modes.py`/
`apps.py`/`exercise_log.py`'s pattern (it started as a `write_nav.py` action and was split
out for consistency, 2026-08-05). Confirmed byte-exact against a real capture, 2026-08-05
(`assets/ambit3 pcap/orbitsync`, a full sync session with routes/POIs/logs mixed in, not an
SGEE-only recording): the wire format is a 4-byte little-endian length prefix followed by the
raw ephemeris bytes, in the usual 1024-byte `0x0b16` chunks, closed by one `0x0b18` whose hash
covers only the written bytes (`GpsSGEE`'s `HASH_WRITTEN` mode, not the whole padded region
`Routes`/`Waypoints` use) - and, unlike those two, no `0x0b04` commit afterward, confirmed
absent from the capture. `--compare` is address-scoped rather than whole-capture
(`compare_sgee_with_capture`), since `orbitsync` carries far more than the SGEE write alone.

```
./tools/sgee.py FILE                          # simulates, nothing is emitted
./tools/sgee.py FILE --compare CAPTURE         # checks against a capture
./tools/sgee.py FILE --write                   # ACTUALLY EMITS
```

`FILE` is a raw ephemeris blob with no wrapper of its own - what SuuntoLink itself caches on
disk, confirmed byte-identical to `assets/WIndows apps/Suuntolink/sgee.7d`. A live, free
source exists and needs no account: `GET https://devices.suunto-operations.com/devices/{gps,
glonass}orbit/binary`, found via `production.json`'s `serverUrl` and confirmed
unauthenticated and current (2026-08-05) - unlike the rest of that host's API, which returns
`401` without the `appKey` also in `production.json`. Real orbital data goes stale after
roughly one to a few weeks; refetch periodically. GLONASS applies only to Traverse, Traverse
Alpha and Ambit3 Vertical - not this project's reference Ambit3 Peak, and not verified against
real hardware here for that reason, not because the endpoint doesn't work.

`0x0b15` (`gps_orbit_head`, printed by `sgee` on a live run) is a 9-byte status decoded from a
real before/after: `[u8 valid][u16 LE year][u8 month][u8 day][u32 LE seconds-since-midnight
UTC]` - confirmed 2026-08-05 by writing fresh data and reading back the exact embedded
timestamp of the source file. Full derivation: `sgee_andre.md`.

Frame encoding is verified by round trip: the 4724 outgoing messages of the 9 captures,
that is 47117 reports of 64 bytes, are re-encoded identically. Established along the way:
`send_recv` and `format` are independent, contrary to what openambit's `legacy_format`
parameter suggests by deriving both from a single integer, the firmware capture showing a
`0x0102` with `send_recv=1` and `format=9`.

## C serializer

`csrc/device_driver_ambit3_navigation.{c,h}` is written to drop into openambit's
`src/libambit/` unmodified: it depends only on `crc16.h` and `sha256.h`, both already
present. The transport layer is left outside, which makes serialization testable without a
watch: `ambit3_navigation_plan()` produces the list of writes, and the caller passes them to
`libambit_pmem20_data_write` then to `ambit_command_data_tail_len`.

```
make -C csrc        # builds the harness
make -C csrc test   # compares its output to the Python reference and to the captures
```

Result: the 10 and 20 `0x0b16` packets of `route12km` and `route128km` are reproduced byte
for byte, as are both closing hashes, and the reset plan matches the hashes of `routedelete`
and `poiimport`.

## Verified format

Reference watch: `Emu` (Ambit3 Peak), fw 2.4.17, hw 70.2.17414.

Regions, as the watch declares them in its `0x0b21` response:

```
Waypoints  0x005000  16384      header 6 B, descriptors 52 x 100 @ 0x005020
Routes     0x14c080  130000     header 32 B, descriptors 52 x 50 @ 0x14c0a0
                                points 12 x 10000 @ 0x14cac8
                                route index 20 x 50 @ 0x169f88
                                waypoint index 4 x 100 @ 0x16a370
```

Point record, **fixed stride of 12 bytes**:

```
0  i32 x_rel        metres east of the bbox centre, signed
4  i32 y_rel        metres north of the bbox centre, signed
8  u16 altitude     metres; 30000 = no data
10 u16 rel_distance 0 at the first point, 65535 at the last
```

Formula, solved by fitting then verified on 1188 points:

```
R    = 10800 * 1852 / pi = 6366707.0195
x    = +/-round( R * cos(mid_lat) * (lon - mid_lon) )      angles in radians
y    = +/-round( R * (lat - mid_lat) )
alt  = trunc(<ele>)
reld = trunc( round_to_4_significant_digits(travelled / total) * 65535 )
```

This is not openambit's `distance_calc()` (haversine, R = 6367 km, truncation), which
reproduces only 124/336 then 508/852 points.

Waypoint descriptor tail (last 12 bytes out of 52):

```
0  u16 magic = 0x0771
2  u8  month, day, hour, minute, second  -- last modification date of the ROUTE,
7  u8  rank of the waypoint in its route    without the year
8  u8  type = 17 ("Waypoint")
9  3 zero bytes
```

**A route needs at least one attached waypoint to appear in the watch's own Navigation menu at
all.** Confirmed on hardware 2026-08-04: a route written with 0 waypoints reads back
self-consistent (both CRCs match) but never shows up in Navigation, regardless of otherwise
valid `distance`/`ascent`/`descent`/timestamp; the same route with one waypoint attached shows
up fine. Every route in every capture this project has ever examined already had one, which is
why `build_route.py`'s `stamp_from_capture` hard-requires a match to recover a route's
timestamp - but the display requirement itself was only established live, not merely inferred
from the captures. `route_from_gpx` (`build_route.py`) now synthesizes a `Start`/`End` pair
from the track's own first/last points when the source GPX supplies no `type="Waypoint"`
entry, so this cannot silently recur. Note real Komoot/Suunto exports mark a route's start/end
as `type="Begin"`/`type="End"`, not `type="Waypoint"` - those are a distinct GPX convention and
are not treated as on-device waypoints by this project's tooling.

Route index entry (20 bytes):

```
0  u32 route number, starting at 1
4  u32 timestamp in seconds, same clock as the date above
8  u32 415, constant
12 u8  index of the first waypoint, u8 waypoint count
14 u8, u8 zero except in one observed case
16 u32 0
```

SBEM0102 payload of the `0x0b25`: entries `[u8 id][u8 len][data]`, with a u32 extension when
`len` is `0xff`. Entry `0x55` is the watch's complete POI list, disjoint from the route
waypoints of the binary region.

A POI record is ten fields and **no altitude**: the schema has none, the Kailash's 2.0.5
descriptor declares the same ten, the 52-byte binary waypoint descriptor has no spare byte,
and a POI the watch created itself, read back on 2026-08-04, has the same ten. Those four
typed bytes are zero on everything SuuntoLink writes and not on what the watch writes:
`Type=17`, the value `WAYPOINT_TYPE_DEFAULT` already held for the binary waypoint tail,
`TypeIndex=1` numbering it, and an unexplained `Flags=1`. Which is why `parse_sbem_poi_list`
no longer looks for the coordinates by skipping zero bytes - that crashed on the first POI
whose fields were not zero. The application layer does carry one - `POST suunto://SDS/LegacyPOI/<serial>` sends an
`altitude` per POI - so it is lost on the way in, for want of anywhere to put it. Its
`Timestamp` is that layer's `creation`, plain Unix epoch as ISO 8601 in UTC, unlike the route
timestamp and its unexplained epoch.

Integrity: `crc16_ccitt_false(descriptors || points)` in the route header,
`crc16_ccitt_false(descriptors)` in the waypoint header, and the `0x0b18` closing hash =
SHA-256 of the whole region, unwritten bytes at `0xff`. `GpsSGEE` is the exception: hash of
the written bytes only.

## SBEM schema dictionary

The `descr+SERIAL+FW` file in SuuntoLink's data folder is an SBEM0102 that names and types
every SML field of the firmware: 278 fields, 27 groups and 19 queryable objects for 2.4.17.
**Its field numbers are the protocol's entry identifiers**, which makes any SBEM payload
readable without guessing:

```
./tools/sbem_schema.py                                       # dump of the schema
./tools/sbem_schema.py --group 85                            # detail of one id
./tools/sbem_schema.py --capture "assets/ambit3 pcap/sync"   # decodes a capture
./tools/sbem_schema.py --verify                              # schema against all captures
```

Verified on the captures: the `0x0b21` carries only `0x4a`
(`BinaryDataArea.{Name,Checksum,Address,Size}`, 9 regions), the `0x0b25` and the `0x0b24`
only `0x55` (`WayPoint`), the `0x1100` the 66 `DeviceSettings` groups, the `0x1200` entries
`0x59`, `0x5a` and `0x8a` of the `DeviceLogBook`. A request is the identifier of the object
wanted: the `0x1200` asks for `0x8d` = `sml.DeviceLogBook`, the `0x1100` makes do with four
zero bytes and returns everything.

What the descriptor adds, beyond navigation:

- the complete POI record, `0x55` = `WayPoint.{Name, RouteName, Timestamp, RouteIndex, Type,
  SubType, TypeIndex, Flags, Latitude, Longitude}`. What `parse_sbem_poi_list` skips as
  padding is in fact an empty `RouteName` then five typed u8: the current decoding yields
  the same coordinates on the captures because those five bytes are zero there, but it would
  misalign on a typed POI.
- the logbook index, `0x8a`, 47 fields including `MemArea.StartAddress1/EndAddress1`: the
  flash address of every activity, that is the whole read path for moves.
- the `DeviceSettings` tree, readable (`0x1100`) and writable (`0x1101`), including
  `WhitelistedBleDevices` - see the BLE milestone in `HANDOFF.md`.
- `settings_write.py`'s curated `AMBIT3_SETTINGS`/`KAILASH_SETTINGS` tables resolve most
  fields directly by schema path, but some real settings are GROUPs packing more than one
  value into a single entry rather than a plain scalar leaf - Kailash's own `HomeLocation`
  (`Latitude`/`Longitude` packed into one 8-byte entry) is the first curated example,
  found 2026-08-08 - see `custom_modes_andre.md`'s "Kailash Home Location" section.
  `settings_write.py`'s `_group_containing()`/`_field_offset_in_group()` resolve a GROUP
  member to its parent entry + byte offset generically, not special-cased to just this one
  field.

The regions declared by the `0x0b21` are nine, not three: `Waypoints`, `Routes`, `Apps`,
`GpsSGEE`, `CustomModes`, `TrainingProgram`, `ExerciseLog`, `EventLog` and `BlePairingInfo`
(450 bytes at address 1332).

## Simplification

Douglas-Peucker, a single pass at 2 m, distance to the line, planar projection, the
waypoints splitting the track. Reproduces 336 points out of 1066 and 852 out of 2911, and
the resulting point body is byte-for-byte identical to both captures.

The 2 m tolerance is pinned (1, 3 and 4 m fail). The projection radius is not: from
6 300 000 to 6 500 000 m the result is the same. The metric is 2D despite `is3D()` returning
true in `route_simplifier.js`, which the Grand Tour fixture proves since it carries
altitudes.

## Table ordering, with a reproduced SuuntoLink defect

Routes are sorted by decreasing modification date. The waypoint descriptor table follows the
**reverse** order of the routes, but the index table follows their **direct** order: the two
are therefore not in correspondence as soon as there is more than one route. This is
reproduced as is, to stay identical to the reference.

In the route index, `@12` is the rank of the first waypoint in the descriptor table and
`@15` the number of waypoints placed after that block. The `sync` capture carries a stale
value there: SuuntoLink computed it over all the routes of the application, including one
that was not enabled and therefore not written (`route.js` filters on `watchEnabled`). One
byte diverges, and that application state is not derivable from a GPX.

## Verification coverage

Mutation test: a flipped bit in a point or in a descriptor is caught by the CRC and by the
hash; in an index table, by the hash alone (the CRC does not cover the indexes); in a
waypoint descriptor, by the fields of the waypoint region only. Every written byte is
covered by at least one field.
