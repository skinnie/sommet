#!/usr/bin/env python3
"""The desktop Bluetooth link (ble_server.py) without a watch or BlueZ: the handshake, the
answer to the watch's sync notices, which reply belongs to which request, refusals, a drop in
the middle of a command, whole frames on the way out, the "pair again" hint, and the sync lock
(ble_activities.sync_lock). The frames are built with the same encoder the link uses."""

import pathlib
import sys
import threading
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import ble_server as S  # noqa: E402
from ble_activities import sync_lock  # noqa: E402
from ble_link import NspAssembler, encode_nsp_frame  # noqa: E402

HELLO = (b"Emu".ljust(16, b"\0") + b"TESTSERIAL000001" + bytes([2, 4, 17, 0])
         + bytes([0x46, 2, 6, 0x44]) + bytes(8))


class Loop:
    """GLib stand-in: timeout_add callbacks run in order when pump() is called."""

    def __init__(self):
        self.queue = []
        self.lock = threading.Lock()

    def timeout_add(self, _ms, fn, *args):
        with self.lock:
            self.queue.append((fn, args))
        return 1

    def pump(self):
        while True:
            with self.lock:
                if not self.queue:
                    return
                fn, args = self.queue.pop(0)
            fn(*args)


class Chrc:
    def __init__(self):
        self.notifying = True
        self.out = []

    def notify(self, chunk):
        self.out.append(bytes(chunk))
        return True


def sent(chrc):
    return NspAssembler().feed(b"".join(chrc.out))


def watch(command, payload=b"", flags=0x0A, pkt=0, err=0):
    return NspAssembler().feed(encode_nsp_frame(command, payload, flags, 9, pkt, err))[0]


def notice(value):
    return watch(0x1201, bytes.fromhex("0000000001015342454d30313032f601") + bytes([value]),
                 flags=0x06)


def linked():
    chrc, loop = Chrc(), Loop()
    link = S.ServerLink(chrc, loop)
    link.dry_run = False
    link.on_message(notice(1))                      # the opener
    loop.pump()
    assert [(m.command, m.flags) for m in sent(chrc)] == [(0x0000, 0x01)]
    link.on_message(watch(0x0002, HELLO, flags=0x02))
    assert link.handshake_done.is_set() and link.device_info["serial"] == "TESTSERIAL000001"
    chrc.out.clear()
    return link, chrc, loop


def start(link, loop, command, payload=b"", timeout=5.0):
    """Runs link.command() in a thread; returns (thread, result list, the request sent)."""
    result = []

    def run():
        try:
            result.append(link.command(command, payload, timeout=timeout))
        except Exception as exc:                    # noqa: BLE001 - the test reads it
            result.append(exc)
    thread = threading.Thread(target=run)
    thread.start()
    for _ in range(200):
        if link._expecting is not None:
            break
        time.sleep(0.01)
    loop.pump()
    return thread, result, sent(link.notify_chrc)[-1]


def test_sync_notice_is_answered():
    link, chrc, loop = linked()
    link.on_message(notice(0))
    loop.pump()
    (ack,) = sent(chrc)
    assert (ack.command, ack.flags, ack.pkt_num, ack.conn_id, ack.payload) == \
        (0x1201, 0x09, 0, 9, b"")
    assert link.sync_notices == 1


def test_device_info_comes_back_as_0x0002():
    link, chrc, loop = linked()
    thread, result, request = start(link, loop, 0x0000, b"\x02\x48\x03\x00")
    assert request.command == 0x0000 and request.pkt_num == 1
    link.on_message(watch(0x0002, HELLO, pkt=1))
    thread.join(2)
    assert result == [HELLO], result


def test_a_refusal_is_an_error():
    link, chrc, loop = linked()
    thread, result, request = start(link, loop, 0x0b25, b"\x01" * 30)
    link.on_message(watch(0x0b25, b"", flags=0x02, pkt=request.pkt_num, err=1))
    thread.join(2)
    assert isinstance(result[0], S.WatchRefused), result


def test_a_late_reply_is_not_the_next_answer():
    link, chrc, loop = linked()
    thread, result, first = start(link, loop, 0x0b17, b"\x00" * 8, timeout=0.2)
    thread.join(2)
    assert isinstance(result[0], TimeoutError)
    thread, result, second = start(link, loop, 0x0b17, b"\x00" * 8)
    link.on_message(watch(0x0b17, b"old", pkt=first.pkt_num))
    link.on_message(watch(0x0b17, b"new", pkt=second.pkt_num))
    thread.join(2)
    assert result == [b"new"], result


