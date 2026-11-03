#!/usr/bin/env python3
"""Release notes for a version tag, taken from docs/reference/v3-changelog.md.

The release page shows these notes, and both apps read their "## YYYY-MM-DD: title" headings
as the "What's new" list of the update note (desktop UpdateService, Android AppUpdate.ts).
The notes are every changelog section above the first one whose heading names an older
version, so a section without a version number (work done between two releases) is included.

Usage: release_notes.py v0.2.58 [changelog.md]   -> prints Markdown on stdout
"""
import os
import re
import sys

VERSION_RE = re.compile(r"(?<![\d.])(\d+)\.(\d+)\.(\d+)(?![\d.])")
DEFAULT_CHANGELOG = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                                 "..", "docs", "reference", "v3-changelog.md")


def parse_version(tag):
    m = VERSION_RE.search(tag)
    if not m:
        raise ValueError(f"no version in {tag!r}")
    return tuple(int(x) for x in m.groups())


def sections(text):
    """[(heading line, body text)] for every '## ' section, in file order."""
    out = []
    heading, body = None, []
    for line in text.splitlines():
        if line.startswith("## "):
            if heading is not None:
                out.append((heading, "\n".join(body)))
            heading, body = line, []
        elif heading is not None:
            body.append(line)
    if heading is not None:
        out.append((heading, "\n".join(body)))
    return out


def notes_for(tag, text):
    target = parse_version(tag)
    picked = []
    for heading, body in sections(text):
        named = [tuple(int(x) for x in m.groups()) for m in VERSION_RE.finditer(heading)]
        if any(v < target for v in named):
            break
        body = re.sub(r"\n-{3,}\s*$", "", body.rstrip())   # a trailing '---' rule
        picked.append(heading + "\n" + body)
    return "\n\n".join(p.strip() for p in picked).strip()


def main(argv):
    if len(argv) < 2:
        print(__doc__.strip().splitlines()[-1], file=sys.stderr)
        return 2
    path = argv[2] if len(argv) > 2 else DEFAULT_CHANGELOG
    with open(path, encoding="utf-8") as f:
        notes = notes_for(argv[1], f.read())
    print(notes if notes else "See docs/reference/v3-changelog.md for the changes.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
