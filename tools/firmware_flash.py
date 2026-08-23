#!/usr/bin/env python3
"""Decodes/replays the Ambit3 + Kailash firmware-update wire protocol (bootloader "BSL").

READ-ONLY / --compare ONLY - there is deliberately no --write here. The protocol is now
fully understood (both former unknowns closed 2026-08-11), but per the standing safety
rule ("never touch the firmware: that is the only write that can brick it") no opcode of
this sequence is sent to real hardware without André's explicit, per-attempt go-ahead.

**Fully cracked, byte-exact, from 5 captures in `assets/pcap/`** - Ambit3 Emu (`firmware`,
`ambit3firwmare2`) and Kailash Hoopoe (`kaylashflashfirmware`, `resumefirmwarekailash`,
`kailashfirmware2withdownload`). Kailash uses the *identical* USB protocol. The full,
now-buildable sequence for a downloaded firmware file F:

    0x0202 (empty)   -- enter bootloader: model flips app-name -> "BSL" on next
                        device_info. SKIP this if already in BSL (resume, see below).
    (poll 0x0000 device_info until the reply model string == "BSL")
    0x0102 (empty)   -- enter firmware-transfer mode (ack empty). Also skipped on resume.
    0x0e00 <prefix><32-byte header>  -- announces the transfer (ack empty). prefix is
                        struct("<II", X, 32): the second u32 is the header length (32);
                        X is a per-SESSION value the watch ignores (the identical file was
                        accepted with 5 different X across the captures, magnitude ~25-105M,
                        looks like a host ms tick). The 32-byte header == F[:32] (the
                        SFI2STmp/SFI2STmt container header - despite the `.zip` name F is
                        NOT a zip; `unzip -l` fails).
    0x0e01 <=512 payload bytes, N times  -- F[32:], chunked at 512B (last chunk short),
                        sent completely unmodified (no re-encoding). ack empty.
    0x0e03 (empty)   -- commit / trigger the real flash + reboot (ack empty).
    0x0200 (empty)   -- reboot; watch then reports the *application* model again.

Verified byte-exact: for every full capture, F[:32]==0x0e00 header and F[32:]==the
concatenated 0x0e01 payload, for both the Emu and Hoopoe firmware files in
`assets/Firmware/`.

**Resume path:** an interrupted transfer leaves the watch in BSL (e.g.
`kailashfirmware2withdownload` sent only 456704 of 3582064 payload bytes). SuuntoLink then
skips 0x0202 AND 0x0102 and re-streams the WHOLE file from offset 0
(`resumefirmwarekailash`). So "resume" = restart-while-already-in-BSL, not a partial
resume; an aborted flash is recoverable as long as the watch still enumerates in BSL.

    ./tools/firmware_flash.py "assets/Firmware/ambit3peak_Emu-fw_2.4.17-70.2.17414.zip" \\
        --compare "assets/pcap/firmware"
"""

import argparse
import pathlib
import struct
import sys
import time

from ambit_pcap import messages

CMD_FW_BOOTLOADER = 0x0202  # enter bootloader: app model -> "BSL"
CMD_FW_MODE = 0x0102        # enter firmware-transfer mode
CMD_FW_HEADER = 0x0E00      # announce transfer: pack("<II", X, 32) + file[:32]
CMD_FW_DATA = 0x0E01        # file[32:], 512-byte chunks
CMD_FW_COMMIT = 0x0E03      # commit / flash + reboot
CMD_FW_REBOOT = 0x0200      # reboot back to the application

FW_OPCODES = (CMD_FW_BOOTLOADER, CMD_FW_MODE, CMD_FW_HEADER,
              CMD_FW_DATA, CMD_FW_COMMIT, CMD_FW_REBOOT)

HEADER_LEN = 32
CHUNK = 512

# THIS TOOL NEVER TOUCHES HARDWARE. It builds the exact outgoing message stream a real
# flasher WOULD send and self-checks it against real captures - there is no code path
# that opens a device or transmits. A real write stays gated on André's explicit,
# per-attempt go-ahead (see the module docstring and the never-touch-firmware rule).


