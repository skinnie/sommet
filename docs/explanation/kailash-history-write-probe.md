# Kailash "countries visited" — can it be written to another watch?

**Goal:** the "sync two Kailashes / same countries visited" feature. This note records
what a live, read-only hardware probe established about whether it's even possible.

**Probe tool:** `tools/kailash_history_probe.py` (read-only: `0x0b21` memory map +
raw `0x1200`/`sml.DeviceHistory` dump, nothing written).
**Watch:** Kailash (Hoopoe), serial `73DC395121001500`, fw 2.0.5. Mac, 2026-08-30.

## Finding 1 — DeviceHistory is not a declared (writable) region

The watch's own `0x0b21` memory map declares:

| region | base | size |
|---|---|---|
| Waypoints | 0x005000 | 16384 |
| Routes | 0x14c080 | 130000 |
| GpsSGEE | 0x0704e0 | 140000 |
| GlonassSGEE | 0x1339e0 | 100000 |
| EventLog | 0x0c3500 | 400000 |
| TrackLog | 0x48a1c0 | 1310713 |
| (Apps / CustomModes / ExerciseLog) | 0xffffffff | 0 (absent) |

**There is no `DeviceHistory` region.** Visited-cities/countries is not stored as a
writable field or region — it is a firmware-*computed* query object served over the
`0x1200` (`CMD_LOG_HEADERS`) query channel. That channel has read/query semantics only;
the command set's only write pair is `0x1100`/`0x1101` (settings) plus the
`0x0b16`/`0b18`/`0b04` flash-region and `0x0b25` POI writers — none target query objects.

## Finding 2 — the summary is derived; the source is EventLog

The raw `0x1200` reply (SBEM0102, 266 B) is a computed summary, not raw storage:

- `0x54` name `Kailash`, `0x55` serial `73DC395121001500`
- `0x56` cities=1, `0x57` countries=1
- `0x5b` last place lon/lat (float32 radians), `0x5c` country `FR`
- `0x5d` last-known time, `0x5e` travelling days, `0x5f/0x60/0x61` distances
- `0x66` activity-mode logbook (sessions on 2026-08-20/21/27)

The counters (`cities`, `countries`) and `visited_places` are aggregates. The plausible
underlying source is the **`EventLog`** region (declared, 400 KB) — the firmware scans it
to compute this summary.

## Verdict / next step

Writing "same countries visited" directly is **not possible** — no field, no region, no
write command for query objects. The only conceivable path is:

1. Read watch A's `EventLog`, decode its format (needs the SBEM **descriptor**,
   `descr+<serial>+2.0.5`, in the Linux `assets/`).
2. Understand what in it drives the visited-places/countries aggregate.
3. Reversible write-test: transplant/merge onto watch B and check whether the firmware
   *recomputes* the summary — it may instead maintain the counters in a separate internal
   NVM structure only updated during live GPS, in which case even this fails.

Phase 2 requires the Linux box: the descriptor + the settings-write pcap
(`kailash7rsettingschange.pklg`, the only captured Kailash write) both live only there.
Everything else (settings, POIs, routes, sport-mode layouts) already has a proven write
path and is unaffected by this — see the sync-feature plan.