def test_a_drop_wakes_the_waiting_command():
    link, chrc, loop = linked()
    thread, result, _ = start(link, loop, 0x0b17, b"\x00" * 8, timeout=10)
    began = time.monotonic()
    link.reset()
    thread.join(2)
    assert isinstance(result[0], RuntimeError) and "disconnected" in str(result[0])
    assert time.monotonic() - began < 1.5
    # the next session starts with the handshake again, not with a sync-notice answer
    link.on_message(notice(0))
    loop.pump()
    assert sent(chrc)[-1].command == 0x0000


def test_frames_go_out_whole():
    link, chrc, loop = linked()
    result = []
    thread = threading.Thread(target=lambda: result.append(
        link.command(0x0b16, bytes(range(200)), timeout=5)))
    thread.start()
    while not loop.queue:
        time.sleep(0.01)
    link.on_message(notice(0))                      # its answer is queued behind the command
    loop.pump()
    frames = sent(chrc)
    assert [(f.command, f.crc_ok) for f in frames] == [(0x0b16, True), (0x1201, True)], frames
    link.on_message(watch(0x0b16, b"", pkt=frames[0].pkt_num))
    thread.join(2)
    assert result == [b""]


def test_the_packet_number_wraps():
    link, chrc, loop = linked()
    link.dry_run = True
    link._pkt_num = 0xFFFF
    link.command(0x0b02, b"")
    link.command(0x0b02, b"")
    assert link._pkt_num == 1


def test_a_damaged_frame_is_dropped():
    got = []

    class Sink:
        def on_message(self, message):
            got.append(message)
    frame = bytearray(encode_nsp_frame(0x0b02, b"\x05\x00\x00\x00", 0x0A, 9, 3))
    frame[14] ^= 0x01                               # a payload byte (no 0x7e/0x7d nearby)
    S.handle_rx(NspAssembler(), Sink(), bytes(frame))
    assert got == []
    S.handle_rx(NspAssembler(), Sink(), encode_nsp_frame(0x0b02, b"\x05\0\0\0", 0x0A, 9, 3))
    assert len(got) == 1


def test_the_pair_again_hint():
    state = S.LinkState()
    path = "/org/bluez/hci0/dev_0C_8C_DC_00_00_01"
    for n in range(S.QUICK_DROPS_FOR_HINT):
        state.linked(path)
        state.connected_at -= 0.1
        hint = state.dropped(path, paired=False)
        assert (hint is not None) == (n == S.QUICK_DROPS_FOR_HINT - 1)
    assert state.pairing_hint == S.HINT_PAIR_AGAIN
    state.linked(path)
    state.connected_at -= 0.1
    assert state.dropped(path, paired=True) == S.HINT_PAIR_AGAIN_BOTH
    # a session clears it; a link that lasted is not a refused key
    state.linked(path)
    state.on_session()
    assert state.pairing_hint is None and state.quick_drops == 0
    state.connected_at -= 30
    assert state.dropped(path, paired=True) is None and state.quick_drops == 0


def test_a_refusing_watch_is_called_less_often():
    state = S.LinkState()
    path = "/org/bluez/hci0/dev_0C_8C_DC_00_00_01"
    assert S.retry_floor(state) == S.CONNECT_RETRY_BACKOFF_S
    for _ in range(S.QUICK_DROPS_FOR_HINT):
        state.linked(path)
        state.connected_at -= 0.15
        state.dropped(path, paired=True)
    assert S.retry_floor(state) == S.REFUSED_RETRY_S
    state.forgotten("/org/bluez/hci0/dev_AA_BB_CC_00_00_02")    # another device: no change
    assert S.retry_floor(state) == S.REFUSED_RETRY_S
    state.forgotten(path)                                        # forgotten before a new pairing
    assert S.retry_floor(state) == S.CONNECT_RETRY_BACKOFF_S and state.quick_drops == 0


