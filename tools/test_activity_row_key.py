#!/usr/bin/env python3
"""On desktop a move is (idx, device): every SQL statement that picks a move by idx also names
its device.

Moves from different watches share idx values (each watch numbers its own moves from 1), and so
did the Edge, Karoo and C406 imports from before 2026-11-07. A statement by idx alone reaches
every move with that number. Before sync/07 the Sommet Sync pull wrote one move's numbers onto
up to five others, and deleting one move deleted the others too.

This test reads the desktop C++ sources. It finds each string literal (adjacent pieces joined)
that reads or writes the activities table with an "idx = ?" predicate, and fails when the
statement doesn't mention device.

    python3 tools/test_activity_row_key.py
"""
import os
import re
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "desktop", "src")

# QStringLiteral("a" "b" ...) / QLatin1String(...) / a plain run of adjacent literals.
_LITERAL_RUN = re.compile(r'(?:"(?:[^"\\\n]|\\.)*"\s*)+')
_PIECE = re.compile(r'"((?:[^"\\\n]|\\.)*)"')
_ACTIVITIES = re.compile(r'\b(?:FROM|UPDATE|INTO)\s+activities\b', re.I)
_BY_IDX = re.compile(r'\bidx\s*=\s*\?', re.I)


def statements(text):
    for m in _LITERAL_RUN.finditer(text):
        sql = "".join(_PIECE.findall(m.group(0)))
        line = text.count("\n", 0, m.start()) + 1
        yield line, sql


def offenders(text):
    """Statements on activities with "idx = ?" whose WHERE doesn't name the device (a device
    column in the SELECT list doesn't count)."""
    for line, sql in statements(text):
        if not (_ACTIVITIES.search(sql) and _BY_IDX.search(sql)):
            continue
        parts = re.split(r'\bWHERE\b', sql, maxsplit=1, flags=re.I)
        if "device" not in parts[-1].lower():
            yield line, sql


class RowKey(unittest.TestCase):
    def test_finder_catches_the_old_statements(self):
        # The statements sync/07 fixed, as they were (the two exported = 1 updates were alike).
        old = """
            up.prepare(QStringLiteral("UPDATE activities SET exported = 1 WHERE idx = ?"));
            sel.prepare(QStringLiteral(
                "SELECT source, external_id, start_time, name, device FROM activities WHERE idx = ?"));
            del.prepare(QStringLiteral(
                "DELETE FROM activities WHERE idx = ? OR (start_time = ? AND name = ?)"));
            up.prepare(QStringLiteral(
                "UPDATE activities SET name=?, duration_s=?, "
                "start_utc=COALESCE(?, start_utc), moving_s=COALESCE(?, moving_s) WHERE idx=?"));
        """
        self.assertEqual(len(list(offenders(old))), 4)
        ok = '"UPDATE activities SET exported = 1 WHERE idx = ? AND COALESCE(device, \'\') = ?"'
        self.assertEqual(list(offenders(ok)), [])

    def test_every_statement_by_idx_names_the_device(self):
        found = []
        for dirpath, _dirs, files in os.walk(SRC):
            for f in files:
                if not f.endswith((".cpp", ".h")):
                    continue
                path = os.path.join(dirpath, f)
                with open(path, encoding="utf-8", errors="replace") as fh:
                    text = fh.read()
                for line, sql in offenders(text):
                    found.append(f"{os.path.relpath(path, ROOT)}:{line}: {sql}")
        self.assertEqual(found, [], "statements that pick a move by idx alone (a move is "
                         "(idx, device), see sync/07):\n  " + "\n  ".join(found))


if __name__ == "__main__":
    unittest.main()
