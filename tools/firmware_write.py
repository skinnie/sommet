#!/usr/bin/env python3
"""REAL firmware writer for the Ambit3 / Kailash family - THE ONE WRITE THAT CAN BRICK.

Sends the firmware-install sequence reverse-engineered in `firmware_flash.py` (which is
dry-run only) to real hardware. This file is the only place in the project that transmits
a firmware opcode. Per the standing never-touch-firmware rule it must only ever be run
with André present and an explicit, per-attempt go-ahead.

Safety design, in order of how far it goes:
  * default (no flag)  : connect, read device, verify the file, build - send NOTHING.
  * --stream-only      : enter BSL, send the header, stream every chunk, then STOP before
                         the commit. Fully recoverable: the watch is left in BSL, the exact
                         state `resumefirmwarekailash` starts from and recovers cleanly.
  * --commit           : the whole sequence including 0x0e03 + 0x0200 - but the commit is
                         only reached if EVERY ack during streaming was the empty ack the
                         real captures show. A framing bug trips the check and aborts while
                         still in BSL, before anything is flashed.

Framing is reproduced exactly from the captures: 0x0102 uses send_recv=1 (not the default
5); the sequence counter is reset to 0 at 0x0102 (0x0e00=1, chunks=2..., commit, reboot),
as SuuntoLink does. The 0x0e00 prefix's free first u32 is a fresh host tick (proven
ignored by the watch). The 32-byte header and payload are the file's own bytes, unmodified.

END-TO-END STANDALONE WORKFLOW (no SuuntoLink) - the firmware FILE is obtained by
`firmware_check.py`, which already talks to Suunto's real device-info service and downloads
the official image (an SFI2ST container, despite the .zip name):

    # 1. download the latest official firmware for the connected, healthy watch:
    ./tools/firmware_check.py --download /tmp/emu.zip
    # 2. flash it:
    ./tools/firmware_write.py /tmp/emu.zip --expect-model Emu --commit

RECOVERY CAVEAT: a watch stuck in BSL reports model "BSL" (fw 1.6.13, the bootloader), so
`firmware_check.py` cannot read its real model/hw to pick the right image. Download BEFORE
it goes into BSL, or name the target explicitly:

    ./tools/firmware_check.py --model Emu --hw 70.2.17414 --download /tmp/emu.zip
    ./tools/firmware_write.py /tmp/emu.zip --expect-model Emu --commit   # resumes from BSL
"""

import argparse
import json
import signal
import struct
import sys
import time

# --json progress mode: when on, each phase is emitted as one JSON line on stdout for a GUI
# front-end to parse (see FIRMWARE_FLASHER_DESIGN.md), instead of the human-readable prints.
_JSON = False


def event(phase, human=None, **fields):
    """Emit one progress event. In --json mode prints a JSON line; otherwise prints the
    human string (if given). Always flushed so a GUI reading the pipe sees it live."""
    if _JSON:
        print(json.dumps({"phase": phase, **fields}), flush=True)
    elif human is not None:
        print(human, flush=True)

from ambit_pcap import encode_message
from device_info import read_device_info, read_battery
from firmware_flash import (CHUNK, HEADER_LEN, parse_container, session_tick,
                            CMD_FW_BOOTLOADER, CMD_FW_MODE, CMD_FW_HEADER,
                            CMD_FW_DATA, CMD_FW_COMMIT, CMD_FW_REBOOT)
from write_nav import Link, PRODUCT_IDS


def pid_for_model(model):
    """The USB product_id whose label names this model (e.g. 'Emu' -> 0x001b), so the whole
    flash targets one watch even if others are on the bus. None if unknown -> open() falls
    back to trying all product_ids."""
    for pid, label in PRODUCT_IDS.items():
        if f"({model})" in label:
            return pid
    return None


# Real, 2026-08-22, live on André's own Ambit1 (Bluebird): unlike the Ambit3/Kailash family
# (where entering BSL keeps the same USB product_id and only the 0x0000 model STRING flips
# to "BSL"), Bluebird's bootloader re-enumerates under a DIFFERENT product_id (0x0011,
# "Suunto AmbitBSL") and its 0x0000 model string stays "Bluebird" - it never becomes "BSL"
# at all. poll_model_reopen()'s "wait for model=='BSL'" would spin forever on this family.
# Only Bluebird (0x0010) is confirmed; not yet verified for the Ambit2 product IDs - if one
# of those turns out to behave the same way (own product_id+1 for BSL), add it here rather
# than assuming.
LEGACY_BSL_PID = {0x0010: 0x0011}