KNOWN_CONTAINER_MAGICS = (b"SFI2ST", b"SFI1")
# SFI1 added 2026-08-22: a real Ambit1 firmware file (Bluebird-fw_2.5.7-69.1.18948.zip,
# André's own SuuntoLink cache) starts "SFI1\x00\x01\x02\x05..." not "SFI2ST" - a related
# but distinct container tag for the legacy family. Verified --compare-clean against a real
# USBPcap capture of André's own Ambit1 firmware update before this was trusted (see
# ambit_app_ambit1_firmware memory) - the command sequence (0x0202/0x0e00/0x0e01.../0x0e03/
# 0x0200) and chunking are otherwise identical to the Ambit3 family already proven here.


def parse_container(path):
    """[32-byte header][raw payload] - see this module's docstring. Despite the
    filename, do not treat this as a zip: `unzip -l` fails on it for real."""
    data = pathlib.Path(path).read_bytes()
    header, payload = data[:HEADER_LEN], data[HEADER_LEN:]
    if not header.startswith(KNOWN_CONTAINER_MAGICS):
        raise ValueError(f"{path}: does not start with a known firmware-container magic "
                          f"({b', '.join(KNOWN_CONTAINER_MAGICS)!r}) - not a firmware "
                          "container this tool recognizes")
    return header, payload


def session_tick():
    """The free first u32 of the 0x0e00 prefix. The watch ignores it (proven: the same
    file installed with 5 different values across the captures), and it looks like a host
    ms tick, so we synthesize one the same way rather than needing a capture to copy."""
    return int(time.monotonic() * 1000) & 0xFFFFFFFF


def header_message(header, x):
    """0x0e00 payload = pack('<II', X, header_len) + the file's own 32-byte header."""
    return CMD_FW_HEADER, struct.pack("<II", x, HEADER_LEN) + header


def build_sequence(header, payload, x, resume=False):
    """The full ordered (command, payload) stream the flasher would send for one file.

    resume=False is a normal install from the application: 0x0202 (enter bootloader) +
    0x0102 (enter transfer mode) first. resume=True is the recovery path when the watch
    is already in BSL from an interrupted transfer - SuuntoLink then skips both and just
    re-streams the whole file from offset 0 (see `resumefirmwarekailash`)."""
    seq = []
    if not resume:
        seq.append((CMD_FW_BOOTLOADER, b""))
        seq.append((CMD_FW_MODE, b""))
    seq.append(header_message(header, x))
    for i in range(0, len(payload), CHUNK):
        seq.append((CMD_FW_DATA, payload[i:i + CHUNK]))
    seq.append((CMD_FW_COMMIT, b""))
    seq.append((CMD_FW_REBOOT, b""))
    return seq


def capture_sequence(capture):
    """The outgoing firmware-opcode messages of one install, in order, as (command,
    payload) - the ground truth. Truncated at the first 0x0200 reboot that ends the
    transfer; anything after (e.g. the 0x0102 seen again once back in the app) is
    post-flash session traffic, not part of the install."""
    seq = []
    for m in messages(capture):
        if m.incoming or m.command not in FW_OPCODES:
            continue
        seq.append((m.command, m.payload))
        if m.command == CMD_FW_REBOOT:
            break
    return seq


def diff_sequences(built, expected):
    """None if identical, else a short human description of the first divergence."""
    if len(built) != len(expected):
        return f"length {len(built)} built vs {len(expected)} in capture"
    for i, (got, want) in enumerate(zip(built, expected)):
        if got != want:
            return (f"message {i}: built 0x{got[0]:04x} len={len(got[1])} "
                    f"vs capture 0x{want[0]:04x} len={len(want[1])}")
    return None


