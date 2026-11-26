#!/usr/bin/env python3
"""Checks the watch-settings tables of settings_write.py and the mobile app's generated copy.

Two parts. The first needs nothing but the code: the tables of each watch family agree with
each other. The second replays every write SuuntoLink was captured sending and requires our
templates to rebuild it byte for byte from the read that preceded it; it needs the captures
and the watches' own descriptors in assets/, which are not in the public repository, so it is
skipped where they are missing.

    ./tools/test_settings_templates.py
"""
import glob
import json
import os
import pathlib
import re
import subprocess
import sys
import unittest

import sbem_schema
import settings_write as S
from ambit_pcap import messages
from write_nav import descriptor_for_product_id

HERE = pathlib.Path(__file__).resolve().parent
PCAP = HERE.parent / "assets" / "pcap"
ANDROID_TS = HERE.parent / "android" / "src" / "services" / "AmbitSettingsTemplates.ts"

AMBIT3, TRAVERSE, TRAVERSE_ALPHA = 0x001B, 0x002B, 0x002D
# family -> (product id, capture name prefix, number of captured settings-screen writes).
# `traverseoldfirmwaretonew` is left out on purpose: its one write is SuuntoLink putting every
# setting back after a firmware update, not a settings screen being saved.
FAMILIES = {
    "ambit3": (AMBIT3, "ambit3", 136),
    "traverse": (TRAVERSE, "traversegeneral", 95),
}


class Tables(unittest.TestCase):
    def test_product_id_picks_the_family(self):
        for pid in (TRAVERSE, TRAVERSE_ALPHA):
            self.assertIs(S.settings_table(pid), S.TRAVERSE_SETTINGS)
            self.assertIs(S.write_templates(pid), S.TRAVERSE_WRITE_TEMPLATES)
            self.assertIs(S.key_template(pid), S.TRAVERSE_KEY_TEMPLATE)
        for pid in (AMBIT3, 0x001C, 0x001E, 0x002C, None):
            self.assertIs(S.settings_table(pid), S.AMBIT3_SETTINGS)
            self.assertIs(S.write_templates(pid), S.AMBIT3_WRITE_TEMPLATES)
            self.assertIs(S.key_template(pid), S.AMBIT3_KEY_TEMPLATE)
        self.assertIs(S.settings_table(S.KAILASH_PRODUCT_ID), S.KAILASH_SETTINGS)

    def test_bluetooth_model_name_picks_the_same_family(self):
        import ble_settings
        self.assertEqual(ble_settings.product_id_from_model("Jabiru"), TRAVERSE)
        self.assertEqual(ble_settings.product_id_from_model("Loon"), TRAVERSE_ALPHA)
        self.assertEqual(ble_settings.product_id_from_model("Hoopoe"), S.KAILASH_PRODUCT_ID)
        self.assertEqual(ble_settings.product_id_from_model("Kaka"), 0x002C)
        for model in ("Emu", "Finch", "Ibisbill", None):
            self.assertIsNone(ble_settings.product_id_from_model(model))

    def test_every_key_rides_in_the_template_of_its_screen(self):
        for pid in (AMBIT3, TRAVERSE):
            table, templates, screens = (S.settings_table(pid), S.write_templates(pid),
                                         S.key_template(pid))
            self.assertEqual(set(screens), set(table))
            for key, suffix in table.items():
                self.assertIn(suffix, templates[screens[key]], f"{key} on 0x{pid:04x}")

    def test_traverse_is_the_ambit3_list_plus_its_own_six(self):
        extra = [k for k in S.TRAVERSE_SETTINGS if k not in S.AMBIT3_SETTINGS]
        self.assertEqual(extra, ["vibration", "backlight_color", "sunrise_alert",
                                 "sunrise_alert_minutes", "sunset_alert",
                                 "sunset_alert_minutes"])
        self.assertEqual([k for k in S.TRAVERSE_SETTINGS if k in S.AMBIT3_SETTINGS],
                         list(S.AMBIT3_SETTINGS))
        # Each one sits next to the row SuuntoLink shows it under.
        keys = list(S.TRAVERSE_SETTINGS)
        self.assertEqual(keys[keys.index("backlight_brightness") + 1], "backlight_color")
        self.assertEqual(keys[keys.index("tones") + 1], "vibration")
        self.assertEqual(keys[keys.index("storm_alarm") + 1], "sunrise_alert")

    def test_pods_ride_only_in_ambit3_templates(self):
        for name, template in S.AMBIT3_WRITE_TEMPLATES.items():
            self.assertEqual("Pods+Pod.Type" in template, name != "units_mode", name)
        for name, template in S.TRAVERSE_WRITE_TEMPLATES.items():
            self.assertNotIn("Pods+Pod.Type", template, name)

    def test_new_rows_have_a_label_and_alert_minutes_a_range(self):
        for key in S.TRAVERSE_SETTINGS:
            if key != "plans_source":
                self.assertIn("label", S.AMBIT3_DISPLAY[key], key)
        for key in ("sunrise_alert_minutes", "sunset_alert_minutes"):
            self.assertEqual(S._display_range(key), (0, 120))