# Seconds to wait between the HID reports of one multi-report message. The BSL bootloader
# is minimal firmware and drops interrupt-OUT reports that arrive back-to-back (unlike the
# full application, where fast multi-report writes like 0x0b16 work). SuuntoLink paces the
# 10 reports of each 0x0e01 chunk ~1 ms apart in the real captures; matched here.
REPORT_PACING_S = 0.0015


def read_ack(link, total_timeout=15.0, per_read_ms=1000):
    """Read one reply, retrying until it arrives or `total_timeout` elapses. The single
    read in Link._read_reply() is not robust to a delayed ack: a data chunk's ack comes
    only after the watch erases a flash page (~0.5 s, visible in the capture timing), and
    this hidapi backend returns an empty read on a NAK rather than blocking the full
    timeout - so one read misses it. Loop until the 0x3f head shows up."""
    deadline = time.time() + total_timeout
    while time.time() < deadline:
        head = link.device.read(64, per_read_ms)
        if head and head[0] == 0x3F:
            total, = struct.unpack("<I", bytes(head[16:20]))
            body = bytes(head[20:20 + min(42, total)])
            while len(body) < total:
                more = link.device.read(64, per_read_ms)
                if not more:
                    break
                body += bytes(more[8:8 + min(54, total - len(body))])
            return body
    raise RuntimeError(f"no reply within {total_timeout:.0f}s")


def raw_command(link, command, payload=b"", send_recv=5, fmt=9, ack_timeout=15.0):
    """Like Link.command but lets us pin send_recv/fmt per opcode, and returns the reply
    body. Uses and advances link.sequence so a reset before 0x0102 propagates correctly.
    Paces multi-report writes so the BSL bootloader does not drop reports, and reads the
    ack with retries (see read_ack)."""
    reports = encode_message(command, payload, sequence=link.sequence,
                             send_recv=send_recv, fmt=fmt)
    link.sequence += 1
    for i, report in enumerate(reports):
        if i:
            time.sleep(REPORT_PACING_S)
        link.device.write(report)
    return read_ack(link, total_timeout=ack_timeout)


def expect_empty(reply, what):
    if reply:
        raise RuntimeError(f"{what}: expected an empty ack, got {len(reply)} bytes: "
                           f"{reply[:32].hex()} - aborting BEFORE any commit")


class ChunkStall(Exception):
    """A chunk's write+ack did not finish inside its watchdog window - the USB read or
    write is blocked (seen intermittently on this laptop, not a protocol issue: the real
    capture streams smoothly). Caught by the transfer loop, which reopens and restarts."""


def _watchdog(signum, frame):
    raise ChunkStall("watchdog fired")


def install_watchdog():
    signal.signal(signal.SIGALRM, _watchdog)


def watched(seconds, fn, *args, **kwargs):
    """Run fn under a SIGALRM watchdog. Unlike read_ack's wall-clock loop, the alarm
    interrupts a blocked libusb read/write C call (which can ignore its own timeout when
    the device goes silent), turning a permanent hang into a ChunkStall we can recover
    from. `seconds` must exceed the operation's own expected worst case."""
    signal.alarm(int(seconds))
    try:
        return fn(*args, **kwargs)
    finally:
        signal.alarm(0)


def reopen(link):
    """Close the current HID handle and open a fresh one. Entering/leaving BSL is a real
    USB re-enumeration (the device drops off the bus and comes back with a new address and
    a new iProduct string), so the old handle is stale afterwards - it must be reopened,
    exactly as SuuntoLink re-reads all descriptors after 0x0202."""
    try:
        link.device.close()
    except Exception:
        pass
    fresh = Link(dry_run=False, verbose=False, product_id=getattr(link, "product_id", None))
    fresh.open()
    return fresh


