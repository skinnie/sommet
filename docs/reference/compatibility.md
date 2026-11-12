# Compatibility matrix — what works on which watch

> Not maintained since 2026-08-12; see the "Checked on" lines in [v3-changelog.md](v3-changelog.md).

What has been **tested on real hardware**, feature by feature, watch by watch. This is the honest
answer to a user's "will it work on *my* watch?" — and the checklist that gates a release. A cell is
only marked working once André has confirmed a real test on that physical watch (see the test ritual
in `CLAUDE.md`). Anything untested stays `—` and the feature is treated as **Experimental** in-app.

**Legend:** ✅ works (date) · ⚠️ partial (see note) · ❌ broken (see note) · — not tested yet ·
n/a not applicable to that watch

## Suunto watches

| Feature | Kailash (Hoopoe) | Ambit3 Peak (Emu) | Traverse (Jabiru) | Ambit (Bluebird) | Ambit2 (Duck) |
|---|:--:|:--:|:--:|:--:|:--:|
| Read watch info / settings      | — | — | — | *incoming* | *incoming* |
| Write settings                  | — | — | — | *incoming* | *incoming* |
| Activities / history read       | — | — | — | *incoming* | *incoming* |
| Routes — write to watch         | — | — | — | *incoming* | *incoming* |
| POIs — add / read               | — | — | — | *incoming* | *incoming* |
| Sport modes — read              | — | — | — | *incoming* | *incoming* |
| Sport modes — create/delete/multisport | — | — | — | *incoming* | *incoming* |
| Firmware — backup / flash       | — | — | — | *incoming* | *incoming* |
| GPS track pod                   | — | — | — | *incoming* | *incoming* |
| Backup & restore                | — | — | — | *incoming* | *incoming* |

Kailash has **no CustomModes region**, so the sport-mode rows are **n/a** there — mark them n/a once
confirmed. Ambit3 Peak (Emu) is the reference watch.

## How this gets filled
Not by hand in a big sitting — it fills itself through the **test ritual**: after any watch-facing
change, Claude asks "did you test this on Kailash / Ambit3 / Traverse?", you answer yes/no, and the
matching cell is updated here with the date. Over time this becomes your real, evidence-based support
table — and the thing you check before tagging a release.

_Last updated: (seeded 2026-08-12 — cells pending your first confirmations)_