def test_a_link_bluez_will_not_let_go():
    state = S.LinkState()
    path = "/org/bluez/hci0/dev_0C_8C_DC_00_00_01"
    state.linked(path)
    state.connected_at -= 18                  # the watchdog's 8 s, then the 10 s check
    state.lost_by_bluez(path, paired=True)
    status = state.status()
    assert status["linked_device"] is None and status["bluez_stuck"] == "dev_0C_8C_DC_00_00_01"
    assert status["pairing_hint"] == S.HINT_BLUEZ_STUCK and state.quick_drops == 0
    assert state.released("/org/bluez/hci0/dev_AA_BB_CC_00_00_02") is False
    assert state.released(path) is True and state.status()["bluez_stuck"] is None
    assert state.status()["pairing_hint"] is None
    state.linked(path)
    state.lost_by_bluez(path, paired=True)
    state.forgotten(path)                     # forgetting the device clears it as well
    assert state.stuck is None


class FakeAdapter:
    """BlueZ's adapter as one client sees it: `answers` is what each call does in turn
    (None = ok, a string = that error)."""

    def __init__(self, log, answers):
        self.log, self.answers = log, answers

    def _call(self, name):
        self.log.append(name)
        answer = self.answers.pop(0) if self.answers else None
        if answer is not None:
            raise RuntimeError(answer)

    def SetDiscoveryFilter(self, _filter, timeout=None):  # noqa: N802 - BlueZ's name
        self.log.append("filter")

    def StartDiscovery(self, timeout=None):  # noqa: N802
        self._call("start")

    def StopDiscovery(self, timeout=None):  # noqa: N802
        self._call("stop")


def scan_session(*answer_lists):
    log, lists = [], list(answer_lists)

    def connect():
        log.append("open")
        return FakeAdapter(log, lists.pop(0) if lists else []), lambda: log.append("close")
    return S.ScanSession(connect, {"Transport": "le"}), log


def test_a_scan_burst_starts_and_stops():
    scan, log = scan_session([])
    scan.start()
    scan.stop()
    assert log == ["open", "filter", "start", "stop"] and not scan.scanning and scan.resets == 0


def test_one_refused_stop_is_retried():
    busy = "org.bluez.Error.InProgress: Operation already in progress"
    scan, log = scan_session([None, busy, None])
    scan.start()
    scan.stop()
    assert scan.scanning and scan.resets == 0
    scan.stop()
    assert not scan.scanning and scan.refused == 0 and scan.resets == 0


def test_a_scan_bluez_keeps_refusing_to_stop_is_reset():
    busy = "org.bluez.Error.InProgress: Operation already in progress"
    scan, log = scan_session([None, busy, busy, busy], [])
    scan.start()
    scan.stop()
    scan.stop()                               # 2026-10-28: refused all night
    assert scan.resets == 1 and not scan.scanning
    assert log[-3:] == ["close", "open", "filter"]
    scan.start()
    scan.stop()
    assert log[-2:] == ["start", "stop"] and not scan.scanning


def test_an_unanswered_start_resets_at_once():
    scan, log = scan_session(["org.freedesktop.DBus.Error.NoReply: Did not receive a reply"], [])
    scan.start()
    assert scan.resets == 1 and not scan.scanning and log[-3:] == ["close", "open", "filter"]


def test_nothing_to_stop_is_stopped():
    scan, _log = scan_session(["org.bluez.Error.Failed: No discovery started"])
    scan.scanning = True
    scan.stop()
    assert not scan.scanning and scan.resets == 0


def test_sync_lock():
    class Fake:
        def __init__(self, held):
            self.held, self.calls = held, []

        def command(self, cmd, payload=b""):
            self.calls.append((cmd, payload))
            if self.held is None:
                raise TimeoutError("no reply")
            return self.held.to_bytes(4, "little") if cmd == 0x0b19 else b""
    take = Fake(0)
    assert sync_lock(take, True) and take.calls[-1] == (0x0b1a, b"\x01\0\0\0")
    release = Fake(1)
    assert sync_lock(release, False) and release.calls[-1] == (0x0b1a, b"\0\0\0\0")
    already = Fake(1)
    assert sync_lock(already, True) and already.calls == [(0x0b19, b"")]
    silent = Fake(None)
    assert sync_lock(silent, True) is False and silent.calls == [(0x0b19, b"")]


def main():
    tests = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for test in tests:
        test()
        print(f"ok  {test.__name__}")


if __name__ == "__main__":
    main()