def do_transfer(link, header, payload):
    """One full data transfer from offset 0: 0x0102 (transfer mode) + 0x0e00 (header) +
    every 0x0e01 chunk, each guarded by a SIGALRM watchdog. Raises ChunkStall if a chunk's
    write+ack hangs (e.g. a USB cable jostle stalls the endpoint)."""
    link.sequence = 0
    event("transfer_mode", "  0x0102 -> transfer mode (send_recv=1)")
    expect_empty(watched(15, raw_command, link, CMD_FW_MODE, b"", send_recv=1),
                 "0x0102 fw-mode ack")
    x = session_tick()
    hdr = struct.pack("<II", x, HEADER_LEN) + header
    event("header", f"  0x0e00 -> header announce (X=0x{x:08x})")
    expect_empty(watched(15, raw_command, link, CMD_FW_HEADER, hdr), "0x0e00 header ack")

    total = len(payload)
    event("erase", f"  0x0e01 -> streaming {total} bytes\n    (first chunk erases the whole "
                   "app-flash, ~57 s; then ~0.1 s/chunk)", total=total)
    sent = 0
    for i in range(0, total, CHUNK):
        chunk = payload[i:i + CHUNK]
        first = i == 0
        ack_to = 150.0 if first else 20.0   # first chunk waits out the full-region erase
        dog = 180 if first else 30          # watchdog must exceed the ack window
        expect_empty(watched(dog, raw_command, link, CMD_FW_DATA, chunk, ack_timeout=ack_to),
                     f"0x0e01 chunk at +{i}")
        sent += len(chunk)
        if (i // CHUNK) % 200 == 0 or sent == total:
            if _JSON:
                event("streaming", sent=sent, total=total, percent=round(100 * sent / total, 1))
            else:
                print(f"\r    {sent}/{total} B", end="", flush=True)
    event("streamed", "\n  streaming complete, every ack was empty (clean).")


def stream_with_restart(link, header, payload, max_restarts=4):
    """Run do_transfer; on a ChunkStall (a stalled USB read/write, typically a cable
    jostle) reopen the still-in-BSL watch and restart the transfer from offset 0. Re-sending
    0x0e00 resets the bootloader to the start (re-incurring the ~57 s erase) - that restart
    is the only recovery the protocol offers, and it is exactly what makes this a robust
    standalone flasher. Give up after max_restarts so a truly dead link can't loop forever."""
    for attempt in range(max_restarts + 1):
        try:
            do_transfer(link, header, payload)
            return link
        except ChunkStall as exc:
            if attempt == max_restarts:
                raise RuntimeError(f"gave up after {max_restarts} restarts: {exc}")
            event("restart", f"\n  !! USB stall ({exc}) on attempt {attempt + 1} - reopening "
                  "and restarting the transfer from the start (keep the cable/watch still)",
                  attempt=attempt + 1, reason=str(exc))
            try:
                link = reopen(link)
                info = watched(10, read_device_info, link)
                if info["model"] != "BSL":
                    link, _ = poll_model_reopen(link, "BSL")
            except ChunkStall:
                link = reopen(link)  # best effort; next attempt re-checks state
    return link


def poll_model_reopen(link, want, tries=30, delay=0.5):
    """Reopen the device each attempt until its model string equals `want` - used across a
    re-enumeration (0x0202 into BSL, 0x0200 back to the app). Returns (link, info)."""
    last = None
    for _ in range(tries):
        try:
            link = reopen(link)
            info = read_device_info(link)
            last = info["model"]
            if info["model"] == want:
                return link, info
        except Exception:
            pass
        time.sleep(delay)
    raise RuntimeError(f"device model never became {want!r} across a re-enumeration "
                       f"(last seen {last!r})")


def poll_pid_reopen(want_pid, tries=30, delay=0.5):
    """Legacy-family counterpart to poll_model_reopen(): waits for `want_pid` itself to
    enumerate (see LEGACY_BSL_PID) instead of waiting for a model string that never
    changes on this family. Builds a fresh Link at want_pid each attempt rather than
    reopen()'s reuse of the OLD link's pinned product_id, which would keep looking for the
    pid that just disappeared. Returns (link, info)."""
    import hid
    for _ in range(tries):
        if hid.enumerate(0x1493, want_pid):
            try:
                fresh = Link(dry_run=False, verbose=False, product_id=want_pid)
                fresh.open()
                return fresh, read_device_info(fresh)
            except Exception:
                pass
        time.sleep(delay)
    raise RuntimeError(f"device never enumerated as product_id 0x{want_pid:04x} across a "
                       "re-enumeration")


def initial_pid_for_flash(app_pid):
    """Which product_id to open at, when a legacy-family watch (Bluebird, so far) might
    already be sitting in BSL from an earlier interrupted attempt - its BSL pid (see
    LEGACY_BSL_PID) is on the bus INSTEAD of the app pid, not alongside it, so Link's own
    "try every known pid" fallback (product_id=None) would never find it: LEGACY_BSL_PID's
    values aren't in the general PRODUCT_IDS table (a BSL identity has no business showing
    up in the normal watch-switcher). Checked directly with hid.enumerate rather than
    guessed - real, 2026-08-22, live on André's own Ambit1 mid-resume."""
    bsl_pid = LEGACY_BSL_PID.get(app_pid)
    if bsl_pid is None:
        return app_pid
    import hid
    if not hid.enumerate(0x1493, app_pid) and hid.enumerate(0x1493, bsl_pid):
        return bsl_pid
    return app_pid


def flash(path, expect_model, do_commit, stream_only, probe_enter=False, diag=False):
    header, payload = parse_container(path)
    n_chunks = (len(payload) + CHUNK - 1) // CHUNK

    app_pid = pid_for_model(expect_model)
    link = Link(dry_run=False, verbose=False, product_id=initial_pid_for_flash(app_pid))
    link.open()
    info = read_device_info(link)
    # Real, 2026-08-22: Bluebird's BSL never reports model=="BSL" the Ambit3/Kailash way -
    # it keeps reporting its own real model name ("Bluebird") and signals bootloader mode
    # purely via the product_id switch (see LEGACY_BSL_PID's own comment). Detect via
    # whichever pid actually answered, not the model string, for a legacy-family watch.
    in_bsl = (info["model"] == "BSL") or (link.opened_product_id in LEGACY_BSL_PID.values())
    battery = None if in_bsl else read_battery(link)  # 0x0306 is not answered in BSL
    event("connected",
          f"  connected: model {info['model']}  serial {info['serial']}  "
          f"fw {info['fw_version']}  hw {info['hw_version']}"
          + ("  (already in BSL - resume path)" if in_bsl else f"  battery {battery}%")
          + f"\n  file: {HEADER_LEN}B header + {len(payload)}B payload -> {n_chunks} chunks",
          model=info["model"], serial=info["serial"], fw=info["fw_version"],
          hw=info["hw_version"], battery=battery, in_bsl=in_bsl)

    import watch_registry
    if in_bsl:
        # A BSL watch's device_info model reads "BSL", but its USB product_id still names the
        # real model - so we can identify it even if it was never connected before. The
        # registry (serial -> specs) is a secondary source, e.g. if the pid is ever unknown.
        usb_model = info.get("usb_model")
        seen = watch_registry.lookup(info["serial"])
        if usb_model:
            event("bsl_identified",
                  f"  identified from USB: {usb_model}  hw {info['hw_version']}",
                  codename=usb_model, hw_version=info["hw_version"], source="usb")
        elif seen:
            event("bsl_identified",
                  f"  registry: this is {seen['product']} ({seen['codename']}) "
                  f"hw {seen['hw_version']}, last fw {seen.get('last_fw')}",
                  source="registry", **seen)
        else:
            event("bsl_unknown",
                  "  can't identify this watch (unknown USB product and no registry entry). "
                  "If you don't have the right image, recover it once with SuuntoLink.")
    else:
        watch_registry.record(info)  # remember serial -> codename/hw for future recovery

    if not in_bsl and info["model"] != expect_model:
        event("error", None, message=f"connected model {info['model']!r} != expected "
              f"{expect_model!r}; refusing to flash a mismatched image")
        raise SystemExit(f"  ABORT: connected model {info['model']!r} != --expect-model "
                         f"{expect_model!r}. Refusing to flash a mismatched image.")
    if battery is not None and battery < 30:
        event("error", None, message=f"battery {battery}% under the 30% floor")
        raise SystemExit(f"  ABORT: battery {battery}% is under the 30% floor for a flash.")

    if not do_commit and not stream_only and not probe_enter and not diag:
        print("\n  dry connection only: nothing sent. Re-run with --probe-enter (BSL entry "
              "only), --stream-only (safe, stops before commit) or --commit (full flash).")
        return 0

    if in_bsl:
        event("enter_bsl", "\n  already in BSL: skipping 0x0202 (resume of an earlier BSL "
              "entry)", already=True)
        if probe_enter:
            print("  --probe-enter: nothing to do, the watch is already in BSL.")
            return 0
    else:
        # --- enter the bootloader (device re-enumerates; reopen the handle) ---
        event("enter_bsl", "\n  0x0202 -> enter bootloader", already=False)
        expect_empty(raw_command(link, CMD_FW_BOOTLOADER), "0x0202 bootloader-enter ack")
        bsl_pid = LEGACY_BSL_PID.get(app_pid)
        if bsl_pid is not None:
            link, _ = poll_pid_reopen(bsl_pid)
        else:
            link, _ = poll_model_reopen(link, "BSL")
        print(f"  watch now in BSL (was {info['model']}), handle reopened")

        if probe_enter:
            print("\n  --probe-enter: BSL entry confirmed. Not streaming, not committing. "
                  "The watch is in BSL now (recoverable - --commit finishes, or power-cycle "
                  "to return to the app if no firmware is committed).")
            return 0

    if diag:
        link.sequence = 0
        expect_empty(raw_command(link, CMD_FW_MODE, b"", send_recv=1), "0x0102")
        x = session_tick()
        expect_empty(raw_command(link, CMD_FW_HEADER,
                                 struct.pack("<II", x, HEADER_LEN) + header), "0x0e00")
        reports = encode_message(CMD_FW_DATA, payload[:CHUNK], sequence=link.sequence,
                                 send_recv=5, fmt=9)
        link.sequence += 1
        print(f"  DIAG: writing 1 chunk as {len(reports)} reports, printing write() rc:")
        for i, report in enumerate(reports):
            print(f"    report {i}: write() -> {link.device.write(report)}")
            time.sleep(REPORT_PACING_S)
        print("  DIAG: polling read(64,2000) x10:")
        for k in range(10):
            r = link.device.read(64, 2000)
            print(f"    read {k}: {len(r) if r else 0} bytes"
                  + (f"  {bytes(r[:16]).hex(' ')}" if r else ""))
        print("  DIAG done (no commit).")
        return 0

    # --- stream, with a watchdog per chunk and automatic restart on a USB stall ---
    install_watchdog()
    link = stream_with_restart(link, header, payload)

    if not do_commit:
        event("stream_only_done",
              "\n  --stream-only: STOPPING before 0x0e03. Nothing has been flashed; the old "
              "firmware is intact. The watch is in BSL now (recoverable - re-run with "
              "--commit to finish, which resumes from here).")
        return 0

    # --- the one irreversible step ---
    event("commit", "\n  >>> COMMIT: 0x0e03 (flash) then 0x0200 (reboot) <<<")
    expect_empty(raw_command(link, CMD_FW_COMMIT, ack_timeout=120.0), "0x0e03 commit ack")
    expect_empty(raw_command(link, CMD_FW_REBOOT, ack_timeout=60.0), "0x0200 reboot ack")
    event("rebooting", "  committed. waiting for the watch to reboot into the application...")
    time.sleep(3)
    # Legacy family: reopen() would keep reusing the BSL pid (0x0011) the link is still
    # pinned to, but the reboot re-enumerates back to the APP pid (0x0010) - same
    # product_id-not-model-string distinction as entering BSL, see LEGACY_BSL_PID.
    if app_pid in LEGACY_BSL_PID:
        link, after = poll_pid_reopen(app_pid, tries=60, delay=0.5)
    else:
        link, after = poll_model_reopen(link, expect_model, tries=60, delay=0.5)
    event("done", f"\n  DONE: back in application. model {after['model']}  "
          f"fw {after['fw_version']}  hw {after['hw_version']}",
          model=after["model"], fw=after["fw_version"], hw=after["hw_version"])
    return 0


def main():
    try:
        sys.stdout.reconfigure(line_buffering=True)  # live output when piped to a log
    except Exception:
        pass
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("file", help="firmware container (firmware_check.py --download)")
    ap.add_argument("--expect-model", required=True,
                     help="the connected watch MUST report this model, else abort "
                          "(e.g. Emu). Guards against flashing a mismatched image.")
    g = ap.add_mutually_exclusive_group()
    g.add_argument("--probe-enter", action="store_true",
                    help="only send 0x0202 and confirm the watch re-enumerates into BSL, "
                         "then stop (no streaming, no commit)")
    g.add_argument("--stream-only", action="store_true",
                    help="enter BSL and stream the whole file but STOP before commit "
                         "(brick-proof rehearsal; leaves the watch in BSL)")
    g.add_argument("--commit", action="store_true",
                    help="the full flash including the irreversible commit")
    ap.add_argument("--json", action="store_true",
                     help="emit one JSON progress event per line for a GUI front-end, "
                          "instead of human-readable text (see FIRMWARE_FLASHER_DESIGN.md)")
    args = ap.parse_args()

    global _JSON
    _JSON = args.json
    try:
        return flash(args.file, args.expect_model, args.commit, args.stream_only,
                     probe_enter=args.probe_enter)
    except SystemExit:
        raise
    except Exception as exc:
        # Always leave a terminal event so a GUI reading the stream knows it ended, and why.
        event("error", f"\n  FAILED: {exc}", message=str(exc))
        return 1


if __name__ == "__main__":
    sys.exit(main())