def verify_against_capture(header, payload, capture):
    """Prove the builder reproduces a real install byte-for-byte, and that the session
    tick X is the only free field. Returns True on success."""
    expected = capture_sequence(capture)
    if not expected:
        print(f"  FAIL  {capture}: no firmware-opcode messages found")
        return False
    resume = expected[0][0] not in (CMD_FW_BOOTLOADER, CMD_FW_MODE)
    cap_x, _ = struct.unpack("<II", next(p for c, p in expected if c == CMD_FW_HEADER)[:8])

    # (1) built with the capture's own X must match every byte.
    exact = build_sequence(header, payload, cap_x, resume=resume)
    d = diff_sequences(exact, expected)
    if d:
        print(f"  FAIL  {capture} ({'resume' if resume else 'full'}): {d}")
        return False

    # (2) built with a different X must differ ONLY in 0x0e00's first 4 bytes - proving X
    #     is the single session-free field and nothing else silently depends on it.
    other = build_sequence(header, payload, cap_x ^ 0x5A5A5A5A, resume=resume)
    differing = [i for i, (a, b) in enumerate(zip(exact, other)) if a != b]
    hdr_idx = next(i for i, (c, _) in enumerate(exact) if c == CMD_FW_HEADER)
    if differing != [hdr_idx] or exact[hdr_idx][1][4:] != other[hdr_idx][1][4:]:
        print(f"  FAIL  {capture}: changing X perturbed more than the 0x0e00 prefix")
        return False

    payload_bytes = sum(len(p) for c, p in exact if c == CMD_FW_DATA)
    print(f"  OK    {capture}  [{'resume' if resume else 'full '}]  "
          f"{len(exact)} msgs, {payload_bytes} payload bytes, X free (cap X=0x{cap_x:08x})")
    return True


# Known (firmware file, capture) pairs in the corpus, for --selftest.
SELFTEST_PAIRS = [
    ("assets/Firmware/ambit3peak_Emu-fw_2.4.17-70.2.17414.zip", "assets/pcap/firmware"),
    ("assets/Firmware/ambit3peak_Emu-fw_2.4.17-70.2.17414.zip", "assets/pcap/ambit3firwmare2"),
    ("assets/Firmware/kaylash_Hoopoe-fw_2.0.5-72.1.0.zip", "assets/pcap/kaylashflashfirmware"),
    ("assets/Firmware/kaylash_Hoopoe-fw_2.0.5-72.1.0.zip", "assets/pcap/resumefirmwarekailash"),
]


def repo_root():
    return pathlib.Path(__file__).resolve().parent.parent


def run_selftest():
    root = repo_root()
    ok = True
    print("self-test: rebuild each firmware file and verify byte-exact against its capture\n")
    for fw, cap in SELFTEST_PAIRS:
        header, payload = parse_container(root / fw)
        print(f"  {pathlib.Path(fw).name}")
        ok &= verify_against_capture(header, payload, str(root / cap))
    print(f"\n  {'ALL OK' if ok else 'FAILURES ABOVE'}")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", nargs="?", help="a downloaded firmware container, e.g. from "
                                            "firmware_check.py --download")
    ap.add_argument("--compare", metavar="CAPTURE",
                     help="verify the built stream byte-exact against a real capture, e.g. "
                          "assets/pcap/ambit3firwmare2 (Emu) or "
                          "assets/pcap/kaylashflashfirmware (Hoopoe)")
    ap.add_argument("--resume", action="store_true",
                     help="build the recovery path (watch already in BSL): omit the "
                          "0x0202/0x0102 preamble")
    ap.add_argument("--selftest", action="store_true",
                     help="rebuild every known firmware file and verify it against its "
                          "capture - needs no arguments")
    args = ap.parse_args()

    if args.selftest:
        return run_selftest()
    if not args.file:
        ap.error("give a firmware file, or --selftest")

    header, payload = parse_container(args.file)
    print(f"  {args.file}")
    print(f"  {HEADER_LEN}-byte header + {len(payload)}-byte payload   header={header.hex()}")

    if args.compare:
        return 0 if verify_against_capture(header, payload, args.compare) else 1

    x = session_tick()
    seq = build_sequence(header, payload, x, resume=args.resume)
    data_msgs = sum(1 for c, _ in seq if c == CMD_FW_DATA)
    print(f"\n  built {'resume' if args.resume else 'full'} sequence, "
          f"generated session X=0x{x:08x}:")
    for c, p in seq:
        if c == CMD_FW_DATA:
            continue
        print(f"    0x{c:04x}  {len(p):>4d} B" + (f"  {p.hex()}" if c == CMD_FW_HEADER else ""))
    print(f"    0x{CMD_FW_DATA:04x}  x{data_msgs} chunks of <= {CHUNK} B")
    print(f"\n  {len(seq)} messages total. Nothing was sent - this tool has no hardware "
          "path. Use --compare CAPTURE to verify it against a real install.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
