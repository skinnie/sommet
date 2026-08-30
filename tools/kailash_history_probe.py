#!/usr/bin/env python3
"""READ-ONLY probe: does the Kailash expose DeviceHistory as a writable region?

Answers, from live hardware, the question the "sync two watches / same countries
visited" feature hinges on: is visited-cities/countries stored in a declared,
writable flash region (0x0b21 map), or is it only a firmware-computed 0x1200
query object with no write path? Nothing is written. Two safe reads:

  1. read_memory_map(0x0b21) - the watch's own declared regions.
  2. the raw sml.DeviceHistory (0x67) reply over 0x1200, dumped as hex.
"""
import sys
from write_nav import Link, read_memory_map, CMD_LOG_HEADERS
from kailash_history import HISTORY_REQUEST, DEVICE_HISTORY_ENTRY
import sbem_schema


def main():
    link = Link(dry_run=False, verbose=False, product_id=0x002A)  # Hoopoe/Kailash only
    link.open()

    print("== 1. declared memory-map regions (0x0b21) ==")
    regions = read_memory_map(link)
    if not regions:
        print("  (watch declared NO named regions)")
    for name, (start, size) in sorted(regions.items()):
        print(f"  {name:16} base=0x{start:06x}  size={size}")
    print(f"  DeviceHistory declared as a region? "
          f"{'YES' if 'DeviceHistory' in regions else 'NO'}")

    print("\n== 2. raw sml.DeviceHistory (0x67) reply over 0x1200 ==")
    payload = link.command(CMD_LOG_HEADERS, HISTORY_REQUEST)
    print(f"  reply length: {len(payload)} bytes")
    head = payload.find(sbem_schema.MAGIC)
    print(f"  SBEM0102 magic at offset: {head}")
    print("  full hex:")
    print("   " + payload.hex())
    if head >= 0:
        print("\n  SBEM entries (id, length):")
        for entry_id, data in sbem_schema.entries(payload[head:]):
            print(f"    0x{entry_id:02x}  {len(data):4d} bytes  {data.hex()}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
