#!/usr/bin/env python3
"""release_notes.py picks the changelog sections since the previous version."""
import os

import release_notes as R

SAMPLE = """# Changelog

Intro text.

---

## 2026-11-05: Update note on both apps (0.2.58)

- A note on Home.

## 2026-11-04: Something between releases (Android)

- Fixed a thing.

## 2026-11-03: Finishing touches on both apps (0.2.57)

- Older.

## 2026-11-02: Even older (desktop 0.2.56 + Android)

- Older still.
"""


def main():
    notes = R.notes_for("v0.2.58", SAMPLE)
    assert notes.startswith("## 2026-11-05: Update note on both apps (0.2.58)"), notes
    assert "## 2026-11-04: Something between releases (Android)" in notes, notes
    assert "0.2.57" not in notes and "Intro text" not in notes, notes

    # An older tag starts further down and stops at the next older version.
    notes = R.notes_for("v0.2.57", SAMPLE)
    assert notes.startswith("## 2026-11-05"), notes          # newer sections are above it
    assert "## 2026-11-03: Finishing touches" in notes and "Even older" not in notes, notes

    # Dates in headings are not versions; a trailing '---' rule is dropped.
    assert R.parse_version("v1.2.3") == (1, 2, 3)
    assert R.parse_version("Sommet 0.2.58-rc.1") == (0, 2, 58)
    notes = R.notes_for("v0.3.0", "## 2026-12-01: A (0.3.0)\n\n- x\n\n---\n")
    assert notes == "## 2026-12-01: A (0.3.0)\n\n- x", repr(notes)

    # The real changelog has notes for the current release.
    here = os.path.dirname(os.path.abspath(__file__))
    with open(os.path.join(here, "..", "docs", "reference", "v3-changelog.md"), encoding="utf-8") as f:
        real = f.read()
    assert "## 2026-11-03: Finishing touches on both apps (0.2.57)" in R.notes_for("v0.2.57", real)
    print("release_notes: ok")


if __name__ == "__main__":
    main()