class KailashTable(unittest.TestCase):
    """watch-protocol/10 K7 (2026-11-26): every Kailash key is a field of its own descriptor and
    an entry of the settings reply its 7R app read (one-entry writes need the entry there), and
    Android lists the same keys with the same entries."""

    def test_every_key_is_in_the_descriptor_the_reply_and_android(self):
        import glob
        import re
        found = sorted(glob.glob(str(HERE.parent / "assets" / "**" / "descr+*+2.0.5"), recursive=True))
        if not found:
            self.skipTest("needs assets/ (the Kailash's descriptor)")
        schema = sbem_schema.load(found[0])
        ids = {}
        for key, suffix in S.KAILASH_SETTINGS.items():
            field = S._find_field(schema, suffix)
            group = S._group_containing(schema, field.fid)
            ids[key] = group if group is not None else field.fid
        reply_ids = set(range(0x01, 0x28)) | {0x35, 0x36}   # kailash7rsettingschange 0x1100 reply
        self.assertEqual({k: hex(v) for k, v in ids.items() if v not in reply_ids}, {})
        ts = (HERE.parent / "android" / "src" / "services" / "AmbitSettingsReader.ts").read_text()
        block = ts[ts.index("export const KAILASH_SETTINGS_FIELDS"):]
        block = block[:block.index("\n];")]
        android = {k: int(v, 16) for k, v in re.findall(r"key: '(\w+)', entryId: (0x[0-9a-f]+)", block)}
        self.assertEqual(android, ids)
        # The 7R app's settings and nothing else (IMG_2741/2742, its NotificationsOff push, and
        # display_dark seen on the watch); the 19 descriptor-only fields went on 2026-11-26.
        self.assertEqual(set(S.KAILASH_SETTINGS), {
            "date_format", "tones", "vibration", "units_mode", "language", "time_format",
            "display_contrast", "backlight_mode", "backlight_brightness", "storm_alarm",
            "display_dark", "home_latitude", "home_longitude", "notifications_off"})
        self.assertEqual(S.KAILASH_READ_ONLY, set())
        for left in ("GpsBackgroundMode", "DualTimeActive", "DaylightSaving", "Alarms"):
            self.assertFalse(any(left in v for v in S.KAILASH_SETTINGS.values()), left)


def _captures(prefix):
    return [p for p in sorted(glob.glob(str(PCAP / (prefix + "*")))) if os.path.isfile(p)]


def _reads_and_writes(path):
    """(preceding 0x1100 reply, 0x1101 payload) for every settings write in a capture."""
    last_read = None
    for m in messages(path):
        if m.incoming and m.command == S.CMD_SETTINGS_READ and len(m.payload) > 40:
            last_read = m.payload
        elif not m.incoming and m.command == S.CMD_SETTINGS_WRITE and last_read is not None:
            yield last_read, m.payload


def _numbered(entries):
    seen, out = {}, []
    for eid, data in entries:
        out.append((eid, seen.get(eid, 0), data))
        seen[eid] = seen.get(eid, 0) + 1
    return out


def _rebuilt_by_python(schema, templates, read, write):
    """True if one of `templates` rebuilds `write` exactly from `read`, the entries whose
    value the user changed being the only thing taken from the write itself."""
    _, read_entries = S._split_entries(read)
    _, write_entries = S._split_entries(write)
    before = {(eid, n): data for eid, n, data in _numbered(read_entries)}
    patches = [(eid, n, data) for eid, n, data in _numbered(write_entries)
               if before.get((eid, n)) != data]
    for template in templates.values():
        try:
            payload, _ = S.build_write_payload(schema, read, template, patches)
        except (KeyError, ValueError):
            continue
        if payload == write:
            return True
    return False


