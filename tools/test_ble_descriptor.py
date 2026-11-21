#!/usr/bin/env python3
"""Bluetooth readers name SBEM entries with the watch's own schema (P5Q, 2026-11-21: on a
computer without SuuntoLink, POIs over Bluetooth came back "CANNOT DECIDE: the SuuntoLink
descriptor is missing" and the POIs page stayed empty). Checked without a watch: the stored
copy for the hello's serial + firmware is used; with none stored the schema is read from the
watch (0x1104) and cached; a rehearsal link asks nothing; SuuntoLink's folder is not needed."""
import pathlib
import struct
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ble_pois                      # noqa: E402
import sbem_schema                   # noqa: E402

SCHEMA = sbem_schema.MAGIC + bytes([0x01, 0x02, 0xAA, 0xBB])          # one whole entry
assert sbem_schema.is_complete(SCHEMA)
HELLO = (b"Emu".ljust(16, b"\0") + b"TESTSERIAL000001" + bytes([2, 4, 17, 0])).ljust(48, b"\0")


class Link:
    dry_run = False

    def __init__(self):
        self.device_info = {"serial": "TESTSERIAL000001", "fw_version": "2.4.17"}
        self.asked = []

    def command(self, cmd, payload=b"", quiet=False):
        self.asked.append(cmd)
        if cmd == 0x0000:
            return HELLO
        if cmd == sbem_schema.CMD_DESCRIPTOR_READ:
            return struct.pack("<I", len(SCHEMA)) + b"\x01\x00" + SCHEMA
        raise AssertionError(hex(cmd))


class Bridge(Link):
    """ble_bridge.BleBridge's shape: the hello is in status(), not an attribute."""
    def __init__(self):
        super().__init__()
        self._info, self.device_info = self.device_info, None

    def status(self):
        return {"device_info": self._info}


with tempfile.TemporaryDirectory() as tmp:
    tmp = pathlib.Path(tmp)
    sbem_schema.CACHE_DIR = tmp / "cache"
    sbem_schema.suuntolink_dirs = lambda: [tmp / "no-suuntolink"]
    sbem_schema.ASSETS = tmp / "no-assets"
    # nothing stored: read from the watch, cached
    link = Link()
    path = ble_pois.descriptor_for_ble(link)
    assert path == tmp / "cache" / "descr+TESTSERIAL000001+2.4.17" and path.read_bytes() == SCHEMA
    assert sbem_schema.CMD_DESCRIPTOR_READ in link.asked
    # stored now: the watch is not asked again, through the bridge's status() too
    for link in (Link(), Bridge()):
        assert ble_pois.descriptor_for_ble(link) == path and link.asked == [], link.asked
    # a rehearsal with nothing stored asks nothing
    path.unlink()
    link = Link()
    link.dry_run = True
    assert ble_pois.descriptor_for_ble(link) is None and link.asked == []
print("ble descriptor: the watch's own schema, stored or read, no SuuntoLink needed OK")
