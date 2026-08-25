# Multi-source sync & de-duplication — design cross-check

Cross-check requested by André (2026-08-24): *"how to have toggles for garmin, intervals.icu
etc. to not have duplicates."* Written in the auto-mode session; **for review, then we debug
and refine together.**

## 1. Your real data flow today

```
  Garmin Index Scale 2 ─────► Garmin Connect ──┐   (weight, body composition)
  Garmin Edge 1040 Solar ───► Garmin Connect ──┤   (rides)
  Suunto Race S ────────────► Suunto (cloud) ──┤   (runs, HRV, sleep)
  Hammerhead Karoo 3 ───────► Suunto (cloud) ──┤   (rides)   [Karoo syncs INTO Suunto]
  Suunto Ambit3 (this app) ── USB ─────────────┤   (native, direct)
                                                │
                                                ▼
                                        intervals.icu  ◄── the current hub
                                        (aggregates everything)
```

**The stated goal:** ditch intervals.icu as the hub, pull each source directly, and make it
work for other users with different gear. The blocker to duplicates is that the *same physical
activity* arrives through more than one path.

## 2. Where duplicates come from (the overlap)

| Real activity | Path A | Path B | Duplicate if we import… |
|---|---|---|---|
| Edge 1040 ride | Garmin Connect (direct) | Garmin → intervals.icu | Garmin **and** intervals |
| Karoo 3 ride | Suunto (direct) | Suunto → intervals.icu | Suunto **and** intervals |
| Race S run | Suunto (direct) | Suunto → intervals.icu | Suunto **and** intervals |
| Ambit3 move | Watch USB (native) | maybe Suunto → intervals | Watch **and** intervals |
| Body weight | Garmin Connect (Index) | Garmin → intervals `weight` | Garmin **and** intervals |

So duplicates are **cross-source**, not within a source. Any anti-dup scheme has to compare
rows *across* sources.

## 3. The de-duplication key

Different sources assign **different `external_id`s to the same activity**, so ids can't match
them. The one thing they agree on is **when the activity started**.

- **Key = start time trimmed to the minute** (`YYYY-MM-DDTHH:MM`). Two different real workouts
  essentially never start in the same minute; the same workout always does.
- Optionally tighten with sport + rough duration if false collisions ever appear (not expected).
- Weight/health de-dup on the **calendar date** instead (one reading per day).

**Implemented now:** `ActivityService::dedupeActivities()` runs after every DB load. It keeps
all source rows in the DB but collapses same-start-minute rows in the list the UI sees, keeping
the highest-priority source. So importing from Garmin *and* intervals no longer double-lists.

## 4. Source priority (why it lets you ditch intervals)

Ties are broken by a priority that **favours direct sources over the aggregator**:

```
  watch (native, empty source)  100
  garmin (direct)                80
  suunto (direct)                70
  etrex (USB)                    60
  intervals (aggregator)         20   ◄── lowest on purpose
  other                          10
```

Because intervals is lowest, an Edge ride present in *both* Garmin and intervals is shown as the
**Garmin** copy. Turn intervals off entirely and you lose nothing a direct source already has —
that is the migration mechanism. (Priority should become user-configurable; see §7.)

## 5. The toggle model — a source × data-type matrix

One toggle per (source, data-type). Import is the common axis; export applies to a few.

| Source | Activities | Weight / body-comp | Health (RHR/HRV/sleep/BB) | Gear |
|---|---|---|---|---|
| **Watch (Ambit3, USB)** | native (always) | — | — | — |
| **eTrex (USB)** | import ✅ | — | — | — |
| **Garmin Connect** | import ✅ · export ✅ | source ✅ (Weight page) | source ✅ (Health page) | — |
| **Suunto (cloud)** | ⛔ not built (see §6) | ⛔ | ⛔ (HRV/sleep live here) | — |
| **intervals.icu** | import ✅ · export ✅ | source ✅ | — | import ✅ |

- **Import** = pull activities/data *into* the app (de-duped on the way in / at display).
- **Export** = push *to* that service (intervals ✅, Garmin ✅ now; both dedup on their side by
  start time, so re-export is a safe no-op).
- **Source** (weight/health) = a single-select "where does this metric come from" — already the
  case on the Weight page (intervals ⇄ Garmin) and Health page (Garmin).

**What already exists in the app:** intervals import + export-scope selector (manual/suunto/
etrex/all), Garmin activity import toggle, Garmin activity export button, Weight source
selector, Health (Garmin). The matrix above is the shape to converge the scattered toggles into.

## 6. The gap that actually blocks "ditch intervals": Suunto

Your **Race S (runs, HRV, sleep) and Karoo (rides) reach us only via intervals today.** There is
no direct Suunto-cloud import built (the Suunto Cloud API is gated/parked — see the
`ambit_app_cloud_api_landscape` note). So:

- **You cannot fully ditch intervals yet** without losing Race S + Karoo data, unless we add a
  **direct Suunto-cloud import** (activities + HRV + sleep).
- Options: (a) build a Suunto-cloud client (needs API access/OAuth — the parked item), or
  (b) keep intervals *only* for the Suunto-origin data and take everything else direct, or
  (c) if the Karoo can export to Garmin/Dropbox too, route it around Suunto.

**Recommendation:** treat "direct Suunto import" as the next real feature — it's the true
long-pole for your goal. Until then, the priority scheme lets intervals fill *only* the Suunto
gap while Garmin/watch come direct.

## 7. What to build next (converging the toggles)

1. **A single "Sources" settings screen** rendering the §5 matrix: one row per source, import/
   export/source toggles per data-type, plus a **drag-to-order priority list** feeding
   `dedupeActivities()` (replace the hard-coded priority).
2. **Show the source on each activity** (already stored) as a small badge, and a "this move is
   also on: intervals, Garmin" tooltip — so dedup is visible, not magic.
3. **Direct Suunto import** (the §6 long-pole).
4. **De-dup on export too:** before exporting to a destination, skip moves that already came
   *from* it (already done for intervals; generalise per destination).
5. **Weight/health source** already single-select; keep it that way (no dedup needed, just
   pick one provider).

## 8. Generalising for other users

The matrix is user-agnostic: each user enables the sources they own and orders priority to
taste. A cyclist on Garmin-only turns everything to Garmin; a Suunto user to Suunto; someone
mid-migration keeps intervals at the bottom as a catch-all. The **start-time de-dup key + a
user-ordered source priority** is the whole mechanism — no per-user special-casing.

## 9. Open decisions for André

- **Priority order** — is `watch > garmin > suunto > eltrex > intervals` right for you, or should
  Suunto outrank Garmin (Race S is your "truth" for HRV/sleep, Garmin for rides)? Per-data-type
  priority may be worth it (activities: Garmin first; health: Suunto first).
- **Suunto direct** — do you have/can you get Suunto Cloud API access, or should we lean on the
  Karoo/Edge export routes to avoid the Suunto cloud entirely?
- **Weight** — keep Garmin as the weight source (Index scale) and drop intervals weight? (It's
  already a toggle.)
- **De-dup visibility** — silent collapse (current) vs. showing "also on X" badges.
```