def _android_tables():
    ts = ANDROID_TS.read_text()

    def table(name):
        m = re.search(r"export const " + name + r"[^=]*= (\{.*?\n\});\n", ts, re.S)
        return json.loads(m.group(1))

    return table("WRITE_TEMPLATES"), table("ENUM_VALUES"), table("BOOL_ENTRIES")


def _rebuilt_by_android(templates, enums, bools, read, write):
    """The mobile writer's algorithm on the mobile tables: same entries, same order, same
    size as the captured write (the changed value is the only difference left)."""
    _, read_entries = S._split_entries(read)
    _, write_entries = S._split_entries(write)
    by_id = {}
    for eid, data in read_entries:
        by_id.setdefault(eid, []).append(data)

    def representable(eid, data):
        if str(eid) in enums:
            return len(data) >= 1 and data[0] in enums[str(eid)]
        if eid in bools:
            return len(data) == 1 and data[0] in (0, 1)
        return True

    want = [(eid, len(data)) for eid, data in write_entries]
    for ids in templates.values():
        got = [(eid, len(data)) for eid in ids for data in by_id.get(eid, [])
               if representable(eid, data)]
        if got == want:
            return True
    return False


class _ReplayLink:
    """Stands in for the watch: answers every settings read with the captured one and keeps
    what would have been written."""
    def __init__(self, read):
        self.read, self.sent = read, []

    def command(self, command, payload=b""):
        if command == S.CMD_SETTINGS_WRITE:
            self.sent.append(payload)
            return b""
        return self.read


@unittest.skipUnless(PCAP.is_dir(), "no captures on this machine (assets/ is not in the repo)")
class Captures(unittest.TestCase):
    def _schema(self, pid):
        descriptor = descriptor_for_product_id(pid) or sbem_schema.default_descriptor()
        if not descriptor.exists():
            self.skipTest(f"no descriptor for 0x{pid:04x} on this machine")
        return sbem_schema.load(descriptor)

    def test_python_templates_rebuild_every_captured_write(self):
        for family, (pid, prefix, expected) in FAMILIES.items():
            schema, templates = self._schema(pid), S.write_templates(pid)
            pairs = [rw for path in _captures(prefix) for rw in _reads_and_writes(path)]
            missed = [len(w) for r, w in pairs
                      if not _rebuilt_by_python(schema, templates, r, w)]
            self.assertEqual(len(pairs), expected, f"{family}: captured writes found")
            self.assertEqual(missed, [], f"{family}: write sizes not rebuilt")

    def test_write_one_sends_what_suuntolink_sent(self):
        """The real write function, fed the read SuuntoLink saw and asked for the change
        SuuntoLink made, must put the same bytes on the wire. Covers every captured write
        that changes exactly one setting of the curated list."""
        for family, (pid, prefix, _) in FAMILIES.items():
            descriptor = descriptor_for_product_id(pid) or sbem_schema.default_descriptor()
            schema = self._schema(pid)
            by_fid = {S._find_field(schema, suffix).fid: key
                      for key, suffix in S.settings_table(pid).items()}
            checked, refused = set(), set()
            for path in _captures(prefix):
                for read, write in _reads_and_writes(path):
                    before = {(e, n): d for e, n, d in _numbered(S._split_entries(read)[1])}
                    changed = [(e, d) for e, n, d in _numbered(S._split_entries(write)[1])
                               if before.get((e, n)) != d]
                    if len(changed) != 1 or changed[0][0] not in by_fid:
                        continue
                    fid, data = changed[0]
                    key = by_fid[fid]
                    raw = S._decode_field(schema.fields[fid], data)
                    value = S._to_display(key, raw)
                    if key not in S._DEGREES_FROM_RADIANS_KEYS \
                            and S._from_display(key, value) != raw:
                        continue    # e.g. a weight typed in pounds: 34,47 kg shows as 34,5
                    link = _ReplayLink(read)
                    result = S.write_one(link, descriptor, key, value, pid)
                    if not link.sent:
                        refused.add((family, key, value, result.get("error", "")[:60]))
                        continue
                    self.assertEqual(link.sent[0].hex(), write.hex(), f"{family} {key}={value}")
                    checked.add(key)
            if family == "traverse":
                for key in ("vibration", "backlight_color", "sunrise_alert",
                            "sunrise_alert_minutes", "sunset_alert", "sunset_alert_minutes"):
                    self.assertIn(key, checked, "no captured write exercised this setting")
            self.assertGreaterEqual(len(checked), 15, f"{family}: {sorted(checked)}")
            # The one captured change we refuse on purpose: position format 15, which
            # SuuntoLink offers although the watch's own list stops at 14 (it stranded a
            # watch on 2026-08-10, see AMBIT3_CHOICE_LABELS).
            self.assertEqual({(key, value) for _, key, value, _ in refused} -
                             {("gps_position_format", 15)}, set(), sorted(refused))

    def test_mobile_tables_rebuild_every_captured_write(self):
        templates, enums, bools = _android_tables()
        for family, (pid, prefix, expected) in FAMILIES.items():
            pairs = [rw for path in _captures(prefix) for rw in _reads_and_writes(path)]
            missed = [len(w) for r, w in pairs
                      if not _rebuilt_by_android(templates[family], enums[family],
                                                 bools[family], r, w)]
            self.assertEqual(len(pairs), expected, f"{family}: captured writes found")
            self.assertEqual(missed, [], f"{family}: write sizes not rebuilt")

    def test_mobile_tables_are_freshly_generated(self):
        for pid in (AMBIT3, TRAVERSE, S.KAILASH_PRODUCT_ID):
            self._schema(pid)
        done = subprocess.run([sys.executable, str(HERE / "gen_android_settings_templates.py"),
                               "--check"], capture_output=True, text=True)
        self.assertEqual(done.returncode, 0, done.stdout + done.stderr)

    def test_vertical_has_no_barometer_settings_or_storm_alarm(self):
        for key in ("alti_baro_profile", "storm_alarm"):
            self.assertIn(0x002C, S._UNSUPPORTED_ON_MODEL[key], key)
            self.assertNotIn(AMBIT3, S._UNSUPPORTED_ON_MODEL[key], key)

    def test_vertical_reads_with_its_own_numbers_and_never_writes(self):
        import sbem_schema
        import write_nav
        peak = sbem_schema.load(write_nav.descriptor_for_product_id(0x001B)
                                or sbem_schema.default_descriptor())
        path = write_nav.descriptor_for_product_id(0x002C)
        if path is None:      # assets/ is not in git: rebuild it with assets/Firmware/re-out/sfi2_descriptor.py
            self.skipTest("descr+FROM-FIRMWARE-Kaka+1.1.22 is not in assets/")
        vertical = sbem_schema.load(path)
        eid = lambda sch, suffix: S._find_field(sch, suffix).fid
        # Units.Language is entry 17 on the Peak but 18 on the Vertical (ticket 06)
        self.assertEqual(eid(peak, "Units.Language"), 17)
        self.assertEqual(eid(vertical, "Units.Language"), 18)
        self.assertEqual(len(vertical.fields) + len(vertical.groups) + len(vertical.queries), 336)
        self.assertIn(0x002C, S._WRITES_UNVERIFIED)
        # A reply laid out by the Vertical's own numbers: language English (18), units Imperial (8)
        ent = lambda i, d: bytes([i, len(d)]) + d
        reply = b"SBEM0102" + ent(18, b"\x02") + ent(8, b"\x01")
        got = S.read_all(reply, path, 0x002C)["settings"]
        self.assertEqual((got["language"]["value"], got["units_mode"]["value"]), (2, 1))
        self.assertFalse(got["language"]["writable"])
        self.assertFalse(got["storm_alarm"]["ok"])
        self.assertFalse(got["alti_baro_profile"]["ok"])
        # the old behaviour, for the record: the Peak's numbers read the same bytes as other settings
        old = S.read_all(reply, write_nav.descriptor_for_product_id(0x001B)
                         or sbem_schema.default_descriptor(), 0x001B)["settings"]
        self.assertNotEqual(old["language"].get("value"), 2)

    def test_sport_mode_name_limit_follows_the_model(self):
        import sport_mode_manage as M
        rows = M.load_catalogue()[1]
        self.assertEqual(M.variant_limits(rows, "Jabiru")["maxNameLength"], 16)
        self.assertEqual(M.variant_limits(rows, "Loon")["maxNameLength"], 16)
        for v in ("Emu", "Kaka", "Hoopoe"):
            self.assertEqual(M.variant_limits(rows, v)["maxNameLength"], 63)


if __name__ == "__main__":
    unittest.main(verbosity=2)
